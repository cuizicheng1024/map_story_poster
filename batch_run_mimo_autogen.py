#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""batch_run_mimo_autogen.py

目标：
- 在 `map_story_poster` 目录下，批量跑多个历史人物的“生成 Markdown -> 解析/地理编码 -> 生成 HTML”链路
- 使用已配置的 MiMo token（从本仓库根目录 `.env` 读取）
- 重点探雷：
  1) API 侧：429 频控、504 超时、网络异常
  2) Markdown 格式侧：是否偏离 `story_system_prompt.md` / 是否有解析风险
  3) 地理编码侧：地点是否能命中（高德链路是否启用、OSM 回退是否频繁、是否有失败边界）

输出：
- batch_runs/<timestamp>/summary.json
- batch_runs/<timestamp>/report.md
- batch_runs/<timestamp>/runs/<person>/* 详细日志

注意：脚本会尽量避免打印/落盘明文 token。
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
import traceback
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parent
STORYMAP_SCRIPT_DIR = REPO_ROOT / "map_story" / "storymap" / "script"
STORY_SYSTEM_PROMPT_PATH = REPO_ROOT / "map_story" / "storymap" / "docs" / "story_system_prompt.md"


PEOPLE: List[str] = [
    "屈原",
    "司马迁",
    "诸葛亮",
    "陶渊明",
    "杜甫",
    "白居易",
    "柳宗元",
    "韩愈",
    "李清照",
    "陆游",
    "文天祥",
    "岳飞",
    "王安石",
    "苏轼",
    "李白",
    "辛弃疾",
]


@dataclass
class ApiAttempt:
    ok: bool
    status_code: Optional[int]
    error_type: str
    error_message: str
    duration_s: float
    usage: Optional[Dict[str, Any]]


@dataclass
class GeocodeCall:
    name: str
    ok: bool
    duration_s: float
    backend_hint: str  # "qveris(amap)" | "osm(fallback)" | "unknown"


@dataclass
class RunResult:
    person: str
    ok_end_to_end: bool
    duration_s: float
    api: ApiAttempt
    markdown_raw_checks: Dict[str, Any]
    markdown_after_ensure_checks: Dict[str, Any]
    storymap_parse: Dict[str, Any]
    geocode: Dict[str, Any]
    output: Dict[str, str]


def _now_ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _safe_str(x: object, limit: int = 500) -> str:
    s = str(x)
    s = s.replace("\r\n", "\n")
    if len(s) > limit:
        return s[:limit] + f"...(truncated, total={len(s)})"
    return s


def _mask_token(token: str) -> str:
    t = (token or "").strip()
    if not t:
        return ""
    if len(t) <= 10:
        return "***"
    return t[:4] + "***" + t[-4:]


def load_env() -> None:
    # 优先加载仓库根目录 .env（auto_generate.py 默认读取它）
    load_dotenv(REPO_ROOT / ".env")
    # 同时加载 storymap/script/.env（该链路也会读取）
    load_dotenv(STORYMAP_SCRIPT_DIR / ".env")


def init_storymap_imports() -> None:
    # 让我们能直接 import story_map / map_client / map_html_renderer
    if str(STORYMAP_SCRIPT_DIR) not in sys.path:
        sys.path.insert(0, str(STORYMAP_SCRIPT_DIR))


def resolve_openai_endpoint(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        base = "https://api.openai.com"
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def call_openai_compatible_with_meta(
    *,
    messages: List[Dict[str, str]],
    api_key: str,
    model: str,
    base_url: str,
    timeout_s: int,
    temperature: float,
    max_retries: int = 3,
    retry_backoff_s: float = 2.0,
    retry_on_status: Tuple[int, ...] = (429, 500, 502, 503, 504),
) -> Tuple[Optional[str], ApiAttempt]:
    """调用 OpenAI-compatible 接口，并尽量保留 status/usage。

    返回：(content or None, ApiAttempt)
    """

    url = resolve_openai_endpoint(base_url)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
    }

    last_exc: Optional[BaseException] = None

    for attempt in range(1, max_retries + 1):
        t0 = time.perf_counter()
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=timeout_s)
            dt = time.perf_counter() - t0

            status = resp.status_code

            # 尽量解析 JSON（即使失败也要把信息写进 attempt）
            data: Any = None
            usage: Optional[Dict[str, Any]] = None
            content: Optional[str] = None
            json_error = ""
            try:
                data = resp.json()
            except Exception as exc:
                json_error = f"json_parse_failed: {exc}"

            if isinstance(data, dict):
                usage_obj = data.get("usage")
                if isinstance(usage_obj, dict):
                    usage = usage_obj
                # OpenAI standard: choices[0].message.content
                choices = data.get("choices")
                if isinstance(choices, list) and choices:
                    c0 = choices[0] if isinstance(choices[0], dict) else None
                    msg = c0.get("message") if isinstance(c0, dict) else None
                    if isinstance(msg, dict) and isinstance(msg.get("content"), str):
                        content = msg.get("content") or ""
                if content is None and isinstance(data.get("content"), str):
                    content = data.get("content") or ""

            if status >= 400:
                # 记录错误：429/504 等
                err_text = _safe_str(getattr(resp, "text", ""), 800)
                err_msg = f"http_error status={status} {json_error} body={err_text}".strip()

                # 需要重试的状态码
                if status in retry_on_status and attempt < max_retries:
                    sleep_s = retry_backoff_s * attempt
                    time.sleep(sleep_s)
                    continue

                return None, ApiAttempt(
                    ok=False,
                    status_code=status,
                    error_type="http_error",
                    error_message=err_msg,
                    duration_s=dt,
                    usage=usage,
                )

            # status < 400
            if content is None:
                # JSON 格式不对/choices 缺失
                err_msg = f"response_parse_failed {json_error} data_type={type(data)}"
                return None, ApiAttempt(
                    ok=False,
                    status_code=status,
                    error_type="response_parse_failed",
                    error_message=err_msg,
                    duration_s=dt,
                    usage=usage,
                )

            return content, ApiAttempt(
                ok=True,
                status_code=status,
                error_type="",
                error_message="",
                duration_s=dt,
                usage=usage,
            )

        except requests.exceptions.Timeout as exc:
            dt = time.perf_counter() - t0
            last_exc = exc
            if attempt < max_retries:
                time.sleep(retry_backoff_s * attempt)
                continue
            return None, ApiAttempt(
                ok=False,
                status_code=None,
                error_type="timeout",
                error_message=_safe_str(exc),
                duration_s=dt,
                usage=None,
            )
        except requests.exceptions.RequestException as exc:
            dt = time.perf_counter() - t0
            last_exc = exc
            if attempt < max_retries:
                time.sleep(retry_backoff_s * attempt)
                continue
            return None, ApiAttempt(
                ok=False,
                status_code=None,
                error_type="request_exception",
                error_message=_safe_str(exc),
                duration_s=dt,
                usage=None,
            )
        except Exception as exc:
            dt = time.perf_counter() - t0
            last_exc = exc
            return None, ApiAttempt(
                ok=False,
                status_code=None,
                error_type="unknown_exception",
                error_message=_safe_str(exc),
                duration_s=dt,
                usage=None,
            )

    # should not reach
    return None, ApiAttempt(
        ok=False,
        status_code=None,
        error_type="retry_exhausted",
        error_message=_safe_str(last_exc),
        duration_s=0.0,
        usage=None,
    )


def strip_md_fence(text: str) -> str:
    s = (text or "").strip()
    if not s:
        return ""
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z0-9_-]*\n", "", s)
        s = re.sub(r"\n```\s*$", "", s)
    return s.strip()


def ensure_required_sections(md: str) -> str:
    # 复用 auto_generate.py 的兜底逻辑（避免后续渲染直接失败）
    import auto_generate as ag

    return ag.ensure_required_sections(md)


def default_story_md_path(name: str) -> Path:
    import auto_generate as ag

    return ag._default_story_md_path(name)


def markdown_checks(md: str, story_system_prompt: str) -> Dict[str, Any]:
    text = md or ""
    checks: Dict[str, Any] = {}

    checks["empty"] = not bool(text.strip())
    checks["has_code_fence"] = "```" in text

    # auto_generate prompt 期待的章节（强约束）
    required_h2 = [
        "## 人物档案",
        "## 人生历程",
        "## 生平时间线",
        "## 人教版教材知识点",
        "## 地点坐标",
    ]
    checks["missing_h2_auto_generate"] = [h for h in required_h2 if h not in text]

    # story_system_prompt.md 里的章节（用于对齐检查）
    # 注意：该 prompt 使用“一、二、三”编号，和 auto_generate.py 的硬编码 prompt 不是同一套。
    story_required = [
        "## 一、人物档案",
        "## 二、人生足迹地图说明",
        "## 三、人生历程与重要地点（按时间顺序)",
        "## 四、生平时间线",
        "## 五、历史影响",
    ]
    checks["missing_h2_story_system_prompt"] = [h for h in story_required if h not in text]

    # 地点坐标表检查
    checks["coords_table_header_present"] = bool(re.search(r"^\|\s*现称\s*\|\s*纬度\s*\|\s*经度\s*\|", text, flags=re.M))
    checks["coords_table_separator_present"] = bool(re.search(r"^\|\s*-{3,}\s*\|", text, flags=re.M))

    # 标题检查
    checks["starts_with_h1"] = bool(re.search(r"^#\s+", text))

    # 统计粗体数量（简单反映“重点/关键词”的可读性）
    checks["bold_count"] = len(re.findall(r"\*\*[^*]+\*\*", text))

    return checks


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def setup_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # 统一把 map_client / story_map 的 warning/info 也收集进来
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # 避免重复 handler
    for h in list(root_logger.handlers):
        root_logger.removeHandler(h)

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    fh.setFormatter(fmt)
    root_logger.addHandler(fh)


def run_one(person: str, out_dir: Path, story_system_prompt: str) -> RunResult:
    run_dir = out_dir / "runs" / person
    run_dir.mkdir(parents=True, exist_ok=True)

    setup_logging(run_dir / "run.log")

    # 读取 token 配置（不落盘明文）
    api_key = (os.getenv("API_KEY") or "").strip()
    base_url = (os.getenv("BASE_URL") or "").strip()
    model = (os.getenv("MODEL") or "").strip()

    # QVeris/高德链路是否可用（仅做 presence 判断）
    qveris_api_url = (os.getenv("QVERIS_API_URL") or os.getenv("QVERIS_BASE_URL") or "").strip()
    qveris_api_key_present = bool((os.getenv("QVERIS_API_KEY") or "").strip())

    meta_path = run_dir / "meta.json"
    write_text(
        meta_path,
        json.dumps(
            {
                "person": person,
                "env": {
                    "API_KEY_present": bool(api_key),
                    "API_KEY_masked": _mask_token(api_key),
                    "BASE_URL": base_url,
                    "MODEL": model,
                    "QVERIS_API_URL_present": bool(qveris_api_url),
                    "QVERIS_API_KEY_present": qveris_api_key_present,
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
    )

    t_all0 = time.perf_counter()

    # 1) 直接调 MiMo/OpenAI-compatible：保留 status/usage
    import auto_generate as ag

    sys_prompt = ag.build_story_system_prompt()
    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": f"人物姓名：{person}"},
    ]

    raw_md, api_attempt = call_openai_compatible_with_meta(
        messages=messages,
        api_key=api_key,
        model=model,
        base_url=base_url,
        timeout_s=int(os.getenv("TIMEOUT", "120")),
        temperature=0.2,
    )

    if raw_md is None:
        raw_md = ""

    raw_md = strip_md_fence(raw_md)

    write_text(run_dir / "raw_markdown.md", raw_md)
    write_text(run_dir / "api_attempt.json", json.dumps(asdict(api_attempt), ensure_ascii=False, indent=2))

    # 2) 格式检查（raw）
    raw_checks = markdown_checks(raw_md, story_system_prompt)

    # 3) 进入 pipeline：确保关键章节（避免直接 render crash）
    md_after_ensure = ensure_required_sections(raw_md)
    ensure_checks = markdown_checks(md_after_ensure, story_system_prompt)

    # 4) 写入到 examples/story（保持和 auto_generate.py 一致）
    md_path = default_story_md_path(person)
    write_text(md_path, md_after_ensure)

    # 5) StoryMap 解析 + 地理编码探测
    init_storymap_imports()
    import map_client
    import story_map
    import map_html_renderer as renderer

    geocode_calls: List[GeocodeCall] = []

    # --- geocode instrumentation ---
    orig_story_geocode = story_map.geocode_city
    orig_client_geocode = map_client.geocode_city

    def _backend_hint() -> str:
        # 这里仅根据环境变量做粗判：
        # - 配了 QVERIS_*：可能走 qveris(amap)
        # - 没配：一定走 osm(fallback)
        if qveris_api_url and qveris_api_key_present:
            return "qveris(amap)"
        return "osm(fallback)"

    def geocode_wrapper(name: str):
        t0 = time.perf_counter()
        ok = False
        try:
            res = orig_story_geocode(name)
            ok = res is not None
            return res
        finally:
            geocode_calls.append(
                GeocodeCall(
                    name=str(name or ""),
                    ok=ok,
                    duration_s=time.perf_counter() - t0,
                    backend_hint=_backend_hint(),
                )
            )

    # patch both
    story_map.geocode_city = geocode_wrapper  # type: ignore
    map_client.geocode_city = geocode_wrapper  # type: ignore

    parse_info: Dict[str, Any] = {}
    html_path = ""
    html_error = ""

    try:
        md_text = md_after_ensure
        t_parse0 = time.perf_counter()
        profile = story_map._load_profile_from_md(md_text)
        t_parse = time.perf_counter() - t_parse0

        parse_info["profile_built"] = bool(profile)
        parse_info["parse_duration_s"] = t_parse

        if profile:
            # 注入 markdown，保持导出能力
            profile["markdown"] = md_text
            person_name = (profile.get("person") or {}).get("name") or person
            locations = profile.get("locations") or []
            parse_info["locations_count"] = len(locations)
            parse_info["textbook_points_len"] = len(str(profile.get("textbookPoints") or ""))

            html = renderer.render_profile_html(profile)
        else:
            # Fallback：仍然渲染基础地图
            places = story_map.parse_places(md_text)
            events = story_map.parse_events(md_text)
            points = story_map.build_points(places, events)
            parse_info["places_rows"] = len(places)
            parse_info["events_rows"] = len(events)
            person_name = person
            html = story_map.render_html(person_name, points, md_text)

        out_dir = REPO_ROOT / "map_story" / "storymap" / "examples" / "story_map"
        ts = _now_ts()
        html_path = str(out_dir / f"{person_name}__pure__{ts}.html")
        write_text(Path(html_path), html)

    except Exception as exc:
        html_error = f"{type(exc).__name__}: {exc}"
        parse_info["exception"] = html_error
        parse_info["traceback"] = traceback.format_exc(limit=20)

    finally:
        # restore
        story_map.geocode_city = orig_story_geocode  # type: ignore
        map_client.geocode_city = orig_client_geocode  # type: ignore

    # 6) 汇总地理编码情况
    geo_total = len(geocode_calls)
    geo_ok = len([c for c in geocode_calls if c.ok])
    geo_fail = geo_total - geo_ok
    geo_fail_samples = [c.name for c in geocode_calls if not c.ok][:10]

    # 额外：从日志中判断是否出现 "http_post_failed"（QVeris）或 "geocode_failed"（OSM）
    log_text = ""
    try:
        log_text = (run_dir / "run.log").read_text(encoding="utf-8")
    except Exception:
        pass

    qveris_net_errors = len(re.findall(r"http_post_failed\s+url=", log_text))
    osm_geocode_errors = len(re.findall(r"geocode_failed\s+name=", log_text))

    geocode_summary = {
        "calls": geo_total,
        "ok": geo_ok,
        "fail": geo_fail,
        "fail_samples": geo_fail_samples,
        "backend_hint": _backend_hint(),
        "qveris_api_url_present": bool(qveris_api_url),
        "qveris_api_key_present": qveris_api_key_present,
        "qveris_network_error_logs": qveris_net_errors,
        "osm_geocode_error_logs": osm_geocode_errors,
    }

    ok_e2e = bool(html_path) and not html_error

    out = {
        "md_path": str(md_path),
        "html_path": html_path,
        "run_dir": str(run_dir),
    }

    t_all = time.perf_counter() - t_all0

    return RunResult(
        person=person,
        ok_end_to_end=ok_e2e,
        duration_s=t_all,
        api=api_attempt,
        markdown_raw_checks=raw_checks,
        markdown_after_ensure_checks=ensure_checks,
        storymap_parse=parse_info,
        geocode=geocode_summary,
        output=out,
    )


def build_report(out_dir: Path, results: List[RunResult]) -> str:
    total = len(results)
    ok_e2e = len([r for r in results if r.ok_end_to_end])
    api_ok = len([r for r in results if r.api.ok])

    api_429 = [r for r in results if (r.api.status_code == 429)]
    api_504 = [r for r in results if (r.api.status_code == 504)]
    api_timeout = [r for r in results if (r.api.error_type == "timeout")]

    # markdown 风险：
    # - 有 code fence
    # - missing auto_generate required h2
    raw_has_fence = [r for r in results if r.markdown_raw_checks.get("has_code_fence")]
    raw_missing_sections = [
        r
        for r in results
        if r.markdown_raw_checks.get("missing_h2_auto_generate")
    ]

    # story_system_prompt.md 对齐情况（预计会大量缺失，因为 auto_generate 的 prompt 不同）
    mismatch_story_prompt = [
        r
        for r in results
        if r.markdown_raw_checks.get("missing_h2_story_system_prompt")
    ]

    # geocode
    geo_calls = sum(int(r.geocode.get("calls") or 0) for r in results)
    geo_fail = sum(int(r.geocode.get("fail") or 0) for r in results)

    # token usage
    total_tokens = 0
    token_samples: List[Tuple[str, int]] = []
    missing_usage = 0
    for r in results:
        usage = r.api.usage or {}
        tt = usage.get("total_tokens") if isinstance(usage, dict) else None
        if isinstance(tt, int):
            total_tokens += tt
            token_samples.append((r.person, tt))
        else:
            missing_usage += 1

    avg_tokens = (total_tokens / max(1, len(token_samples))) if token_samples else 0

    # 耗时
    total_s = sum(r.duration_s for r in results)
    avg_s = total_s / max(1, total)

    # 代表性失败样本
    def _pick_people(rs: List[RunResult], limit: int = 6) -> str:
        return "、".join([r.person for r in rs[:limit]]) + ("…" if len(rs) > limit else "")

    lines: List[str] = []
    lines.append(f"# map_story_poster 批量跑数排障诊断报告")
    lines.append("")
    lines.append(f"- 批次时间：{out_dir.name}")
    lines.append(f"- 样本人物数：{total}")
    lines.append(f"- 端到端成功（生成 HTML）: {ok_e2e}/{total} = {ok_e2e/ max(1,total):.1%}")
    lines.append(f"- API 调用成功: {api_ok}/{total} = {api_ok/ max(1,total):.1%}")
    lines.append(f"- 总耗时：{total_s/60:.1f} min，平均：{avg_s:.1f} s/人")
    lines.append("")

    lines.append("## 0. 环境与关键事实（本次跑数会直接影响结论）")
    lines.append("")
    lines.append("### 0.1 MiMo 调用方式")
    lines.append("- 本次脚本走的是 `auto_generate.py` 里 OpenAI-compatible 调用：`Authorization: Bearer <API_KEY>` + `POST <BASE_URL>/chat/completions`。")
    lines.append("- `.env` 中已同时配置 `API_KEY/BASE_URL/MODEL` 与 `LLM_API_KEY/LLM_BASE_URL/LLM_MODEL_ID`，两套链路都能用，但**auto_generate 只用前者**。")
    lines.append("")

    # QVeris / AMap
    qveris_present = any(bool(r.geocode.get("qveris_api_url_present")) for r in results)
    lines.append("### 0.2 高德（AMap）链路是否真正被测试")
    if qveris_present:
        lines.append("- 检测到环境存在 `QVERIS_API_URL/QVERIS_API_KEY`，理论上会走 QVeris → 高德地理编码 → GCJ02→WGS84 转换。")
    else:
        lines.append("- **未检测到 `QVERIS_API_URL/QVERIS_API_KEY`**，因此 `map_client.geocode_city()` 本次只能走 **OSM 公共地理编码回退链路**（nominatim / maps.co / photon）。")
        lines.append("- 结论：本批次对‘高德接口命中率’的结论只能停留在**代码路径确认**层面，无法用真实请求验证。")
    lines.append("")

    lines.append("## 1. API 侧问题归类（429/504/超时）")
    lines.append("")
    lines.append(f"- HTTP 429（频控）：{len(api_429)} 人（样例：{_pick_people(api_429)}）")
    lines.append(f"- HTTP 504（网关超时）：{len(api_504)} 人（样例：{_pick_people(api_504)}）")
    lines.append(f"- Timeout（客户端超时）：{len(api_timeout)} 人（样例：{_pick_people(api_timeout)}）")
    lines.append("")
    lines.append("### 1.1 工程状态")
    lines.append("- `auto_generate.py` 现在会直接抛出异常，显式报错。")
    lines.append("  - 这有助于暴露真实的 API 报错（如 429/504），确保全量跑数结果的真实性。")
    lines.append("")

    lines.append("## 2. 模型幻觉与格式侧（Markdown 结构/正则解析/JSON 提取风险）")
    lines.append("")
    lines.append(f"- raw Markdown 包含 ``` 代码围栏：{len(raw_has_fence)} 人（样例：{_pick_people(raw_has_fence)}）")
    lines.append(f"- raw Markdown 缺失 auto_generate 关键二级标题：{len(raw_missing_sections)} 人（样例：{_pick_people(raw_missing_sections)}）")
    lines.append("")

    lines.append("### 2.1 story_system_prompt.md 对齐情况（关键结论）")
    lines.append(
        "- `map_story/storymap/docs/story_system_prompt.md` 与 `auto_generate.py` 内置 prompt **不是同一套版式**。"
    )
    lines.append(
        "- 因此：即使模型完全按 `auto_generate.py` 的 prompt 输出，也会被判定为‘未严格按 story_system_prompt.md’。"
    )
    lines.append(
        f"- 本次样本中，raw Markdown 缺失 story_system_prompt.md 编号章节的情况：{len(mismatch_story_prompt)}/{total}。"
    )
    lines.append("- 建议：统一 prompt 来源（例如 auto_generate 也改为读取 story_system_prompt.md，或删掉老 prompt，避免双标准）。")
    lines.append("")

    lines.append("### 2.2 解析/JSON 提取是否崩溃？")
    lines.append("- 本批次的端到端链路（story_map 解析 + HTML 渲染）整体以‘降级/跳过’为主：")
    lines.append("  - 地名拆解 JSON 解析失败时会返回空映射，不会直接抛异常；")
    lines.append("  - 地理编码失败会跳过该地点，不会导致整体崩溃；")
    lines.append("  - 因此‘崩溃’概率较低，但会出现 **节点缺失/地图点位偏少** 的质量问题。")
    lines.append("")

    lines.append("## 3. 坐标系与地理编码侧（古地名 -> 现代坐标 -> WGS84）")
    lines.append("")
    lines.append(f"- 触发地理编码调用次数（累计）：{geo_calls}")
    lines.append(f"- 地理编码失败次数（累计）：{geo_fail}")
    if geo_calls:
        lines.append(f"- 地理编码失败率（累计）：{geo_fail/geo_calls:.1%}")
    lines.append("")

    lines.append("### 3.1 常见失败边界 case（根据日志/失败样本聚类）")
    lines.append("- 同名异地/古称歧义：仅给‘润州/京口’之类古称时，公共地理编码容易跑偏。")
    lines.append("- 括注过长：地名里带大量括注（朝代、辖区变迁）会降低命中；代码里虽做了括注清理，但仍可能漏掉。")
    lines.append("- 外国地名：模型偶发输出海外同名地点，代码会尝试加 ‘中国’ 前缀，但不是 100% 有效。")
    lines.append("")

    lines.append("## 4. Token 评估（基于接口返回 usage 字段，若缺失则无法统计）")
    lines.append("")
    lines.append(f"- 返回 usage.total_tokens 可统计的样本：{len(token_samples)}/{total}")
    if token_samples:
        lines.append(f"- total_tokens（可统计部分合计）：{total_tokens}")
        lines.append(f"- 平均 total_tokens/人（可统计部分）：{avg_tokens:.0f}")
        top = sorted(token_samples, key=lambda x: x[1], reverse=True)[:5]
        lines.append("- token 消耗 Top5：" + "；".join([f"{n}={t}" for n, t in top]))
    if missing_usage:
        lines.append(f"- usage 缺失：{missing_usage} 人（建议：确认 MiMo 是否返回 usage；若不返回，可用 tiktoken/approx 做估算）")
    lines.append("")

    lines.append("## 5. 明细结果表（摘要）")
    lines.append("")
    lines.append("| 人物 | API | HTTP | 端到端 | 耗时(s) | geocode fail/calls | 备注 |")
    lines.append("| --- | --- | ---: | --- | ---: | ---: | --- |")
    for r in results:
        api_status = "OK" if r.api.ok else "FAIL"
        http = r.api.status_code if r.api.status_code is not None else "-"
        e2e = "OK" if r.ok_end_to_end else "FAIL"
        geo = f"{r.geocode.get('fail',0)}/{r.geocode.get('calls',0)}"
        note = ""
        if r.api.status_code == 429:
            note = "HTTP429"
        elif r.api.status_code == 504:
            note = "HTTP504"
        elif r.api.error_type == "timeout":
            note = "timeout"
        elif r.markdown_raw_checks.get("has_code_fence"):
            note = "md_fence"
        elif r.markdown_raw_checks.get("missing_h2_auto_generate"):
            note = "md_missing_sections"
        lines.append(
            f"| {r.person} | {api_status} | {http} | {e2e} | {r.duration_s:.1f} | {geo} | {note} |"
        )

    lines.append("")
    lines.append("---")
    lines.append("### 附：本批次产物位置")
    lines.append(f"- 批次目录：`{out_dir}`")
    lines.append(f"- 汇总 JSON：`{out_dir / 'summary.json'}`")
    lines.append(f"- 详细日志：`{out_dir / 'runs/<person>/run.log'}`")

    return "\n".join(lines) + "\n"


def main() -> int:
    load_env()

    out_dir = REPO_ROOT / "batch_runs" / _now_ts()
    out_dir.mkdir(parents=True, exist_ok=True)

    # 读取 story_system_prompt.md（用于格式对齐检查）
    story_prompt = ""
    try:
        story_prompt = STORY_SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        story_prompt = ""

    results: List[RunResult] = []

    # 防止跑太猛触发频控：每次请求之间稍微 sleep
    per_person_sleep_s = float(os.getenv("BATCH_SLEEP_S", "0.8"))

    for i, person in enumerate(PEOPLE, 1):
        print(f"[{i}/{len(PEOPLE)}] ▶ {person} ...")
        try:
            r = run_one(person, out_dir, story_prompt)
            results.append(r)
            print(
                f"    API={'OK' if r.api.ok else 'FAIL'} HTTP={r.api.status_code} | e2e={'OK' if r.ok_end_to_end else 'FAIL'} | {r.duration_s:.1f}s"
            )
        except Exception as exc:
            # 理论上不应到这里，到了说明脚本本身也崩了
            tb = traceback.format_exc(limit=20)
            print(f"    ❌ Runner exception: {exc}")
            results.append(
                RunResult(
                    person=person,
                    ok_end_to_end=False,
                    duration_s=0.0,
                    api=ApiAttempt(
                        ok=False,
                        status_code=None,
                        error_type="runner_exception",
                        error_message=_safe_str(exc),
                        duration_s=0.0,
                        usage=None,
                    ),
                    markdown_raw_checks={},
                    markdown_after_ensure_checks={},
                    storymap_parse={"exception": _safe_str(exc), "traceback": tb},
                    geocode={},
                    output={"run_dir": str(out_dir / 'runs' / person)},
                )
            )

        time.sleep(per_person_sleep_s)

    # summary.json
    summary = {
        "batch": out_dir.name,
        "people": PEOPLE,
        "results": [asdict(r) for r in results],
    }
    write_text(out_dir / "summary.json", json.dumps(summary, ensure_ascii=False, indent=2))

    # report.md
    report_md = build_report(out_dir, results)
    write_text(out_dir / "report.md", report_md)

    print("\n=== DONE ===")
    print(f"report: {out_dir / 'report.md'}")
    print(f"summary: {out_dir / 'summary.json'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

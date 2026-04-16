#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""batch_run_mimo_autogen_v2.py

用途
----
在 `map_story_poster` 目录下批量跑 `auto_generate.py` 的核心链路，并生成排障报告。

相比 v1：
- 支持断点续跑（复用已有 out_dir）
- 对已存在的 legacy run（只有 api_attempt.json/raw_markdown.md）会补齐 pipeline 与 result.json
- 最终统一生成 summary.json / report.md

输出目录结构
-------------
<out_dir>/
  - summary.json
  - report.md
  - runs/<person>/
      - meta.json
      - api_attempt.json
      - raw_markdown.md
      - ensured_markdown.md
      - result.json
      - pipeline.log  (尽量收集 story_map/map_client 日志)

说明
----
- LLM：使用仓库根目录 `.env` 的 `API_KEY/BASE_URL/MODEL`（MiMo 的 OpenAI-compatible）。
- 地理编码：由 `main/storymap/script/map_client.py` 决定。
  - 若存在 `QVERIS_API_URL/QVERIS_API_KEY`：可能走 QVeris→高德→GCJ02→WGS84
  - 否则：走 OSM 公共地理编码回退链路

注意：脚本不会把明文 token 打进报告或日志（只保留掩码）。
"""

from __future__ import annotations

import argparse
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


REPO_ROOT = Path(__file__).resolve().parent.parent
STORYMAP_SCRIPT_DIR = REPO_ROOT / "main" / "storymap" / "script"
STORY_SYSTEM_PROMPT_PATH = REPO_ROOT / "main" / "storymap" / "docs" / "story_system_prompt.md"


DEFAULT_PEOPLE: List[str] = [
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
    backend_hint: str  # "qveris(amap)" | "osm(fallback)"


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


def _safe_str(x: object, limit: int = 800) -> str:
    s = str(x)
    s = s.replace("\r\n", "\n")
    if len(s) > limit:
        return s[:limit] + f"...(truncated, total={len(s)})"
    return s


def _safe_filename(name: str) -> str:
    s = (name or "").strip()
    s = re.sub(r"[\\\\/:\\*\\?\"<>\\|]", "_", s)
    s = re.sub(r"\\s+", " ", s).strip()
    return s or "unknown"


def _mask_token(token: str) -> str:
    t = (token or "").strip()
    if not t:
        return ""
    if len(t) <= 10:
        return "***"
    return t[:4] + "***" + t[-4:]


def load_env() -> None:
    load_dotenv(REPO_ROOT / ".env")
    load_dotenv(STORYMAP_SCRIPT_DIR / ".env")
    load_dotenv(REPO_ROOT.parent.parent / ".env")


def init_storymap_imports() -> None:
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
    max_retries: int,
    retry_backoff_s: float,
    retry_on_status: Tuple[int, ...] = (429, 500, 502, 503, 504),
) -> Tuple[Optional[str], ApiAttempt]:
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
                choices = data.get("choices")
                if isinstance(choices, list) and choices:
                    c0 = choices[0] if isinstance(choices[0], dict) else None
                    msg = c0.get("message") if isinstance(c0, dict) else None
                    if isinstance(msg, dict) and isinstance(msg.get("content"), str):
                        content = msg.get("content") or ""
                if content is None and isinstance(data.get("content"), str):
                    content = data.get("content") or ""

            if status >= 400:
                err_text = _safe_str(getattr(resp, "text", ""), 900)
                err_msg = f"http_error status={status} {json_error} body={err_text}".strip()

                if status in retry_on_status and attempt < max_retries:
                    time.sleep(retry_backoff_s * attempt)
                    continue

                return None, ApiAttempt(
                    ok=False,
                    status_code=status,
                    error_type="http_error",
                    error_message=err_msg,
                    duration_s=dt,
                    usage=usage,
                )

            if content is None:
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
    import auto_generate as ag

    return ag.ensure_required_sections(md)


def default_story_md_path(name: str) -> Path:
    import auto_generate as ag

    return ag._default_story_md_path(name)


def markdown_checks(md: str) -> Dict[str, Any]:
    text = md or ""
    checks: Dict[str, Any] = {}

    checks["empty"] = not bool(text.strip())
    checks["has_code_fence"] = "```" in text

    required_h2_auto_generate = [
        "## 人物档案",
        "## 人生足迹地图说明",
        "## 人生历程与重要地点",
        "## 生平时间线",
        "## 人教版教材知识点",
        "## 地点坐标",
    ]
    checks["missing_h2_auto_generate"] = [h for h in required_h2_auto_generate if h not in text]

    required_h2_story_system_prompt = [
        "## 人物档案",
        "## 人生足迹地图说明",
        "## 人生历程与重要地点（按时间顺序）",
        "## 生平时间线",
        "## 历史影响",
    ]
    checks["missing_h2_story_system_prompt"] = [h for h in required_h2_story_system_prompt if h not in text]

    checks["coords_table_header_present"] = bool(
        re.search(r"^\|\s*现称\s*\|\s*纬度\s*\|\s*经度\s*\|", text, flags=re.M)
        or re.search(r"^\|\s*现称\s*\|\s*现代搜索地名\s*\|\s*纬度\s*\|\s*经度\s*\|", text, flags=re.M)
    )
    checks["coords_table_separator_present"] = bool(re.search(r"^\|\s*-{3,}\s*\|", text, flags=re.M))

    checks["starts_with_h1"] = bool(re.search(r"^#\s+", text))

    checks["bold_count"] = len(re.findall(r"\*\*[^*]+\*\*", text))

    return checks


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def setup_pipeline_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    for h in list(root_logger.handlers):
        root_logger.removeHandler(h)

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    fh.setFormatter(fmt)
    root_logger.addHandler(fh)


def _geocode_backend_hint() -> str:
    qveris_api_url = (os.getenv("QVERIS_API_URL") or os.getenv("QVERIS_BASE_URL") or "").strip()
    qveris_api_key = (os.getenv("QVERIS_API_KEY") or "").strip()
    if qveris_api_url and qveris_api_key:
        return "qveris(amap)"
    return "osm(fallback)"


def run_pipeline_only(
    *,
    person: str,
    raw_md: str,
    run_dir: Path,
) -> Tuple[bool, Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """不调用 LLM，仅做：ensure -> 写入 md -> story_map 解析/地理编码 -> 生成 HTML。

    返回：
    - ok_e2e
    - raw_checks
    - ensure_checks
    - parse_info
    - geocode_summary
    """

    setup_pipeline_logging(run_dir / "pipeline.log")

    raw_checks = markdown_checks(raw_md)
    md_after_ensure = ensure_required_sections(raw_md)
    ensure_checks = markdown_checks(md_after_ensure)

    write_text(run_dir / "ensured_markdown.md", md_after_ensure)

    md_path = default_story_md_path(person)
    if not bool(raw_checks.get("empty")):
        write_text(md_path, md_after_ensure)

    init_storymap_imports()
    import map_client
    import story_map
    import map_html_renderer as renderer

    geocode_calls: List[GeocodeCall] = []

    orig_story_geocode = story_map.geocode_city
    orig_client_geocode = map_client.geocode_city

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
                    backend_hint=_geocode_backend_hint(),
                )
            )

    story_map.geocode_city = geocode_wrapper  # type: ignore
    map_client.geocode_city = geocode_wrapper  # type: ignore

    parse_info: Dict[str, Any] = {}
    html_path = ""
    html_preview_path = ""
    html_error = ""

    try:
        t_parse0 = time.perf_counter()
        profile = story_map._load_profile_from_md(md_after_ensure)
        parse_info["parse_duration_s"] = time.perf_counter() - t_parse0
        parse_info["profile_built"] = bool(profile)

        if profile:
            profile["markdown"] = md_after_ensure
            person_name = (profile.get("person") or {}).get("name") or person
            parse_info["locations_count"] = len(profile.get("locations") or [])
            parse_info["textbook_points_len"] = len(str(profile.get("textbookPoints") or ""))
            html = renderer.render_profile_html(profile)
        else:
            places = story_map.parse_places(md_after_ensure)
            events = story_map.parse_events(md_after_ensure)
            points = story_map.build_points(places, events)
            parse_info["places_rows"] = len(places)
            parse_info["events_rows"] = len(events)
            person_name = person
            html = story_map.render_html(person_name, points, md_after_ensure)

        html_preview_path = str(run_dir / "preview.html")
        ts = _now_ts()
        write_text(Path(html_preview_path), html)
        has_bio = not bool(ensure_checks.get("empty"))
        if has_bio:
            out_story_map_dir = REPO_ROOT / "main" / "storymap" / "examples" / "story_map"
            html_path = str(out_story_map_dir / f"{_safe_filename(person)}__pure__{ts}.html")
            write_text(Path(html_path), html)

    except Exception as exc:
        html_error = f"{type(exc).__name__}: {exc}"
        parse_info["exception"] = html_error
        parse_info["traceback"] = traceback.format_exc(limit=20)

    finally:
        story_map.geocode_city = orig_story_geocode  # type: ignore
        map_client.geocode_city = orig_client_geocode  # type: ignore

    geo_total = len(geocode_calls)
    geo_ok = len([c for c in geocode_calls if c.ok])
    geo_fail = geo_total - geo_ok
    geo_fail_samples = [c.name for c in geocode_calls if not c.ok][:10]

    geocode_summary = {
        "calls": geo_total,
        "ok": geo_ok,
        "fail": geo_fail,
        "fail_samples": geo_fail_samples,
        "backend_hint": _geocode_backend_hint(),
        "qveris_api_url_present": bool((os.getenv("QVERIS_API_URL") or os.getenv("QVERIS_BASE_URL") or "").strip()),
        "qveris_api_key_present": bool((os.getenv("QVERIS_API_KEY") or "").strip()),
    }

    loc_count = int(parse_info.get("locations_count") or 0)
    places_rows = int(parse_info.get("places_rows") or 0)
    ok_e2e = bool(html_path) and not html_error and has_bio and (loc_count > 0 or places_rows > 0)

    # 额外把 html_path 写进 parse_info，便于汇总
    if html_path:
        parse_info["html_path"] = html_path
    if html_preview_path:
        parse_info["html_preview_path"] = html_preview_path

    return ok_e2e, raw_checks, ensure_checks, parse_info, geocode_summary


def run_one_full(
    *,
    person: str,
    out_dir: Path,
    timeout_s: int,
    retries: int,
    retry_backoff_s: float,
) -> RunResult:
    run_dir = out_dir / "runs" / person
    run_dir.mkdir(parents=True, exist_ok=True)

    # meta
    api_key = (os.getenv("MIMO_API_KEY") or os.getenv("API_KEY") or os.getenv("LLM_API_KEY") or "").strip()
    base_url = (os.getenv("MIMO_BASE_URL") or os.getenv("BASE_URL") or os.getenv("LLM_BASE_URL") or "https://api.xiaomimimo.com/v1").strip()
    model = (os.getenv("MODEL") or os.getenv("LLM_MODEL_ID") or "mimo-v2-pro").strip()

    write_text(
        run_dir / "meta.json",
        json.dumps(
            {
                "person": person,
                "env": {
                    "API_KEY_present": bool(api_key),
                    "API_KEY_masked": _mask_token(api_key),
                    "BASE_URL": base_url,
                    "MODEL": model,
                    "QVERIS_API_URL_present": bool((os.getenv("QVERIS_API_URL") or os.getenv("QVERIS_BASE_URL") or "").strip()),
                    "QVERIS_API_KEY_present": bool((os.getenv("QVERIS_API_KEY") or "").strip()),
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
    )

    t_all0 = time.perf_counter()

    import auto_generate as ag

    sys_prompt = ag.build_story_system_prompt()
    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": f"人物姓名：{person}"},
    ]

    if not api_key:
        api_attempt = ApiAttempt(
            ok=False,
            status_code=None,
            error_type="missing_api_key",
            error_message="missing MIMO_API_KEY/API_KEY/LLM_API_KEY",
            duration_s=0.0,
            usage=None,
        )
        raw_md = ""
    else:
        raw_md, api_attempt = call_openai_compatible_with_meta(
            messages=messages,
            api_key=api_key,
            model=model,
            base_url=base_url,
            timeout_s=timeout_s,
            temperature=0.2,
            max_retries=retries,
            retry_backoff_s=retry_backoff_s,
        )

    if raw_md is None:
        raw_md = ""

    raw_md = strip_md_fence(raw_md)

    write_text(run_dir / "raw_markdown.md", raw_md)
    write_text(run_dir / "api_attempt.json", json.dumps(asdict(api_attempt), ensure_ascii=False, indent=2))

    ok_e2e, raw_checks, ensure_checks, parse_info, geocode_summary = run_pipeline_only(
        person=person,
        raw_md=raw_md,
        run_dir=run_dir,
    )

    duration_s = time.perf_counter() - t_all0

    result = RunResult(
        person=person,
        ok_end_to_end=ok_e2e,
        duration_s=duration_s,
        api=api_attempt,
        markdown_raw_checks=raw_checks,
        markdown_after_ensure_checks=ensure_checks,
        storymap_parse=parse_info,
        geocode=geocode_summary,
        output={
            "run_dir": str(run_dir),
            "md_path": str(default_story_md_path(person)),
            "html_path": str(parse_info.get("html_path") or ""),
        },
    )

    write_text(run_dir / "result.json", json.dumps(asdict(result), ensure_ascii=False, indent=2))

    return result


def load_existing_results(out_dir: Path) -> Dict[str, RunResult]:
    results: Dict[str, RunResult] = {}
    runs_dir = out_dir / "runs"
    if not runs_dir.exists():
        return results

    for d in runs_dir.iterdir():
        if not d.is_dir():
            continue
        person = d.name
        result_path = d / "result.json"
        if not result_path.exists():
            continue
        try:
            data = json.loads(result_path.read_text(encoding="utf-8"))
            api = ApiAttempt(**data.get("api"))
            rr = RunResult(
                person=data.get("person") or person,
                ok_end_to_end=bool(data.get("ok_end_to_end")),
                duration_s=float(data.get("duration_s") or 0),
                api=api,
                markdown_raw_checks=data.get("markdown_raw_checks") or {},
                markdown_after_ensure_checks=data.get("markdown_after_ensure_checks") or {},
                storymap_parse=data.get("storymap_parse") or {},
                geocode=data.get("geocode") or {},
                output=data.get("output") or {},
            )
            results[rr.person] = rr
        except Exception:
            continue

    return results


def build_report(out_dir: Path, people: List[str], results: List[RunResult]) -> str:
    total = len(people)
    by_person = {r.person: r for r in results}

    ok_e2e = len([r for r in results if r.ok_end_to_end])
    api_ok = len([r for r in results if r.api.ok])

    api_429 = [r for r in results if (r.api.status_code == 429)]
    api_504 = [r for r in results if (r.api.status_code == 504)]
    api_timeout = [r for r in results if (r.api.error_type == "timeout")]

    raw_has_fence = [r for r in results if r.markdown_raw_checks.get("has_code_fence")]
    raw_missing_sections = [r for r in results if r.markdown_raw_checks.get("missing_h2_auto_generate")]

    mismatch_story_prompt = [r for r in results if r.markdown_raw_checks.get("missing_h2_story_system_prompt")]

    geo_calls = sum(int(r.geocode.get("calls") or 0) for r in results)
    geo_fail = sum(int(r.geocode.get("fail") or 0) for r in results)

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

    total_s = sum(r.duration_s for r in results)
    avg_s = total_s / max(1, len(results)) if results else 0

    def pick_people(rs: List[RunResult], limit: int = 6) -> str:
        if not rs:
            return "-"
        return "、".join([r.person for r in rs[:limit]]) + ("…" if len(rs) > limit else "")

    # 地理编码后端
    geo_backend = "-"
    if results:
        geo_backend = str((results[0].geocode or {}).get("backend_hint") or "-")

    lines: List[str] = []
    lines.append("# map_story_poster 批量跑数排障诊断报告")
    lines.append("")
    lines.append(f"- 批次目录：`{out_dir}`")
    lines.append(f"- 目标人物数：{total}")
    lines.append(f"- 已完成（有 result.json）：{len(results)}/{total}")
    lines.append(f"- 端到端成功（生成 HTML）：{ok_e2e}/{len(results)} = {ok_e2e / max(1, len(results)):.1%}")
    lines.append(f"- API 调用成功：{api_ok}/{len(results)} = {api_ok / max(1, len(results)):.1%}")
    lines.append(f"- 总耗时：{total_s/60:.1f} min，平均：{avg_s:.1f} s/人")
    lines.append("")

    lines.append("## 1) API 侧（HTTP 429 / 504 / 超时等）")
    lines.append("")
    lines.append(f"- HTTP 429（频控）：{len(api_429)} 人（样例：{pick_people(api_429)}）")
    lines.append(f"- HTTP 504（网关超时）：{len(api_504)} 人（样例：{pick_people(api_504)}）")
    lines.append(f"- Timeout（客户端超时）：{len(api_timeout)} 人（样例：{pick_people(api_timeout)}）")
    lines.append("")

    lines.append("### 关键工程状态")
    lines.append(
        "- `auto_generate.py` 里 `generate_story_markdown()` 现在会直接抛出异常，显式报错。"
    )
    lines.append(
        "  - 这有助于暴露真实的 API 报错（如 429/504），确保全量跑数结果的真实性。"
    )
    lines.append("")

    lines.append("## 2) 模型幻觉与格式侧（Markdown 标准化/解析风险）")
    lines.append("")
    lines.append(f"- raw Markdown 含 ``` 代码围栏：{len(raw_has_fence)} 人（样例：{pick_people(raw_has_fence)}）")
    lines.append(
        f"- raw Markdown 缺失 auto_generate 必需章节（人物档案/人生历程/生平时间线/教材知识点/地点坐标）：{len(raw_missing_sections)} 人（样例：{pick_people(raw_missing_sections)}）"
    )
    lines.append("")

    lines.append("### 关于 story_system_prompt.md 的模板一致性")
    lines.append(
        "- 当前批量链路的 H2 章节校验已与 `story_system_prompt.md` 保持一致，重点关注：人物档案、人生足迹地图说明、人生历程与重要地点、生平时间线、历史影响。"
    )
    lines.append(
        "- 在本批次中，raw Markdown 缺失上述模板章节的情况："
        f"{len(mismatch_story_prompt)}/{len(results)}"
    )
    lines.append("- 坐标表允许使用 `| 现称 | 现代搜索地名 | 纬度 | 经度 |` 新表头，以支持优先使用现代搜索地名做地理编码。")
    lines.append("")

    lines.append("## 3) 坐标系与高德解析侧（古地名 -> 现代 WGS84）")
    lines.append("")
    lines.append(f"- 当前地理编码后端判定：`{geo_backend}`")
    if geo_calls:
        lines.append(f"- 地理编码调用次数（累计）：{geo_calls}")
        lines.append(f"- 地理编码失败次数（累计）：{geo_fail}（失败率：{geo_fail/geo_calls:.1%}）")
    else:
        lines.append("- 地理编码调用次数：0（多数样本 Markdown 自带坐标表，或走本地索引命中）")
    lines.append("")

    if geo_backend == "osm(fallback)":
        lines.append("### 结论限制（重要）")
        lines.append(
            "- 本环境未配置 `QVERIS_API_URL/QVERIS_API_KEY`，因此 **无法在本次批量跑数中真实验证高德接口命中率**。"
        )
        lines.append(
            "- 如果你希望专门探雷‘高德解析失败边界 case’，建议补齐 QVeris 凭据或改为直接调用 AMap WebService。"
        )
        lines.append("")

    lines.append("## 4) Token 评估")
    lines.append("")
    lines.append(f"- 能统计 usage.total_tokens 的样本：{len(token_samples)}/{len(results)}")
    if token_samples:
        lines.append(f"- total_tokens（可统计部分合计）：{total_tokens}")
        lines.append(f"- 平均 total_tokens/人（可统计部分）：{avg_tokens:.0f}")
        top = sorted(token_samples, key=lambda x: x[1], reverse=True)[:5]
        lines.append("- token 消耗 Top5：" + "；".join([f"{n}={t}" for n, t in top]))
    if missing_usage:
        lines.append(f"- usage 缺失：{missing_usage} 人（如果要评估成本，需要确认 MiMo 是否始终返回 usage 字段）")
    lines.append("")

    lines.append("## 5) 明细摘要")
    lines.append("")
    lines.append("| 人物 | API | HTTP | 端到端 | 耗时(s) | geocode fail/calls | tokens | 备注 |")
    lines.append("| --- | --- | ---: | --- | ---: | ---: | ---: | --- |")

    for name in people:
        r = by_person.get(name)
        if not r:
            lines.append(f"| {name} | - | - | - | - | - | - | 未跑/未汇总 |")
            continue
        api_status = "OK" if r.api.ok else "FAIL"
        http = r.api.status_code if r.api.status_code is not None else "-"
        e2e = "OK" if r.ok_end_to_end else "FAIL"
        geo = f"{r.geocode.get('fail',0)}/{r.geocode.get('calls',0)}"
        usage = r.api.usage or {}
        tt = usage.get("total_tokens") if isinstance(usage, dict) else None
        tokens = tt if isinstance(tt, int) else "-"
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
        lines.append(f"| {name} | {api_status} | {http} | {e2e} | {r.duration_s:.1f} | {geo} | {tokens} | {note} |")

    lines.append("")
    lines.append("---")
    lines.append("### 附：产物索引")
    lines.append(f"- 汇总 JSON：`{out_dir / 'summary.json'}`")
    lines.append(f"- 报告 Markdown：`{out_dir / 'report.md'}`")
    lines.append(f"- 单人详细：`{out_dir / 'runs/<person>/'}`")

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="批量跑 MiMo auto_generate 并生成排障报告（可断点续跑）")
    parser.add_argument("--out-dir", type=str, default="", help="指定输出目录（用于断点续跑）；不填则新建 batch_runs/<ts>")
    parser.add_argument("--people", type=str, default="", help="逗号分隔的人物列表；不填则用内置 16 人")
    parser.add_argument("--timeout", type=int, default=int(os.getenv("TIMEOUT", "120")), help="单次 API timeout（秒）")
    parser.add_argument("--retries", type=int, default=int(os.getenv("BATCH_RETRIES", "3")), help="API 最大重试次数")
    parser.add_argument("--retry-backoff", type=float, default=float(os.getenv("BATCH_RETRY_BACKOFF_S", "2")), help="重试退避基数（秒）")
    parser.add_argument("--sleep", type=float, default=float(os.getenv("BATCH_SLEEP_S", "0.8")), help="每个人之间 sleep 秒数")
    parser.add_argument("--skip-done", action="store_true", help="如果 runs/<person>/result.json 已存在，则跳过")
    args = parser.parse_args()

    load_env()

    if args.out_dir:
        out_dir = Path(args.out_dir)
        if not out_dir.is_absolute():
            out_dir = REPO_ROOT / args.out_dir
    else:
        out_dir = REPO_ROOT / "batch_runs" / _now_ts()

    out_dir.mkdir(parents=True, exist_ok=True)

    # people
    if args.people.strip():
        people = [p.strip() for p in args.people.split(",") if p.strip()]
    else:
        people = DEFAULT_PEOPLE[:]

    existing = load_existing_results(out_dir)

    results: Dict[str, RunResult] = dict(existing)

    # 处理 legacy run（已有 api_attempt/raw_md，但缺 result.json）：补齐 pipeline
    for person in people:
        if person in results:
            continue
        run_dir = out_dir / "runs" / person
        api_path = run_dir / "api_attempt.json"
        raw_path = run_dir / "raw_markdown.md"
        if api_path.exists() and raw_path.exists():
            try:
                api_data = json.loads(api_path.read_text(encoding="utf-8"))
                api = ApiAttempt(**api_data)
            except Exception:
                api = ApiAttempt(
                    ok=False,
                    status_code=None,
                    error_type="legacy_parse_failed",
                    error_message="无法解析 legacy api_attempt.json",
                    duration_s=0.0,
                    usage=None,
                )
            raw_md = raw_path.read_text(encoding="utf-8")
            t0 = time.perf_counter()
            ok_e2e, raw_checks, ensure_checks, parse_info, geocode_summary = run_pipeline_only(
                person=person,
                raw_md=raw_md,
                run_dir=run_dir,
            )
            duration_s = time.perf_counter() - t0
            rr = RunResult(
                person=person,
                ok_end_to_end=ok_e2e,
                duration_s=duration_s,
                api=api,
                markdown_raw_checks=raw_checks,
                markdown_after_ensure_checks=ensure_checks,
                storymap_parse=parse_info,
                geocode=geocode_summary,
                output={
                    "run_dir": str(run_dir),
                    "md_path": str(default_story_md_path(person)),
                    "html_path": str(parse_info.get("html_path") or ""),
                },
            )
            write_text(run_dir / "result.json", json.dumps(asdict(rr), ensure_ascii=False, indent=2))
            results[person] = rr

    # 跑剩余的（调用 LLM）
    for i, person in enumerate(people, 1):
        if args.skip_done and (out_dir / "runs" / person / "result.json").exists():
            continue
        if person in results and args.skip_done:
            continue
        if person in results and not args.skip_done:
            # 已有结果但不 skip：允许重跑覆盖
            pass

        print(f"[{i}/{len(people)}] ▶ {person} ...")
        rr = run_one_full(
            person=person,
            out_dir=out_dir,
            timeout_s=args.timeout,
            retries=args.retries,
            retry_backoff_s=args.retry_backoff,
        )
        results[person] = rr
        print(
            f"    API={'OK' if rr.api.ok else 'FAIL'} HTTP={rr.api.status_code} | e2e={'OK' if rr.ok_end_to_end else 'FAIL'} | {rr.duration_s:.1f}s"
        )
        time.sleep(args.sleep)

    # 汇总
    ordered_results = [results[p] for p in people if p in results]

    summary = {
        "batch": out_dir.name,
        "people": people,
        "results": [asdict(r) for r in ordered_results],
    }
    write_text(out_dir / "summary.json", json.dumps(summary, ensure_ascii=False, indent=2))

    report_md = build_report(out_dir, people, ordered_results)
    write_text(out_dir / "report.md", report_md)

    print("\n=== DONE ===")
    print(f"report: {out_dir / 'report.md'}")
    print(f"summary: {out_dir / 'summary.json'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

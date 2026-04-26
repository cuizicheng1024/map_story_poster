#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import random
import re
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import requests
from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLES_STORY_DIR = REPO_ROOT / "storymap" / "examples" / "story"
EXAMPLES_STORY_MAP_DIR = REPO_ROOT / "storymap" / "examples" / "story_map"
FACT_CHECK_PROMPT_PATH = REPO_ROOT / "storymap" / "docs" / "fact_check_prompt.md"
KNOWLEDGE_GRAPH_JSON = REPO_ROOT / "data" / "people_knowledge_graph.json"

STRICT_AUDIT_SYSTEM_PROMPT = """你是“极其严格、偏保守”的历史人物知识库审查官（LLM Auditor）。
你将审查：人物 Markdown、人物 HTML（从 Markdown 渲染而来）、以及人物关联关系边（edges）。
目标：最大化准确性与一致性，最小化幻觉与不可靠推断。

硬性要求：
1) 只输出 JSON，禁止输出任何其它文本。
2) 不允许编造史料来源；不能确定就写 uncertain=true，并把 claim 改写为“存疑/说法不一/待考”。
3) 任何置信度>=0.7 的严重问题必须让 overall_pass=false。
4) 需要同时做三类一致性检查：
   A. Markdown 内部自洽（生卒/享年/朝代/事件顺序/地点）
   B. Markdown 与 HTML 渲染一致（关键字段是否丢失/错渲/错解析）
   C. 关系边是否可证（每条边必须给出 evidence_text：引用 Markdown 原文句/或教材共现依据；否则判为 weak_edge 建议删除）
5) 对“疑似生成幻觉/非真实人物/同名误配/现代虚构信息”要特别敏感：一旦命中，risk_level=high，并给出处理建议（删除/标注虚构/需要人工确认）。
6) 只要 overall_pass=false 或 risk_level 不为 low 或 issues 非空，patched_markdown 必须给出非空字符串：在尽量保留原结构与措辞的前提下做“最小必要修订”，把不确定内容改为“存疑/说法不一/待考”，禁止补充未经证实的具体细节。
7) 地点可用性要求（为了保证足迹地图可用）：
   - 任何“出生/去世/重要地点”的地点描述，尽量改写为可被现代地理编码检索的现代行政区表达，例如“（今中国XX省XX市）”“（今英国伦敦）”，避免“某地附近/境内/北部/一带/等地/不详”等不可解析表述。
   - “## 地点坐标”表格中，地点名不得使用“存疑/不详/无确切信息/—”作为地点本体；若确实无法确定，请删除该行或将地点名改为更高层级可定位的行政区（如“江苏南京”“安徽凤阳”等），并在其他文本中标注存疑。
8) “时代/朝代”字段要求（用于首页悬浮与检索）：
   - 必须与人物所处文明/国家/地区匹配；外国人物不要硬套中国朝代（如“春秋战国/唐宋元明清”等）。
   - 若不确定，用更宽泛且可验证的表达（如“古希腊时期/罗马共和国时期/中世纪欧洲/文艺复兴时期/维多利亚时代”等）并标注存疑。
9) “籍贯/出生地”字段必须是地名而非年份或判断语：
   - 不得出现纯年份、年号、或“出生年份存疑”等非地名内容；若出生地不确定，应写“存疑/说法不一/待考”并给出可定位的上收行政区（若能确定）。

输出 JSON schema：
{
  "overall_pass": boolean,
  "risk_level": "low|medium|high",
  "entity_identity": {
    "is_real_person_likely": boolean,
    "uncertain": boolean,
    "reason": string
  },
  "facts": {
    "dynasty": string|null,
    "birth_year": int|null,
    "death_year": int|null,
    "birthplace_modern": string|null
  },
  "issues": [
    {
      "severity": "info|warn|error",
      "category": "fact|consistency|timeline|location|render|relation|format|hallucination",
      "claim": string,
      "evidence_text": string,
      "suggested_fix": string,
      "confidence": number
    }
  ],
  "relation_audit": {
    "keep": [ { "target": string, "relation_type": string, "evidence_text": string, "confidence": number } ],
    "drop": [ { "target": string, "reason": string, "confidence": number } ],
    "add":  [ { "target": string, "relation_type": string, "evidence_text": string, "confidence": number } ]
  },
  "patched_markdown": string|null
}
""".strip()

QUICK_AUDIT_SYSTEM_PROMPT = """你是“极其严格、偏保守”的历史人物信息快速审查官（Quick Auditor）。
你将收到某位人物的 Markdown（可能含生成痕迹）。你的目标是：用更低成本做第一轮质量提升，让内容更可靠、更可用、更便于地图渲染。

硬性要求：
1) 只输出 JSON，禁止输出其它文本。
2) 不允许编造史料来源；不能确定就写 uncertain=true，并把对应表述改写为“存疑/说法不一/待考”。
3) 重点处理四类问题：
   A. 结构/格式：H1 必须是人物名；必须包含“人物档案/人生历程与重要地点/地点坐标”等核心章节；表格必须有分隔线。
   B. 自洽性：朝代/生卒年/享年/时间顺序明显冲突要修正或标注存疑。
   C. 地点可用性：避免“等地/一带/附近/不详/—/境内/北部”等不可地理编码表述；将地点尽量改写为现代可检索的行政区（如“今XX省XX市/今英国伦敦”）；“地点坐标”表不得用“存疑/不详/—”作为地点名，如无法确定应删除该行或用更高层级行政区替代。
   D. 实体消歧：补充最小必要的“别名/外文名/领域标签”，用于区分同名人物或增强 hover 信息；如果无法确定必须标注存疑。
   E. 时代字段：外国人物的“时代/朝代”不要硬套中国朝代；应使用其所处文明/国家的时代划分（不确定则标注存疑）。
4) 如果发现疑似虚构/同名误配/严重不可信，risk_level=high，并将内容尽量改写为“存疑/需人工确认”，减少误导。

输出 JSON schema：
{
  "need_strict": boolean,
  "risk_level": "low|medium|high",
  "entity_disambiguation": {
    "aliases": [string],
    "foreign_name": string|null,
    "domain_tags": [string],
    "uncertain": boolean,
    "reason": string
  },
  "issues": [
    { "severity":"info|warn|error", "category":"format|fact|timeline|location|disambiguation|hallucination", "claim":string, "suggested_fix":string, "confidence":number }
  ],
  "patched_markdown": string|null
}
""".strip()

BIRTHPLACE_AUDIT_SYSTEM_PROMPT = """你是“极其严格、偏保守”的历史人物籍贯/出生地信息审查官（Birthplace Auditor）。
你将收到某位人物的 Markdown。你的目标是：核查并纠偏人物的“出生地/籍贯（页面悬浮显示用）”信息，使其准确、可地理编码、且不引入新错误。

硬性要求：
1) 只输出 JSON，禁止输出任何其它文本。
2) 不允许编造史料来源；不能确定就写 uncertain=true，并把相关表述改为“存疑/说法不一/待考”，不要硬写具体地名。
3) 你只能改动与地点相关的最小必要部分：
   - “### 基本信息”中的 **出生** 字段（优先使用“古地名（今XX省XX市XX区/县）”表达）
   - “### 🟢 出生地：...”小节中的 **位置**（保持与出生字段一致）
   - “## 地点坐标”表中与出生地相关的行（地点名/现代搜索地名/经纬度若明显不一致可修正；不确定则删除该行或上收行政区并标注存疑）
   - 其他内容（生平、作品、评价等）一律不要改
4) 地点可用性要求：
   - 避免“附近/一带/等地/不详/—/境内/北部”等不可解析表述
   - 优先给出可被现代地理编码检索的行政区表达（中国用“省/市/县/区”，国外用“国家+城市/州省”）
   - “出生地/籍贯”必须是地名，禁止写成时间信息（如“某年/年号/公元前XXX年/出生年份存疑”等）；若不确定，写“存疑/说法不一/待考”，不要用年份顶替地点。
5) 输出 patched_markdown：
   - 如果你认为原文已经准确且可用，patched_markdown 必须为 null
   - 如果需要纠偏，patched_markdown 必须给出完整 Markdown（在原文基础上做最小改动）

输出 JSON schema：
{
  "overall_pass": boolean,
  "uncertain": boolean,
  "birthplace_modern": string|null,
  "issues": [
    { "severity":"info|warn|error", "claim":string, "suggested_fix":string, "confidence":number }
  ],
  "patched_markdown": string|null
}
""".strip()

WEB_SEARCH_TRUTH_AUDIT_SYSTEM_PROMPT = """你是“极其严格、偏保守”的历史人物真实性/关键事实核查官（WebSearch Truth Auditor）。
你将收到某位人物的 Markdown（可能存在幻觉/同名误配/虚构内容）。你必须结合联网搜索结果，核查该人物是否为真实历史人物，并核对关键事实（生卒年、时代/国家、出生地/籍贯、代表性身份）。

硬性要求：
1) 只输出 JSON，禁止输出任何其它文本。
2) 必须使用联网搜索作为证据；若公开信息不足或冲突，必须标注 uncertain=true，并将对应字段置为 null 或“存疑”。
3) 不允许编造来源；sources 里的 url/title 必须来自联网搜索的真实结果（如果无法确认就不要写）。
4) 若判断为虚构人物/小说人物/同名误配，is_real_person_likely=false，并给出 reason 与 recommended_action（例如：标注虚构、删除人物、需要人工确认）。
5) 若发现 Markdown 中的关键事实明显错误（如死亡年离谱、时代不匹配、籍贯写成年份等），issues 必须列出，并在 apply_fixes=true 时给出 patched_markdown（最小必要修订：把错误改正或改成存疑，不要扩写生平）。

输出 JSON schema：
{
  "overall_pass": boolean,
  "is_real_person_likely": boolean,
  "uncertain": boolean,
  "canonical_name": string|null,
  "facts": {
    "birth_year": int|null,
    "death_year": int|null,
    "era": string|null,
    "birthplace_modern": string|null
  },
  "issues": [
    { "severity":"info|warn|error", "claim":string, "suggested_fix":string, "confidence":number }
  ],
  "sources": [
    { "url": string, "title": string|null, "site_name": string|null }
  ],
  "recommended_action": "keep|mark_uncertain|mark_fictional|drop",
  "patched_markdown": string|null
}
""".strip()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _load_env() -> None:
    load_dotenv(REPO_ROOT / "data" / ".env")
    load_dotenv(REPO_ROOT / "storymap" / "script" / ".env")
    load_dotenv(REPO_ROOT / ".env")
    load_dotenv(REPO_ROOT.parent.parent / ".env")


def _endpoint(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        base = "https://api.xiaomimimo.com/v1"
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def _strip_fence(text: str) -> str:
    s = (text or "").strip()
    if not s:
        return ""
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z0-9_-]*\n", "", s)
        s = re.sub(r"\n```\s*$", "", s)
    return s.strip()


def _safe_str(x: object, limit: int) -> str:
    s = str(x or "")
    if len(s) > limit:
        return s[:limit] + f"...(truncated,total={len(s)})"
    return s


def _safe_filename(name: str) -> str:
    s = (name or "").strip()
    s = re.sub(r"[\\/:\\*\\?\"<>\\|]", "_", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s or "unknown"


def _now_ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _load_latest_person_html(person: str, limit_chars: int) -> str:
    prefix = _safe_filename(person)
    cands = [
        p
        for p in EXAMPLES_STORY_MAP_DIR.glob(f"{prefix}*.html")
        if p.is_file() and p.name != "index.html" and p.name.startswith(prefix)
    ]
    if not cands:
        return ""
    cands.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    try:
        return _safe_str(cands[0].read_text(encoding="utf-8", errors="ignore"), limit_chars)
    except Exception:
        return ""


def _load_edges_for_person(person: str, kg: Dict[str, Any], limit_edges: int) -> List[Dict[str, Any]]:
    raw = kg.get("edges") if isinstance(kg, dict) else None
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for e in raw:
        if not isinstance(e, dict):
            continue
        a = str(e.get("source") or "").strip()
        b = str(e.get("target") or "").strip()
        if a != person and b != person:
            continue
        out.append(
            {
                "source": a,
                "target": b,
                "type": e.get("type"),
                "weight": e.get("weight"),
                "evidence": e.get("evidence"),
            }
        )
        if limit_edges > 0 and len(out) >= limit_edges:
            break
    return out


def _strict_audit_messages(person: str, markdown: str, html: str, edges_json: str) -> List[Dict[str, str]]:
    user_msg = (
        f"人物：{person}\n\n"
        f"【Markdown】\n{markdown}\n\n"
        f"【HTML】\n{html}\n\n"
        f"【当前关系边（与该人物相关）】\n{edges_json}\n"
    )
    return [{"role": "system", "content": STRICT_AUDIT_SYSTEM_PROMPT}, {"role": "user", "content": user_msg}]


def _quick_audit_messages(person: str, markdown: str) -> List[Dict[str, str]]:
    user_msg = f"人物：{person}\n\n【Markdown】\n{markdown}\n"
    return [{"role": "system", "content": QUICK_AUDIT_SYSTEM_PROMPT}, {"role": "user", "content": user_msg}]


def _birthplace_audit_messages(person: str, markdown: str) -> List[Dict[str, str]]:
    user_msg = f"人物：{person}\n\n【Markdown】\n{markdown}\n"
    return [{"role": "system", "content": BIRTHPLACE_AUDIT_SYSTEM_PROMPT}, {"role": "user", "content": user_msg}]


def _web_search_truth_audit_messages(person: str, markdown: str) -> List[Dict[str, str]]:
    user_msg = (
        f"人物：{person}\n\n"
        f"【Markdown】\n{markdown}\n\n"
        f"请结合联网搜索核查：\n"
        f"- 此人物是否真实存在？是否为小说/虚构人物或同名误配？\n"
        f"- 生卒年、时代/国家、出生地/籍贯是否与公开资料一致？\n"
        f"- 若不确定必须标注存疑；若错误需给出最小修订 patched_markdown。\n"
    )
    return [{"role": "system", "content": WEB_SEARCH_TRUTH_AUDIT_SYSTEM_PROMPT}, {"role": "user", "content": user_msg}]


def _should_apply_patch(audit: Dict[str, Any]) -> bool:
    if not isinstance(audit, dict):
        return False
    if not isinstance(audit.get("patched_markdown"), str) or not str(audit.get("patched_markdown") or "").strip():
        return False
    if audit.get("overall_pass") is False:
        return True
    if str(audit.get("risk_level") or "").lower() == "high":
        return True
    issues = audit.get("issues")
    if isinstance(issues, list):
        for it in issues:
            if not isinstance(it, dict):
                continue
            if str(it.get("severity") or "").lower() != "error":
                continue
            try:
                c = float(it.get("confidence"))
            except Exception:
                c = 0.0
            if c >= 0.7:
                return True
    return False


def _write_person_markdown_and_render_html(person: str, md_text: str) -> str:
    story_md_dir = EXAMPLES_STORY_DIR
    story_map_dir = EXAMPLES_STORY_MAP_DIR

    md_path = story_md_dir / f"{person}.md"
    md_path.write_text(md_text, encoding="utf-8")

    import sys

    cli_dir = (REPO_ROOT / "cli").resolve()
    script_dir = (REPO_ROOT / "storymap" / "script").resolve()
    if str(cli_dir) not in sys.path:
        sys.path.insert(0, str(cli_dir))
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))

    import auto_generate as ag  # type: ignore
    import map_html_renderer as renderer  # type: ignore
    import story_map  # type: ignore

    ensured = ag.ensure_required_sections(md_text)
    profile = story_map._load_profile_from_md(ensured, allow_geocode=False)
    if profile:
        profile["markdown"] = ensured
        html_out = renderer.render_profile_html(profile)
    else:
        places = story_map.parse_places(ensured)
        events = story_map.parse_events(ensured)
        points = story_map.build_points(places, events, allow_geocode=False)
        fields = story_map._extract_intro_fields(ensured)
        info_panel_html = renderer.build_info_panel_html(person, fields) if any(fields.values()) else ""
        html_out = story_map.render_osm_html(person, points, info_panel_html)

    out_path = story_map_dir / f"{person}.html"
    out_path.write_text(html_out, encoding="utf-8")
    return str(out_path)


def _safe_json_load(text: str) -> Optional[Dict[str, Any]]:
    raw = _strip_fence(text)
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        pass
    try:
        start = raw.find("{")
        if start < 0:
            return None
        in_str = False
        esc = False
        depth = 0
        for i in range(start, len(raw)):
            ch = raw[i]
            if esc:
                esc = False
                continue
            if ch == "\\":
                esc = True
                continue
            if ch == "\"":
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == "{":
                depth += 1
                continue
            if ch == "}":
                depth -= 1
                if depth == 0:
                    frag = raw[start : i + 1]
                    data = json.loads(frag)
                    return data if isinstance(data, dict) else None
        return None
    except Exception:
        return None


def _apply_relation_drops_to_knowledge_graph(
    *,
    strict_rows: List[Dict[str, Any]],
    kg_path: Path,
    min_confidence: float,
    drop_types: Set[str],
) -> Dict[str, Any]:
    drop_pairs: Set[Tuple[str, str]] = set()
    for row in strict_rows:
        person = str(row.get("person") or "").strip()
        strict_path = str(row.get("strict_audit_path") or "").strip()
        if not person or not strict_path:
            continue
        try:
            payload = json.loads(Path(strict_path).read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        audit = payload.get("audit")
        if not isinstance(audit, dict):
            continue
        ra = audit.get("relation_audit")
        if not isinstance(ra, dict):
            continue
        drops = ra.get("drop")
        if not isinstance(drops, list):
            continue
        for it in drops:
            if not isinstance(it, dict):
                continue
            target = str(it.get("target") or "").strip()
            if not target:
                continue
            try:
                c = float(it.get("confidence"))
            except Exception:
                c = 0.0
            if c < float(min_confidence):
                continue
            a, b = (person, target) if person <= target else (target, person)
            drop_pairs.add((a, b))

    if not drop_pairs:
        return {"changed": False, "dropped": 0, "candidates": 0}

    try:
        kg = json.loads(kg_path.read_text(encoding="utf-8"))
    except Exception:
        return {"changed": False, "dropped": 0, "candidates": len(drop_pairs), "error": "kg_load_failed"}
    if not isinstance(kg, dict):
        return {"changed": False, "dropped": 0, "candidates": len(drop_pairs), "error": "kg_invalid"}
    edges = kg.get("edges")
    if not isinstance(edges, list):
        return {"changed": False, "dropped": 0, "candidates": len(drop_pairs), "error": "kg_edges_missing"}

    kept = []
    dropped = 0
    for e in edges:
        if not isinstance(e, dict):
            kept.append(e)
            continue
        typ = str(e.get("type") or "").strip()
        if typ not in drop_types:
            kept.append(e)
            continue
        s = str(e.get("source") or "").strip()
        t = str(e.get("target") or "").strip()
        if not s or not t:
            kept.append(e)
            continue
        a, b = (s, t) if s <= t else (t, s)
        if (a, b) in drop_pairs:
            dropped += 1
            continue
        kept.append(e)

    if dropped <= 0:
        return {"changed": False, "dropped": 0, "candidates": len(drop_pairs)}

    kg["edges"] = kept
    meta = kg.get("meta")
    if not isinstance(meta, dict):
        meta = {}
        kg["meta"] = meta
    meta["nodes"] = len(kg.get("nodes") or []) if isinstance(kg.get("nodes"), list) else meta.get("nodes")
    meta["edges"] = len(kept)
    try:
        meta["types"] = sorted({str(x.get("type") or "") for x in kept if isinstance(x, dict) and x.get("type")})
    except Exception:
        pass

    kg_path.write_text(json.dumps(kg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"changed": True, "dropped": dropped, "candidates": len(drop_pairs)}


@dataclass
class ApiAttempt:
    ok: bool
    status_code: Optional[int]
    error_type: str
    error_message: str
    duration_s: float
    usage: Optional[Dict[str, Any]]


_API_SEM: Optional[threading.Semaphore] = None
_API_SEM_LOCK = threading.Lock()


def _api_semaphore() -> threading.Semaphore:
    global _API_SEM
    if _API_SEM is not None:
        return _API_SEM
    with _API_SEM_LOCK:
        if _API_SEM is not None:
            return _API_SEM
        try:
            n = int(os.getenv("MIMO_MAX_INFLIGHT", "20") or "20")
        except Exception:
            n = 20
        n = max(1, min(80, n))
        _API_SEM = threading.Semaphore(n)
        return _API_SEM


def _sleep_backoff(base_s: float, attempt: int, status_code: Optional[int], retry_after_s: Optional[float]) -> None:
    base = float(base_s) if base_s and base_s > 0 else 1.5
    mult = min(60.0, base * (attempt ** 1.35))
    if status_code == 429:
        mult = max(mult, 8.0)
    if retry_after_s is not None:
        mult = max(mult, float(retry_after_s))
    jitter = random.uniform(0.0, min(2.0, mult * 0.15))
    time.sleep(mult + jitter)


def _call_openai_compatible(
    *,
    api_key: str,
    base_url: str,
    model: str,
    messages: List[Dict[str, str]],
    tools: Optional[List[Dict[str, Any]]] = None,
    tool_choice: Optional[str] = None,
    timeout_s: int,
    max_retries: int,
    retry_backoff_s: float,
) -> Tuple[Optional[str], ApiAttempt]:
    url = _endpoint(base_url)
    headers = {"Authorization": f"Bearer {api_key}", "api-key": api_key, "Content-Type": "application/json"}
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.0,
        "stream": False,
        "response_format": {"type": "json_object"},
    }
    if tools:
        payload["tools"] = tools
    if tool_choice:
        payload["tool_choice"] = tool_choice

    last = ApiAttempt(ok=False, status_code=None, error_type="not_run", error_message="", duration_s=0.0, usage=None)
    for attempt in range(1, max_retries + 1):
        t0 = time.perf_counter()
        try:
            sem = _api_semaphore()
            with sem:
                resp = requests.post(url, headers=headers, json=payload, timeout=(10, int(timeout_s)))
            dt = time.perf_counter() - t0
            status = resp.status_code
            if status >= 400:
                if status == 400 and attempt == 1 and "response_format" in payload:
                    payload.pop("response_format", None)
                    time.sleep(0.2)
                    continue
                last = ApiAttempt(
                    ok=False,
                    status_code=status,
                    error_type="http_error",
                    error_message=(resp.text[:400] if isinstance(resp.text, str) else ""),
                    duration_s=dt,
                    usage=None,
                )
                if status in (429, 500, 502, 503, 504) and attempt < max_retries:
                    ra = None
                    try:
                        h = resp.headers.get("Retry-After")
                        if h:
                            ra = float(h)
                    except Exception:
                        ra = None
                    _sleep_backoff(float(retry_backoff_s), attempt, status, ra)
                    continue
                return None, last

            try:
                data = resp.json()
            except Exception as exc:
                return None, ApiAttempt(ok=False, status_code=status, error_type="json_parse_failed", error_message=str(exc), duration_s=dt, usage=None)

            choices = data.get("choices") if isinstance(data, dict) else None
            if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                msg = choices[0].get("message")
                if isinstance(msg, dict):
                    content = msg.get("content")
                    if not isinstance(content, str):
                        content = msg.get("reasoning_content")
                    if isinstance(content, str):
                        return content, ApiAttempt(ok=True, status_code=status, error_type="", error_message="", duration_s=dt, usage=data.get("usage"))
            return None, ApiAttempt(ok=False, status_code=status, error_type="response_parse_failed", error_message="", duration_s=dt, usage=data.get("usage"))
        except requests.exceptions.Timeout as exc:
            dt = time.perf_counter() - t0
            last = ApiAttempt(ok=False, status_code=None, error_type="timeout", error_message=str(exc), duration_s=dt, usage=None)
            if attempt < max_retries:
                _sleep_backoff(float(retry_backoff_s), attempt, None, None)
                continue
            return None, last
        except requests.exceptions.RequestException as exc:
            dt = time.perf_counter() - t0
            last = ApiAttempt(ok=False, status_code=None, error_type="request_exception", error_message=str(exc), duration_s=dt, usage=None)
            if attempt < max_retries:
                _sleep_backoff(float(retry_backoff_s), attempt, None, None)
                continue
            return None, last

    return None, last


def web_search_truth_audit_one(
    *,
    md_path: Path,
    out_dir: Path,
    api_key: str,
    base_url: str,
    model: str,
    timeout_s: int,
    retries: int,
    retry_backoff_s: float,
    skip_existing: bool,
    apply_fixes: bool,
    md_limit_chars: int,
    max_keyword: int,
    search_limit: int,
) -> Dict[str, Any]:
    t0 = time.perf_counter()
    md = md_path.read_text(encoding="utf-8")
    person_title = _safe_person_from_md(md_path, md)
    person = md_path.stem
    result: Dict[str, Any] = {"person": person, "person_title": person_title, "md_path": str(md_path)}

    audit_dir = out_dir / "web_search_truth_audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_out = audit_dir / f"{md_path.stem}.json"
    if skip_existing and audit_out.exists():
        result["web_search_truth_audit_path"] = str(audit_out)
        result["skipped"] = True
        result["duration_s"] = round(time.perf_counter() - t0, 3)
        return result

    md_snip = _safe_str(md, md_limit_chars)
    messages = _web_search_truth_audit_messages(person_title or person, md_snip)
    tools = [
        {
            "type": "web_search",
            "max_keyword": max(1, min(6, int(max_keyword))),
            "force_search": True,
            "limit": max(1, min(5, int(search_limit))),
        }
    ]
    content, api = _call_openai_compatible(
        api_key=api_key,
        base_url=base_url,
        model=model,
        messages=messages,
        tools=tools,
        tool_choice="auto",
        timeout_s=timeout_s,
        max_retries=retries,
        retry_backoff_s=retry_backoff_s,
    )
    audit = _safe_json_load(content or "") or {
        "overall_pass": False,
        "is_real_person_likely": False,
        "uncertain": True,
        "canonical_name": None,
        "facts": {"birth_year": None, "death_year": None, "era": None, "birthplace_modern": None},
        "issues": [],
        "sources": [],
        "recommended_action": "mark_uncertain",
        "patched_markdown": None,
    }

    applied = False
    html_path = ""
    if apply_fixes and isinstance(audit, dict):
        patched = str(audit.get("patched_markdown") or "").strip()
        if patched:
            try:
                html_path = _write_person_markdown_and_render_html(person, patched)
                applied = True
            except Exception as exc:
                audit.setdefault("issues", [])
                if isinstance(audit.get("issues"), list):
                    audit["issues"].append(
                        {"severity": "error", "claim": "apply patched_markdown and render html", "suggested_fix": str(exc), "confidence": 1.0}
                    )

    payload = {"person": person, "time": _now(), "api": asdict(api), "audit": audit, "applied": applied, "html_path": html_path}
    audit_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    result["web_search_truth_audit_path"] = str(audit_out)
    result["applied"] = applied
    if html_path:
        result["html_path"] = html_path
    if isinstance(audit, dict):
        result["audit_overall_pass"] = audit.get("overall_pass")
        result["audit_uncertain"] = audit.get("uncertain")
        result["audit_is_real_person_likely"] = audit.get("is_real_person_likely")
        issues = audit.get("issues")
        if isinstance(issues, list):
            result["issues_count"] = len([x for x in issues if isinstance(x, dict)])
        else:
            result["issues_count"] = 0
    result["api_ok"] = bool(api.ok)
    result["api_status"] = api.status_code
    result["duration_s"] = round(time.perf_counter() - t0, 3)
    return result


def _extract_section(lines: List[str], start_pat: str, stop_pat: str) -> str:
    start_idx = None
    for i, line in enumerate(lines):
        if re.match(start_pat, line):
            start_idx = i + 1
            break
    if start_idx is None:
        return ""
    out: List[str] = []
    for j in range(start_idx, len(lines)):
        if re.match(stop_pat, lines[j]):
            break
        out.append(lines[j])
    return "\n".join(out).strip()


def _extract_basic_info(md: str) -> str:
    lines = md.splitlines()
    sec = _extract_section(lines, r"^###\s*基本信息\s*$", r"^###\s+|^##\s+|^---\s*$")
    if sec:
        return sec.strip()
    sec = _extract_section(lines, r"^##\s*人物档案\s*$", r"^##\s+|^---\s*$")
    if not sec:
        return ""
    m = re.search(r"###\s*基本信息\s*([\s\S]*?)(?:\n###\s+|\n##\s+|\n---\s*$|$)", sec)
    return m.group(1).strip() if m else ""


def _extract_summary_excerpt(md: str, limit_chars: int = 900) -> str:
    lines = md.splitlines()
    sec = _extract_section(lines, r"^###\s*生平概述\s*$", r"^###\s+|^##\s+|^---\s*$")
    if not sec:
        return ""
    s = re.sub(r"\s+", " ", sec).strip()
    return s[:limit_chars]


def _extract_timeline_excerpt(md: str, limit_items: int = 24) -> str:
    lines = md.splitlines()
    start = None
    for i, line in enumerate(lines):
        if re.match(r"^##\s*人生历程与重要地点", line):
            start = i + 1
            break
    if start is None:
        return ""
    items: List[str] = []
    cur_title = ""
    cur_time = ""
    for j in range(start, len(lines)):
        line = lines[j].strip()
        if line.startswith("## "):
            break
        if line.startswith("### "):
            cur_title = re.sub(r"^###\s*", "", line).strip()
            cur_time = ""
            continue
        if line.startswith("- **时间**："):
            cur_time = line.replace("- **时间**：", "").strip()
        if cur_title and cur_time:
            items.append(f"{cur_time} | {cur_title}")
            cur_title = ""
            cur_time = ""
            if len(items) >= limit_items:
                break
    return "\n".join(items).strip()


def _required_headings() -> List[str]:
    return [
        "## 人物档案",
        "### 基本信息",
        "### 生平概述",
        "## 人生足迹地图说明",
        "## 人生历程与重要地点（按时间顺序）",
    ]


def _format_check(md: str) -> Dict[str, Any]:
    checks: Dict[str, Any] = {"ok": True, "errors": [], "warnings": []}
    if not md.strip():
        checks["ok"] = False
        checks["errors"].append("empty_markdown")
        return checks

    for h in _required_headings():
        if h not in md:
            checks["ok"] = False
            checks["errors"].append(f"missing_heading:{h}")

    basic = _extract_basic_info(md)
    need_fields = ["姓名", "时代", "出生", "去世"]
    for f in need_fields:
        if f not in basic:
            checks["warnings"].append(f"basic_info_missing:{f}")

    loc_count = len(re.findall(r"^###\s+", md, flags=re.MULTILINE))
    checks["metrics"] = {"h3_count": loc_count}
    if loc_count < 5:
        checks["ok"] = False
        checks["errors"].append("too_few_h3_locations")

    years = [int(x) for x in re.findall(r"(?<!\d)(\d{3,4})(?!\d)", md)]
    if years:
        if any(y > 2100 for y in years):
            checks["warnings"].append("suspicious_future_year")
    return checks


def _render_fact_check_prompt(person: str, basic_info: str, summary_excerpt: str, timeline_excerpt: str) -> Tuple[str, str]:
    tpl = FACT_CHECK_PROMPT_PATH.read_text(encoding="utf-8")
    m = re.split(r"\n##\s*User\s*\n", tpl, maxsplit=1)
    sys_part = m[0]
    user_part = m[1] if len(m) > 1 else ""
    sys_msg = re.sub(r"^#.*\n", "", sys_part).strip()
    user_msg = (
        user_part.replace("{person}", person)
        .replace("{basic_info}", basic_info or "")
        .replace("{summary_excerpt}", summary_excerpt or "")
        .replace("{timeline_excerpt}", timeline_excerpt or "")
        .strip()
    )
    return sys_msg, user_msg


def _iter_md_files(input_dir: Path) -> List[Path]:
    return sorted([p for p in input_dir.glob("*.md") if p.is_file()], key=lambda p: p.name)


def _safe_person_from_md(path: Path, md: str) -> str:
    m = re.search(r"^#\s*(.+?)\s*$", md, flags=re.MULTILINE)
    if m:
        return m.group(1).strip()
    return path.stem


def validate_one(
    *,
    md_path: Path,
    out_dir: Path,
    do_fact_check: bool,
    do_format_check: bool,
    api_key: str,
    base_url: str,
    model: str,
    timeout_s: int,
    retries: int,
    retry_backoff_s: float,
    skip_existing: bool,
) -> Dict[str, Any]:
    md = md_path.read_text(encoding="utf-8")
    person_title = _safe_person_from_md(md_path, md)
    person = md_path.stem
    result: Dict[str, Any] = {"person": person, "person_title": person_title, "md_path": str(md_path)}

    fact_dir = out_dir / "fact_check"
    fmt_dir = out_dir / "format_check"
    fact_dir.mkdir(parents=True, exist_ok=True)
    fmt_dir.mkdir(parents=True, exist_ok=True)
    fact_out = fact_dir / f"{md_path.stem}.json"
    fmt_out = fmt_dir / f"{md_path.stem}.json"

    if do_fact_check:
        if not (skip_existing and fact_out.exists()):
            basic = _extract_basic_info(md)
            summary = _extract_summary_excerpt(md)
            timeline = _extract_timeline_excerpt(md)
            sys_msg, user_msg = _render_fact_check_prompt(person, basic, summary, timeline)
            content, api = _call_openai_compatible(
                api_key=api_key,
                base_url=base_url,
                model=model,
                messages=[{"role": "system", "content": sys_msg}, {"role": "user", "content": user_msg}],
                timeout_s=timeout_s,
                max_retries=retries,
                retry_backoff_s=retry_backoff_s,
            )
            fc = _safe_json_load(content or "") or {"pass": False, "risk_level": "high", "issues": [], "corrected_facts": {}, "notes": "invalid_json"}
            if isinstance(fc, dict):
                issues = fc.get("issues") if isinstance(fc.get("issues"), list) else []
                high_conf = False
                for it in issues:
                    if not isinstance(it, dict):
                        continue
                    try:
                        c = float(it.get("confidence"))
                    except Exception:
                        c = 0.0
                    if c >= 0.7:
                        high_conf = True
                        break
                if high_conf:
                    fc["pass"] = False
                    fc["risk_level"] = "high"
            payload = {"person": person, "api": asdict(api), "fact_check": fc}
            fact_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        result["fact_check_path"] = str(fact_out)

    if do_format_check:
        if not (skip_existing and fmt_out.exists()):
            fmt = _format_check(md)
            fmt_out.write_text(json.dumps({"person": person, "format_check": fmt}, ensure_ascii=False, indent=2), encoding="utf-8")
        result["format_check_path"] = str(fmt_out)

    return result


def quick_audit_one(
    *,
    md_path: Path,
    out_dir: Path,
    api_key: str,
    base_url: str,
    model: str,
    timeout_s: int,
    retries: int,
    retry_backoff_s: float,
    skip_existing: bool,
    apply_fixes: bool,
    md_limit_chars: int,
) -> Dict[str, Any]:
    t0 = time.perf_counter()
    md = md_path.read_text(encoding="utf-8")
    person_title = _safe_person_from_md(md_path, md)
    person = md_path.stem
    result: Dict[str, Any] = {"person": person, "person_title": person_title, "md_path": str(md_path)}

    audit_dir = out_dir / "quick_audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_out = audit_dir / f"{md_path.stem}.json"
    if skip_existing and audit_out.exists():
        result["quick_audit_path"] = str(audit_out)
        result["skipped"] = True
        result["duration_s"] = round(time.perf_counter() - t0, 3)
        return result

    md_snip = _safe_str(md, md_limit_chars)
    messages = _quick_audit_messages(person_title or person, md_snip)
    content, api = _call_openai_compatible(
        api_key=api_key,
        base_url=base_url,
        model=model,
        messages=messages,
        timeout_s=timeout_s,
        max_retries=retries,
        retry_backoff_s=retry_backoff_s,
    )
    audit = _safe_json_load(content or "") or {
        "need_strict": True,
        "risk_level": "high",
        "entity_disambiguation": {"aliases": [], "foreign_name": None, "domain_tags": [], "uncertain": True, "reason": "invalid_json"},
        "issues": [],
        "patched_markdown": None,
    }

    applied = False
    html_path = ""
    if apply_fixes and isinstance(audit, dict):
        patched = str(audit.get("patched_markdown") or "").strip()
        if patched:
            try:
                html_path = _write_person_markdown_and_render_html(person, patched)
                applied = True
            except Exception as exc:
                audit.setdefault("issues", [])
                if isinstance(audit.get("issues"), list):
                    audit["issues"].append(
                        {
                            "severity": "error",
                            "category": "render",
                            "claim": "apply patched_markdown and render html",
                            "suggested_fix": str(exc),
                            "confidence": 1.0,
                        }
                    )

    payload = {
        "person": person,
        "time": _now(),
        "api": asdict(api),
        "audit": audit,
        "applied": applied,
        "html_path": html_path,
    }
    audit_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    result["quick_audit_path"] = str(audit_out)
    result["applied"] = applied
    if html_path:
        result["html_path"] = html_path
    if isinstance(audit, dict):
        result["audit_risk_level"] = audit.get("risk_level")
        result["need_strict"] = audit.get("need_strict")
        issues = audit.get("issues")
        if isinstance(issues, list):
            result["issues_count"] = len([x for x in issues if isinstance(x, dict)])
        else:
            result["issues_count"] = 0
    result["api_ok"] = bool(api.ok)
    result["api_status"] = api.status_code
    result["duration_s"] = round(time.perf_counter() - t0, 3)
    return result


def two_stage_audit_one(
    *,
    md_path: Path,
    out_dir: Path,
    api_key: str,
    base_url: str,
    model: str,
    timeout_s: int,
    retries: int,
    retry_backoff_s: float,
    skip_existing: bool,
    include_html: bool,
    include_relations: bool,
    apply_fixes: bool,
    quick_md_limit_chars: int,
    strict_md_limit_chars: int,
    html_limit_chars: int,
    edges_limit: int,
    kg: Dict[str, Any],
) -> Dict[str, Any]:
    quick = quick_audit_one(
        md_path=md_path,
        out_dir=out_dir,
        api_key=api_key,
        base_url=base_url,
        model=model,
        timeout_s=timeout_s,
        retries=retries,
        retry_backoff_s=retry_backoff_s,
        skip_existing=skip_existing,
        apply_fixes=apply_fixes,
        md_limit_chars=quick_md_limit_chars,
    )
    need_strict = bool(quick.get("need_strict") is True)
    risk = str(quick.get("audit_risk_level") or "").strip().lower()
    if (not need_strict) and risk in {"low", ""}:
        quick["strict_skipped"] = True
        return quick

    strict = strict_audit_one(
        md_path=md_path,
        out_dir=out_dir,
        api_key=api_key,
        base_url=base_url,
        model=model,
        timeout_s=timeout_s,
        retries=retries,
        retry_backoff_s=retry_backoff_s,
        skip_existing=skip_existing,
        include_html=include_html,
        include_relations=include_relations,
        apply_fixes=apply_fixes,
        md_limit_chars=strict_md_limit_chars,
        html_limit_chars=html_limit_chars,
        edges_limit=edges_limit,
        kg=kg,
    )
    strict["quick_audit_path"] = quick.get("quick_audit_path")
    strict["quick_applied"] = quick.get("applied")
    strict["strict_skipped"] = strict.get("skipped") is True
    return strict


def birthplace_audit_one(
    *,
    md_path: Path,
    out_dir: Path,
    api_key: str,
    base_url: str,
    model: str,
    timeout_s: int,
    retries: int,
    retry_backoff_s: float,
    skip_existing: bool,
    apply_fixes: bool,
    md_limit_chars: int,
) -> Dict[str, Any]:
    t0 = time.perf_counter()
    md = md_path.read_text(encoding="utf-8")
    person_title = _safe_person_from_md(md_path, md)
    person = md_path.stem
    result: Dict[str, Any] = {"person": person, "person_title": person_title, "md_path": str(md_path)}

    audit_dir = out_dir / "birthplace_audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_out = audit_dir / f"{md_path.stem}.json"
    if skip_existing and audit_out.exists():
        result["birthplace_audit_path"] = str(audit_out)
        result["skipped"] = True
        result["duration_s"] = round(time.perf_counter() - t0, 3)
        return result

    md_snip = _safe_str(md, md_limit_chars)
    messages = _birthplace_audit_messages(person_title or person, md_snip)
    content, api = _call_openai_compatible(
        api_key=api_key,
        base_url=base_url,
        model=model,
        messages=messages,
        timeout_s=timeout_s,
        max_retries=retries,
        retry_backoff_s=retry_backoff_s,
    )
    audit = _safe_json_load(content or "") or {
        "overall_pass": False,
        "uncertain": True,
        "birthplace_modern": None,
        "issues": [],
        "patched_markdown": None,
    }

    applied = False
    html_path = ""
    if apply_fixes and isinstance(audit, dict):
        patched = str(audit.get("patched_markdown") or "").strip()
        if patched:
            try:
                html_path = _write_person_markdown_and_render_html(person, patched)
                applied = True
            except Exception as exc:
                audit.setdefault("issues", [])
                if isinstance(audit.get("issues"), list):
                    audit["issues"].append(
                        {"severity": "error", "claim": "apply patched_markdown and render html", "suggested_fix": str(exc), "confidence": 1.0}
                    )

    payload = {"person": person, "time": _now(), "api": asdict(api), "audit": audit, "applied": applied, "html_path": html_path}
    audit_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    result["birthplace_audit_path"] = str(audit_out)
    result["applied"] = applied
    if html_path:
        result["html_path"] = html_path
    if isinstance(audit, dict):
        result["audit_overall_pass"] = audit.get("overall_pass")
        result["audit_uncertain"] = audit.get("uncertain")
        result["audit_birthplace_modern"] = audit.get("birthplace_modern")
        issues = audit.get("issues")
        if isinstance(issues, list):
            result["issues_count"] = len([x for x in issues if isinstance(x, dict)])
        else:
            result["issues_count"] = 0
    result["api_ok"] = bool(api.ok)
    result["api_status"] = api.status_code
    result["duration_s"] = round(time.perf_counter() - t0, 3)
    return result


def strict_audit_one(
    *,
    md_path: Path,
    out_dir: Path,
    api_key: str,
    base_url: str,
    model: str,
    timeout_s: int,
    retries: int,
    retry_backoff_s: float,
    skip_existing: bool,
    include_html: bool,
    include_relations: bool,
    apply_fixes: bool,
    md_limit_chars: int,
    html_limit_chars: int,
    edges_limit: int,
    kg: Dict[str, Any],
) -> Dict[str, Any]:
    t0 = time.perf_counter()
    md = md_path.read_text(encoding="utf-8")
    person_title = _safe_person_from_md(md_path, md)
    person = md_path.stem
    result: Dict[str, Any] = {"person": person, "person_title": person_title, "md_path": str(md_path)}

    audit_dir = out_dir / "strict_audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_out = audit_dir / f"{md_path.stem}.json"
    if skip_existing and audit_out.exists():
        result["strict_audit_path"] = str(audit_out)
        result["skipped"] = True
        result["duration_s"] = round(time.perf_counter() - t0, 3)
        return result

    md_snip = _safe_str(md, md_limit_chars)
    html = _load_latest_person_html(person, html_limit_chars) if include_html else ""
    edges = _load_edges_for_person(person, kg, edges_limit) if include_relations else []
    edges_json = json.dumps(edges, ensure_ascii=False, indent=2) if edges else "[]"

    messages = _strict_audit_messages(person_title or person, md_snip, html, edges_json)
    content, api = _call_openai_compatible(
        api_key=api_key,
        base_url=base_url,
        model=model,
        messages=messages,
        timeout_s=timeout_s,
        max_retries=retries,
        retry_backoff_s=retry_backoff_s,
    )
    audit = _safe_json_load(content or "") or {
        "overall_pass": False,
        "risk_level": "high",
        "entity_identity": {"is_real_person_likely": False, "uncertain": True, "reason": "invalid_json"},
        "facts": {"dynasty": None, "birth_year": None, "death_year": None, "birthplace_modern": None},
        "issues": [],
        "relation_audit": {"keep": [], "drop": [], "add": []},
        "patched_markdown": None,
    }

    applied = False
    html_path = ""
    if apply_fixes and isinstance(audit, dict) and _should_apply_patch(audit):
        patched = str(audit.get("patched_markdown") or "").strip()
        if patched:
            try:
                html_path = _write_person_markdown_and_render_html(person, patched)
                applied = True
            except Exception as exc:
                audit.setdefault("issues", [])
                if isinstance(audit.get("issues"), list):
                    audit["issues"].append(
                        {
                            "severity": "error",
                            "category": "render",
                            "claim": "apply patched_markdown and render html",
                            "evidence_text": "",
                            "suggested_fix": str(exc),
                            "confidence": 1.0,
                        }
                    )

    payload = {"person": person, "time": _now(), "api": asdict(api), "audit": audit, "applied": applied, "html_path": html_path}
    audit_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    result["strict_audit_path"] = str(audit_out)
    result["applied"] = applied
    if html_path:
        result["html_path"] = html_path
    if isinstance(audit, dict):
        result["audit_overall_pass"] = audit.get("overall_pass")
        result["audit_risk_level"] = audit.get("risk_level")
        issues = audit.get("issues")
        if isinstance(issues, list):
            result["issues_count"] = len([x for x in issues if isinstance(x, dict)])
        else:
            result["issues_count"] = 0
    result["api_ok"] = bool(api.ok)
    result["api_status"] = api.status_code
    result["duration_s"] = round(time.perf_counter() - t0, 3)
    return result


def main() -> int:
    p = argparse.ArgumentParser(description="人物信息真实性校验（MiMo）+ 格式校验（本地）")
    p.add_argument("--input-dir", default=str(EXAMPLES_STORY_DIR))
    p.add_argument("--out-dir", default=str(REPO_ROOT / "data" / "validation_reports"))
    p.add_argument("--strict-audit", action="store_true", help="严格审查 Markdown+HTML+关系边，并可回写修正")
    p.add_argument("--web-search-truth-audit", action="store_true", help="使用联网搜索做人物真实性/关键事实核查（可回写修正）")
    p.add_argument("--birthplace-audit", action="store_true", help="只做籍贯/出生地信息的 MiMo 核查与最小纠偏（可回写）")
    p.add_argument("--two-stage", action="store_true", help="strict-audit 前先做 quick-audit（更快更省），仅对高风险/需严格审查者再跑 strict-audit")
    p.add_argument("--apply-fixes", action="store_true", help="strict-audit 输出 patched_markdown 时回写 md 并重渲 html")
    p.add_argument("--no-html", action="store_true", help="strict-audit 不提供 HTML 上下文")
    p.add_argument("--no-relations", action="store_true", help="strict-audit 不提供关系边上下文")
    p.add_argument("--progress-log", default="", help="将进度行追加写入该文件（同时仍输出到终端）")
    p.add_argument("--quick-md-limit-chars", type=int, default=int(os.getenv("QUICK_MD_LIMIT", "25000")))
    p.add_argument("--birthplace-md-limit-chars", type=int, default=int(os.getenv("BIRTHPLACE_MD_LIMIT", "45000")))
    p.add_argument("--web-search-md-limit-chars", type=int, default=int(os.getenv("WEB_SEARCH_MD_LIMIT", "22000")))
    p.add_argument("--web-search-max-keyword", type=int, default=int(os.getenv("WEB_SEARCH_MAX_KEYWORD", "3")))
    p.add_argument("--web-search-limit", type=int, default=int(os.getenv("WEB_SEARCH_LIMIT", "2")))
    p.add_argument("--md-limit-chars", type=int, default=int(os.getenv("STRICT_MD_LIMIT", "65000")))
    p.add_argument("--html-limit-chars", type=int, default=int(os.getenv("STRICT_HTML_LIMIT", "65000")))
    p.add_argument("--edges-limit", type=int, default=int(os.getenv("STRICT_EDGES_LIMIT", "80")))
    p.add_argument("--fact-check", action="store_true")
    p.add_argument("--format-check", action="store_true")
    p.add_argument("--only", choices=["fact", "format", "both"], default="both")
    p.add_argument("--limit", type=int, default=0, help="只校验前 N 个文件（0 表示全量）")
    p.add_argument("--only-format-fails", action="store_true", help="只处理 format_check 未通过的人物（依据 out-dir 下 format_check 报告）")
    p.add_argument("--only-strict-fails", action="store_true", help="strict-audit 时：只处理未生成审查报告 / 审查不通过 / 风险较高 / API 失败 的人物")
    p.add_argument("--only-high-risk", action="store_true", help="strict-audit 时：优先只跑高风险/高置信问题（risk=high 或 overall_pass=false 或存在 error 且 confidence>=0.7 或疑似非真实人物）")
    p.add_argument("--only-risk-levels", default="", help="strict-audit 时：只处理指定风险等级（逗号分隔，如 high,medium）。依据 out-dir/strict_audit/<人>.json")
    p.add_argument(
        "--only-location-bad",
        action="store_true",
        help="只处理地点可用性疑似较差的 Markdown（本地规则：含“附近/一带/等地/不详/—”等，或坐标表含不可定位字段）",
    )
    p.add_argument("--persons", default="", help="只处理指定人物（用英文逗号分隔）。如：--persons \"李白,苏轼\"")
    p.add_argument("--exclude-persons", default="", help="排除指定人物（用英文逗号分隔）。如：--exclude-persons \"朱元璋\"")
    p.add_argument("--apply-relation-drops", action="store_true", help="strict-audit 结束后，将 relation_audit.drop 的高置信结论用于清理知识图谱中的弱关系边")
    p.add_argument("--relation-drop-min-confidence", type=float, default=float(os.getenv("RELATION_DROP_MIN_CONF", "0.8") or "0.8"))
    p.add_argument("--relation-drop-types", default=os.getenv("RELATION_DROP_TYPES", "manual") or "manual", help="要清理的边类型（逗号分隔），默认 manual")
    p.add_argument("--concurrency", type=int, default=int(os.getenv("VALIDATE_CONCURRENCY", "20")))
    p.add_argument("--timeout", type=int, default=int(os.getenv("VALIDATE_TIMEOUT", "120")))
    p.add_argument("--retries", type=int, default=int(os.getenv("VALIDATE_RETRIES", "2")))
    p.add_argument("--retry-backoff", type=float, default=float(os.getenv("VALIDATE_RETRY_BACKOFF_S", "2")))
    p.add_argument("--skip-existing", action="store_true")
    args = p.parse_args()

    _load_env()
    api_key = (
        os.getenv("MIMO_API_KEY")
        or os.getenv("MIMO_API_Key")
        or os.getenv("MIMO_APIKEY")
        or os.getenv("API_KEY")
        or os.getenv("LLM_API_KEY")
        or os.getenv("Amap_API_Key")
        or ""
    ).strip()
    base_url = (
        os.getenv("MIMO_BASE_URL")
        or os.getenv("BASE_URL")
        or os.getenv("LLM_BASE_URL")
        or os.getenv("Amap_API_Base_URL")
        or "https://api.xiaomimimo.com/v1"
    ).strip()
    model = (os.getenv("MODEL") or os.getenv("LLM_MODEL_ID") or "mimo-v2-pro").strip()
    if not api_key:
        raise SystemExit("missing api key: set MIMO_API_KEY (preferred) or API_KEY")

    input_dir = Path(args.input_dir).resolve()
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    kg: Dict[str, Any] = {}
    try:
        if KNOWLEDGE_GRAPH_JSON.exists():
            kg = json.loads(KNOWLEDGE_GRAPH_JSON.read_text(encoding="utf-8"))
    except Exception:
        kg = {}

    do_fact = args.only in ("fact", "both")
    do_fmt = args.only in ("format", "both")

    md_files = _iter_md_files(input_dir)
    persons_filter: Optional[set[str]] = None
    if isinstance(args.persons, str) and args.persons.strip():
        persons_filter = {p.strip() for p in args.persons.split(",") if p.strip()}
        if persons_filter:
            md_files = [p for p in md_files if p.stem in persons_filter]
    if isinstance(args.exclude_persons, str) and args.exclude_persons.strip():
        exclude = {p.strip() for p in args.exclude_persons.split(",") if p.strip()}
        if exclude:
            md_files = [p for p in md_files if p.stem not in exclude]
    if bool(args.only_format_fails):
        fmt_dir = out_dir / "format_check"
        keep: List[Path] = []
        for md_path in md_files:
            report_path = fmt_dir / f"{md_path.stem}.json"
            if not report_path.exists():
                continue
            try:
                payload = json.loads(report_path.read_text(encoding="utf-8"))
                fc = payload.get("format_check") if isinstance(payload, dict) else None
                ok = bool(fc.get("ok")) if isinstance(fc, dict) else False
            except Exception:
                ok = False
            if not ok:
                keep.append(md_path)
        md_files = keep
    if bool(args.strict_audit) and bool(args.only_strict_fails):
        audit_dir = out_dir / "strict_audit"
        keep2: List[Path] = []
        for md_path in md_files:
            report_path = audit_dir / f"{md_path.stem}.json"
            if not report_path.exists():
                keep2.append(md_path)
                continue
            try:
                payload = json.loads(report_path.read_text(encoding="utf-8"))
            except Exception:
                keep2.append(md_path)
                continue
            if not isinstance(payload, dict):
                keep2.append(md_path)
                continue
            api = payload.get("api")
            if isinstance(api, dict) and api.get("ok") is False:
                keep2.append(md_path)
                continue
            audit = payload.get("audit")
            if not isinstance(audit, dict):
                keep2.append(md_path)
                continue
            if audit.get("overall_pass") is False:
                keep2.append(md_path)
                continue
            risk = str(audit.get("risk_level") or "").strip().lower()
            if risk in ("high", "medium"):
                keep2.append(md_path)
                continue
            issues = audit.get("issues")
            if isinstance(issues, list):
                for it in issues:
                    if not isinstance(it, dict):
                        continue
                    sev = str(it.get("severity") or "").strip().lower()
                    if sev not in ("warn", "error"):
                        continue
                    try:
                        c = float(it.get("confidence"))
                    except Exception:
                        c = 0.0
                    if c >= 0.6:
                        keep2.append(md_path)
                        break
        md_files = keep2
    if bool(args.strict_audit) and bool(args.only_high_risk):
        audit_dir = out_dir / "strict_audit"
        keep3: List[Path] = []
        for md_path in md_files:
            report_path = audit_dir / f"{md_path.stem}.json"
            if not report_path.exists():
                keep3.append(md_path)
                continue
            try:
                payload = json.loads(report_path.read_text(encoding="utf-8"))
            except Exception:
                keep3.append(md_path)
                continue
            if not isinstance(payload, dict):
                keep3.append(md_path)
                continue
            api = payload.get("api")
            if isinstance(api, dict) and api.get("ok") is False:
                keep3.append(md_path)
                continue
            audit = payload.get("audit")
            if not isinstance(audit, dict):
                keep3.append(md_path)
                continue
            if audit.get("overall_pass") is False:
                keep3.append(md_path)
                continue
            risk = str(audit.get("risk_level") or "").strip().lower()
            if risk == "high":
                keep3.append(md_path)
                continue
            ent = audit.get("entity_identity")
            if isinstance(ent, dict):
                if ent.get("is_real_person_likely") is False:
                    keep3.append(md_path)
                    continue
                if ent.get("uncertain") is True:
                    keep3.append(md_path)
                    continue
            issues = audit.get("issues")
            if isinstance(issues, list):
                for it in issues:
                    if not isinstance(it, dict):
                        continue
                    if str(it.get("severity") or "").strip().lower() != "error":
                        continue
                    try:
                        c = float(it.get("confidence"))
                    except Exception:
                        c = 0.0
                    if c >= 0.7:
                        keep3.append(md_path)
                        break
        md_files = keep3
    if bool(args.strict_audit) and isinstance(args.only_risk_levels, str) and args.only_risk_levels.strip():
        want = {s.strip().lower() for s in args.only_risk_levels.split(",") if s.strip()}
        audit_dir = out_dir / "strict_audit"
        keep4: List[Path] = []
        for md_path in md_files:
            report_path = audit_dir / f"{md_path.stem}.json"
            if not report_path.exists():
                continue
            try:
                payload = json.loads(report_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            audit = payload.get("audit") if isinstance(payload, dict) else None
            if not isinstance(audit, dict):
                continue
            risk = str(audit.get("risk_level") or "").strip().lower()
            if risk in want:
                keep4.append(md_path)
                continue
            if audit.get("overall_pass") is False and ("fail" in want or "failed" in want):
                keep4.append(md_path)
                continue
        md_files = keep4
    if bool(args.only_location_bad):
        bad_re = re.compile(r"(附近|一带|等地|不详|未详|未知|无考|待考|—|暂无|不明)")
        keep5: List[Path] = []
        for md_path in md_files:
            try:
                md = md_path.read_text(encoding="utf-8")
            except Exception:
                continue
            if bad_re.search(md):
                keep5.append(md_path)
                continue
            table = ""
            m = re.search(r"^##\s*地点坐标\s*$([\s\S]{0,4000})", md, flags=re.MULTILINE)
            if m:
                table = m.group(1) or ""
            if table and bad_re.search(table):
                keep5.append(md_path)
                continue
        md_files = keep5
    if int(args.limit) > 0:
        md_files = md_files[: int(args.limit)]
    total = len(md_files)
    if total == 0:
        raise SystemExit(f"no md files in {input_dir}")

    conc = max(1, min(80, int(args.concurrency)))
    done = 0
    ok_fact = 0
    high_risk = 0
    fmt_ok = 0

    summary_rows: List[Dict[str, Any]] = []
    t0 = time.perf_counter()
    progress_fp = None
    if args.progress_log:
        progress_path = Path(str(args.progress_log)).expanduser()
        if not progress_path.is_absolute():
            progress_path = (out_dir / progress_path).resolve()
        progress_path.parent.mkdir(parents=True, exist_ok=True)
        progress_fp = progress_path.open("a", encoding="utf-8")

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=conc) as ex:
            futs = []
            fut_to_person: Dict[concurrent.futures.Future, str] = {}
            for md_path in md_files:
                if args.web_search_truth_audit:
                    fut = ex.submit(
                        web_search_truth_audit_one,
                        md_path=md_path,
                        out_dir=out_dir,
                        api_key=api_key,
                        base_url=base_url,
                        model=model,
                        timeout_s=int(args.timeout),
                        retries=int(args.retries),
                        retry_backoff_s=float(args.retry_backoff),
                        skip_existing=bool(args.skip_existing),
                        apply_fixes=bool(args.apply_fixes),
                        md_limit_chars=int(args.web_search_md_limit_chars),
                        max_keyword=int(args.web_search_max_keyword),
                        search_limit=int(args.web_search_limit),
                    )
                    futs.append(fut)
                    fut_to_person[fut] = md_path.stem
                elif args.birthplace_audit:
                    fut = ex.submit(
                        birthplace_audit_one,
                        md_path=md_path,
                        out_dir=out_dir,
                        api_key=api_key,
                        base_url=base_url,
                        model=model,
                        timeout_s=int(args.timeout),
                        retries=int(args.retries),
                        retry_backoff_s=float(args.retry_backoff),
                        skip_existing=bool(args.skip_existing),
                        apply_fixes=bool(args.apply_fixes),
                        md_limit_chars=int(args.birthplace_md_limit_chars),
                    )
                    futs.append(fut)
                    fut_to_person[fut] = md_path.stem
                elif args.strict_audit:
                    if bool(args.two_stage):
                        fut = ex.submit(
                            two_stage_audit_one,
                            md_path=md_path,
                            out_dir=out_dir,
                            api_key=api_key,
                            base_url=base_url,
                            model=model,
                            timeout_s=int(args.timeout),
                            retries=int(args.retries),
                            retry_backoff_s=float(args.retry_backoff),
                            skip_existing=bool(args.skip_existing),
                            include_html=not bool(args.no_html),
                            include_relations=not bool(args.no_relations),
                            apply_fixes=bool(args.apply_fixes),
                            quick_md_limit_chars=int(args.quick_md_limit_chars),
                            strict_md_limit_chars=int(args.md_limit_chars),
                            html_limit_chars=int(args.html_limit_chars),
                            edges_limit=int(args.edges_limit),
                            kg=kg,
                        )
                    else:
                        fut = ex.submit(
                            strict_audit_one,
                            md_path=md_path,
                            out_dir=out_dir,
                            api_key=api_key,
                            base_url=base_url,
                            model=model,
                            timeout_s=int(args.timeout),
                            retries=int(args.retries),
                            retry_backoff_s=float(args.retry_backoff),
                            skip_existing=bool(args.skip_existing),
                            include_html=not bool(args.no_html),
                            include_relations=not bool(args.no_relations),
                            apply_fixes=bool(args.apply_fixes),
                            md_limit_chars=int(args.md_limit_chars),
                            html_limit_chars=int(args.html_limit_chars),
                            edges_limit=int(args.edges_limit),
                            kg=kg,
                        )
                    futs.append(fut)
                    fut_to_person[fut] = md_path.stem
                else:
                    fut = ex.submit(
                        validate_one,
                        md_path=md_path,
                        out_dir=out_dir,
                        do_fact_check=do_fact,
                        do_format_check=do_fmt,
                        api_key=api_key,
                        base_url=base_url,
                        model=model,
                        timeout_s=int(args.timeout),
                        retries=int(args.retries),
                        retry_backoff_s=float(args.retry_backoff),
                        skip_existing=bool(args.skip_existing),
                    )
                    futs.append(fut)
                    fut_to_person[fut] = md_path.stem

            for fut in concurrent.futures.as_completed(futs):
                row = fut.result()
                summary_rows.append(row)
                done += 1
                if args.web_search_truth_audit:
                    person = str(row.get("person") or fut_to_person.get(fut) or "")
                    passed = row.get("audit_overall_pass")
                    ptxt = "PASS" if passed is True else ("FAIL" if passed is False else "-")
                    uncertain = row.get("audit_uncertain")
                    utxt = "UNCERTAIN" if uncertain is True else ""
                    realp = row.get("audit_is_real_person_likely")
                    rtxt = "REAL" if realp is True else ("NOT_REAL" if realp is False else "-")
                    applied = "APPLIED" if row.get("applied") is True else "-"
                    api_ok = row.get("api_ok")
                    api_txt = "OK" if api_ok is True else ("FAIL" if api_ok is False else "-")
                    http = row.get("api_status") if "api_status" in row else "-"
                    dt = row.get("duration_s") if "duration_s" in row else "-"
                    issues_n = row.get("issues_count") if "issues_count" in row else "-"
                    line = f"[{done}/{total}] {person} | {ptxt} {rtxt} issues={issues_n} {utxt} | API={api_txt} HTTP={http} | {applied} | {dt}s"
                    print(line, flush=True)
                    if progress_fp is not None:
                        progress_fp.write(line + "\n")
                        progress_fp.flush()
                elif args.birthplace_audit:
                    person = str(row.get("person") or fut_to_person.get(fut) or "")
                    passed = row.get("audit_overall_pass")
                    ptxt = "PASS" if passed is True else ("FAIL" if passed is False else "-")
                    uncertain = row.get("audit_uncertain")
                    utxt = "UNCERTAIN" if uncertain is True else ""
                    applied = "APPLIED" if row.get("applied") is True else "-"
                    api_ok = row.get("api_ok")
                    api_txt = "OK" if api_ok is True else ("FAIL" if api_ok is False else "-")
                    http = row.get("api_status") if "api_status" in row else "-"
                    dt = row.get("duration_s") if "duration_s" in row else "-"
                    issues_n = row.get("issues_count") if "issues_count" in row else "-"
                    line = f"[{done}/{total}] {person} | {ptxt} issues={issues_n} {utxt} | API={api_txt} HTTP={http} | {applied} | {dt}s"
                    print(line, flush=True)
                    if progress_fp is not None:
                        progress_fp.write(line + "\n")
                        progress_fp.flush()
                elif args.strict_audit:
                    person = str(row.get("person") or fut_to_person.get(fut) or "")
                    risk = str(row.get("audit_risk_level") or "-")
                    passed = row.get("audit_overall_pass")
                    ptxt = "PASS" if passed is True else ("FAIL" if passed is False else "-")
                    applied = "APPLIED" if row.get("applied") is True else "-"
                    skipped = "SKIP" if row.get("skipped") is True else ""
                    api_ok = row.get("api_ok")
                    api_txt = "OK" if api_ok is True else ("FAIL" if api_ok is False else "-")
                    http = row.get("api_status") if "api_status" in row else "-"
                    dt = row.get("duration_s") if "duration_s" in row else "-"
                    issues_n = row.get("issues_count") if "issues_count" in row else "-"
                    line = f"[{done}/{total}] {person} | {ptxt} risk={risk} issues={issues_n} | API={api_txt} HTTP={http} | {applied} {skipped} | {dt}s"
                    print(line, flush=True)
                    if progress_fp is not None:
                        progress_fp.write(line + "\n")
                        progress_fp.flush()
    finally:
        if progress_fp is not None:
            try:
                progress_fp.close()
            except Exception:
                pass

    elapsed = time.perf_counter() - t0

    if args.web_search_truth_audit:
        applied_n = sum(1 for r in summary_rows if r.get("applied") is True)
        out_summary = {
            "time": _now(),
            "input_dir": str(input_dir),
            "total": total,
            "elapsed_s": elapsed,
            "web_search_truth_audit": {
                "enabled": True,
                "applied": applied_n,
                "out_dir": str(out_dir / "web_search_truth_audit"),
            },
        }
        (out_dir / "summary_web_search_truth.json").write_text(json.dumps(out_summary, ensure_ascii=False, indent=2), encoding="utf-8")
        idx_rows = sorted(summary_rows, key=lambda x: str(x.get("person") or ""))
        (out_dir / "index_web_search_truth.json").write_text(json.dumps(idx_rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(out_summary, ensure_ascii=False))
        return 0

    if args.strict_audit:
        applied_n = sum(1 for r in summary_rows if r.get("applied") is True)
        rel_drop_result = None
        if bool(args.apply_relation_drops):
            drop_types = {s.strip() for s in str(args.relation_drop_types or "").split(",") if s.strip()}
            rel_drop_result = _apply_relation_drops_to_knowledge_graph(
                strict_rows=[r for r in summary_rows if isinstance(r, dict)],
                kg_path=KNOWLEDGE_GRAPH_JSON,
                min_confidence=float(args.relation_drop_min_confidence),
                drop_types=drop_types or {"manual"},
            )
        out_summary = {
            "time": _now(),
            "input_dir": str(input_dir),
            "total": total,
            "elapsed_s": elapsed,
            "strict_audit": {
                "enabled": True,
                "applied": applied_n,
                "out_dir": str(out_dir / "strict_audit"),
            },
        }
        if isinstance(rel_drop_result, dict):
            out_summary["relation_drops"] = rel_drop_result
        (out_dir / "summary_strict.json").write_text(json.dumps(out_summary, ensure_ascii=False, indent=2), encoding="utf-8")
        idx_rows = sorted(summary_rows, key=lambda x: str(x.get("person") or ""))
        (out_dir / "index_strict.json").write_text(json.dumps(idx_rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(out_summary, ensure_ascii=False))
        return 0

    if do_fact:
        for r in summary_rows:
            p = r.get("fact_check_path")
            if not p:
                continue
            data = _safe_json_load(Path(p).read_text(encoding="utf-8")) or {}
            fc = data.get("fact_check") if isinstance(data.get("fact_check"), dict) else {}
            if fc.get("pass") is True:
                ok_fact += 1
            if str(fc.get("risk_level") or "").lower() == "high":
                high_risk += 1

    if do_fmt:
        for r in summary_rows:
            p = r.get("format_check_path")
            if not p:
                continue
            data = _safe_json_load(Path(p).read_text(encoding="utf-8")) or {}
            fc = data.get("format_check") if isinstance(data.get("format_check"), dict) else {}
            if fc.get("ok") is True:
                fmt_ok += 1

    out_summary = {
        "time": _now(),
        "input_dir": str(input_dir),
        "total": total,
        "elapsed_s": elapsed,
        "fact_check": {"enabled": do_fact, "pass": ok_fact, "high_risk": high_risk},
        "format_check": {"enabled": do_fmt, "ok": fmt_ok},
    }
    (out_dir / "summary.json").write_text(json.dumps(out_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    idx_rows = sorted(summary_rows, key=lambda x: str(x.get("person") or ""))
    (out_dir / "index.json").write_text(json.dumps(idx_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "index_fact.json").write_text(json.dumps([r for r in idx_rows if r.get("fact_check_path")], ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "index_format.json").write_text(json.dumps([r for r in idx_rows if r.get("format_check_path")], ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out_summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

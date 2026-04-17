#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLES_STORY_DIR = REPO_ROOT / "main" / "storymap" / "examples" / "story"
FACT_CHECK_PROMPT_PATH = REPO_ROOT / "main" / "storymap" / "docs" / "fact_check_prompt.md"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _load_env() -> None:
    load_dotenv(REPO_ROOT / "data" / ".env")
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


def _safe_json_load(text: str) -> Optional[Dict[str, Any]]:
    raw = _strip_fence(text)
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        pass
    try:
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            return None
        data = json.loads(m.group(0))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


@dataclass
class ApiAttempt:
    ok: bool
    status_code: Optional[int]
    error_type: str
    error_message: str
    duration_s: float
    usage: Optional[Dict[str, Any]]


def _call_openai_compatible(
    *,
    api_key: str,
    base_url: str,
    model: str,
    messages: List[Dict[str, str]],
    timeout_s: int,
    max_retries: int,
    retry_backoff_s: float,
) -> Tuple[Optional[str], ApiAttempt]:
    url = _endpoint(base_url)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload: Dict[str, Any] = {"model": model, "messages": messages, "temperature": 0.0, "stream": False}

    last = ApiAttempt(ok=False, status_code=None, error_type="not_run", error_message="", duration_s=0.0, usage=None)
    for attempt in range(1, max_retries + 1):
        t0 = time.perf_counter()
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=(10, int(timeout_s)))
            dt = time.perf_counter() - t0
            status = resp.status_code
            if status >= 400:
                last = ApiAttempt(
                    ok=False,
                    status_code=status,
                    error_type="http_error",
                    error_message=(resp.text[:400] if isinstance(resp.text, str) else ""),
                    duration_s=dt,
                    usage=None,
                )
                if status in (429, 500, 502, 503, 504) and attempt < max_retries:
                    time.sleep(retry_backoff_s * attempt)
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
                time.sleep(retry_backoff_s * attempt)
                continue
            return None, last
        except requests.exceptions.RequestException as exc:
            dt = time.perf_counter() - t0
            last = ApiAttempt(ok=False, status_code=None, error_type="request_exception", error_message=str(exc), duration_s=dt, usage=None)
            if attempt < max_retries:
                time.sleep(retry_backoff_s * attempt)
                continue
            return None, last

    return None, last


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
    person = _safe_person_from_md(md_path, md)
    result: Dict[str, Any] = {"person": person, "md_path": str(md_path)}

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


def main() -> int:
    p = argparse.ArgumentParser(description="人物信息真实性校验（MiMo）+ 格式校验（本地）")
    p.add_argument("--input-dir", default=str(EXAMPLES_STORY_DIR))
    p.add_argument("--out-dir", default=str(REPO_ROOT / "data" / "validation_reports"))
    p.add_argument("--fact-check", action="store_true")
    p.add_argument("--format-check", action="store_true")
    p.add_argument("--only", choices=["fact", "format", "both"], default="both")
    p.add_argument("--concurrency", type=int, default=int(os.getenv("VALIDATE_CONCURRENCY", "20")))
    p.add_argument("--timeout", type=int, default=int(os.getenv("VALIDATE_TIMEOUT", "120")))
    p.add_argument("--retries", type=int, default=int(os.getenv("VALIDATE_RETRIES", "2")))
    p.add_argument("--retry-backoff", type=float, default=float(os.getenv("VALIDATE_RETRY_BACKOFF_S", "2")))
    p.add_argument("--skip-existing", action="store_true")
    args = p.parse_args()

    _load_env()
    api_key = (os.getenv("MIMO_API_KEY") or os.getenv("API_KEY") or os.getenv("LLM_API_KEY") or "").strip()
    base_url = (os.getenv("MIMO_BASE_URL") or os.getenv("BASE_URL") or os.getenv("LLM_BASE_URL") or "https://api.xiaomimimo.com/v1").strip()
    model = (os.getenv("MODEL") or os.getenv("LLM_MODEL_ID") or "mimo-v2-pro").strip()
    if not api_key:
        raise SystemExit("missing api key: set MIMO_API_KEY (preferred) or API_KEY")

    input_dir = Path(args.input_dir).resolve()
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    do_fact = args.only in ("fact", "both")
    do_fmt = args.only in ("format", "both")

    md_files = _iter_md_files(input_dir)
    total = len(md_files)
    if total == 0:
        raise SystemExit(f"no md files in {input_dir}")

    conc = max(1, min(30, int(args.concurrency)))
    done = 0
    ok_fact = 0
    high_risk = 0
    fmt_ok = 0

    summary_rows: List[Dict[str, Any]] = []
    t0 = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=conc) as ex:
        futs = []
        for md_path in md_files:
            futs.append(
                ex.submit(
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
            )

        for fut in concurrent.futures.as_completed(futs):
            row = fut.result()
            summary_rows.append(row)
            done += 1

    elapsed = time.perf_counter() - t0

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

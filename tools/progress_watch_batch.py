#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _scan_runs(out_dir: Path) -> Tuple[int, int, int, List[Dict[str, Any]]]:
    runs_dir = out_dir / "runs"
    if not runs_dir.exists():
        return 0, 0, 0, []

    done = 0
    ok = 0
    fail = 0
    latest: List[Dict[str, Any]] = []

    for d in sorted(runs_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not d.is_dir():
            continue
        result_path = d / "result.json"
        if not result_path.exists():
            continue
        done += 1
        data = _read_json(result_path)
        is_ok = bool(data.get("ok_end_to_end"))
        if is_ok:
            ok += 1
        else:
            fail += 1
        if len(latest) < 8:
            api = data.get("api") or {}
            latest.append(
                {
                    "person": data.get("person") or d.name,
                    "ok": is_ok,
                    "api_ok": bool(api.get("ok")),
                    "api_error_type": api.get("error_type") or "",
                    "api_status": api.get("status_code"),
                    "duration_s": data.get("duration_s"),
                }
            )

    return done, ok, fail, latest


def _render_md(
    *,
    out_dir: Path,
    total: int,
    done: int,
    ok: int,
    fail: int,
    latest: List[Dict[str, Any]],
) -> str:
    pct = 0.0 if total <= 0 else (done / total * 100.0)
    lines: List[str] = []
    lines.append(f"# 批量进度（每 5 分钟刷新）")
    lines.append("")
    lines.append(f"- out_dir: `{out_dir}`")
    lines.append(f"- time: `{_now()}`")
    lines.append(f"- progress: `{done}/{total}` ({pct:.1f}%)")
    lines.append(f"- ok_end_to_end: `{ok}`")
    lines.append(f"- failed: `{fail}`")
    lines.append("")
    if latest:
        lines.append("## 最近完成")
        for r in latest:
            tag = "OK" if r.get("ok") else "FAIL"
            api_tag = "API_OK" if r.get("api_ok") else "API_FAIL"
            e = r.get("api_error_type") or ""
            sc = r.get("api_status")
            ds = r.get("duration_s")
            lines.append(f"- `{tag}` `{api_tag}` {r.get('person')} (status={sc}, err={e}, duration_s={ds})")
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out-dir", required=True)
    p.add_argument("--total", type=int, default=10)
    p.add_argument("--interval", type=int, default=300)
    args = p.parse_args()

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "progress_5min.md"

    while True:
        done, ok, fail, latest = _scan_runs(out_dir)
        md = _render_md(out_dir=out_dir, total=int(args.total), done=done, ok=ok, fail=fail, latest=latest)
        md_path.write_text(md, encoding="utf-8")
        print(md.strip())
        time.sleep(max(30, int(args.interval)))


if __name__ == "__main__":
    raise SystemExit(main())


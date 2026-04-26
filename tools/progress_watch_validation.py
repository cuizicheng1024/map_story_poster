#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import time
from datetime import datetime
from pathlib import Path
from typing import List


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _count_json(dir_path: Path) -> int:
    if not dir_path.exists():
        return 0
    return sum(1 for _ in dir_path.glob("*.json"))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out-dir", required=True)
    p.add_argument("--total", type=int, required=True)
    p.add_argument("--interval", type=int, default=60)
    p.add_argument("--out-file", default="progress_live.md")
    args = p.parse_args()

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out_file)
    if not out_path.is_absolute():
        out_path = out_dir / args.out_file

    interval_s = max(15, int(args.interval))
    total = int(args.total)
    fact_dir = out_dir / "fact_check"
    fmt_dir = out_dir / "format_check"

    while True:
        fact_done = _count_json(fact_dir)
        fmt_done = _count_json(fmt_dir)
        lines: List[str] = []
        lines.append("# 校验进度")
        lines.append("")
        lines.append(f"- time: `{_now()}`")
        lines.append(f"- out_dir: `{out_dir}`")
        lines.append(f"- total: `{total}`")
        lines.append(f"- fact_check_done: `{fact_done}/{total}` ({(fact_done/total*100.0 if total else 0):.1f}%)")
        lines.append(f"- format_check_done: `{fmt_done}/{total}` ({(fmt_done/total*100.0 if total else 0):.1f}%)")
        out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"{_now()} fact={fact_done}/{total} format={fmt_done}/{total}")
        time.sleep(interval_s)


if __name__ == "__main__":
    raise SystemExit(main())


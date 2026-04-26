#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import subprocess
import time
from pathlib import Path


def _count_json(dir_path: Path) -> int:
    if not dir_path.exists():
        return 0
    return sum(1 for _ in dir_path.glob("*.json"))


def main() -> int:
    p = argparse.ArgumentParser(description="等待事实核查完成后再做格式校验（两阶段）")
    p.add_argument("--input-dir", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--total", type=int, required=True)
    p.add_argument("--poll", type=int, default=60)
    p.add_argument("--format-concurrency", type=int, default=30)
    p.add_argument("--skip-existing", action="store_true")
    args = p.parse_args()

    out_dir = Path(args.out_dir).resolve()
    fact_dir = out_dir / "fact_check"
    poll_s = max(15, int(args.poll))
    total = int(args.total)

    while True:
        done = _count_json(fact_dir)
        if done >= total:
            break
        time.sleep(poll_s)

    cmd = [
        "python3",
        str((Path(__file__).resolve().parent / "validate_people_info.py").resolve()),
        "--input-dir",
        str(Path(args.input_dir).resolve()),
        "--out-dir",
        str(out_dir),
        "--only",
        "format",
        "--concurrency",
        str(int(args.format_concurrency)),
        "--skip-existing" if args.skip_existing else "",
    ]
    cmd = [c for c in cmd if c]
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())


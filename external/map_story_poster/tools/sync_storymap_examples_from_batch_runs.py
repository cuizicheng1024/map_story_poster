#!/usr/bin/env python3

from __future__ import annotations

import argparse
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


REPO_ROOT = Path(__file__).resolve().parent.parent


def _person_dirname(p: Path) -> str:
    return p.name


def _format_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y%m%d_%H%M%S")


def _find_latest_preview_by_person(batch_runs_dir: Path) -> Dict[str, Path]:
    latest: Dict[str, Tuple[float, Path]] = {}
    if not batch_runs_dir.exists():
        return {}
    for preview in batch_runs_dir.glob("**/runs/*/preview.html"):
        if not preview.is_file():
            continue
        person = _person_dirname(preview.parent)
        try:
            mt = preview.stat().st_mtime
        except Exception:
            continue
        cur = latest.get(person)
        if cur is None or mt > cur[0]:
            latest[person] = (mt, preview)
    return {k: v[1] for k, v in latest.items()}


def _scan_existing_storymap_people(story_map_dir: Path) -> Set[str]:
    out: Set[str] = set()
    for p in story_map_dir.glob("*.html"):
        if not p.is_file():
            continue
        stem = p.stem
        if "__pure__" in stem:
            out.add(stem.split("__pure__", 1)[0])
        else:
            out.add(stem)
    return out


def _safe_filename(person: str, ts: str) -> str:
    return f"{person}__pure__{ts}.html"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch-runs-dir", default=str(REPO_ROOT / "batch_runs"))
    ap.add_argument("--story-map-dir", default=str(REPO_ROOT / "storymap" / "examples" / "story_map"))
    ap.add_argument("--only-missing", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    batch_runs_dir = Path(args.batch_runs_dir).resolve()
    story_map_dir = Path(args.story_map_dir).resolve()
    story_map_dir.mkdir(parents=True, exist_ok=True)

    latest = _find_latest_preview_by_person(batch_runs_dir)
    have = _scan_existing_storymap_people(story_map_dir)

    copied = 0
    skipped = 0
    missing = 0
    for person, preview in sorted(latest.items(), key=lambda x: x[0]):
        if args.only_missing and person in have:
            skipped += 1
            continue
        if person in have:
            missing += 1
        ts = _format_ts(preview.stat().st_mtime)
        out_file = story_map_dir / _safe_filename(person, ts)
        if args.dry_run:
            copied += 1
            continue
        shutil.copy2(preview, out_file)
        copied += 1

    print(
        f"synced copied={copied} skipped_existing={skipped} already_have_but_copied_anyway={missing} "
        f"story_map_dir={story_map_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

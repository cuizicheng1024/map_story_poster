from __future__ import annotations

import argparse
import concurrent.futures
import importlib.util
import json
import os
from pathlib import Path
from typing import List, Optional, Tuple


REPO_ROOT = Path(__file__).resolve().parent.parent
STORY_DIR = REPO_ROOT / "storymap" / "examples" / "story"
STORY_MAP_DIR = REPO_ROOT / "storymap" / "examples" / "story_map"


def _list_people() -> List[str]:
    if not STORY_DIR.exists():
        return []
    out: List[str] = []
    for p in STORY_DIR.glob("*.md"):
        if p.is_file():
            out.append(p.stem)
    return sorted(set([x.strip() for x in out if str(x).strip()]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", type=str, default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--concurrency", type=int, default=12)
    ap.add_argument("--skip-existing", dest="skip_existing", action="store_true", default=True)
    ap.add_argument("--no-skip-existing", dest="skip_existing", action="store_false")
    ap.add_argument("--llm-split", action="store_true", default=False)
    args = ap.parse_args()

    os.environ["STORY_AGENT_SILENT"] = "1"
    only = [x.strip() for x in str(args.only or "").split(",") if x.strip()]
    people = only if only else _list_people()

    if not args.llm_split:
        os.environ["STORY_MAP_DISABLE_LLM_SPLIT"] = "1"

    mod_path = REPO_ROOT / "cli" / "generate_pure_story_map.py"
    spec = importlib.util.spec_from_file_location("generate_pure_story_map", str(mod_path))
    if not spec or not spec.loader:
        raise RuntimeError(f"无法加载模块：{mod_path}")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)  # type: ignore[call-arg]
    generate_pure_html = getattr(m, "generate_pure_html")

    STORY_MAP_DIR.mkdir(parents=True, exist_ok=True)
    targets: List[str] = []
    for name in people:
        md_path = STORY_DIR / f"{name}.md"
        if not md_path.exists():
            continue
        out_path = STORY_MAP_DIR / f"{name}.html"
        if args.skip_existing and out_path.exists():
            continue
        targets.append(name)
        if args.limit and len(targets) >= int(args.limit):
            break

    done = 0
    failed = 0

    workers = max(1, int(args.concurrency or 1))

    def _job(name: str) -> Tuple[str, Optional[str]]:
        md_path = STORY_DIR / f"{name}.md"
        out_path = STORY_MAP_DIR / f"{name}.html"
        generate_pure_html(md_path=str(md_path), out_path=str(out_path))
        return name, None

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_job, name): name for name in targets}
        for fut in concurrent.futures.as_completed(futs):
            name = futs[fut]
            try:
                fut.result()
                done += 1
                print(f"✅ 已生成 HTML: {name} ({done}/{len(targets)})", flush=True)
            except Exception as e:
                failed += 1
                print(f"⚠️ 生成失败: {name} - {type(e).__name__}: {e}", flush=True)

    print(json.dumps({"ok": True, "done": done, "failed": failed, "count": len(people), "targets": len(targets)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

import argparse
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _http_json(url: str, timeout: int = 30) -> Dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "StoryMapBatch/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _exists(path: Path) -> bool:
    try:
        return path.exists()
    except Exception:
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", type=str, default="http://localhost:8765")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--sleep", type=float, default=0.5)
    parser.add_argument("--only", type=str, default="")
    parser.add_argument("--skip-existing", action="store_true", default=True)
    args = parser.parse_args()

    root = _repo_root()
    names = _load_json(root / "data" / "pep_people_merged.json")
    names = [str(x).strip() for x in names if str(x).strip()]

    only = [x.strip() for x in args.only.split(",") if x.strip()]
    if only:
        names = [n for n in names if n in set(only)]

    story_map_dir = root / "storymap" / "examples" / "story_map"
    story_dir = root / "storymap" / "examples" / "story"
    story_map_dir.mkdir(parents=True, exist_ok=True)
    story_dir.mkdir(parents=True, exist_ok=True)

    total = 0
    done = 0
    failed = 0
    for name in names:
        total += 1
        if args.limit and done + failed >= args.limit:
            break

        html_path = story_map_dir / f"{name}.html"
        md_path = story_dir / f"{name}.md"
        if args.skip_existing and _exists(html_path) and _exists(md_path):
            continue

        q = urllib.parse.quote(name)
        gen = _http_json(f"{args.server.rstrip('/')}/generate?person={q}", timeout=30)
        if not gen.get("ok"):
            failed += 1
            print(f"[FAIL] {name} generate: {gen.get('error')}")
            continue
        task_id = gen.get("task_id") or ""

        status = ""
        err: Optional[str] = None
        for _ in range(3600):
            snap = _http_json(f"{args.server.rstrip('/')}/task?id={task_id}", timeout=30)
            status = str(snap.get("status") or "")
            if status in ("completed", "failed"):
                err = snap.get("error")
                break
            time.sleep(1.0)

        if status == "completed":
            done += 1
            print(f"[OK] {name}")
        else:
            failed += 1
            print(f"[FAIL] {name}: {err or 'unknown error'}")

        time.sleep(float(args.sleep))

    print(f"summary total={total} ok={done} failed={failed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

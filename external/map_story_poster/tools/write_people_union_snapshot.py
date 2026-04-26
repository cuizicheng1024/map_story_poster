from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List


REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
STORY_DIR = REPO_ROOT / "storymap" / "examples" / "story"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _uniq(xs: List[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for x in xs:
        x = str(x or "").strip()
        if not x or x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def _read_list(path: Path) -> List[str]:
    if not path.exists():
        return []
    data = _read_json(path)
    if isinstance(data, list):
        return _uniq([str(x).strip() for x in data if str(x).strip()])
    return []


def _read_by_book(path: Path) -> List[str]:
    if not path.exists():
        return []
    data = _read_json(path)
    out: List[str] = []
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list):
                out.extend([str(x).strip() for x in v if str(x).strip()])
    return _uniq(out)


def _read_kg_nodes(path: Path) -> List[str]:
    if not path.exists():
        return []
    data = _read_json(path)
    out: List[str] = []
    if isinstance(data, dict) and isinstance(data.get("nodes"), list):
        for n in data["nodes"]:
            if isinstance(n, dict):
                out.append(str(n.get("id") or n.get("label") or "").strip())
            else:
                out.append(str(n).strip())
    return _uniq([x for x in out if x])


def _read_story_md_names() -> List[str]:
    if not STORY_DIR.exists():
        return []
    names: List[str] = []
    for p in STORY_DIR.glob("*.md"):
        if p.is_file() and p.stem.strip():
            names.append(p.stem.strip())
    return _uniq(sorted(names))


def main() -> int:
    pep = _read_list(DATA_DIR / "pep_people_merged.json")
    junior = _read_list(DATA_DIR / "pep_junior_all_people.json")
    sample = _read_list(DATA_DIR / "pep_history_figures_sample.json")
    by_book = _read_by_book(DATA_DIR / "pep_junior_all_people_by_book.json")
    kg = _read_kg_nodes(DATA_DIR / "people_knowledge_graph.json")
    story_md = _read_story_md_names()

    pep_set = set(pep)
    md_set = set(story_md)

    union_all = _uniq(pep + junior + sample + by_book + kg + story_md)
    union_pep_plus_story_md = _uniq(pep + story_md)

    payload: Dict[str, Any] = {
        "counts": {
            "pep_people_merged": len(pep),
            "pep_junior_all_people": len(junior),
            "pep_history_figures_sample": len(sample),
            "pep_junior_all_people_by_book": len(by_book),
            "people_knowledge_graph.nodes": len(kg),
            "story_md_files": len(story_md),
            "union_all_sources": len(union_all),
            "union_pep_plus_story_md": len(union_pep_plus_story_md),
            "pep_with_story_md": len(pep_set & md_set),
            "pep_missing_story_md": len(pep_set - md_set),
            "story_md_not_in_pep": len(md_set - pep_set),
        },
        "samples": {
            "pep_missing_story_md": sorted(list(pep_set - md_set))[:50],
            "story_md_not_in_pep": sorted(list(md_set - pep_set))[:50],
        },
        "union_all_sources": union_all,
    }

    out = DATA_DIR / "people_union_snapshot.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["counts"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


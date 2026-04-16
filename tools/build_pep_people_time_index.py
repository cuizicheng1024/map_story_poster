import json
import re
from pathlib import Path
from typing import Optional, Dict, Any


_YEAR_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")


def _first_year(text: str) -> Optional[int]:
    if not text:
        return None
    s = str(text)
    m = _YEAR_RE.search(s)
    if not m:
        return None
    y = int(m.group(1))
    if "前" in s[: m.start() + 1]:
        y = -y
    if y == 0:
        return None
    if y < -3000 or y > 2100:
        return None
    return y


def _parse_story_md(path: Path) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    head = "\n".join(text.splitlines()[:140])

    name = path.stem
    dynasty = ""
    birth_year = None
    death_year = None

    m = re.search(r"^\s*-\s*\*\*时代\*\*：(.+)$", head, re.M)
    if m:
        dynasty = m.group(1).strip()

    m = re.search(r"^\s*-\s*\*\*出生\*\*：(.+)$", head, re.M)
    if m:
        birth_year = _first_year(m.group(1))

    m = re.search(r"^\s*-\s*\*\*去世\*\*：(.+)$", head, re.M)
    if m:
        death_year = _first_year(m.group(1))

    m = re.search(r"^\s*-\s*\*\*生卒\*\*：(.+)$", head, re.M)
    if m and (birth_year is None or death_year is None):
        raw = m.group(1)
        parts = re.split(r"[—\\-~～至]", raw)
        if parts:
            if birth_year is None:
                birth_year = _first_year(parts[0])
            if death_year is None and len(parts) >= 2:
                death_year = _first_year(parts[1])

    if birth_year is None:
        m = re.search(r"\(([^)]{0,24})\)", head)
        if m:
            maybe = m.group(1)
            if "—" in maybe or "-" in maybe:
                parts = re.split(r"[—\-~～]", maybe)
                if parts:
                    birth_year = _first_year(parts[0])
                    if len(parts) >= 2:
                        death_year = _first_year(parts[1])

    return {
        "name": name,
        "dynasty": dynasty,
        "birthYear": birth_year,
        "deathYear": death_year,
        "source": str(path),
    }


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    story_dir = repo_root / "map_story" / "storymap" / "examples" / "story"
    data_dir = repo_root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    items = []
    for p in sorted(story_dir.glob("*.md")):
        items.append(_parse_story_md(p))

    out = {
        "items": items,
        "meta": {
            "count": len(items),
            "hasBirthYear": sum(1 for x in items if x.get("birthYear") is not None),
            "hasDynasty": sum(1 for x in items if (x.get("dynasty") or "").strip()),
        },
    }
    out_path = data_dir / "pep_people_time_index.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(str(out_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

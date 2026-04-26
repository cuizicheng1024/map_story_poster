import json
import re
from pathlib import Path
from typing import Dict, List, Any


def _pick_first_nonempty(lines: List[str]) -> str:
    for ln in lines:
        t = (ln or "").strip()
        if t:
            return t
    return ""


def _extract_quotes(md: str) -> List[str]:
    out: List[str] = []
    for m in re.finditer(r"“([^”]{6,80})”", md):
        out.append(m.group(0))
        if len(out) >= 6:
            break
    return out


def _extract_section(md: str, title: str) -> str:
    lines = md.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if ln.strip() == title:
            start = i + 1
            break
    if start is None:
        return ""
    buf: List[str] = []
    for ln in lines[start:]:
        if ln.strip().startswith("## "):
            break
        buf.append(ln.rstrip())
    return "\n".join(buf).strip()


def _summarize(md: str) -> Dict[str, Any]:
    head = "\n".join(md.splitlines()[:200])
    quotes = _extract_quotes(md)
    intro = _extract_section(md, "### 生平概述")
    if not intro:
        intro = _extract_section(md, "## 一、人物档案")
    intro_line = _pick_first_nonempty(re.split(r"[。！？\n]", intro))
    if intro_line and len(intro_line) > 70:
        intro_line = intro_line[:70].rstrip() + "…"

    review = _extract_section(md, "### 历史评价")
    review_lines = [ln.strip() for ln in review.splitlines() if ln.strip().startswith(("-", "1.", "2.", "3."))]
    review_pick = _pick_first_nonempty(review_lines)
    if review_pick and len(review_pick) > 90:
        review_pick = review_pick[:90].rstrip() + "…"

    best_quote = quotes[0] if quotes else ""
    best_text = best_quote or review_pick or intro_line
    if best_text and len(best_text) > 110:
        best_text = best_text[:110].rstrip() + "…"

    return {
        "spotlight": best_text,
        "quotes": quotes,
        "review": review_pick,
        "intro": intro_line,
    }


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    story_dir = repo_root / "storymap" / "examples" / "story"
    data_dir = repo_root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    out: Dict[str, Any] = {}
    for p in sorted(story_dir.glob("*.md")):
        name = p.stem
        md = p.read_text(encoding="utf-8", errors="ignore")
        out[name] = _summarize(md)

    payload = {"items": out, "meta": {"count": len(out)}}
    path = data_dir / "pep_people_spotlight.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(str(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

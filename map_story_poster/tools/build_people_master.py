from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
STORY_DIR = REPO_ROOT / "storymap" / "examples" / "story"
STORY_DIR.mkdir(parents=True, exist_ok=True)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _load_env() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv(REPO_ROOT / ".env")
        load_dotenv(REPO_ROOT.parent / ".env")
        load_dotenv(REPO_ROOT.parent.parent / ".env")
    except Exception:
        pass


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_name(s: object) -> str:
    return str(s or "").strip()


def _uniq(xs: Sequence[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for x in xs:
        x = _safe_name(x)
        if not x or x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def _pick_years(md_text: str) -> Tuple[Optional[int], Optional[int]]:
    text = md_text or ""

    def pick_year(s: str) -> Optional[int]:
        ys = re.findall(r"(?<!\d)(-?\d{1,4})(?!\d)", str(s or ""))
        if not ys:
            return None
        try:
            return int(ys[0])
        except Exception:
            return None

    def pick_two_years(s: str) -> Tuple[Optional[int], Optional[int]]:
        ys = re.findall(r"(?<!\d)(-?\d{1,4})(?!\d)", str(s or ""))
        if len(ys) < 2:
            return None, None
        try:
            return int(ys[0]), int(ys[1])
        except Exception:
            return None, None

    m = re.search(r"\*\*生卒年\*\*[:：]\s*([^\n]+)", text)
    if not m:
        m = re.search(r"(?:生卒年|生卒)[:：]\s*([^\n]+)", text)
    if m:
        b, d = pick_two_years(m.group(1))
        if b is not None or d is not None:
            return b, d

    birth = None
    death = None
    mb = re.search(r"\*\*出生\*\*[:：]\s*([^\n]+)", text)
    if not mb:
        mb = re.search(r"(?:出生)[:：]\s*([^\n]+)", text)
    if mb:
        birth = pick_year(mb.group(1))

    md = re.search(r"\*\*(去世|逝世)\*\*[:：]\s*([^\n]+)", text)
    if not md:
        md = re.search(r"(去世|逝世)[:：]\s*([^\n]+)", text)
    if md:
        death = pick_year(md.group(2))

    return birth, death


def _pick_dynasty(md_text: str) -> str:
    for pat in [
        r"\*\*时代\*\*[:：]\s*([^\n]+)",
        r"时代[：:]\s*([^\n]+)",
        r"\*\*朝代\*\*[:：]\s*([^\n]+)",
        r"朝代[：:]\s*([^\n]+)",
    ]:
        m = re.search(pat, md_text or "")
        if m:
            return str(m.group(1) or "").strip()
    return ""


def _pick_birthplace(md_text: str) -> Tuple[str, str, str]:
    if not isinstance(md_text, str) or not md_text.strip():
        return "", "", ""
    m = re.search(r"\*\*出生\*\*[:：]\s*([^\n]+)", md_text)
    if not m:
        m = re.search(r"(?:出生)[:：]\s*([^\n]+)", md_text)
    if not m:
        return "", "", ""
    text = m.group(1).strip()
    parts = [p.strip() for p in re.split(r"[，,]", text) if p.strip()]
    loc = parts[-1] if parts else text
    loc = re.sub(r"^一说[^，,]+[，,]\s*", "", loc).strip()
    ancient = loc
    modern = ""
    if "（" in loc and "）" in loc:
        left, right = loc.split("（", 1)
        ancient = left.strip()
        modern = right.split("）", 1)[0].strip()
    elif "(" in loc and ")" in loc:
        left, right = loc.split("(", 1)
        ancient = left.strip()
        modern = right.split(")", 1)[0].strip()
    modern = re.sub(r"^今", "", modern).strip()
    return loc, ancient, modern


def _collect_people() -> List[str]:
    names: List[str] = []

    for p in [
        DATA_DIR / "pep_people_merged.json",
        DATA_DIR / "pep_junior_all_people.json",
        DATA_DIR / "pep_history_figures_sample.json",
    ]:
        if p.exists():
            data = _read_json(p)
            if isinstance(data, list):
                names.extend([_safe_name(x) for x in data])

    p_by_book = DATA_DIR / "pep_junior_all_people_by_book.json"
    if p_by_book.exists():
        data = _read_json(p_by_book)
        if isinstance(data, dict):
            for v in data.values():
                if isinstance(v, list):
                    names.extend([_safe_name(x) for x in v])

    kg = DATA_DIR / "people_knowledge_graph.json"
    if kg.exists():
        data = _read_json(kg)
        if isinstance(data, dict):
            ns = data.get("nodes")
            if isinstance(ns, list):
                for n in ns:
                    if isinstance(n, dict):
                        names.append(_safe_name(n.get("id") or n.get("label")))
                    else:
                        names.append(_safe_name(n))

    for p in STORY_DIR.glob("*.md"):
        if p.is_file():
            names.append(p.stem.strip())

    return sorted(_uniq(names))


def _collect_people_pep() -> List[str]:
    p = DATA_DIR / "pep_people_merged.json"
    if p.exists():
        data = _read_json(p)
        if isinstance(data, list):
            return sorted(_uniq([_safe_name(x) for x in data]))
    kg = DATA_DIR / "people_knowledge_graph.json"
    if kg.exists():
        data = _read_json(kg)
        if isinstance(data, dict):
            ns = data.get("nodes")
            if isinstance(ns, list):
                names: List[str] = []
                for n in ns:
                    if isinstance(n, dict):
                        names.append(_safe_name(n.get("id") or n.get("label")))
                    else:
                        names.append(_safe_name(n))
                return sorted(_uniq(names))
    return []


def _add_storymap_to_syspath() -> None:
    p = str(REPO_ROOT / "storymap" / "script")
    if p not in sys.path:
        sys.path.insert(0, p)


def _ensure_story_md(
    people: List[str], fill_missing: bool, limit: int, skip_existing: bool, concurrency: int
) -> Dict[str, object]:
    created = 0
    attempted = 0
    failures: List[Dict[str, str]] = []
    if not fill_missing:
        return {"attempted": 0, "created": 0, "failures": []}

    _load_env()
    _add_storymap_to_syspath()
    from story_agents import StoryAgentLLM, generate_historical_markdown, save_markdown  # type: ignore

    try:
        StoryAgentLLM()
    except Exception as e:
        return {"attempted": 0, "created": 0, "failures": [{"person": "", "error": f"{type(e).__name__}: {e}"}]}

    targets: List[str] = []
    for name in people:
        path = STORY_DIR / f"{name}.md"
        if skip_existing and path.exists():
            continue
        targets.append(name)
        if limit and len(targets) >= limit:
            break

    if not targets:
        return {"attempted": 0, "created": 0, "failures": []}

    attempted = len(targets)
    workers = max(1, int(concurrency or 1))

    def _job(person: str) -> Tuple[str, Optional[str]]:
        client = StoryAgentLLM()
        md = generate_historical_markdown(client, person)
        if not md or not str(md).strip():
            raise RuntimeError("empty response")
        save_markdown(person, str(md))
        return person, None

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_job, person): person for person in targets}
        for fut in concurrent.futures.as_completed(futs):
            name = futs[fut]
            try:
                fut.result()
                created += 1
                print(f"✅ 已保存人物生平: {name} ({created}/{attempted})", flush=True)
            except Exception as e:
                failures.append({"person": name, "error": f"{type(e).__name__}: {e}"})
                print(f"⚠️ 生成失败: {name} ({created}/{attempted}) - {type(e).__name__}: {e}", flush=True)
    return {"attempted": attempted, "created": created, "failures": failures[:20]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DATA_DIR / "people_master.json"))
    ap.add_argument("--scope", choices=["pep", "all"], default="pep")
    ap.add_argument("--fill-missing", action="store_true", default=False)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--concurrency", type=int, default=30)
    ap.add_argument("--skip-existing", action="store_true", default=True)
    ap.add_argument("--only", type=str, default="")
    args = ap.parse_args()

    people = _collect_people_pep() if str(args.scope) == "pep" else _collect_people()
    only = _uniq([x.strip() for x in str(args.only or "").split(",") if x.strip()])
    if only:
        s = set(only)
        people = [p for p in people if p in s]

    gen = _ensure_story_md(
        people,
        bool(args.fill_missing),
        int(args.limit),
        bool(args.skip_existing),
        int(args.concurrency),
    )

    items: List[Dict[str, object]] = []
    for name in people:
        md_path = STORY_DIR / f"{name}.md"
        md_text = md_path.read_text(encoding="utf-8", errors="ignore") if md_path.exists() else ""
        birth_year, death_year = _pick_years(md_text) if md_text else (None, None)
        dynasty = _pick_dynasty(md_text) if md_text else ""
        bp_raw, bp_ancient, bp_modern = _pick_birthplace(md_text) if md_text else ("", "", "")
        items.append(
            {
                "person": name,
                "has_story": md_path.exists(),
                "story_md": str(md_path.relative_to(REPO_ROOT)) if md_path.exists() else "",
                "birth_year": birth_year,
                "death_year": death_year,
                "dynasty": dynasty,
                "birthplace": bp_ancient,
                "birthplace_raw": bp_raw,
                "birthplace_modern": bp_modern,
            }
        )

    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": _now(),
        "count": len(items),
        "people": items,
        "generation": gen,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "out": str(out_path), "count": len(items), "generation": gen}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

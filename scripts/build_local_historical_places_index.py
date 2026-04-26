"""
从已生成的 StoryMap HTML 中抽取地名与坐标，构建本地 historical_places_index.jsonl。

用途：
- 提升后续人物生成时的地名命中率，减少在线地理编码调用。
- 作为“古今地名索引”的可增量更新素材之一。
"""

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


def _iter_html_files(html_dir: Path) -> List[Path]:
    if not html_dir.exists():
        return []
    files = [p for p in html_dir.rglob("*.html") if p.is_file()]
    return sorted(files)


def _extract_js_object(text: str, marker: str) -> Optional[str]:
    idx = text.find(marker)
    if idx < 0:
        return None
    i = idx + len(marker)
    while i < len(text) and text[i].isspace():
        i += 1
    if i >= len(text) or text[i] != "{":
        return None

    depth = 0
    in_str = False
    esc = False
    start = i
    for j in range(i, len(text)):
        ch = text[j]
        if in_str:
            if esc:
                esc = False
                continue
            if ch == "\\":
                esc = True
                continue
            if ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == "{":
            depth += 1
            continue
        if ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : j + 1]
    return None


def _normalize(s: str) -> str:
    t = (s or "").strip()
    t = re.sub(r"\s+", "", t)
    t = re.sub(r"[（(].*?[）)]", "", t)
    t = re.sub(r"[，,。.;；:：、】【\\[\\]{}<>《》\"'“”‘’·•/\\\\|-]+", "", t)
    return t.lower()


def _iter_place_records_from_profile(data: Dict[str, Any]) -> Iterable[Tuple[str, str, float, float]]:
    person = data.get("person") if isinstance(data.get("person"), dict) else {}

    def emit_pair(ancient: str, modern: str, lat: Any, lon: Any) -> Iterable[Tuple[str, str, float, float]]:
        if not ancient and not modern:
            return []
        try:
            lat_f = float(lat)
            lon_f = float(lon)
        except Exception:
            return []
        if abs(lat_f) > 90 or abs(lon_f) > 180:
            return []
        a = str(ancient or "").strip()
        m = str(modern or "").strip()
        if not a:
            a = m
        if not m:
            m = a
        return [(a, m, lat_f, lon_f)]

    if isinstance(person, dict):
        birth = person.get("birth") if isinstance(person.get("birth"), dict) else {}
        death = person.get("death") if isinstance(person.get("death"), dict) else {}
        if isinstance(birth, dict):
            yield from emit_pair(birth.get("location") or "", birth.get("location") or "", birth.get("lat"), birth.get("lng"))
        if isinstance(death, dict):
            yield from emit_pair(death.get("location") or "", death.get("location") or "", death.get("lat"), death.get("lng"))

    locations = data.get("locations") if isinstance(data.get("locations"), list) else []
    for loc in locations:
        if not isinstance(loc, dict):
            continue
        yield from emit_pair(
            loc.get("ancientName") or loc.get("name") or "",
            loc.get("modernName") or loc.get("location") or loc.get("name") or "",
            loc.get("lat"),
            loc.get("lng"),
        )


def build_index_from_storymap_html(html_dir: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    seen = set()
    for html_path in _iter_html_files(html_dir):
        text = html_path.read_text(encoding="utf-8", errors="ignore")
        obj_text = _extract_js_object(text, "const data = ")
        if not obj_text:
            continue
        try:
            data = json.loads(obj_text)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        for ancient, modern, lat, lon in _iter_place_records_from_profile(data):
            key = (_normalize(ancient), _normalize(modern))
            if not key[0] and not key[1]:
                continue
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "ancient_name": ancient,
                    "modern_name": modern,
                    "lat": lat,
                    "lon": lon,
                    "source": str(html_path),
                }
            )
    return rows


def write_jsonl(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            r = dict(row)
            r.pop("source", None)
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="从已生成的 StoryMap HTML 中提取地点->坐标映射，生成本地 historical_places_index.jsonl")
    parser.add_argument("--html_dir", default="outputs/output_batch_storymap_pep_history", help="StoryMap HTML 目录（批量输出目录）")
    parser.add_argument("--out", default="data/historical_places_index.jsonl", help="输出 JSONL 文件路径")
    args = parser.parse_args(list(argv) if argv is not None else None)

    repo_root = Path(__file__).resolve().parents[1]
    html_dir = (repo_root / args.html_dir).resolve() if not Path(args.html_dir).is_absolute() else Path(args.html_dir)
    out_path = (repo_root / args.out).resolve() if not Path(args.out).is_absolute() else Path(args.out)

    rows = build_index_from_storymap_html(html_dir)
    write_jsonl(out_path, rows)
    print(f"已写入: {out_path}（{len(rows)} 条）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

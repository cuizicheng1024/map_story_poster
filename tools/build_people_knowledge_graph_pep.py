import json
from pathlib import Path


def _seeded_unit(s: str) -> float:
    h = 2166136261
    for ch in s:
        h = (h ^ ord(ch)) * 16777619 & 0xFFFFFFFF
    return h / 2**32


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    data_dir = root / "data"

    names = _load_json(data_dir / "pep_people_merged.json")
    names = [str(x).strip() for x in names if str(x).strip()]
    name_set = set(names)

    by_book_sources = [
        ("history", data_dir / "pep_history_figures_sample_by_book.json"),
        ("junior", data_dir / "pep_junior_all_people_by_book.json"),
    ]

    edges = []

    def add_edge(a: str, b: str, typ: str, w: int, evidence: str = ""):
        if a == b:
            return
        if a not in name_set or b not in name_set:
            return
        edges.append(
            {"source": a, "target": b, "type": typ, "weight": int(w), "evidence": evidence}
        )

    for tag, path in by_book_sources:
        if not path.exists():
            continue
        obj = _load_json(path)
        if not isinstance(obj, dict):
            continue
        for book, arr in obj.items():
            if not isinstance(arr, list):
                continue
            cleaned = [str(x).strip() for x in arr if str(x).strip() in name_set]
            cleaned = list(dict.fromkeys(cleaned))
            for i in range(len(cleaned) - 1):
                add_edge(
                    cleaned[i],
                    cleaned[i + 1],
                    "same_book",
                    2,
                    f"同册（{tag}）：{book}",
                )

    graph = {
        "nodes": [{"id": n, "label": n} for n in names],
        "edges": edges,
        "meta": {
            "nodes": len(names),
            "edges": len(edges),
            "types": sorted({e["type"] for e in edges}),
        },
    }
    out_path = data_dir / "people_knowledge_graph.json"
    out_path.write_text(json.dumps(graph, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(str(out_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

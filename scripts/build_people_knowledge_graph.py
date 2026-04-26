"""
基于已有人物资料（Markdown 或 HTML）构建一个轻量知识图谱（JSON）。

当前图谱边类型：
- mentions：人物 A 的文本中提及人物 B（基于字符串出现次数）
- same_dynasty：同朝代人物连线（若能解析到 dynasty）

输出格式：
{
  "nodes": [{"id": "...", "label": "...", "dynasty": "..."}],
  "edges": [{"source": "...", "target": "...", "type": "...", "weight": 1, "evidence": "..."}]
}
"""

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


def _load_names(path: Path) -> List[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("names 文件必须是 JSON 数组")
    out: List[str] = []
    for x in data:
        s = str(x).strip()
        if s:
            out.append(s)
    # preserve order + dedupe
    seen = set()
    deduped = []
    for n in out:
        if n in seen:
            continue
        seen.add(n)
        deduped.append(n)
    return deduped


def _extract_markdown_from_html(html_text: str) -> str:
    m = re.search(r'"markdown"\s*:\s*"((?:\\.|[^"\\])*)"', html_text, flags=re.S)
    if not m:
        return ""
    raw = m.group(1)
    try:
        return json.loads(f'"{raw}"')
    except Exception:
        return raw


def _extract_dynasty_from_html(html_text: str) -> str:
    m = re.search(r'"dynasty"\s*:\s*"((?:\\.|[^"\\])*)"', html_text, flags=re.S)
    if not m:
        return ""
    raw = m.group(1)
    try:
        return json.loads(f'"{raw}"')
    except Exception:
        return raw


def _safe_stem(name: str) -> str:
    s = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "_", name).strip("._-")
    return s or "unknown"


def _load_person_doc(
    *,
    person: str,
    html_dir: Path,
    md_dir: Path,
) -> Tuple[str, str]:
    md_path = md_dir / f"{person}.md"
    if md_path.exists():
        md = md_path.read_text(encoding="utf-8", errors="ignore")
    else:
        html_path = html_dir / f"storymap_{_safe_stem(person)}.html"
        md = ""
        if html_path.exists():
            html_text = html_path.read_text(encoding="utf-8", errors="ignore")
            md = _extract_markdown_from_html(html_text)

    # dynasty prefers html (parsed data), fallback markdown
    html_path = html_dir / f"storymap_{_safe_stem(person)}.html"
    dynasty = ""
    if html_path.exists():
        html_text = html_path.read_text(encoding="utf-8", errors="ignore")
        dynasty = _extract_dynasty_from_html(html_text).strip()
    if not dynasty and md:
        m = re.search(r"-\s*\*\*(?:时代|朝代)\*\*：\s*(.+)", md)
        if m:
            dynasty = m.group(1).strip()
    return md, dynasty


def _collect_mentions(text: str, names: Sequence[str], self_name: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    if not text.strip():
        return out
    for n in names:
        if n == self_name:
            continue
        c = text.count(n)
        if c > 0:
            out[n] = c
    return out


def build_graph(
    *,
    names: Sequence[str],
    html_dir: Path,
    md_dir: Path,
) -> Dict[str, object]:
    nodes = []
    person_md: Dict[str, str] = {}
    person_dynasty: Dict[str, str] = {}

    for n in names:
        md, dynasty = _load_person_doc(person=n, html_dir=html_dir, md_dir=md_dir)
        person_md[n] = md
        person_dynasty[n] = dynasty
        nodes.append({"id": n, "label": n, "dynasty": dynasty})

    edges = []
    edge_keys = set()

    # 1) mention edges
    for a in names:
        mentions = _collect_mentions(person_md.get(a, ""), names, a)
        for b, cnt in mentions.items():
            key = ("mentions", a, b)
            if key in edge_keys:
                continue
            edge_keys.add(key)
            edges.append(
                {
                    "source": a,
                    "target": b,
                    "type": "mentions",
                    "weight": cnt,
                    "evidence": f"{a} 文本中提及 {b}（{cnt} 次）",
                }
            )

    # 2) same dynasty undirected edges (stored once by lexical order)
    bucket: Dict[str, List[str]] = defaultdict(list)
    for n, d in person_dynasty.items():
        if d:
            bucket[d].append(n)
    for dynasty, arr in bucket.items():
        arr = sorted(arr)
        for i in range(len(arr)):
            for j in range(i + 1, len(arr)):
                a, b = arr[i], arr[j]
                key = ("same_dynasty", a, b)
                if key in edge_keys:
                    continue
                edge_keys.add(key)
                edges.append(
                    {
                        "source": a,
                        "target": b,
                        "type": "same_dynasty",
                        "weight": 1,
                        "evidence": f"同属 {dynasty}",
                    }
                )

    return {"nodes": nodes, "edges": edges}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="基于已有人物资料生成知识图谱（JSON）。")
    parser.add_argument("--names", default="data/pep_history_figures_sample.json", help="人物名单 JSON")
    parser.add_argument(
        "--html_dir",
        default="outputs/output_batch_storymap_pep_history",
        help="StoryMap HTML 输出目录",
    )
    parser.add_argument(
        "--md_dir",
        default="external/map_story_poster/map_story/storymap/examples/story",
        help="人物 Markdown 目录",
    )
    parser.add_argument("--out", default="data/people_knowledge_graph.json", help="输出图谱 JSON")
    args = parser.parse_args(list(argv) if argv is not None else None)

    root = Path(__file__).resolve().parents[1]
    names_path = (root / args.names).resolve() if not Path(args.names).is_absolute() else Path(args.names)
    html_dir = (root / args.html_dir).resolve() if not Path(args.html_dir).is_absolute() else Path(args.html_dir)
    md_dir = (root / args.md_dir).resolve() if not Path(args.md_dir).is_absolute() else Path(args.md_dir)
    out_path = (root / args.out).resolve() if not Path(args.out).is_absolute() else Path(args.out)

    names = _load_names(names_path)
    graph = build_graph(names=names, html_dir=html_dir, md_dir=md_dir)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(graph, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "nodes": len(graph["nodes"]),
                "edges": len(graph["edges"]),
                "out": str(out_path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

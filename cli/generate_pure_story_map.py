#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""generate_pure_story_map.py

纯 HTML（Leaflet）地图生成入口。

目标：
- 只产出交互式 HTML 地图（Leaflet）
- 不依赖 / 不触发任何 OSMnx、matplotlib 等静态海报逻辑

用法示例：
  python3 cli/generate_pure_story_map.py --md storymap/examples/story/苏轼.md
  python3 cli/generate_pure_story_map.py --person 苏轼

输出：默认写入 storymap/examples/story_map/ 目录。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote


def _repo_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _add_import_paths() -> None:
    root = _repo_root()
    new_path = os.path.join(root, "storymap", "script")
    old_path = os.path.join(root, "map_story", "storymap", "script")
    for p in [new_path, old_path]:
        if p not in sys.path:
            sys.path.insert(0, p)


def _default_md_path(person: str) -> str:
    root = _repo_root()
    return os.path.join(root, "storymap", "examples", "story", f"{person}.md")


def _read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def _write_text(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def generate_pure_html(md_path: str, out_path: Optional[str] = None) -> dict:
    """Generate pure Leaflet HTML and return metadata."""

    _add_import_paths()
    import story_map as sm
    import map_html_renderer as renderer

    md_path = os.path.abspath(md_path)
    md = _read_text(md_path)
    if not md.strip():
        raise RuntimeError(f"Markdown 为空或无法读取：{md_path}")

    t0 = time.perf_counter()

    profile = sm._load_profile_from_md(md)

    t1 = time.perf_counter()

    if profile:
        # Ensure export markdown is available in generated HTML.
        profile["markdown"] = md
        html = renderer.render_profile_html(profile)
        person_name = (profile.get("person") or {}).get("name") or Path(md_path).stem
    else:
        # Fallback: still generate a basic map if profile structure is incomplete.
        places = sm.parse_places(md)
        events = sm.parse_events(md)
        points = sm.build_points(places, events)
        person_name = Path(md_path).stem
        html = sm.render_html(person_name, points, md)

    t2 = time.perf_counter()

    if not out_path:
        out_dir = os.path.join(_repo_root(), "storymap", "examples", "story_map")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(out_dir, f"{person_name}__pure__{ts}.html")

    _write_text(out_path, html)

    t3 = time.perf_counter()

    return {
        "person": person_name,
        "md_path": md_path,
        "html_path": out_path,
        "duration": {
            "parse": f"{(t1 - t0) * 1000:.1f}ms",
            "render": f"{(t2 - t1) * 1000:.1f}ms",
            "write": f"{(t3 - t2) * 1000:.1f}ms",
            "total": f"{(t3 - t0) * 1000:.1f}ms",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="纯 HTML（Leaflet）地图生成：只输出交互式 HTML")
    parser.add_argument("--md", type=str, help="直接指定人物 Markdown 路径")
    parser.add_argument("-p", "--person", type=str, help="人物名（用于定位 examples/story/<person>.md）")
    parser.add_argument("--out", type=str, help="输出 HTML 路径（可选）")
    args = parser.parse_args()

    md_path: Optional[str] = args.md
    if not md_path:
        if not args.person:
            raise SystemExit("需要提供 --md 或 --person")
        md_path = _default_md_path(args.person)

    result = generate_pure_html(md_path=md_path, out_path=args.out)

    html_path = result["html_path"]
    file_url = "file://" + quote(os.path.abspath(html_path))

    print(f"HTML: {html_path}")
    print(f"Open: {file_url}")

    # 自动在默认浏览器中打开 HTML 地图
    try:
        webbrowser.open(f"file://{os.path.abspath(html_path)}")
    except Exception:
        pass

    d = result.get("duration") or {}
    print(f"耗时：解析 {d.get('parse')}，渲染 {d.get('render')}，写入 {d.get('write')}，总计 {d.get('total')}")


if __name__ == "__main__":
    main()

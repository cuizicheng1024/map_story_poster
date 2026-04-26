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
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Set
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


def generate_pure_html(md_path: str, out_path: Optional[str] = None, *, no_geocode: bool = False) -> dict:
    """Generate pure Leaflet HTML and return metadata."""

    _add_import_paths()
    import story_map as sm
    import map_html_renderer as renderer

    md_path = os.path.abspath(md_path)
    md = _read_text(md_path)
    if not md.strip():
        raise RuntimeError(f"Markdown 为空或无法读取：{md_path}")

    t0 = time.perf_counter()

    person_name = Path(md_path).stem
    if no_geocode:
        profile = sm._load_profile_from_md(md, allow_geocode=False)
        t1 = time.perf_counter()
        if profile:
            profile["markdown"] = md
            html = renderer.render_profile_html(profile)
            person_name = (profile.get("person") or {}).get("name") or person_name
        else:
            places = sm.parse_places(md)
            events = sm.parse_events(md)
            points = sm.build_points(places, events, allow_geocode=False)
            fields = sm._extract_intro_fields(md)
            info_panel_html = sm.build_info_panel_html(person_name, fields) if any(fields.values()) else ""
            html = sm.render_osm_html(person_name, points, info_panel_html)
    else:
        profile = sm._load_profile_from_md(md)
        t1 = time.perf_counter()
        if profile:
            profile["markdown"] = md
            html = renderer.render_profile_html(profile)
            person_name = (profile.get("person") or {}).get("name") or person_name
        else:
            places = sm.parse_places(md)
            events = sm.parse_events(md)
            points = sm.build_points(places, events)
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


def _person_from_filename(name: str) -> str:
    stem = Path(name).stem
    if "__pure__" in stem:
        return stem.split("__pure__", 1)[0]
    return stem


def _scan_people_from_story_md(story_md_dir: Path) -> Set[str]:
    if not story_md_dir.exists():
        return set()
    return {p.stem.strip() for p in story_md_dir.glob("*.md") if p.is_file() and p.stem.strip()}


def _scan_people_from_story_map_html(story_map_dir: Path) -> Set[str]:
    if not story_map_dir.exists():
        return set()
    out: Set[str] = set()
    for p in story_map_dir.glob("*.html"):
        if not p.is_file():
            continue
        if p.name == "index.html":
            continue
        person = _person_from_filename(p.name).strip()
        if person:
            out.add(person)
    return out


def render_missing_people_html(*, max_people: int = 0, mode: str = "pure") -> int:
    _add_import_paths()
    import story_map as sm

    root = Path(_repo_root()).resolve()
    md_dir = root / "storymap" / "examples" / "story"
    html_dir = root / "storymap" / "examples" / "story_map"

    bad_names = {"人物", "母亲", "刘某", "人物 生平传记与足迹"}

    md_people = _scan_people_from_story_md(md_dir)
    html_people = _scan_people_from_story_map_html(html_dir)
    missing = sorted([p for p in (md_people - html_people) if p not in bad_names])

    if max_people and max_people > 0:
        missing = missing[:max_people]

    print(f"md={len(md_people)} html_people={len(html_people)} missing={len(missing)}")

    def work(person: str) -> tuple[str, bool, float, str]:
        t0 = time.perf_counter()
        try:
            if mode == "cache":
                sm._generate_for_person(client=None, person=person, progress=None, allow_cache=True)
            else:
                md_path = str((md_dir / f"{person}.md").resolve())
                out_path = str((html_dir / f"{person}.html").resolve())
                generate_pure_html(md_path=md_path, out_path=out_path, no_geocode=(mode == "nogeocode"))
            return (person, True, time.perf_counter() - t0, "")
        except Exception as exc:
            return (person, False, time.perf_counter() - t0, str(exc).replace("\n", " ").strip())

    ok = 0
    fail = 0
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, int(os.getenv("MAP_STORY_RENDER_CONCURRENCY", "4") or "4"))) as ex:
        futs = [ex.submit(work, person) for person in missing]
        for fut in as_completed(futs):
            person, is_ok, dt, info = fut.result()
            done += 1
            if is_ok:
                ok += 1
                tag = "OK"
            else:
                fail += 1
                tag = "FAIL"
            info = (info[:160] + "…") if len(info) > 160 else info
            print(f"[{done}/{len(missing)}] {tag} {person} {dt:.2f}s {info}".rstrip(), flush=True)
    print(f"done ok={ok} fail={fail} total={ok+fail}")
    return 0 if fail == 0 else 2


def render_all_people_html(*, mode: str = "nogeocode") -> int:
    _add_import_paths()
    import story_map as sm

    root = Path(_repo_root()).resolve()
    md_dir = root / "storymap" / "examples" / "story"
    html_dir = root / "storymap" / "examples" / "story_map"

    bad_names = {"人物", "母亲", "刘某", "人物 生平传记与足迹"}
    md_people = sorted([p for p in _scan_people_from_story_md(md_dir) if p not in bad_names])
    print(f"md={len(md_people)} mode={mode}")

    def work(person: str) -> tuple[str, bool, float, str]:
        t0 = time.perf_counter()
        try:
            if mode == "cache":
                sm._generate_for_person(client=None, person=person, progress=None, allow_cache=False)
            else:
                md_path = str((md_dir / f"{person}.md").resolve())
                out_path = str((html_dir / f"{person}.html").resolve())
                generate_pure_html(md_path=md_path, out_path=out_path, no_geocode=(mode == "nogeocode"))
            return (person, True, time.perf_counter() - t0, "")
        except Exception as exc:
            return (person, False, time.perf_counter() - t0, str(exc).replace("\n", " ").strip())

    ok = 0
    fail = 0
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, int(os.getenv("MAP_STORY_RENDER_CONCURRENCY", "4") or "4"))) as ex:
        futs = [ex.submit(work, person) for person in md_people]
        for fut in as_completed(futs):
            person, is_ok, dt, info = fut.result()
            done += 1
            if is_ok:
                ok += 1
                tag = "OK"
            else:
                fail += 1
                tag = "FAIL"
            info = (info[:160] + "…") if len(info) > 160 else info
            print(f"[{done}/{len(md_people)}] {tag} {person} {dt:.2f}s {info}".rstrip(), flush=True)
    print(f"done ok={ok} fail={fail} total={ok+fail}")
    return 0 if fail == 0 else 2


def main() -> None:
    parser = argparse.ArgumentParser(description="纯 HTML（Leaflet）地图生成：只输出交互式 HTML")
    parser.add_argument("--md", type=str, help="直接指定人物 Markdown 路径")
    parser.add_argument("-p", "--person", type=str, help="人物名（用于定位 examples/story/<person>.md）")
    parser.add_argument("--out", type=str, help="输出 HTML 路径（可选）")
    parser.add_argument("--render-missing", action="store_true", help="批量渲染：为缺失 HTML 的人物补齐 examples/story_map/<person>.html（不调用模型）")
    parser.add_argument("--render-all", action="store_true", help="批量渲染：重渲染所有人物 HTML 到 examples/story_map/<person>.html")
    parser.add_argument("--missing-limit", type=int, default=0, help="批量渲染时，最多处理多少人（0 表示不限制）")
    parser.add_argument("--missing-mode", type=str, default="pure", choices=["nogeocode", "pure", "cache"], help="nogeocode=不做地理编码（最快）；pure=正常渲染（可能触发地理编码）；cache=复用 Markdown 并做地理编码+渲染（最慢）")
    parser.add_argument("--all-mode", type=str, default="pure", choices=["nogeocode", "pure", "cache"], help="render-all 时的模式：nogeocode=最快；pure=可能触发地理编码；cache=强制刷新缓存")
    parser.add_argument("--no-geocode", action="store_true", help="生成单人 HTML 时不触发地理编码（只渲染现有坐标）")
    parser.add_argument("--no-browser", action="store_true", help="生成单人 HTML 后不自动打开浏览器")
    args = parser.parse_args()

    if args.render_missing:
        raise SystemExit(render_missing_people_html(max_people=int(args.missing_limit or 0), mode=str(args.missing_mode or "pure")))
    if args.render_all:
        raise SystemExit(render_all_people_html(mode=str(args.all_mode or "nogeocode")))

    md_path: Optional[str] = args.md
    if not md_path:
        if not args.person:
            raise SystemExit("需要提供 --md 或 --person")
        md_path = _default_md_path(args.person)

    result = generate_pure_html(md_path=md_path, out_path=args.out, no_geocode=bool(args.no_geocode))

    html_path = result["html_path"]
    file_url = "file://" + quote(os.path.abspath(html_path))

    print(f"HTML: {html_path}")
    print(f"Open: {file_url}")

    # 自动在默认浏览器中打开 HTML 地图
    if not args.no_browser:
        try:
            webbrowser.open(f"file://{os.path.abspath(html_path)}")
        except Exception:
            pass

    d = result.get("duration") or {}
    print(f"耗时：解析 {d.get('parse')}，渲染 {d.get('render')}，写入 {d.get('write')}，总计 {d.get('total')}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote


def _repo_root() -> str:
    return os.path.abspath(os.path.dirname(__file__))


def _add_import_paths() -> None:
    root = _repo_root()
    sys.path.insert(0, os.path.join(root, "map_story", "storymap", "script"))
    sys.path.insert(0, os.path.join(root, "maptoposter"))


def _read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def _default_md_path(person: str) -> str:
    root = _repo_root()
    return os.path.join(root, "map_story", "storymap", "examples", "story", f"{person}.md")


def _safe_slug(text: str) -> str:
    value = "".join([c if c.isalnum() or c in {"_", "-"} else "_" for c in (text or "")])
    value = value.strip("_")
    return value or "item"


def _ensure_ext(path: str, ext: str) -> str:
    ext = ext.lstrip(".").lower()
    if path.lower().endswith(f".{ext}"):
        return path
    return f"{path}.{ext}"


def _relpath_for_html(from_html_dir: str, target_path: str) -> str:
    try:
        rel = os.path.relpath(target_path, start=from_html_dir)
    except Exception:
        rel = target_path
    return rel.replace(os.sep, "/")


def _write_text(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _pick_location_name(loc: dict) -> str:
    for key in ("modernName", "name", "ancientName"):
        v = str(loc.get(key) or "").strip()
        if v:
            return v
    return "location"


def _fmt_dim(value: float) -> str:
    text = f"{value}".strip()
    return text.replace(".", "p")


def _render_placeholder(
    output_file: str,
    city: str,
    country: str,
    width: float,
    height: float,
    theme: dict,
    fonts: Optional[dict],
) -> None:
    import matplotlib.pyplot as plt
    from matplotlib.font_manager import FontProperties

    fig, ax = plt.subplots(figsize=(width, height), facecolor=theme.get("bg", "#ffffff"))
    ax.set_facecolor(theme.get("bg", "#ffffff"))
    ax.axis("off")
    text_color = theme.get("text", "#111111")
    title_font = None
    sub_font = None
    if fonts:
        title_font = FontProperties(fname=fonts.get("bold") or fonts.get("regular"), size=36)
        sub_font = FontProperties(fname=fonts.get("regular"), size=16)
    ax.text(
        0.5,
        0.62,
        city,
        ha="center",
        va="center",
        color=text_color,
        fontproperties=title_font,
        transform=ax.transAxes,
    )
    ax.text(
        0.5,
        0.52,
        country,
        ha="center",
        va="center",
        color=text_color,
        alpha=0.8,
        fontproperties=sub_font,
        transform=ax.transAxes,
    )
    ax.text(
        0.5,
        0.40,
        "MAP DATA UNAVAILABLE",
        ha="center",
        va="center",
        color=text_color,
        alpha=0.55,
        fontproperties=sub_font,
        transform=ax.transAxes,
    )
    fig.savefig(output_file, dpi=300, bbox_inches="tight", pad_inches=0.0)
    plt.close(fig)


def _run_in_subprocess_with_timeout(func, kwargs: dict, timeout_s: int) -> None:
    """Run a function in a subprocess so we can hard-timeout and kill it.

    This is mainly to guard against OSMnx/Overpass hanging for too long.
    """
    import multiprocessing as mp

    def _worker(q):
        try:
            func(**kwargs)
            q.put({"ok": True})
        except Exception as exc:  # pragma: no cover
            q.put({"ok": False, "error": repr(exc)})

    q: "mp.Queue" = mp.Queue()
    p = mp.Process(target=_worker, args=(q,))
    p.daemon = True
    p.start()
    p.join(timeout_s)
    if p.is_alive():
        try:
            p.terminate()
        except Exception:
            pass
        p.join(2)
        raise TimeoutError(f"poster generation timeout after {timeout_s}s")

    result = None
    try:
        if not q.empty():
            result = q.get_nowait()
    except Exception:
        result = None
    if isinstance(result, dict) and not result.get("ok", False):
        raise RuntimeError(result.get("error") or "poster generation failed")


def generate_pack(
    md_path: str,
    theme: str,
    distance: int,
    limit: int,
    output_format: str,
    width: float,
    height: float,
    font_family: Optional[str],
    force: bool,
) -> dict:
    _add_import_paths()
    import story_map as sm

    md = _read_text(md_path)
    if not md.strip():
        raise RuntimeError(f"Markdown 为空或无法读取：{md_path}")

    profile = sm._load_profile_from_md(md)
    if not profile:
        raise RuntimeError("无法从 Markdown 构建人物数据（可能缺少“人物档案/人生历程”章节）")

    person = profile.get("person") or {}
    person_name = str(person.get("name") or Path(md_path).stem).strip() or "person"

    try:
        import create_map_poster as mp
    except Exception as exc:
        raise RuntimeError(f"无法导入 maptoposter 依赖：{exc}") from exc

    out_html_dir = os.path.join(_repo_root(), "map_story", "storymap", "examples", "story_map")
    os.makedirs(out_html_dir, exist_ok=True)

    out_dir = os.path.join(_repo_root(), "maptoposter", "posters")
    os.makedirs(out_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    mp.THEME = mp.load_theme(theme)
    fonts = mp.load_fonts(font_family) if font_family else None

    locations = list(profile.get("locations") or [])
    if limit <= 0:
        limit = len(locations)
    posters = []
    generated = 0
    used_slugs = {}
    slug_person = _safe_slug(person_name)
    dim_part = f"{_fmt_dim(width)}x{_fmt_dim(height)}"
    for idx, loc in enumerate(locations, start=1):
        if generated >= limit:
            break
        lat = loc.get("lat")
        lng = loc.get("lng")
        if lat is None or lng is None:
            continue
        try:
            lat_f = float(lat)
            lng_f = float(lng)
        except Exception:
            continue
        city_label = _pick_location_name(loc)
        slug_city = _safe_slug(city_label)
        used_slugs[slug_city] = int(used_slugs.get(slug_city, 0)) + 1
        suffix = f"__{used_slugs[slug_city]}" if used_slugs[slug_city] > 1 else ""
        out_name = f"{slug_person}__{idx:02d}__{slug_city}{suffix}__{theme}__d{distance}__{dim_part}"
        out_file = _ensure_ext(os.path.join(out_dir, out_name), output_format)
        if force or not os.path.exists(out_file):
            try:
                timeout_s = int(os.getenv("MAPTOPSTER_POSTER_TIMEOUT", "40"))
                _run_in_subprocess_with_timeout(
                    mp.create_poster,
                    {
                        "city": city_label,
                        "country": "China",
                        "point": (lat_f, lng_f),
                        "dist": distance,
                        "output_file": out_file,
                        "output_format": output_format,
                        "width": width,
                        "height": height,
                        "country_label": "中国",
                        "display_city": city_label,
                        "display_country": "中国",
                        "fonts": fonts,
                    },
                    timeout_s=timeout_s,
                )
            except Exception:
                if output_format == "png":
                    _render_placeholder(
                        output_file=out_file,
                        city=city_label,
                        country="中国",
                        width=width,
                        height=height,
                        theme=mp.THEME,
                        fonts=fonts,
                    )
                else:
                    raise
        posters.append({"location": city_label, "file": out_file})
        rel = _relpath_for_html(out_html_dir, out_file)
        loc["poster"] = {"png": rel} if output_format == "png" else {"file": rel}
        generated += 1

    try:
        import map_html_renderer as renderer
    except Exception as exc:
        raise RuntimeError(f"无法导入 StoryMap 渲染模块：{exc}") from exc

    html = renderer.render_profile_html(profile)
    out_html = os.path.join(out_html_dir, f"{person_name}__poster_pack__{ts}.html")
    _write_text(out_html, html)

    return {
        "person": person_name,
        "md_path": md_path,
        "html_path": out_html,
        "posters": posters,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="将 StoryMap 人物足迹与 MapToPoster 海报生成拼接为一个可交互页面")
    parser.add_argument("-p", "--person", type=str, help="人物名（用于定位 examples/story/<person>.md）")
    parser.add_argument("--md", type=str, help="直接指定人物 Markdown 路径")
    parser.add_argument("--theme", type=str, default="noir", help="海报主题（maptoposter/themes/*.json）")
    parser.add_argument("--distance", type=int, default=6000, help="海报半径（米）")
    parser.add_argument("--limit", type=int, default=0, help="最多生成多少个地点海报（0 表示全部）")
    parser.add_argument("--format", type=str, default="png", choices=["png", "svg", "pdf"], help="海报输出格式")
    parser.add_argument("--width", type=float, default=8.3, help="海报宽度（英寸）")
    parser.add_argument("--height", type=float, default=11.7, help="海报高度（英寸）")
    parser.add_argument("--font-family", type=str, help="Google Fonts 字体（如 Noto Sans SC），用于正确渲染中文")
    parser.add_argument("--force", action="store_true", help="强制重新生成海报（否则命中同名文件会跳过）")
    args = parser.parse_args()

    md_path: Optional[str] = args.md
    if not md_path:
        if not args.person:
            raise SystemExit("需要提供 --person 或 --md")
        md_path = _default_md_path(args.person)

    md_path = os.path.abspath(md_path)
    result = generate_pack(
        md_path=md_path,
        theme=args.theme,
        distance=args.distance,
        limit=args.limit,
        output_format=args.format,
        width=args.width,
        height=args.height,
        font_family=args.font_family,
        force=args.force,
    )
    html_path = result["html_path"]
    file_url = "file://" + quote(html_path)
    print(f"HTML: {html_path}")
    print(f"Open: {file_url}")
    for p in result["posters"]:
        print(f"Poster: {p['file']}")


if __name__ == "__main__":
    main()

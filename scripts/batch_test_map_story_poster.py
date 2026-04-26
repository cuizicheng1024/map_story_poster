"""
批量生成 StoryMap（人物足迹地图 HTML）。

用途：
- 用已有人物名单批量生成 StoryMap HTML，用于快速回归/演示。
- 支持自动补齐缺失的“教材知识点/考点”章节（当旧版 Markdown 缺失时自动重生）。

输入：
- data/*.json 人物名单（JSON 数组）

输出：
- outputs/ 下的 storymap_*.html 以及 batch_report.json

依赖：
- 需要配置 MIMO_API_KEY（可放在仓库根 .env）
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import extract_historical_figures as ehf


def _repo_root() -> Path:
    return _REPO_ROOT


def _poster_repo() -> Path:
    return _repo_root() / "external" / "map_story_poster"


def _storymap_examples_story_dir() -> Path:
    return _poster_repo() / "map_story" / "storymap" / "examples" / "story"


def _load_names(path: Path) -> List[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("names 文件必须是 JSON 数组")
    out: List[str] = []
    for x in data:
        s = str(x).strip()
        if s:
            out.append(s)
    return ehf._dedupe_preserve_order(out)


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _ensure_env_for_mimo() -> None:
    ehf._load_env_from_dotenv_if_needed(_repo_root() / ".env")
    mimo_key = (os.environ.get("MIMO_API_KEY") or "").strip()
    if not mimo_key:
        raise RuntimeError("未检测到 MIMO_API_KEY。请在环境变量或 .env 中配置。")
    os.environ.setdefault("LLM_PROVIDER", "mimo")
    os.environ.setdefault("LLM_API_KEY", mimo_key)
    os.environ.setdefault("LLM_BASE_URL", "https://api.xiaomimimo.com/v1")
    os.environ.setdefault("LLM_MODEL_ID", "mimo-v2-pro")


def _import_poster_modules():
    poster = _poster_repo()
    sys.path.insert(0, str(poster))
    sys.path.insert(0, str(poster / "map_story" / "storymap" / "script"))
    import generate_pure_story_map as gpsm
    import story_agents as sa

    return gpsm, sa


def _md_has_teaching_sections(md_text: str) -> bool:
    if not isinstance(md_text, str) or not md_text.strip():
        return False
    markers = [
        "## 人教版教材知识点",
        "## 教材知识点",
        "## 教材知识点与考点",
        "## 教材知识点和考点",
        "## 初高中阶段考点",
        "## 初高中考点",
        "## 考点",
        "## 教材考点",
    ]
    return any(m in md_text for m in markers)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="用 map_story_poster 仓库批量生成 StoryMap HTML，并输出报告。")
    parser.add_argument("--names", default="data/pep_history_figures_sample.json", help="人物名单JSON文件")
    parser.add_argument("--person", default="", help="单个人物姓名（指定后将忽略 --names）")
    parser.add_argument("--out_dir", default="outputs/output_batch_storymap", help="输出目录（生成HTML）")
    parser.add_argument("--max_people", type=int, default=0, help="最多处理多少人（0表示全量）")
    parser.add_argument("--start_index", type=int, default=1, help="从名单第几个开始（1-based）")
    parser.add_argument("--skip_existing", action="store_true", help="若输出HTML已存在则跳过")
    parser.add_argument("--regen_md", action="store_true", help="即使已存在 Markdown 也重新生成并覆盖")
    parser.add_argument("--sleep_between_people", type=float, default=0.0, help="每个人之间sleep秒数")

    args = parser.parse_args(list(argv) if argv is not None else None)

    _ensure_env_for_mimo()
    gpsm, sa = _import_poster_modules()

    if str(args.person).strip():
        names = [str(args.person).strip()]
    else:
        names_path = Path(args.names)
        if not names_path.is_absolute():
            names_path = (_repo_root() / names_path).resolve()
        names = _load_names(names_path)

    start_index = int(args.start_index)
    if start_index < 1:
        start_index = 1
    if start_index > len(names):
        print("start_index 超出名单长度", file=sys.stderr)
        return 2
    names = names[start_index - 1 :]
    if args.max_people and args.max_people > 0:
        names = names[: int(args.max_people)]
    if not names:
        print("名单为空", file=sys.stderr)
        return 2

    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = (_repo_root() / out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    story_dir = _storymap_examples_story_dir()
    story_dir.mkdir(parents=True, exist_ok=True)

    llm = sa.StoryAgentLLM()

    items: List[Dict[str, Any]] = []
    summary = {
        "total": len(names),
        "ok": 0,
        "skipped_existing": 0,
        "md_exists": 0,
        "md_generated": 0,
        "md_regen_missing_points": 0,
        "md_generate_failed": 0,
        "html_failed": 0,
        "exceptions": 0,
        "out_dir": str(out_dir),
        "story_dir": str(story_dir),
    }

    for idx, person in enumerate(names, start=1):
        t0 = time.time()
        item: Dict[str, Any] = {"index": idx, "name": person}
        stem = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "_", person).strip("._-") or "unknown"
        out_html = out_dir / f"storymap_{stem}.html"
        item["html"] = str(out_html)

        try:
            md_path = story_dir / f"{person}.md"
            item["md_path"] = str(md_path)
            if args.skip_existing and out_html.exists() and md_path.exists():
                try:
                    md_text = md_path.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    md_text = ""
                if _md_has_teaching_sections(md_text):
                    item["status"] = "skipped_existing"
                    summary["skipped_existing"] += 1
                    item["elapsed_ms"] = int((time.time() - t0) * 1000)
                    items.append(item)
                    print(f"[{idx}/{len(names)}] {person} -> skipped_existing")
                    continue
            if md_path.exists() and not args.regen_md:
                summary["md_exists"] += 1
                item["md_status"] = "exists"
                try:
                    md_text = md_path.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    md_text = ""
                if not _md_has_teaching_sections(md_text):
                    md = sa.generate_historical_markdown(llm, person)
                    if md:
                        sa.save_markdown(person, md)
                        summary["md_regen_missing_points"] += 1
                        item["md_status"] = "regen_missing_points"
            else:
                md = sa.generate_historical_markdown(llm, person)
                if not md:
                    summary["md_generate_failed"] += 1
                    item["md_status"] = "generate_failed"
                else:
                    sa.save_markdown(person, md)
                    summary["md_generated"] += 1
                    item["md_status"] = "generated"

            if not md_path.exists():
                item["status"] = "md_missing"
                item["elapsed_ms"] = int((time.time() - t0) * 1000)
                items.append(item)
                print(f"[{idx}/{len(names)}] {person} -> md_missing")
                continue

            res = gpsm.generate_pure_html(md_path=str(md_path), out_path=str(out_html))
            item["status"] = "ok"
            item["result"] = res
            summary["ok"] += 1
            print(f"[{idx}/{len(names)}] {person} -> ok")
        except Exception as e:
            summary["exceptions"] += 1
            item["status"] = "exception"
            item["error"] = str(e)
            summary["html_failed"] += 1
            print(f"[{idx}/{len(names)}] {person} -> exception: {e}")
        finally:
            item["elapsed_ms"] = int((time.time() - t0) * 1000)
            items.append(item)

        if args.sleep_between_people and args.sleep_between_people > 0:
            time.sleep(float(args.sleep_between_people))

    report = {"summary": summary, "items": items}
    report_path = out_dir / "batch_report.json"
    _save_json(report_path, report)
    print(f"报告: {report_path}")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

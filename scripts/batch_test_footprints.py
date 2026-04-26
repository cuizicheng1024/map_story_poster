"""
批量生成“足迹地图”演示页（旧版 footprint_map 链路）。

用途：
- 用少量人物做链路回归与可视化对比（与 storymap 生成器不同）。
- 输出 HTML/GeoJSON/报告到 outputs/ 目录，便于快速浏览。

说明：
- 该脚本更偏“回归/评估”用途；对外展示建议优先使用 storymap 链路。
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import extract_historical_figures as ehf
import footprint_map as fm


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


def _is_cjk(s: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in s)


def _looks_like_misgeocode(display_name: str, person_name: str) -> bool:
    dn = (display_name or "").strip()
    if not dn:
        return False
    if _is_cjk(person_name):
        bad_markers = ["日本", "대한민국", "Republic of Korea", "United States", "USA", "France", "Deutschland"]
        good_markers = ["中国", "China"]
        if any(m in dn for m in good_markers):
            return False
        return any(m in dn for m in bad_markers)
    return False


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="对历史人物名单进行批量足迹地图生成测试，输出报告与HTML文件。")
    parser.add_argument("--names", default="data/pep_history_figures_sample.json", help="人物名单JSON文件路径")
    parser.add_argument("--out_dir", default="outputs/output_batch", help="输出目录（每人生成HTML/GeoJSON）")
    parser.add_argument("--cache_dir", default=".cache_batch", help="缓存目录（LLM候选与地理编码缓存）")
    parser.add_argument("--max_people", type=int, default=0, help="最多处理多少人（0表示全量）")
    parser.add_argument("--start_index", type=int, default=1, help="从名单第几个开始（1-based），默认: 1")
    parser.add_argument("--skip_existing", action="store_true", help="若输出HTML已存在则跳过该人物")
    parser.add_argument("--max_places", type=int, default=3, help="每人最多取多少个地点（用于测试），默认: 3")
    parser.add_argument("--countrycodes", default="", help="Nominatim countrycodes，可选（如 cn），留空自动")
    parser.add_argument("--min_delay_seconds", type=float, default=1.0, help="地理编码最小间隔秒数")
    parser.add_argument("--sleep_between_people", type=float, default=0.0, help="每个人之间额外sleep秒数")

    parser.add_argument("--base_url", default="https://api.xiaomimimo.com/v1", help="MiMo API Base URL")
    parser.add_argument("--auth_mode", default="api-key", choices=["api-key", "bearer"], help="MiMo 认证方式")
    parser.add_argument("--model", default="mimo-v2-omni", help="用于推断足迹地点的模型")
    parser.add_argument("--max_completion_tokens", type=int, default=1024, help="模型输出token上限")
    parser.add_argument("--temperature", type=float, default=0.2, help="温度")
    parser.add_argument("--top_p", type=float, default=0.95, help="top_p")
    parser.add_argument("--timeout_seconds", type=float, default=120.0, help="请求超时秒数")
    parser.add_argument("--max_retries", type=int, default=2, help="MiMo 请求重试次数")

    args = parser.parse_args(list(argv) if argv is not None else None)

    repo_root = _REPO_ROOT
    ehf._load_env_from_dotenv_if_needed(repo_root / ".env")
    api_key = os.environ.get("MIMO_API_KEY", "").strip()
    if not api_key:
        print("未检测到 MIMO_API_KEY。请在环境变量或 .env 中配置。", file=sys.stderr)
        return 2

    names_path = (repo_root / args.names).resolve() if not Path(args.names).is_absolute() else Path(args.names)
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

    out_dir = (repo_root / args.out_dir).resolve()
    cache_dir = (repo_root / args.cache_dir).resolve()
    geo_cache_path = cache_dir / "geocode_cache.json"
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    report_items: List[Dict[str, Any]] = []
    ok = 0
    llm_empty = 0
    geocode_empty = 0
    misgeocode = 0
    exceptions = 0

    for idx, person in enumerate(names, start=1):
        t0 = time.time()
        item: Dict[str, Any] = {"name": person}
        try:
            stem = fm._safe_stem(person)
            geo_path = out_dir / f"footprint_{stem}.geojson"
            html_path = out_dir / f"footprint_{stem}.html"
            if args.skip_existing and html_path.exists():
                item["status"] = "skipped_existing"
                item["geojson"] = str(geo_path)
                item["html"] = str(html_path)
                item["candidates_count"] = None
                item["features_count"] = None
                report_items.append(item)
                print(f"[{idx}/{len(names)}] {person} -> skipped_existing")
                continue

            candidates = fm.extract_footprint_candidates(
                person_name=person,
                api_key=api_key,
                base_url=str(args.base_url),
                auth_mode=str(args.auth_mode),
                model=str(args.model),
                max_places=int(args.max_places),
                max_completion_tokens=int(args.max_completion_tokens),
                temperature=float(args.temperature),
                top_p=float(args.top_p),
                timeout_seconds=float(args.timeout_seconds),
                max_retries=int(args.max_retries),
            )
            item["candidates_count"] = len(candidates)
            if not candidates:
                llm_empty += 1
                item["status"] = "llm_empty"
            geojson = fm.build_geojson(
                person_name=person,
                candidates=candidates,
                geocode_cache_path=geo_cache_path,
                timeout_seconds=float(args.timeout_seconds),
                min_delay_seconds=float(args.min_delay_seconds),
                max_places=int(args.max_places),
                countrycodes=(str(args.countrycodes).strip() or None),
            )
            features = geojson.get("features") if isinstance(geojson, dict) else None
            item["features_count"] = len(features) if isinstance(features, list) else 0
            if not isinstance(features, list) or not features:
                geocode_empty += 1
                item["status"] = item.get("status") or "geocode_empty"
            else:
                bad = 0
                for f in features:
                    dn = ((f or {}).get("properties") or {}).get("display_name") if isinstance(f, dict) else ""
                    if _looks_like_misgeocode(str(dn or ""), person):
                        bad += 1
                item["misgeocode_count"] = bad
                if bad:
                    misgeocode += 1
                    item["status"] = item.get("status") or "misgeocode"

            _save_json(geo_path, geojson)
            html_path.write_text(fm.build_html(person_name=person, geojson=geojson), encoding="utf-8")
            item["geojson"] = str(geo_path)
            item["html"] = str(html_path)

            if "status" not in item:
                item["status"] = "ok"
                ok += 1
        except Exception as e:
            exceptions += 1
            item["status"] = "exception"
            item["error"] = str(e)
        finally:
            item["elapsed_ms"] = int((time.time() - t0) * 1000)
            item["index"] = idx
            report_items.append(item)

        print(f"[{idx}/{len(names)}] {person} -> {item['status']} (features={item.get('features_count',0)})")
        if args.sleep_between_people and args.sleep_between_people > 0:
            time.sleep(float(args.sleep_between_people))

    summary = {
        "total": len(names),
        "ok": ok,
        "llm_empty": llm_empty,
        "geocode_empty": geocode_empty,
        "misgeocode_people": misgeocode,
        "exceptions": exceptions,
        "out_dir": str(out_dir),
        "cache_dir": str(cache_dir),
    }
    report = {"summary": summary, "items": report_items}
    report_path = out_dir / "batch_report.json"
    _save_json(report_path, report)
    print(f"报告: {report_path}")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

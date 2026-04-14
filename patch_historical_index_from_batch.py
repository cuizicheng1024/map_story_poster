#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性补丁脚本：

1. 扫描 batch_runs/246audit_fixed_20260415_* 下所有运行结果；
2. 提取 result.json geocode.fail_samples（缺失时回退 pipeline.log）；
3. 调用现有 OpenAI 兼容接口，批量查询这些“死地名”的现代近似坐标；
4. 将结果合并到 historical_places_index.jsonl；
5. 输出简明统计信息。
"""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

from dotenv import load_dotenv

from auto_generate import _strip_md_fence, call_openai_compatible


DEFAULT_BATCH_GLOB = "batch_runs/246audit_fixed_20260415_*"
DEFAULT_INDEX_FILE = "historical_places_index.jsonl"
FAIL_LOG_RE = re.compile(r"geocode_failed name=(.*?) error=")
VALID_CONFIDENCE = {"high", "medium", "low"}


@dataclass(frozen=True)
class ResolvedPlace:
    ancient_name: str
    modern_name: str
    lon: float
    lat: float
    confidence: str


def repo_root() -> Path:
    return Path(__file__).resolve().parent


def _clean_name(value: object) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text


def _iter_run_dirs(batch_glob: str) -> Iterable[Path]:
    root = repo_root()
    for batch_dir in sorted(root.glob(batch_glob)):
        runs_dir = batch_dir / "runs"
        if not runs_dir.is_dir():
            continue
        for run_dir in sorted(runs_dir.iterdir()):
            if run_dir.is_dir():
                yield run_dir


def _extract_fail_names_from_result(run_dir: Path) -> Tuple[bool, List[str]]:
    result_path = run_dir / "result.json"
    if not result_path.is_file():
        return False, []

    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
    except Exception:
        return False, []

    geocode = payload.get("geocode") if isinstance(payload, dict) else None
    if not isinstance(geocode, dict) or "fail_samples" not in geocode:
        return False, []

    samples = geocode.get("fail_samples") or []
    if not isinstance(samples, list):
        return True, []

    return True, [_clean_name(item) for item in samples if _clean_name(item)]


def _extract_fail_names_from_log(run_dir: Path) -> List[str]:
    log_path = run_dir / "pipeline.log"
    if not log_path.is_file():
        return []

    text = log_path.read_text(encoding="utf-8", errors="ignore")
    return [_clean_name(match.group(1)) for match in FAIL_LOG_RE.finditer(text) if _clean_name(match.group(1))]


def collect_dead_names(batch_glob: str) -> Tuple[List[str], Dict[str, int]]:
    raw_names: List[str] = []
    stats = {
        "batch_count": 0,
        "run_count": 0,
        "result_fail_runs": 0,
        "log_fallback_runs": 0,
        "raw_fail_mentions": 0,
        "unique_dead_names": 0,
    }

    root = repo_root()
    stats["batch_count"] = len(list(root.glob(batch_glob)))

    for run_dir in _iter_run_dirs(batch_glob):
        stats["run_count"] += 1
        has_result_samples, result_names = _extract_fail_names_from_result(run_dir)
        if has_result_samples:
            if result_names:
                stats["result_fail_runs"] += 1
                raw_names.extend(result_names)
            continue

        log_names = _extract_fail_names_from_log(run_dir)
        if log_names:
            stats["log_fallback_runs"] += 1
            raw_names.extend(log_names)

    stats["raw_fail_mentions"] = len(raw_names)

    deduped: List[str] = []
    seen = set()
    for name in raw_names:
        if name in seen:
            continue
        seen.add(name)
        deduped.append(name)

    stats["unique_dead_names"] = len(deduped)
    return deduped, stats


def _chunked(items: Sequence[str], size: int) -> Iterable[List[str]]:
    for start in range(0, len(items), size):
        yield list(items[start : start + size])


def _load_llm_config() -> Dict[str, object]:
    root = repo_root()
    load_dotenv(root / ".env")
    load_dotenv(root / "map_story" / "storymap" / "script" / ".env")

    api_key = (os.getenv("API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
    base_url = (os.getenv("BASE_URL") or os.getenv("OPENAI_BASE_URL") or "").strip()
    model = (os.getenv("MODEL") or os.getenv("OPENAI_MODEL") or "gpt-4o-mini").strip()
    timeout = int((os.getenv("TIMEOUT") or "120").strip())

    if not api_key:
        raise RuntimeError("未找到 API_KEY / OPENAI_API_KEY，无法调用现有 LLM 接口。")

    return {
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
        "timeout": timeout,
    }


def _build_resolve_messages(names: Sequence[str]) -> List[Dict[str, str]]:
    schema = {
        "places": [
            {
                "ancient_name": "原始输入地名，必须逐字一致",
                "modern_name": "最可能对应的现代中文地名或地点描述",
                "coords": [116.3975, 39.9087],
                "confidence": "high"
            }
        ]
    }
    user_prompt = (
        "请为下面这批跑批 geocode.fail_samples 中的地名补出近似现代落点与坐标。\n"
        "要求：\n"
        "1. 必须覆盖全部输入地名，一条不少、一条不多。\n"
        "2. ancient_name 字段必须与输入字符串逐字一致。\n"
        "3. modern_name 使用现代中文地名描述；如果原词范围模糊，就给出最合理的现代行政中心、区域中心或遗址落点。\n"
        "4. coords 严格输出为 [经度, 纬度]，十进制小数。\n"
        "5. confidence 只能填 high / medium / low。\n"
        "6. 只输出 JSON，不要 Markdown，不要解释。\n"
        "7. 禁止返回 null；即便原词模糊，也要给出最合理的近似坐标。\n\n"
        f"输出 schema 示例：\n{json.dumps(schema, ensure_ascii=False)}\n\n"
        f"输入地名列表：\n{json.dumps(list(names), ensure_ascii=False)}"
    )
    return [
        {
            "role": "system",
            "content": "你是历史地名坐标补丁助手，只返回可直接 json.loads 的 JSON。",
        },
        {
            "role": "user",
            "content": user_prompt,
        },
    ]


def _validate_coords(coords: object) -> Tuple[float, float]:
    if not isinstance(coords, list) or len(coords) != 2:
        raise ValueError(f"coords 不是长度为 2 的数组: {coords!r}")
    lon = float(coords[0])
    lat = float(coords[1])
    if not (-180.0 <= lon <= 180.0):
        raise ValueError(f"经度越界: {lon}")
    if not (-90.0 <= lat <= 90.0):
        raise ValueError(f"纬度越界: {lat}")
    return lon, lat


def _parse_resolved_places(raw: str, expected_names: Sequence[str]) -> List[ResolvedPlace]:
    text = _strip_md_fence(raw)
    payload = json.loads(text)

    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict) and isinstance(payload.get("places"), list):
        items = payload["places"]
    else:
        raise ValueError("模型返回 JSON 结构不符合要求")

    expected_set = set(expected_names)
    resolved_map: Dict[str, ResolvedPlace] = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError(f"存在非对象条目: {item!r}")
        ancient_name = _clean_name(item.get("ancient_name"))
        modern_name = _clean_name(item.get("modern_name"))
        confidence = _clean_name(item.get("confidence")).lower()
        lon, lat = _validate_coords(item.get("coords"))

        if ancient_name not in expected_set:
            raise ValueError(f"返回了未请求的地名: {ancient_name}")
        if not modern_name:
            raise ValueError(f"modern_name 为空: {ancient_name}")
        if confidence not in VALID_CONFIDENCE:
            raise ValueError(f"confidence 非法: {ancient_name} -> {confidence}")

        resolved_map[ancient_name] = ResolvedPlace(
            ancient_name=ancient_name,
            modern_name=modern_name,
            lon=round(lon, 6),
            lat=round(lat, 6),
            confidence=confidence,
        )

    missing = [name for name in expected_names if name not in resolved_map]
    if missing:
        raise ValueError(f"模型漏掉了 {len(missing)} 个地名: {missing[:5]}")

    return [resolved_map[name] for name in expected_names]


def resolve_places_with_llm(names: Sequence[str], chunk_size: int) -> List[ResolvedPlace]:
    config = _load_llm_config()
    resolved: List[ResolvedPlace] = []

    for idx, chunk in enumerate(_chunked(names, chunk_size), start=1):
        messages = _build_resolve_messages(chunk)
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                raw = call_openai_compatible(
                    messages=messages,
                    api_key=str(config["api_key"]),
                    model=str(config["model"]),
                    base_url=str(config["base_url"]),
                    timeout=int(config["timeout"]),
                    temperature=0.0,
                )
                batch_items = _parse_resolved_places(raw, chunk)
                resolved.extend(batch_items)
                print(
                    f"[LLM] batch={idx} size={len(chunk)} attempt={attempt} ok",
                    flush=True,
                )
                last_error = None
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                print(
                    f"[LLM] batch={idx} size={len(chunk)} attempt={attempt} failed: {exc}",
                    flush=True,
                )
                messages = messages + [
                    {"role": "assistant", "content": raw if 'raw' in locals() else ""},
                    {
                        "role": "user",
                        "content": "上一个输出不符合要求。请严格按指定 schema 重新输出可直接 json.loads 的 JSON，覆盖全部输入地名。",
                    },
                ]
        if last_error is not None:
            raise RuntimeError(f"第 {idx} 个批次解析失败: {last_error}") from last_error

    return resolved


def _load_index_records(index_path: Path) -> List[Dict[str, object]]:
    if not index_path.exists():
        return []

    records: List[Dict[str, object]] = []
    for line in index_path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except Exception:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def merge_into_index(index_path: Path, resolved_places: Sequence[ResolvedPlace]) -> Dict[str, int]:
    existing_records = _load_index_records(index_path)
    existing_by_name: Dict[str, Dict[str, object]] = {}
    ordered_names: List[str] = []
    for record in existing_records:
        name = _clean_name(record.get("ancient_name"))
        if not name:
            continue
        existing_by_name[name] = dict(record)
        ordered_names.append(name)

    added = 0
    updated = 0
    unchanged = 0
    for place in resolved_places:
        new_record = {
            "ancient_name": place.ancient_name,
            "modern_name": place.modern_name,
            "lat": place.lat,
            "lon": place.lon,
        }
        old_record = existing_by_name.get(place.ancient_name)
        if old_record is None:
            existing_by_name[place.ancient_name] = new_record
            ordered_names.append(place.ancient_name)
            added += 1
            continue
        if (
            _clean_name(old_record.get("modern_name")) == place.modern_name
            and float(old_record.get("lat", 0.0)) == place.lat
            and float(old_record.get("lon", 0.0)) == place.lon
        ):
            unchanged += 1
            continue
        existing_by_name[place.ancient_name] = new_record
        updated += 1

    lines = []
    emitted = set()
    for name in ordered_names:
        if name in emitted:
            continue
        record = existing_by_name.get(name)
        if not record:
            continue
        lines.append(json.dumps(record, ensure_ascii=False))
        emitted.add(name)

    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "before": len(existing_records),
        "after": len(lines),
        "added": added,
        "updated": updated,
        "unchanged": unchanged,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="批量修补 historical_places_index.jsonl")
    parser.add_argument("--batch-glob", default=DEFAULT_BATCH_GLOB, help="批跑目录 glob")
    parser.add_argument("--index-file", default=DEFAULT_INDEX_FILE, help="索引文件路径（相对仓库根目录）")
    parser.add_argument("--chunk-size", type=int, default=25, help="单批送给 LLM 的地名数量")
    parser.add_argument("--scan-only", action="store_true", help="只扫描死地名，不调用 LLM，不落盘")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    dead_names, scan_stats = collect_dead_names(args.batch_glob)

    print(
        json.dumps(
            {
                "scan": scan_stats,
                "dead_names_preview": dead_names[:20],
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    if args.scan_only:
        return 0

    if not dead_names:
        print("[OK] 没有发现需要补丁的死地名。")
        return 0

    resolved_places = resolve_places_with_llm(dead_names, max(1, args.chunk_size))
    low_confidence = [item.ancient_name for item in resolved_places if item.confidence == "low"]

    index_path = (repo_root() / args.index_file).resolve()
    merge_stats = merge_into_index(index_path, resolved_places)

    print(
        json.dumps(
            {
                "scan": scan_stats,
                "resolved_count": len(resolved_places),
                "low_confidence_count": len(low_confidence),
                "low_confidence_preview": low_confidence[:20],
                "index": {
                    "path": str(index_path),
                    **merge_stats,
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

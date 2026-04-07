"""
简要说明：
- 读取人物生平 Markdown，解析“年份”表中的地点与事件列
- 调用 geocode_city 获取 GCJ-02 坐标
- 生成可交互 HTML 地图：支持行政/地形/Esri 多种底图，连线展示顺序，Markdown 弹窗显示大事
"""
import argparse
import atexit
import csv
import io
import json
import logging
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv
from map_client import (
    append_coords_section,
    compute_total_distance_km,
    geocode_city,
    insert_distance_intro,
)
from map_html_renderer import (
    build_info_panel_html,
    render_multi_html,
    render_osm_html,
    render_profile_html,
)
from story_agents import (
    StoryAgentLLM,
    extract_historical_figures,
    generate_historical_markdown,
    save_markdown,
)


def _project_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


local_env = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(dotenv_path=local_env)

_LOGGER = logging.getLogger("story_map")
if not _LOGGER.handlers:
    logging.basicConfig(level=logging.INFO)


def _parse_timeline_table(md: str) -> tuple[List[str], List[List[str]]]:
    """
    解析“年份”表，返回表头与行数据。
    """
    if not isinstance(md, str):
        return [], []
    lines = md.splitlines()
    in_sec = False
    header: List[str] = []
    rows: List[List[str]] = []
    table_started = False
    header_seen = False
    for line in lines:
        if line.strip().startswith("## "):
            title = line.strip().lstrip("#").strip()
            # 仅解析“年份”章节下第一张表
            in_sec = title.startswith("年份")
            table_started = False
            header_seen = False
            header = []
            continue
        if not in_sec:
            continue
        if line.strip().startswith("|") and not table_started:
            header = [c.strip() for c in line.strip().strip("|").split("|")]
            table_started = True
            continue
        if table_started:
            if re.match(r"^\|\s*-{3,}\s*\|", line.strip()):
                header_seen = True
                continue
            if header_seen and line.strip().startswith("|"):
                rows.append([c.strip() for c in line.strip().strip("|").split("|")])
            else:
                # 遇到非表格行即停止，避免越界读取
                break
    if header and rows:
        return header, rows
    header = []
    rows = []
    table_started = False
    header_seen = False
    for line in lines:
        if line.strip().startswith("|") and not table_started:
            header = [c.strip() for c in line.strip().strip("|").split("|")]
            table_started = True
            continue
        if table_started:
            if re.match(r"^\|\s*-{3,}\s*\|", line.strip()):
                header_seen = True
                continue
            if header_seen and line.strip().startswith("|"):
                rows.append([c.strip() for c in line.strip().strip("|").split("|")])
            else:
                if header and rows and any(
                    any(k in c for k in ("现称", "事件", "年号", "公元")) for c in header
                ):
                    return header, rows
                header = []
                rows = []
                table_started = False
                header_seen = False
    if header and rows and any(
        any(k in c for k in ("现称", "事件", "年号", "公元")) for c in header
    ):
        return header, rows
    return [], []


def _parse_basic_info(md: str) -> Dict[str, str]:
    """
    解析“人物档案/基本信息”小节，提取键值对（如姓名、朝代、出生等）。
    """
    if not isinstance(md, str):
        return {}
    lines = md.splitlines()
    in_profile = False
    in_basic = False
    info: Dict[str, str] = {}
    for line in lines:
        if line.strip().startswith("## "):
            title = line.strip().lstrip("#").strip()
            in_profile = "人物档案" in title
            in_basic = False
            continue
        if not in_profile:
            continue
        if line.strip().startswith("### "):
            title = line.strip().lstrip("#").strip()
            in_basic = "基本信息" in title
            continue
        if in_basic:
            m = re.match(r"-\s*\*\*(.+?)\*\*：\s*(.+)", line.strip())
            if m:
                info[m.group(1).strip()] = m.group(2).strip()
    return info


def _parse_overview(md: str) -> str:
    """
    解析“人物档案/生平概述”内容，用于生成简介文本。
    """
    if not isinstance(md, str):
        return ""
    lines = md.splitlines()
    in_profile = False
    in_overview = False
    buf: List[str] = []
    for line in lines:
        if line.strip().startswith("## "):
            title = line.strip().lstrip("#").strip()
            in_profile = "人物档案" in title
            if not in_profile:
                in_overview = False
            continue
        if not in_profile:
            continue
        if line.strip().startswith("### "):
            title = line.strip().lstrip("#").strip()
            in_overview = "生平概述" in title
            continue
        if in_overview:
            t = line.strip()
            if not t or re.match(r"^-{3,}$", t):
                continue
            buf.append(t)
    return "".join(buf).strip()


def _extract_works(text: str) -> List[str]:
    if not text:
        return []
    items = re.findall(r"《([^》]+)》", text)
    seen = set()
    works: List[str] = []
    for item in items:
        name = item.strip()
        if name and name not in seen:
            seen.add(name)
            works.append(name)
    return works


def _split_quote_lines(text: str) -> List[str]:
    if not text:
        return []
    parts = [p.strip() for p in re.split(r"[；;]\s*", text) if p.strip()]
    return parts


def _parse_location_sections(md: str) -> List[Dict[str, str]]:
    """
    解析“人生历程/重要地点”段落为结构化地点事件列表。
    """
    if not isinstance(md, str):
        return []
    lines = md.splitlines()
    in_section = False
    current: Dict[str, str] | None = None
    locations: List[Dict[str, str]] = []
    for line in lines:
        if line.strip().startswith("## "):
            title = line.strip().lstrip("#").strip()
            if "人生历程" in title or "重要地点" in title:
                in_section = True
                current = None
                continue
            if in_section:
                break
        if not in_section:
            continue
        if line.strip().startswith("### "):
            if current:
                locations.append(current)
            raw_title = line.strip().lstrip("#").strip()
            loc_type = "normal"
            if "出生地" in raw_title:
                loc_type = "birth"
            elif "去世地" in raw_title:
                loc_type = "death"
            if "：" in raw_title:
                name = raw_title.split("：", 1)[-1].strip()
            else:
                name = raw_title
            name = re.sub(r"^[^0-9A-Za-z\u4e00-\u9fff]+", "", name).strip()
            current = {
                "name": name,
                "type": loc_type,
                "time": "",
                "location": "",
                "event": "",
                "significance": "",
                "duration": "",
                "quotes": "",
            }
            continue
        if current:
            m = re.match(r"-\s*\*\*(.+?)\*\*：\s*(.+)", line.strip())
            if m:
                key = m.group(1).strip()
                val = m.group(2).strip()
                if key in {"时间", "时段", "时期", "年代", "公元纪年", "年号纪年"}:
                    current["time"] = val
                elif key in {"位置", "地点"}:
                    current["location"] = val
                elif key in {"事迹", "背景", "经过", "事件"}:
                    current["event"] = (current["event"] + " " + val).strip()
                elif key in {"意义", "影响"}:
                    current["significance"] = val
                elif key in {"停留", "停留时间", "停留时长", "居留", "驻留", "逗留", "在此时间", "在此时长"}:
                    current["duration"] = val
                elif key in {"名篇名句", "代表名句", "名句", "诗句"}:
                    current["quotes"] = (current["quotes"] + "；" + val).strip("；")
    if current:
        locations.append(current)
    return locations


_LLM_CLIENT: Optional[StoryAgentLLM] = None
_LLM_LOCK = threading.Lock()
_SPLIT_CACHE: Dict[str, Tuple[str, str]] = {}
_CACHE_LOCK = threading.Lock()
_MAX_TEXT_LEN = 200
_ALLOWED_ORIGINS = [o.strip() for o in os.getenv("STORY_MAP_ALLOWED_ORIGINS", "*").split(",") if o.strip()]


def _resolve_cors_origin(origin: str) -> Optional[str]:
    if not origin:
        return "*" if "*" in _ALLOWED_ORIGINS else None
    if "*" in _ALLOWED_ORIGINS:
        return "*"
    if origin in _ALLOWED_ORIGINS:
        return origin
    return None


def _get_llm_client(event_callback: Optional[callable] = None) -> StoryAgentLLM:
    global _LLM_CLIENT
    if event_callback:
        return StoryAgentLLM(event_callback=event_callback)
    if _LLM_CLIENT is None:
        with _LLM_LOCK:
            if _LLM_CLIENT is None:
                _LLM_CLIENT = StoryAgentLLM()
    return _LLM_CLIENT


def _parse_split_json(raw: str) -> Tuple[str, str]:
    try:
        data = json.loads(raw.strip())
        if isinstance(data, dict):
            ancient = str(data.get("ancient", "")).strip()
            modern = str(data.get("modern", "")).strip()
            return ancient, modern
    except Exception:
        pass
    return "", ""


def _extract_json_block(raw: str) -> str:
    text = raw.strip()
    for start, end in [("[", "]"), ("{", "}")]:
        idx = text.find(start)
        if idx == -1:
            continue
        tail = text[idx:]
        j = tail.rfind(end)
        if j != -1:
            return tail[: j + 1]
    return text


def _parse_split_batch(raw: str, expected: List[str]) -> Dict[str, Tuple[str, str]]:
    block = _extract_json_block(raw)
    try:
        data = json.loads(block)
    except Exception:
        return {}
    mapping: Dict[str, Tuple[str, str]] = {}
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                text = (
                    item.get("text")
                    or item.get("loc")
                    or item.get("name")
                    or item.get("source")
                    or ""
                )
                if not text and len(expected) == 1:
                    text = expected[0]
                text = str(text).strip()
                ancient = str(item.get("ancient", "")).strip()
                modern = str(item.get("modern", "")).strip()
                if text:
                    mapping[text] = (ancient, modern)
            elif isinstance(item, list) and len(item) >= 3:
                text = str(item[0]).strip()
                ancient = str(item[1]).strip()
                modern = str(item[2]).strip()
                if text:
                    mapping[text] = (ancient, modern)
        return mapping
    if isinstance(data, dict):
        for key, val in data.items():
            text = str(key).strip()
            if not text:
                continue
            ancient = ""
            modern = ""
            if isinstance(val, dict):
                ancient = str(val.get("ancient", "")).strip()
                modern = str(val.get("modern", "")).strip()
            elif isinstance(val, list):
                if len(val) > 0:
                    ancient = str(val[0]).strip()
                if len(val) > 1:
                    modern = str(val[1]).strip()
            else:
                modern = str(val).strip()
            mapping[text] = (ancient, modern)
    return mapping


def _batch_split_ancient_modern(
    loc_texts: List[str], event_callback: Optional[callable] = None
) -> Dict[str, Tuple[str, str]]:
    texts = [t.strip() for t in loc_texts if t and t.strip()]
    if not texts:
        return {}
    seen = set()
    ordered: List[str] = []
    for t in texts:
        if t in seen:
            continue
        seen.add(t)
        ordered.append(t)
    with _CACHE_LOCK:
        pending = [t for t in ordered if t not in _SPLIT_CACHE]
    if not pending:
        with _CACHE_LOCK:
            return {t: _SPLIT_CACHE[t] for t in ordered if t in _SPLIT_CACHE}
    try:
        client = _get_llm_client(event_callback=event_callback)
    except Exception:
        with _CACHE_LOCK:
            for text in pending:
                if text in _SPLIT_CACHE:
                    continue
                _SPLIT_CACHE[text] = _split_ancient_modern_heuristic(text)
            return {t: _SPLIT_CACHE.get(t, ("", "")) for t in ordered}
    sys_prompt = (
        "你是地名拆解助手。请按输入顺序输出严格 JSON 数组，"
        "元素格式为 {\"text\":\"\",\"ancient\":\"\",\"modern\":\"\"}。"
        "无法判断时 ancient/modern 置空。不要输出多余文本。"
    )
    for i in range(0, len(pending), 20):
        chunk = pending[i : i + 20]
        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": f"地名列表：{json.dumps(chunk, ensure_ascii=False)}"},
        ]
        raw = client.think(messages, temperature=0)
        mapping = _parse_split_batch(raw or "", chunk)
        with _CACHE_LOCK:
            for text in chunk:
                if text in _SPLIT_CACHE:
                    continue
                result = mapping.get(text) or ("", "")
                _SPLIT_CACHE[text] = result
    with _CACHE_LOCK:
        return {t: _SPLIT_CACHE.get(t, ("", "")) for t in ordered}


def _split_ancient_modern_heuristic(loc_text: str) -> Tuple[str, str]:
    text = str(loc_text or "").strip()
    if not text:
        return "", ""
    modern = ""
    ancient = ""
    m = re.search(r"[（(]\s*今(?:称)?\s*([^）)]+)\s*[）)]", text)
    if m:
        modern = m.group(1).strip()
        ancient = re.sub(r"[（(].*?[）)]", "", text).strip()
        ancient = re.sub(r"^(古称|又称|旧称)[:：]?\s*", "", ancient).strip()
        return ancient, modern
    m = re.search(r"\b今(?:称)?\s*([^\s，。；;、/]+)", text)
    if m:
        modern = m.group(1).strip()
        rest = text[: m.start()].strip()
        rest = re.sub(r"[（(].*?[）)]", "", rest).strip()
        rest = re.sub(r"^(古称|又称|旧称)[:：]?\s*", "", rest).strip()
        ancient = rest
        return ancient, modern
    return "", ""


def _split_ancient_modern(loc_text: str, event_callback: Optional[callable] = None) -> Tuple[str, str]:
    if not loc_text:
        return "", ""
    with _CACHE_LOCK:
        cached = _SPLIT_CACHE.get(loc_text)
    if cached:
        return cached
    try:
        client = _get_llm_client(event_callback=event_callback)
    except Exception:
        result = _split_ancient_modern_heuristic(loc_text)
        with _CACHE_LOCK:
            _SPLIT_CACHE[loc_text] = result
        return result
    prompts = [
        "你是地名拆解助手。仅返回严格 JSON：{\"ancient\":\"\",\"modern\":\"\"}。不要输出多余文本。无法判断时输出空字符串。",
        "请只输出 JSON 对象，不要任何解释：{\"ancient\":\"古称或历史地名\",\"modern\":\"现代地名\"}。如果无法判断，两个值都输出空字符串。",
    ]
    ancient = ""
    modern = ""
    for sys_prompt in prompts:
        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": f"地名文本：{loc_text}"},
        ]
        raw = client.think(messages, temperature=0)
        if not raw:
            continue
        a, m = _parse_split_json(raw)
        if a or m:
            ancient, modern = a, m
            break
    result = (ancient, modern)
    with _CACHE_LOCK:
        _SPLIT_CACHE[loc_text] = result
    return result


def _pick_geocode_name(text: str) -> str:
    """
    为地理编码选取最稳妥的候选名称。
    """
    if not text:
        return ""
    match = re.search(r"今([^）)]+)", text)
    if match:
        # 优先取“今XX”里的现代地名，降低古称歧义
        text = match.group(1).strip()
    else:
        for sep in [" / ", "/", "或", "、", "，", ",", "；", ";"]:
            if sep in text:
                text = text.split(sep, 1)[0]
                break
        text = re.sub(r"[（(].*?[）)]", "", text).strip()
    return text


def _validate_input_text(text: object) -> Optional[str]:
    if not isinstance(text, str):
        return "输入必须是字符串"
    cleaned = text.strip()
    if not cleaned:
        return "输入不能为空"
    if len(cleaned) > _MAX_TEXT_LEN:
        return f"输入过长（最多 {_MAX_TEXT_LEN} 字符）"
    return None


def _extract_title_from_text(text: str) -> str:
    m = re.search(r"“([^”]+)”", text)
    if m:
        return m.group(1).strip()
    return ""


def _parse_date_location(text: str, keys: List[str]) -> tuple[str, str]:
    date = ""
    m = re.search(r"(公元前|前)?\d{3,4}年", text)
    if m:
        date = m.group(0)
    loc = ""
    for k in keys:
        if k in text:
            loc = text.split(k, 1)[-1].strip("。；; ")
            break
    if not loc:
        parts = re.split(r"[，,]", text, maxsplit=1)
        if len(parts) > 1:
            loc = parts[1].strip("。；; ")
    return date, loc


def _parse_coords_table(md: str) -> Dict[str, tuple[float, float]]:
    """
    解析“地点坐标”表，提供名称到经纬度的缓存映射。
    """
    if not isinstance(md, str):
        return {}
    lines = md.splitlines()
    in_section = False
    table_started = False
    header_seen = False
    idx_name = None
    idx_lat = None
    idx_lon = None
    coords: Dict[str, tuple[float, float]] = {}
    for line in lines:
        if line.strip().startswith("## "):
            title = line.strip().lstrip("#").strip()
            in_section = "地点坐标" in title
            table_started = False
            header_seen = False
            idx_name = None
            idx_lat = None
            idx_lon = None
            continue
        if not in_section:
            continue
        if line.strip().startswith("|") and not table_started:
            table_started = True
            header = [c.strip() for c in line.strip().strip("|").split("|")]
            for i, c in enumerate(header):
                if "现称" in c or "地点" in c:
                    idx_name = i
                if "纬度" in c or "lat" in c.lower():
                    idx_lat = i
                if "经度" in c or "lon" in c.lower() or "lng" in c.lower():
                    idx_lon = i
            continue
        if table_started:
            if re.match(r"^\|\s*-{3,}\s*\|", line.strip()):
                header_seen = True
                continue
            if header_seen and line.strip().startswith("|"):
                row = [c.strip() for c in line.strip().strip("|").split("|")]
                if idx_name is None or idx_lat is None or idx_lon is None:
                    continue
                if idx_name >= len(row) or idx_lat >= len(row) or idx_lon >= len(row):
                    continue
                name = _pick_geocode_name(row[idx_name])
                try:
                    lat = float(row[idx_lat])
                    lon = float(row[idx_lon])
                except Exception:
                    continue
                if name:
                    coords[name] = (lat, lon)
            else:
                break
    return coords


def _build_profile_data(md: str, event_callback: Optional[callable] = None) -> Optional[Dict[str, object]]:
    """
    汇总人物档案与地点数据，形成完整人物页渲染所需结构。
    """
    if not isinstance(md, str) or not md.strip():
        return None
    info = _parse_basic_info(md)
    locations = _parse_location_sections(md)
    if not info or not locations:
        return None
    name_raw = info.get("姓名", "")
    name = name_raw.split("（", 1)[0].strip() or name_raw.strip()
    title = (
        _extract_title_from_text(info.get("历史地位", ""))
        or _extract_title_from_text(name_raw)
        or ""
    )
    description = _parse_overview(md)
    if not description:
        description = "；".join(
            [t for t in [info.get("历史地位", ""), info.get("主要成就", "")] if t]
        )
    description = re.sub(r"-{3,}$", "", description).strip()
    works = _extract_works(" ".join([description, info.get("主要成就", ""), info.get("历史地位", "")]))
    birth_text = info.get("出生", "")
    death_text = info.get("去世", "")
    birth_date, birth_loc = _parse_date_location(birth_text, ["出生于", "生于"])
    death_date, death_loc = _parse_date_location(death_text, ["卒于", "去世于", "卒"])
    loc_texts = [birth_loc, death_loc]
    loc_texts.extend([loc.get("location") or loc.get("name") or "" for loc in locations])
    # 批量拆解古称/今称，减少逐条调用
    _batch_split_ancient_modern(loc_texts, event_callback=event_callback)
    lifespan = info.get("享年", "")
    birth_modern = _split_ancient_modern(birth_loc, event_callback=event_callback)[1]
    death_modern = _split_ancient_modern(death_loc, event_callback=event_callback)[1]
    birth_geo = _pick_geocode_name(birth_modern or birth_loc)
    death_geo = _pick_geocode_name(death_modern or death_loc)
    # 出生/去世地点优先直连地理编码
    birth_coord = geocode_city(birth_geo) if birth_geo else None
    death_coord = geocode_city(death_geo) if death_geo else None
    dynasty = (info.get("时代", "") or info.get("朝代", "")).strip()
    avatar = ""
    person = {
        "name": name or "人物",
        "title": title,
        "description": description,
        "quote": title,
        "dynasty": dynasty,
        "birthplace": birth_loc,
        "avatar": avatar,
        "birth": {
            "date": birth_date,
            "location": birth_loc,
            "lat": birth_coord[0] if birth_coord else None,
            "lng": birth_coord[1] if birth_coord else None,
        },
        "death": {
            "date": death_date,
            "location": death_loc,
            "lat": death_coord[0] if death_coord else None,
            "lng": death_coord[1] if death_coord else None,
        },
        "lifespan": lifespan,
    }
    coords_cache = _parse_coords_table(md)
    loc_items: List[Dict[str, object]] = []
    for loc in locations:
        loc_text = loc.get("location") or loc.get("name") or ""
        ancient, modern = _split_ancient_modern(loc_text, event_callback=event_callback)
        geo_name = _pick_geocode_name(modern or loc_text or loc.get("name") or ancient)
        coord = None
        # 优先使用 Markdown 中自动写入的坐标表，降低地理编码调用次数
        if geo_name:
            coord = coords_cache.get(geo_name)
        if not coord and modern:
            coord = coords_cache.get(_pick_geocode_name(modern))
        if not coord and loc_text:
            coord = coords_cache.get(_pick_geocode_name(loc_text))
        if not coord and loc.get("name"):
            coord = coords_cache.get(_pick_geocode_name(loc.get("name") or ""))
        if not coord and geo_name:
            # 坐标表缺失时才触发在线地理编码
            coord = geocode_city(geo_name)
        if not coord:
            continue
        works = _extract_works(" ".join([loc.get("event", ""), loc.get("significance", "")]))
        quote_lines = _split_quote_lines(loc.get("quotes", ""))
        loc_items.append(
            {
                "name": loc.get("name") or geo_name,
                "ancientName": ancient or loc.get("name") or "",
                "modernName": modern or loc_text,
                "lat": coord[0],
                "lng": coord[1],
                "type": loc.get("type", "normal"),
                "event": loc.get("event", ""),
                "time": loc.get("time", ""),
                "duration": loc.get("duration", ""),
                "significance": loc.get("significance", ""),
                "works": works,
                "quoteLines": quote_lines,
            }
        )
    if not loc_items:
        return None
    for loc in loc_items:
        quote_lines = loc.get("quoteLines") or []
        if quote_lines:
            person["quote"] = quote_lines[0]
            break
    map_style = {
        "pathColor": "#1e40af",
        "markers": {
            "normal": {
                "iconUrl": "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png",
                "color": "#3498db",
            },
            "birth": {
                "iconUrl": "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png",
                "color": "#2ecc71",
            },
            "death": {
                "iconUrl": "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png",
                "color": "#e74c3c",
            },
        },
    }
    return {"person": person, "locations": loc_items, "mapStyle": map_style}


def parse_places(md: str) -> List[Dict[str, str]]:
    """
    从 Markdown 中解析“年份”表，提取古称/现称列。
    返回每行字典：{"ancient": 古称, "modern": 现称}
    """
    if not isinstance(md, str):
        return []
    header, rows = _parse_timeline_table(md)
    if not header or not rows:
        return []
    idx_ancient = None
    idx_modern = None
    for i, c in enumerate(header):
        if "古称" in c:
            idx_ancient = i
        if "现称" in c:
            idx_modern = i
    if idx_ancient is None and idx_modern is None:
        return []
    res: List[Dict[str, str]] = []
    for row in rows:
        a = row[idx_ancient] if idx_ancient is not None and idx_ancient < len(row) else ""
        b = row[idx_modern] if idx_modern is not None and idx_modern < len(row) else ""
        if "：" in a:
            a = a.split("：", 1)[-1].strip()
        if "：" in b:
            b = b.split("：", 1)[-1].strip()
        a = re.sub(r"[（）()].*?[）)]", "", a).strip()
        b = re.sub(r"[（）()].*?[）)]", "", b).strip()
        if a or b:
            res.append({"ancient": a, "modern": b})
    return res


def parse_events(md: str) -> List[Dict[str, str]]:
    """
    从 Markdown 中解析“年份”表，提取 年号纪年/公元纪年/事件简述 三列。
    返回每行字典：{"era": ..., "ad": ..., "desc": ...}
    """
    if not isinstance(md, str):
        return []
    header, rows = _parse_timeline_table(md)
    if not header or not rows:
        return []
    idx_era = None
    idx_ad = None
    idx_desc = None
    for i, c in enumerate(header):
        if "年号" in c:
            idx_era = i
        if "公元" in c:
            idx_ad = i
        if "事件" in c:
            idx_desc = i
    if idx_era is None and idx_ad is None and idx_desc is None:
        return []
    res: List[Dict[str, str]] = []
    for row in rows:
        era = row[idx_era] if idx_era is not None and idx_era < len(row) else ""
        ad = row[idx_ad] if idx_ad is not None and idx_ad < len(row) else ""
        desc = row[idx_desc] if idx_desc is not None and idx_desc < len(row) else ""
        if era or ad or desc:
            res.append({"era": era, "ad": ad, "desc": desc})
    return res


def _summarize_samples(items: List[str], limit: int = 3) -> str:
    if not items:
        return ""
    samples = items[:limit]
    more = len(items) - len(samples)
    sample_text = "、".join(samples)
    if more > 0:
        return f"{sample_text} 等 {more} 个"
    return sample_text


def _collect_quality_metrics(md: str) -> Dict[str, int]:
    if not isinstance(md, str):
        return {"timeline_rows": 0, "places": 0, "locations": 0, "coords": 0}
    rows = _parse_timeline_table(md)[1]
    places = parse_places(md)
    locations = _parse_location_sections(md)
    coords = _parse_coords_table(md)
    return {
        "timeline_rows": len(rows),
        "places": len(places),
        "locations": len(locations),
        "coords": len(coords),
    }


def _validate_data_quality(md: str) -> List[str]:
    if not isinstance(md, str) or not md.strip():
        return ["内容为空或格式不正确"]
    issues: List[str] = []
    header, rows = _parse_timeline_table(md)
    if not header or not rows:
        issues.append("年份表缺失或为空")
    else:
        if not any("现称" in c for c in header):
            issues.append("年份表缺少现称列")
        if not any("事件" in c for c in header):
            issues.append("年份表缺少事件列")
    locations = _parse_location_sections(md)
    if not locations:
        issues.append("重要地点段落缺失或为空")
    else:
        missing_event = [l for l in locations if not (l.get("event") or "").strip()]
        if missing_event and len(missing_event) >= max(1, len(locations) // 2):
            issues.append(f"重要地点事迹缺失较多（{len(missing_event)} / {len(locations)}）")
    places = parse_places(md)
    place_names = []
    for p in places:
        name = p.get("modern") or p.get("ancient") or ""
        name = _pick_geocode_name(name)
        if name:
            place_names.append(name)
    coords = _parse_coords_table(md)
    if place_names and not coords:
        issues.append("地点坐标表缺失或为空")
    if coords:
        invalid = []
        for name, coord in coords.items():
            lat, lon = coord
            if abs(lat) > 90 or abs(lon) > 180:
                invalid.append(name)
        if invalid:
            issues.append(f"地点坐标存在异常范围：{_summarize_samples(invalid)}")
        missing = []
        for name in place_names:
            if name not in coords:
                missing.append(name)
        if missing:
            issues.append(f"地点坐标缺失：{_summarize_samples(missing)}")
    return issues


def _print_quality_report(md: str) -> None:
    if not isinstance(md, str):
        print("数据质量检查：\n- 内容为空或格式不正确")
        return
    metrics = _collect_quality_metrics(md)
    issues = _validate_data_quality(md)
    print("数据质量检查：")
    print(f"- 年份表行数：{metrics['timeline_rows']}")
    print(f"- 地点条目：{metrics['places']}")
    print(f"- 坐标条目：{metrics['coords']}")
    print(f"- 结构化地点：{metrics['locations']}")
    if issues:
        for item in issues:
            print(f"- {item}")
    else:
        print("- 未发现明显问题")


def _format_seconds(sec: float) -> str:
    return f"{sec:.2f}s"


def build_points(places: List[Dict[str, str]], events: List[Dict[str, str]]) -> List[Dict[str, object]]:
    """
    将地点列表转为带坐标与弹窗内容的点位：
    - 对每个地点进行地理编码
    - 优先收集包含该地名的事件；无匹配则取前若干条
    - 弹窗内容使用 Markdown 列表
    """
    if not isinstance(places, list) or not isinstance(events, list):
        return []
    pts: List[Dict[str, object]] = []
    for p in places:
        name = p.get("modern") or p.get("ancient") or ""
        if not name:
            continue
        coord = geocode_city(name)
        if not coord:
            continue
        lat, lon = coord
        matched = []
        for e in events:
            d = e.get("desc") or ""
            if name and name in d:
                matched.append(e)
        lines = [f"**{name}**", ""]
        items = matched[:6] if matched else events[:3]
        for e in items:
            era = e.get("era", "")
            ad = e.get("ad", "")
            desc = e.get("desc", "")
            lines.append(f"- {era} / {ad}：{desc}")
        md = "\n".join(lines)
        pts.append({"name": name, "lat": lat, "lon": lon, "md": md})
    return pts


def _extract_intro_fields(md: str) -> Dict[str, str]:
    """
    从“简介”版块提取字段，用于信息面板展示。
    """
    if not isinstance(md, str):
        return {"朝代": "", "身份": "", "生卒年": "", "主要事件": "", "主要作品": "", "历史地位": "", "一生行程": ""}
    lines = md.splitlines()
    in_intro = False
    fields = {"朝代": "", "身份": "", "生卒年": "", "主要事件": "", "主要作品": "", "历史地位": "", "一生行程": ""}
    for line in lines:
        if line.strip().startswith("## "):
            title = line.strip().lstrip("#").strip()
            in_intro = (title == "简介")
            continue
        if not in_intro:
            continue
        if line.strip().startswith("## "):
            break
        t = line.strip()
        if "：" in t:
            k, v = t.split("：", 1)
            k = k.strip()
            v = v.strip()
            if k in fields:
                fields[k] = v
    if any(fields.values()):
        return fields
    info = _parse_basic_info(md)
    if info:
        if not fields["朝代"]:
            fields["朝代"] = info.get("时代", "") or info.get("朝代", "")
        if not fields["身份"]:
            fields["身份"] = info.get("主要身份", "")
        if not fields["历史地位"]:
            fields["历史地位"] = info.get("历史地位", "")
        if not fields["主要事件"]:
            fields["主要事件"] = info.get("主要成就", "")
        if not fields["生卒年"]:
            birth_text = info.get("出生", "")
            death_text = info.get("去世", "")
            birth_date, _ = _parse_date_location(birth_text, ["出生于", "生于"])
            death_date, _ = _parse_date_location(death_text, ["卒于", "去世于", "卒"])
            if birth_date or death_date:
                fields["生卒年"] = f"{birth_date}-{death_date}".strip("-")
            else:
                merged = " / ".join([t for t in [birth_text, death_text] if t])
                fields["生卒年"] = merged
    in_section = False
    for line in lines:
        if line.strip().startswith("## "):
            title = line.strip().lstrip("#").strip()
            if "人生足迹地图说明" in title:
                in_section = True
                continue
            if in_section:
                break
        if not in_section:
            continue
        if "：" not in line:
            continue
        label = ""
        m = re.search(r"\*\*(.+?)\*\*", line)
        if m:
            label = m.group(1).strip()
        val = line.split("：", 1)[-1].strip()
        if label == "行程概览":
            fields["一生行程"] = val
            break
        if not fields["一生行程"] and label in {"时间跨度", "地理范围"}:
            fields["一生行程"] = val
    return fields


def render_html(title: str, points: List[Dict[str, object]], md: str = "") -> str:
    """
    优先输出完整人物页；若缺少结构化信息则回退为基础地图页。
    """
    if md and isinstance(md, str):
        profile = _build_profile_data(md)
        if profile:
            profile["markdown"] = md
            return render_profile_html(profile)
        fields = _extract_intro_fields(md)
        if any(fields.values()):
            info_panel_html = build_info_panel_html(title, fields)
            return render_osm_html(title, points, info_panel_html)
    return render_osm_html(title, points, "")


def save_html(person: str, content: str) -> str:
    """
    保存 HTML 到 examples/story_map/ 目录，若存在则覆盖。
    """
    root = _project_root()
    base = os.path.join(root, "storymap", "examples", "story_map")
    os.makedirs(base, exist_ok=True)
    filename = f"{person}.html"
    path = os.path.join(base, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"✅ 交互式地图已保存: {path}")
    return path


def save_geojson(person: str, geojson: Dict) -> str:
    """
    保存 GeoJSON 到 examples/story_map/ 目录。
    """
    root = _project_root()
    base = os.path.join(root, "storymap", "examples", "story_map")
    os.makedirs(base, exist_ok=True)
    filename = f"{person}.geojson"
    path = os.path.join(base, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False, indent=2)
    print(f"✅ GeoJSON 已保存: {path}")
    return path


def save_csv(person: str, csv_text: str) -> str:
    """
    保存 CSV 到 examples/story_map/ 目录。
    """
    root = _project_root()
    base = os.path.join(root, "storymap", "examples", "story_map")
    os.makedirs(base, exist_ok=True)
    filename = f"{person}.csv"
    path = os.path.join(base, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(csv_text)
    print(f"✅ CSV 已保存: {path}")
    return path


def _safe_name(text: str) -> str:
    safe = re.sub(r'[\\\\/:*?"<>|]', "_", text).strip()
    return safe or "map"


def _story_paths(person: str) -> Tuple[str, str]:
    root = _project_root()
    safe = _safe_name(person)
    md_path = os.path.join(root, "story", f"{safe}.md")
    html_path = os.path.join(root, "story_map", f"{safe}.html")
    return md_path, html_path


def _read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def _load_profile_from_md(md: str, event_callback: Optional[callable] = None) -> Optional[Dict[str, object]]:
    if not md:
        return None
    return _build_profile_data(md, event_callback=event_callback)


def run_interactive() -> None:
    """
    交互模式：
    - 输入人物或一句包含人物的句子
    - 生成并输出地图文件路径
    """
    client = StoryAgentLLM()
    while True:
        try:
            text = input("请输入人物或一句包含人物的句子（q 退出）：").strip()
        except EOFError:
            break
        if not text:
            continue
        err = _validate_input_text(text)
        if err:
            print(err)
            continue
        if text.lower() in {"q", "quit", "exit"}:
            print("已退出。")
            break
        targets = extract_historical_figures(client, text)
        if not targets:
            print("未识别到历史人物")
            continue
        print(f"识别到人物数量：{len(targets)}")
        stats = {"markdown": 0, "html": 0, "failed": 0}
        for person in targets:
            print(f"正在生成 {person} 生平文档，可能需要一些时间...")
            t0 = time.perf_counter()
            t_step = time.perf_counter()
            md = generate_historical_markdown(client, person)
            t_md = time.perf_counter() - t_step
            if not md:
                print(f"未取得：{person}")
                stats["failed"] += 1
                continue
            km = compute_total_distance_km(md)
            if isinstance(km, float):
                md = insert_distance_intro(md, km)
            print("正在进行地点地理编码，可能需要一些时间...")
            t_step = time.perf_counter()
            md = append_coords_section(md)
            t_geo = time.perf_counter() - t_step
            _print_quality_report(md)
            saved = save_markdown(person, md)
            print(f"已生成：{saved}")
            t_step = time.perf_counter()
            try:
                places = parse_places(md)
                events = parse_events(md)
                pts = build_points(places, events)
                html = render_html(person, pts, md=md)
            except Exception as exc:
                _LOGGER.warning("render_failed person=%s error=%s", person, exc)
                html = render_osm_html(person, [], "")
            t_render = time.perf_counter() - t_step
            out = save_html(person, html)
            print(out)
            total = time.perf_counter() - t0
            print(
                f"耗时：生平生成 {_format_seconds(t_md)}，地理编码 {_format_seconds(t_geo)}，"
                f"地图渲染 {_format_seconds(t_render)}，总计 {_format_seconds(total)}"
            )
            stats["markdown"] += 1
            stats["html"] += 1
        print(
            f"本次完成：人物 {len(targets)}，文档 {stats['markdown']}，地图 {stats['html']}，失败 {stats['failed']}"
        )


def _generate_for_person(
    client: StoryAgentLLM,
    person: str,
    progress: Optional[callable] = None,
    allow_cache: bool = True,
    event_callback: Optional[callable] = None,
) -> Dict[str, object]:
    md_path, html_path = _story_paths(person)
    if allow_cache and os.path.exists(md_path) and os.path.exists(html_path):
        md = _read_text(md_path)
        profile = _load_profile_from_md(md, event_callback=event_callback)
        if profile:
            cached_html = _read_text(html_path)
            birth_date = (profile.get("person") or {}).get("birth", {}).get("date", "")
            death_date = (profile.get("person") or {}).get("death", {}).get("date", "")
            has_birth = bool(birth_date and f'"birth": {{"date": "{birth_date}"' in cached_html)
            has_death = bool(death_date and f'"death": {{"date": "{death_date}"' in cached_html)
            if 'data-export="markdown"' not in cached_html or (birth_date and not has_birth) or (death_date and not has_death):
                profile["markdown"] = md
                html = render_profile_html(profile)
                _write_text(html_path, html)
            if progress:
                progress(f"{person} 命中缓存")
            return {
                "ok": True,
                "person": person,
                "markdown_path": md_path,
                "html_path": html_path,
                "steps": [{"label": "命中缓存", "duration": "0.00s"}],
                "duration": {"total": "0.00s"},
                "_profile": profile,
                "cached": True,
            }
    t0 = time.perf_counter()
    if progress:
        progress(f"{person} 生平生成")
    t_step = time.perf_counter()
    md = generate_historical_markdown(client, person)
    t_md = time.perf_counter() - t_step
    if not md:
        return {"ok": False, "person": person, "error": "未取得内容"}
    km = compute_total_distance_km(md)
    if isinstance(km, float):
        md = insert_distance_intro(md, km)
    if progress:
        progress(f"{person} 地理编码")
    t_step = time.perf_counter()
    md = append_coords_section(md)
    t_geo = time.perf_counter() - t_step
    _print_quality_report(md)
    saved = save_markdown(person, md)
    if progress:
        progress(f"{person} 地图渲染")
    t_step = time.perf_counter()
    render_error = ""
    try:
        places = parse_places(md)
        events = parse_events(md)
        pts = build_points(places, events)
        html = render_html(person, pts, md=md)
    except Exception as exc:
        render_error = str(exc).strip() or "地图渲染失败"
        _LOGGER.warning("render_failed person=%s error=%s", person, exc)
        html = render_osm_html(person, [], "")
    t_render = time.perf_counter() - t_step
    if progress:
        progress(f"{person} 文件写入")
    t_step = time.perf_counter()
    out = save_html(person, html)
    t_save = time.perf_counter() - t_step
    total = time.perf_counter() - t0
    profile = _load_profile_from_md(md, event_callback=event_callback)
    steps = [
        {"label": "生平生成", "duration": _format_seconds(t_md)},
        {"label": "地理编码", "duration": _format_seconds(t_geo)},
        {"label": "地图渲染", "duration": _format_seconds(t_render)},
        {"label": "文件写入", "duration": _format_seconds(t_save)},
    ]
    result = {
        "ok": True,
        "person": person,
        "markdown_path": saved,
        "html_path": out,
        "steps": steps,
        "duration": {
            "markdown": _format_seconds(t_md),
            "geocode": _format_seconds(t_geo),
            "render": _format_seconds(t_render),
            "save": _format_seconds(t_save),
            "total": _format_seconds(total),
        },
        "_profile": profile,
        "cached": False,
    }
    if render_error:
        result["warning"] = render_error
    return result


def _build_geojson_for_profile(profile: Dict[str, object]) -> Dict[str, object]:
    person = profile.get("person") or {}
    locations = profile.get("locations") or []
    features = []
    coords = []
    for loc in locations:
        lat = loc.get("lat")
        lng = loc.get("lng")
        if not _is_valid_coord(lat, lng):
            continue
        coords.append([lng, lat])
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lng, lat]},
                "properties": {
                    "person": person.get("name", ""),
                    "name": loc.get("name", ""),
                    "type": loc.get("type", ""),
                    "time": loc.get("time", ""),
                    "modernName": loc.get("modernName", ""),
                    "ancientName": loc.get("ancientName", ""),
                },
            }
        )
    if len(coords) > 1:
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": coords},
                "properties": {"person": person.get("name", ""), "name": "轨迹"},
            }
        )
    return {"type": "FeatureCollection", "features": features}


def _build_csv_for_profile(profile: Dict[str, object]) -> str:
    person = profile.get("person") or {}
    locations = profile.get("locations") or []
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["person", "name", "lat", "lng", "type", "time", "modernName", "ancientName"])
    for loc in locations:
        writer.writerow(
            [
                person.get("name", ""),
                loc.get("name", ""),
                loc.get("lat", ""),
                loc.get("lng", ""),
                loc.get("type", ""),
                loc.get("time", ""),
                loc.get("modernName", ""),
                loc.get("ancientName", ""),
            ]
        )
    return buffer.getvalue()


def _build_geojson_for_multi(people: List[Dict[str, object]]) -> Dict[str, object]:
    features = []
    for item in people:
        person = item.get("person") or {}
        locations = item.get("locations") or []
        coords = []
        for loc in locations:
            lat = loc.get("lat")
            lng = loc.get("lng")
            if not _is_valid_coord(lat, lng):
                continue
            coords.append([lng, lat])
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [lng, lat]},
                    "properties": {
                        "person": person.get("name", ""),
                        "name": loc.get("name", ""),
                        "type": loc.get("type", ""),
                        "time": loc.get("time", ""),
                        "modernName": loc.get("modernName", ""),
                        "ancientName": loc.get("ancientName", ""),
                    },
                }
            )
        if len(coords) > 1:
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": coords},
                    "properties": {"person": person.get("name", ""), "name": "轨迹"},
                }
            )
    return {"type": "FeatureCollection", "features": features}


def _build_csv_for_multi(people: List[Dict[str, object]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["person", "name", "lat", "lng", "type", "time", "modernName", "ancientName"])
    for item in people:
        person = item.get("person") or {}
        locations = item.get("locations") or []
        for loc in locations:
            writer.writerow(
                [
                    person.get("name", ""),
                    loc.get("name", ""),
                    loc.get("lat", ""),
                    loc.get("lng", ""),
                    loc.get("type", ""),
                    loc.get("time", ""),
                    loc.get("modernName", ""),
                    loc.get("ancientName", ""),
                ]
            )
    return buffer.getvalue()


def _is_valid_coord(lat: object, lng: object) -> bool:
    try:
        lat_f = float(lat)
        lng_f = float(lng)
    except Exception:
        return False
    if abs(lat_f) > 90 or abs(lng_f) > 180:
        return False
    return True


def _write_text(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _ensure_profile_exports(profile: Dict[str, object], base_name: str, allow_cache: bool = True) -> Dict[str, str]:
    root = _project_root()
    safe = _safe_name(base_name)
    geo_path = os.path.join(root, "story_map", f"{safe}.geojson")
    csv_path = os.path.join(root, "story_map", f"{safe}.csv")
    if not (allow_cache and os.path.exists(geo_path)):
        geo = _build_geojson_for_profile(profile)
        _write_text(geo_path, json.dumps(geo, ensure_ascii=False, indent=2))
    if not (allow_cache and os.path.exists(csv_path)):
        csv_text = _build_csv_for_profile(profile)
        _write_text(csv_path, csv_text)
    return {"geojson": geo_path, "csv": csv_path}


def _ensure_multi_exports(people: List[Dict[str, object]], base_name: str, allow_cache: bool = True) -> Dict[str, str]:
    root = _project_root()
    safe = _safe_name(base_name)
    geo_path = os.path.join(root, "story_map", f"{safe}.geojson")
    csv_path = os.path.join(root, "story_map", f"{safe}.csv")
    if not (allow_cache and os.path.exists(geo_path)):
        geo = _build_geojson_for_multi(people)
        _write_text(geo_path, json.dumps(geo, ensure_ascii=False, indent=2))
    if not (allow_cache and os.path.exists(csv_path)):
        csv_text = _build_csv_for_multi(people)
        _write_text(csv_path, csv_text)
    return {"geojson": geo_path, "csv": csv_path}


def _relative_path(path: str) -> str:
    root = _project_root()
    if not path:
        return ""
    try:
        return os.path.relpath(path, root)
    except Exception:
        return path


def _compute_overlaps(people: List[Dict[str, object]]) -> List[Dict[str, object]]:
    counts: Dict[str, int] = {}
    for item in people:
        locations = item.get("locations") or []
        names = set()
        for loc in locations:
            name = (loc.get("modernName") or loc.get("name") or "").strip()
            if name:
                names.add(name)
        for name in names:
            counts[name] = counts.get(name, 0) + 1
    overlaps = [{"name": k, "count": v} for k, v in counts.items() if v >= 2]
    overlaps.sort(key=lambda x: (-x["count"], x["name"]))
    return overlaps


def _build_conclusion(results: List[Dict[str, object]], multi: bool) -> str:
    ok = [r for r in results if r.get("ok")]
    failed = [r for r in results if not r.get("ok")]
    if multi:
        return f"合并视图完成：人物 {len(ok)}，失败 {len(failed)}"
    if ok:
        return f"生成完成：人物 {len(ok)}，失败 {len(failed)}"
    return "未生成成功"


_MAX_CONCURRENCY = 5
_COLOR_PALETTE = ("#1e40af", "#c2410c", "#15803d", "#7c3aed", "#0f766e", "#b91c1c")
_EXECUTOR = ThreadPoolExecutor(max_workers=_MAX_CONCURRENCY)
_QUEUE_LOCK = threading.Lock()
_PENDING = 0
_ACTIVE = 0
_TASK_LOCK = threading.Lock()
_TASKS: Dict[str, Dict[str, object]] = {}


def _shutdown_executor() -> None:
    _EXECUTOR.shutdown(wait=False)


atexit.register(_shutdown_executor)


def _create_task(text: str) -> str:
    task_id = uuid.uuid4().hex
    now = time.time()
    task = {
        "id": task_id,
        "text": text,
        "status": "queued",
        "created_at": now,
        "updated_at": now,
        "progress": [],
        "result": None,
        "error": "",
        "queue": {},
    }
    with _TASK_LOCK:
        _TASKS[task_id] = task
    return task_id


def _update_task(task_id: str, **fields: object) -> None:
    with _TASK_LOCK:
        task = _TASKS.get(task_id)
        if not task:
            return
        task.update(fields)
        task["updated_at"] = time.time()


def _append_progress(task_id: str, label: str, detail: str = "") -> None:
    # 进度写入必须持锁，避免并发写导致顺序错乱
    event = {"label": label, "time": time.strftime("%H:%M:%S", time.localtime())}
    if detail:
        event["detail"] = detail
    with _TASK_LOCK:
        task = _TASKS.get(task_id)
        if not task:
            return
        task["progress"].append(event)
        task["updated_at"] = time.time()


def _snapshot_task(task_id: str) -> Dict[str, object]:
    with _TASK_LOCK:
        task = _TASKS.get(task_id)
        if not task:
            return {"ok": False, "error": "task not found"}
        # 返回快照避免外部直接修改全局任务状态
        return {"ok": True, **task}


def _run_task(task_id: str, text: str, allow_cache: bool = True) -> None:
    t0 = time.perf_counter()
    _LOGGER.info("task_start id=%s text=%s", task_id, text)
    _update_task(task_id, status="running")
    # 识别人物属于全流程第一步
    _append_progress(task_id, "人物识别")
    def _llm_event(message: str) -> None:
        _append_progress(task_id, "模型日志", message)
    client = _get_llm_client(event_callback=_llm_event)
    targets = extract_historical_figures(client, text)
    if not targets:
        error = "未识别到历史人物"
        _update_task(task_id, status="failed", error=error)
        _append_progress(task_id, "失败", error)
        _append_progress(task_id, "完成", "失败")
        _LOGGER.warning("task_failed id=%s error=%s", task_id, error)
        return
    results = []
    people_payload = []
    for idx, person in enumerate(targets):
        def _progress(msg: str) -> None:
            _append_progress(task_id, msg)
        result = _generate_for_person(
            client,
            person,
            progress=_progress,
            allow_cache=allow_cache,
            event_callback=_llm_event,
        )
        results.append(result)
        if result.get("ok") and result.get("_profile"):
            profile = result.get("_profile") or {}
            people_payload.append(
                {
                    "person": profile.get("person", {}),
                    "locations": profile.get("locations", []),
                    "mapStyle": profile.get("mapStyle", {}),
                    "color": _COLOR_PALETTE[idx % len(_COLOR_PALETTE)],
                }
            )
            exports = _ensure_profile_exports(profile, person, allow_cache=allow_cache)
            result["exports"] = exports
    overlaps = _compute_overlaps(people_payload) if len(people_payload) > 1 else []
    multi_html_path = ""
    multi_exports: Dict[str, str] = {}
    if len(people_payload) > 1:
        _append_progress(task_id, "合并视图渲染")
        title = "多人物合并视图"
        multi_data = {"title": title, "people": people_payload, "overlaps": overlaps}
        multi_html = render_multi_html(multi_data)
        multi_name = f"{title}_{task_id[:8]}"
        multi_html_path = save_html(multi_name, multi_html)
        multi_exports = _ensure_multi_exports(people_payload, multi_name, allow_cache=allow_cache)
    duration = _format_seconds(time.perf_counter() - t0)
    conclusion = _build_conclusion(results, len(people_payload) > 1)
    summary = {
        "ok": any(r.get("ok") for r in results),
        "people": targets,
        "results": results,
        "multi_html_path": multi_html_path,
        "multi_exports": multi_exports,
        "overlaps": overlaps,
        "duration": duration,
        "conclusion": conclusion,
    }
    summary["files"] = []
    for r in results:
        if not r.get("ok"):
            continue
        files = {
            "markdown": _relative_path(r.get("markdown_path", "")),
            "html": _relative_path(r.get("html_path", "")),
        }
        exports = r.get("exports") or {}
        if exports.get("geojson"):
            files["geojson"] = _relative_path(exports.get("geojson", ""))
        if exports.get("csv"):
            files["csv"] = _relative_path(exports.get("csv", ""))
        summary["files"].append(files)
    if multi_html_path:
        summary["multi"] = {
            "html": _relative_path(multi_html_path),
            "geojson": _relative_path(multi_exports.get("geojson", "")) if multi_exports else "",
            "csv": _relative_path(multi_exports.get("csv", "")) if multi_exports else "",
        }
    _append_progress(task_id, "完成")
    _update_task(task_id, status="completed", result=summary)
    _LOGGER.info("task_completed id=%s duration=%s", task_id, duration)


def _submit_task(text: str) -> Dict[str, object]:
    error = _validate_input_text(text)
    if error:
        return {"ok": False, "error": error}
    queued_at = time.perf_counter()
    with _QUEUE_LOCK:
        global _PENDING
        _PENDING += 1
        position = _PENDING
        active_now = _ACTIVE
    task_id = _create_task(text)
    # 任务创建即返回，避免阻塞前端请求
    _update_task(task_id, queue={"position": position, "limit": _MAX_CONCURRENCY, "active": active_now})

    def _run() -> None:
        started_at = time.perf_counter()
        with _QUEUE_LOCK:
            global _PENDING, _ACTIVE
            _PENDING -= 1
            _ACTIVE += 1
            active_at_start = _ACTIVE
        _update_task(
            task_id,
            queue={
                "position": position,
                "limit": _MAX_CONCURRENCY,
                "active_at_start": active_at_start,
                "wait": _format_seconds(started_at - queued_at),
            },
        )
        try:
            # 任务真正执行发生在后台线程
            _run_task(task_id, text, allow_cache=True)
        except Exception as e:
            error = str(e).strip() or "任务执行失败"
            _update_task(task_id, status="failed", error=error)
            _append_progress(task_id, "失败", error)
            _append_progress(task_id, "完成", "失败")
            _LOGGER.exception("task_crash id=%s", task_id)
        finally:
            with _QUEUE_LOCK:
                _ACTIVE -= 1

    _EXECUTOR.submit(_run)
    return {"ok": True, "task_id": task_id, "queue": {"position": position, "limit": _MAX_CONCURRENCY}}


class StoryMapServerHandler(BaseHTTPRequestHandler):
    def _set_headers(self, status: int, length: int, origin: Optional[str]) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(length))
        self.end_headers()

    def do_OPTIONS(self):
        origin = self.headers.get("Origin", "")
        allowed = _resolve_cors_origin(origin)
        if origin and not allowed:
            self.send_response(403)
            self.end_headers()
            return
        self.send_response(204)
        if allowed:
            self.send_header("Access-Control-Allow-Origin", allowed)
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        origin = self.headers.get("Origin", "")
        allowed = _resolve_cors_origin(origin)
        if origin and not allowed:
            payload = json.dumps({"ok": False, "error": "origin not allowed"}, ensure_ascii=False).encode("utf-8")
            self._set_headers(403, len(payload), None)
            self.wfile.write(payload)
            return
        parsed = urlparse(self.path)
        if parsed.path == "/task":
            params = parse_qs(parsed.query)
            task_id = (params.get("id") or [""])[0].strip()
            if not task_id:
                payload = json.dumps({"ok": False, "error": "id required"}, ensure_ascii=False).encode("utf-8")
                self._set_headers(400, len(payload), allowed)
                self.wfile.write(payload)
                return
            snapshot = _snapshot_task(task_id)
            payload = json.dumps(snapshot, ensure_ascii=False).encode("utf-8")
            status = 200 if snapshot.get("ok") else 404
            self._set_headers(status, len(payload), allowed)
            self.wfile.write(payload)
            return
        if parsed.path != "/generate":
            payload = json.dumps({"ok": False, "error": "not found"}, ensure_ascii=False).encode("utf-8")
            self._set_headers(404, len(payload), allowed)
            self.wfile.write(payload)
            return
        params = parse_qs(parsed.query)
        text = (params.get("person") or params.get("text") or [""])[0].strip()
        if not text:
            payload = json.dumps({"ok": False, "error": "person required"}, ensure_ascii=False).encode("utf-8")
            self._set_headers(400, len(payload), allowed)
            self.wfile.write(payload)
            return
        result = _submit_task(text)
        payload = json.dumps(result, ensure_ascii=False).encode("utf-8")
        status = 200 if result.get("ok") else 400
        self._set_headers(status, len(payload), allowed)
        self.wfile.write(payload)

    def do_POST(self):
        origin = self.headers.get("Origin", "")
        allowed = _resolve_cors_origin(origin)
        if origin and not allowed:
            payload = json.dumps({"ok": False, "error": "origin not allowed"}, ensure_ascii=False).encode("utf-8")
            self._set_headers(403, len(payload), None)
            self.wfile.write(payload)
            return
            
        # Add proxy for LLM calls from frontend
        if self.path == "/api/ai/proxy":
            length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(length).decode("utf-8", errors="ignore") if length else ""
            if not body:
                payload = json.dumps({"ok": False, "error": "body required"}, ensure_ascii=False).encode("utf-8")
                self._set_headers(400, len(payload), allowed)
                self.wfile.write(payload)
                return
            
            try:
                data = json.loads(body)
                messages = data.get("messages", [])
                temperature = data.get("temperature", 0.1)
                
                client = _get_llm_client()
                content = client.think(messages, temperature=temperature)
                
                # Ensure content is valid string and clean surrogate pairs if any
                if content:
                    # First try standard replacement
                    content = content.encode("utf-8", "replace").decode("utf-8", "replace")
                
                resp_data = {"choices": [{"message": {"content": content or ""}}]}
                # Use ensure_ascii=True to avoid "illegal UTF-16 sequence" errors with surrogates
                payload = json.dumps(resp_data, ensure_ascii=True).encode("utf-8")
                self._set_headers(200, len(payload), allowed)
                self.wfile.write(payload)
            except Exception as e:
                _LOGGER.error("llm_proxy_failed error=%s", e)
                payload = json.dumps({"error": str(e)}, ensure_ascii=True).encode("utf-8")
                self._set_headers(500, len(payload), allowed)
                self.wfile.write(payload)
            return

        if self.path != "/generate":
            payload = json.dumps({"ok": False, "error": "not found"}, ensure_ascii=False).encode("utf-8")
            self._set_headers(404, len(payload), allowed)
            self.wfile.write(payload)
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length).decode("utf-8", errors="ignore") if length else ""
        text = ""
        if body:
            try:
                data = json.loads(body)
                if isinstance(data, dict):
                    text = str(data.get("person") or data.get("text") or "").strip()
            except Exception:
                text = ""
        if not text:
            payload = json.dumps({"ok": False, "error": "person required"}, ensure_ascii=False).encode("utf-8")
            self._set_headers(400, len(payload), allowed)
            self.wfile.write(payload)
            return
        result = _submit_task(text)
        payload = json.dumps(result, ensure_ascii=False).encode("utf-8")
        status = 200 if result.get("ok") else 400
        self._set_headers(status, len(payload), allowed)
        self.wfile.write(payload)


def _run_server(port: int) -> None:
    server = ThreadingHTTPServer(("0.0.0.0", port), StoryMapServerHandler)
    _LOGGER.info("server_start port=%s", port)
    print(f"服务已启动：http://localhost:{port}")
    server.serve_forever()


def main():
    """
    命令行入口：
    - 可指定人物与底图
    - 未指定人物时进入交互模式
    """
    parser = argparse.ArgumentParser(
        description="生成人物生平 Markdown，并导出可交互地图 HTML"
    )
    parser.add_argument("-p", "--person", help="历史人物姓名或一句包含人物的句子", required=False)
    parser.add_argument("--serve", action="store_true", help="启动 HTTP 服务")
    parser.add_argument("--port", type=int, default=8765, help="HTTP 服务端口")
    args = parser.parse_args()
    if args.serve:
        return _run_server(args.port)
    if not args.person:
        return run_interactive()
    err = _validate_input_text(args.person)
    if err:
        print(err)
        return
    client = StoryAgentLLM()
    targets = extract_historical_figures(client, args.person)
    if not targets:
        print("未识别到历史人物")
        return
    stats = {"markdown": 0, "html": 0, "failed": 0}
    for person in targets:
        print(f"正在生成 {person} 生平文档，可能需要一些时间...")
        result = _generate_for_person(client, person)
        if not result.get("ok"):
            print(f"未取得：{person}")
            stats["failed"] += 1
            continue
        print(f"已生成：{result.get('markdown_path')}")
        print(result.get("html_path"))
        duration = result.get("duration") or {}
        print(
            "耗时：生平生成 {markdown}，地理编码 {geocode}，地图渲染 {render}，总计 {total}".format(
                markdown=duration.get("markdown", ""),
                geocode=duration.get("geocode", ""),
                render=duration.get("render", ""),
                total=duration.get("total", ""),
            )
        )
        stats["markdown"] += 1
        stats["html"] += 1
    print(
        f"运行完成：人物 {len(targets)}，文档 {stats['markdown']}，地图 {stats['html']}，失败 {stats['failed']}"
    )


if __name__ == "__main__":
    main()

"""
map_client
职责：与地图与地理计算相关的通用能力层，供 story_map 集成调用。
- 地理编码：优先通过 QVeris 接入的高德工具（AMap/GCJ-02），拿到结果后统一转换为 WGS84；失败回退 OSM（原生 WGS84）
- 距离计算：本地 Haversine
- 地图渲染：通过 QVeris 提供的高德地图渲染接口生成 HTML 片段
依赖环境变量：QVERIS_API_URL/QVERIS_BASE_URL、QVERIS_API_KEY（可选）
"""
import json
import math
import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote
from urllib.request import Request, urlopen

from dotenv import load_dotenv


_DEFAULT_USER_AGENT = "map-story/1.0"
_TABLE_SEPARATOR_RE = re.compile(r"^\|\s*-{3,}\s*\|")
_PAREN_CONTENT_RE = re.compile(r"[（(].*?[)）]")
_GEOCODE_ENDPOINTS = [
    ("https://nominatim.openstreetmap.org/search?format=json&limit=1&q={}", "list"),
    ("https://geocode.maps.co/search?q={}", "list"),
    ("https://photon.komoot.io/api/?limit=1&q={}", "photon"),
]

_LOGGER = logging.getLogger("map_client")
if not _LOGGER.handlers:
    logging.basicConfig(level=logging.INFO)

_GEOCODE_CACHE: Dict[str, Tuple[float, float]] = {}
_GEOCODE_CACHE_LOCK = threading.Lock()


def _geocode_cache_get(name: str) -> Optional[Tuple[float, float]]:
    if not name:
        return None
    with _GEOCODE_CACHE_LOCK:
        return _GEOCODE_CACHE.get(name)


def _geocode_cache_set(name: str, coord: Tuple[float, float]) -> None:
    if not name or not coord:
        return
    with _GEOCODE_CACHE_LOCK:
        _GEOCODE_CACHE[name] = coord


def _project_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))


local_env = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(dotenv_path=local_env)
load_dotenv(dotenv_path=os.path.join(_project_root(), ".env"))


def _http_post_json(url: str, headers: Dict[str, str], body: Dict[str, object]) -> Optional[object]:
    """
    使用 POST 请求发送 JSON，并返回解析后的 JSON 对象。
    任何网络或解析异常统一回退为 None，避免上层调用中断。
    """
    try:
        req = Request(url, headers=headers, data=json.dumps(body).encode("utf-8"), method="POST")
        with urlopen(req, timeout=20) as resp:
            data = resp.read()
            return json.loads(data.decode("utf-8", errors="ignore"))
    except Exception as exc:
        _LOGGER.warning("http_post_failed url=%s error=%s", url, exc)
        return None


def _parse_latlon_pair(parts: Iterable[str]) -> Optional[Tuple[float, float]]:
    """
    将字符串列表解析为 (lat, lon)。
    - 如果出现纬度经度颠倒，则自动纠正。
    - 失败返回 None。
    """
    items = [p.strip() for p in parts if p.strip()]
    if len(items) != 2:
        return None
    try:
        a = float(items[0])
        b = float(items[1])
    except Exception:
        return None
    if abs(a) > 90 and abs(b) <= 90:
        # lon,lat → lat,lon
        return b, a
    if abs(b) > 90 and abs(a) <= 90:
        # lat,lon → lat,lon
        return a, b
    return a, b


def _extract_latlon(value: object) -> Optional[Tuple[float, float]]:
    """
    递归从多形态数据中提取 (lat, lon)。
    支持字符串 "lon,lat" / "lat,lon"，列表嵌套与常见字段名称。
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return None
    if isinstance(value, str):
        # 兼容 "lat,lon" 或 "lon,lat" 字符串格式
        return _parse_latlon_pair(value.split(","))
    if isinstance(value, list):
        # 列表内可能嵌套多种结构，递归寻找首个可用坐标
        for item in value:
            res = _extract_latlon(item)
            if res:
                return res
        return None
    if isinstance(value, dict):
        # 常见字段组合优先提取
        if "lat" in value and ("lon" in value or "lng" in value):
            try:
                lat = float(value.get("lat"))
                lon = float(value.get("lon", value.get("lng")))
                return lat, lon
            except Exception:
                pass
        if "latitude" in value and ("longitude" in value or "lng" in value):
            try:
                lat = float(value.get("latitude"))
                lon = float(value.get("longitude", value.get("lng")))
                return lat, lon
            except Exception:
                pass
        for key in ("location", "center", "lnglat"):
            if key in value:
                res = _extract_latlon(value.get(key))
                if res:
                    return res
        # 兜底遍历字典子项
        for v in value.values():
            res = _extract_latlon(v)
            if res:
                return res
    return None


class QVerisClient:
    """
    QVeris 的轻量调用器：
    - 统一管理 API URL 与 API Key
    - 提供地理编码与距离计算的薄封装
    """
    def __init__(self, api_url: str, api_key: str):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key

    def _execute(self, tool_id: str, parameters: Dict[str, object]) -> Optional[object]:
        """
        调用 QVeris 工具执行接口，返回 data 字段或原始数据。
        """
        if not tool_id:
            return None
        url = f"{self.api_url}/tools/execute?tool_id={tool_id}"
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}
        body = {"parameters": parameters, "max_response_size": 20480}
        data = _http_post_json(url, headers, body)
        if not isinstance(data, dict):
            return None
        result = data.get("result")
        if isinstance(result, dict) and "data" in result:
            # 兼容 result.data 的响应形态
            return result.get("data")
        return data.get("data", data)

    def geocode(self, name: str) -> Optional[Tuple[float, float]]:
        """
        使用 QVeris 的地理编码工具解析地点坐标。
        兼容 address / keywords / q 三种参数形式。
        """
        tool_id = os.getenv("QVERIS_GEOCODE_TOOL_ID") or "amap_webservice.geocode.geo.retrieve.v3"
        payload = self._execute(tool_id, {"address": name})
        res = _extract_latlon(payload)
        if res:
            return res
        payload = self._execute(tool_id, {"keywords": name})
        res = _extract_latlon(payload)
        if res:
            return res
        payload = self._execute(tool_id, {"q": name})
        return _extract_latlon(payload)

def _is_valid_coord(lat: object, lon: object) -> bool:
    try:
        lat_f = float(lat)
        lon_f = float(lon)
    except Exception:
        return False
    if abs(lat_f) > 90 or abs(lon_f) > 180:
        return False
    return True


def _is_inside_china(lat: object, lon: object) -> bool:
    if not _is_valid_coord(lat, lon):
        return False
    lat_f = float(lat)
    lon_f = float(lon)
    return 17.5 <= lat_f <= 55.5 and 72.0 <= lon_f <= 136.5


# ---------------------------------------------------------------------------
# Coordinate system conversion
# - AMap (Gaode) geocoding result is typically GCJ-02.
# - Leaflet + most public tiles are aligned to WGS84.
# We therefore normalize coordinates to WGS84 as early as possible.
# ---------------------------------------------------------------------------

_PI = math.pi
_A = 6378245.0
_EE = 0.00669342162296594323


def _transform_lat(x: float, y: float) -> float:
    ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * _PI) + 20.0 * math.sin(2.0 * x * _PI)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * _PI) + 40.0 * math.sin(y / 3.0 * _PI)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * _PI) + 320 * math.sin(y * _PI / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lon(x: float, y: float) -> float:
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * _PI) + 20.0 * math.sin(2.0 * x * _PI)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * _PI) + 40.0 * math.sin(x / 3.0 * _PI)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * _PI) + 300.0 * math.sin(x / 30.0 * _PI)) * 2.0 / 3.0
    return ret


def _gcj02_to_wgs84(lat: float, lon: float) -> Tuple[float, float]:
    """Convert GCJ-02 -> WGS84.

    Notes:
    - The conversion is only meaningful inside mainland China; outside China we return input.
    - This is the commonly used approximate inverse transform.
    """

    if not _is_inside_china(lat, lon):
        return lat, lon

    d_lat = _transform_lat(lon - 105.0, lat - 35.0)
    d_lon = _transform_lon(lon - 105.0, lat - 35.0)

    rad_lat = lat / 180.0 * _PI
    magic = math.sin(rad_lat)
    magic = 1 - _EE * magic * magic
    sqrt_magic = math.sqrt(magic)

    d_lat = (d_lat * 180.0) / (((_A * (1 - _EE)) / (magic * sqrt_magic)) * _PI)
    d_lon = (d_lon * 180.0) / ((_A / sqrt_magic) * math.cos(rad_lat) * _PI)

    mg_lat = lat + d_lat
    mg_lon = lon + d_lon

    return lat * 2.0 - mg_lat, lon * 2.0 - mg_lon


def _looks_chinese(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def _looks_foreign_location(text: str) -> bool:
    value = str(text or "")
    if not value:
        return False
    markers = [
        "斯坦",
        "共和国",
        "王国",
        "联邦",
        "俄罗斯",
        "美国",
        "英国",
        "法国",
        "德国",
        "日本",
        "韩国",
        "朝鲜",
        "越南",
        "泰国",
        "缅甸",
        "老挝",
        "柬埔寨",
        "印度",
        "巴基斯坦",
        "阿富汗",
        "伊朗",
        "伊拉克",
        "土耳其",
        "埃及",
        "澳大利亚",
        "新西兰",
        "加拿大",
        "墨西哥",
        "巴西",
        "阿根廷",
        "西班牙",
        "意大利",
        "葡萄牙",
        "荷兰",
        "比利时",
        "瑞士",
        "瑞典",
        "挪威",
        "芬兰",
        "丹麦",
        "爱尔兰",
        "以色列",
        "沙特",
        "阿联酋",
        "卡塔尔",
        "南非",
        "吉尔吉斯斯坦",
    ]
    return any(m in value for m in markers)


def _build_geocode_candidates(name: str) -> List[str]:
    base = str(name or "").strip()
    if not base:
        return []
    seen = set()
    items = [base]
    if (
        _looks_chinese(base)
        and "中国" not in base
        and "China" not in base
        and not _looks_foreign_location(base)
    ):
        items.append(f"中国{base}")
        items.append(f"{base} 中国")
    out = []
    for item in items:
        t = item.strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _geocode_nominatim(name: str, force_cn: bool = False) -> Optional[Tuple[float, float]]:
    """
    公共地理编码回退链路：
    - nominatim.openstreetmap.org
    - geocode.maps.co
    - photon.komoot.io
    """
    if not name:
        return None
    country_param = "&countrycodes=cn" if force_cn else ""
    mapsco_key = (os.getenv("MAPSCO_API_KEY") or "").strip()
    for url_tpl, kind in _GEOCODE_ENDPOINTS:
        try:
            if "geocode.maps.co" in url_tpl:
                if not mapsco_key:
                    continue
                url = f"{url_tpl.format(quote(name))}&api_key={quote(mapsco_key)}"
            else:
                url = url_tpl.format(quote(name))
            if kind == "list" and country_param:
                url = f"{url}{country_param}"
            req = Request(url, headers={"User-Agent": _DEFAULT_USER_AGENT})
            with urlopen(req, timeout=20) as resp:
                data = resp.read()
                payload = json.loads(data.decode("utf-8", errors="ignore"))
            if kind == "list" and isinstance(payload, list) and payload:
                # Nominatim / maps.co 返回列表
                lat = float(payload[0].get("lat"))
                lon = float(payload[0].get("lon"))
                if not force_cn or _is_inside_china(lat, lon):
                    return lat, lon
            if kind == "photon" and isinstance(payload, dict):
                # Photon 返回 features 数组
                features = payload.get("features") or []
                if features:
                    coords = features[0].get("geometry", {}).get("coordinates") or []
                    if len(coords) >= 2:
                        lon = float(coords[0])
                        lat = float(coords[1])
                        if not force_cn or _is_inside_china(lat, lon):
                            return lat, lon
        except Exception as exc:
            _LOGGER.warning("geocode_failed name=%s error=%s", name, exc)
            continue
    return None


def _get_qveris_client_class():
    """
    预留扩展：允许在单测或外部注入自定义客户端。
    """
    return QVerisClient


def geocode_city(name: str) -> Optional[Tuple[float, float]]:
    """城市/地址字符串 → WGS84 经纬度。

    - 若命中 QVeris 接入的高德地理编码（通常为 GCJ-02），则在落库/渲染前统一转换为 WGS84。
    - 若回退公共地理编码（OSM 系），则其结果本身即为 WGS84。
    """
    name = str(name or "").strip()
    if not name:
        return None
    candidates = _build_geocode_candidates(name)
    looks_cn = _looks_chinese(name)
    looks_foreign = _looks_foreign_location(name)
    # 优先使用命中缓存，减少外部地理编码调用
    cached = _geocode_cache_get(name)
    if cached:
        return cached
    api_url = os.getenv("QVERIS_API_URL") or os.getenv("QVERIS_BASE_URL")
    api_key = os.getenv("QVERIS_API_KEY")
    if api_url and api_key:
        for cand in candidates:
            try:
                QVC = _get_qveris_client_class()
                if not QVC:
                    raise RuntimeError("QVerisClient unavailable")
                client = QVC(api_url=api_url, api_key=api_key)
                res = client.geocode(cand)
                if res:
                    lat, lon = res
                    # 中文地址默认要求落在国内范围，避免解析到海外同名地点
                    if not looks_cn or _is_inside_china(lat, lon):
                        # 高德地理编码结果通常为 GCJ-02，这里在落库/渲染前统一纠偏到 WGS84
                        res_wgs84 = _gcj02_to_wgs84(lat, lon)
                        _geocode_cache_set(name, res_wgs84)
                        _geocode_cache_set(cand, res_wgs84)
                        return res_wgs84
            except Exception as e:
                _LOGGER.warning("QVeris 地理编码调用失败 (candidate=%s): %s", cand, e)
                continue
    for cand in candidates:
        res = _geocode_nominatim(cand, force_cn=looks_cn and not looks_foreign)
        if res:
            _geocode_cache_set(name, res)
            _geocode_cache_set(cand, res)
            return res
    return None


def _clean_place_name(text: str) -> str:
    """
    去除地名中的括注内容，保留核心名称，提升地理编码命中率。
    """
    if not isinstance(text, str):
        return ""
    text = _PAREN_CONTENT_RE.sub("", text)
    return text.strip()


def extract_places_in_order(md: str) -> List[str]:
    """
    从“年份/生平时间线”表解析地点，优先使用“现代搜索地名”列，按出现顺序返回地点列表（去重保序）。
    """
    if not isinstance(md, str):
        return []
    lines = md.splitlines()
    in_loc = False
    table_started = False
    header_seen = False
    display_idx = None
    search_idx = None
    places: List[str] = []
    for line in lines:
        if line.strip().startswith("## "):
            title = line.strip().lstrip("#").strip()
            if title.startswith("年份") or "生平时间线" in title:
                in_loc = True
                table_started = False
                header_seen = False
                display_idx = None
                search_idx = None
                continue
            else:
                in_loc = False
        if not in_loc:
            continue
        if line.strip().startswith("|") and not table_started:
            table_started = True
            header_cells = [c.strip() for c in line.strip().strip("|").split("|")]
            for j, c in enumerate(header_cells):
                if search_idx is None and "现代搜索地名" in c:
                    search_idx = j
                if display_idx is None and ("现称" in c or "地点" in c):
                    display_idx = j
            if display_idx is None and search_idx is None:
                display_idx = len(header_cells) - 1
            continue
        if table_started:
            stripped = line.strip()
            if _TABLE_SEPARATOR_RE.match(stripped):
                header_seen = True
                continue
            if stripped.startswith("|"):
                cells = [c.strip() for c in stripped.strip("|").split("|")]
                cell = ""
                if search_idx is not None and search_idx < len(cells):
                    cell = cells[search_idx]
                if not cell and display_idx is not None and display_idx < len(cells):
                    cell = cells[display_idx]
                if cell:
                    if "：" in cell:
                        cell = cell.split("：", 1)[-1].strip()
                    clean = _clean_place_name(cell)
                    if clean and clean != "—":
                        places.append(clean)
            else:
                break
    if not places:
        return []
    return list(dict.fromkeys(places))


def append_coords_section(md: str) -> str:
    """
    依据“年份”表逐个地理编码，并在文末追加“地点坐标（自动地理编码）”表。
    如果没有识别出地点或均编码失败，则不做改动直接返回原文。
    """
    if not isinstance(md, str):
        return ""
    lines = md.splitlines()
    coords: Dict[str, Tuple[float, float]] = {}
    places = extract_places_in_order(md)
    if not places:
        return md
    max_workers = min(8, max(1, len(places)))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {executor.submit(geocode_city, p): p for p in places}
        for future in as_completed(future_map):
            place = future_map[future]
            try:
                coord = future.result()
            except Exception:
                coord = None
            if coord:
                coords[place] = coord
    if not coords:
        return md
    section = []
    section.append("")
    section.append("## 地点坐标（自动地理编码）")
    section.append("| 现称 | 现代搜索地名 | 纬度 | 经度 |")
    section.append("| --- | --- | --- | --- |")
    for p in places:
        if p in coords:
            lat, lon = coords[p]
            section.append(f"| {p} | {p} | {lat:.6f} | {lon:.6f} |")
    return "\n".join(lines + section)


def compute_total_distance_km(md: str) -> Optional[float]:
    """
    从“地点坐标（自动地理编码）”表获取经纬度，计算总直线距离（公里）。
    使用 Haversine 公式计算。
    """
    if not isinstance(md, str):
        return None
    coords: List[Tuple[float, float]] = []
    lines = md.splitlines()
    in_section = False
    header_seen = False
    for line in lines:
        if line.strip().startswith("## "):
            title = line.strip().lstrip("#").strip()
            in_section = "地点坐标" in title
            header_seen = False
            continue
        if not in_section:
            continue
        if line.strip().startswith("|") and not header_seen:
            header_seen = True
            continue
        if header_seen:
            if not line.strip().startswith("|"):
                break
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 3:
                continue
            try:
                lat = float(cells[1])
                lon = float(cells[2])
                coords.append((lat, lon))
            except Exception:
                continue
    if len(coords) < 2:
        return None
    total = 0.0
    for i in range(len(coords) - 1):
        lat1, lon1 = coords[i]
        lat2, lon2 = coords[i + 1]
        total += _haversine(lat1, lon1, lat2, lon2)
    return total


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    import math

    R = 6371.0
    dLat = math.radians(lat2 - lat1)
    dLon = math.radians(lon2 - lon1)
    a = math.sin(dLat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(
        math.radians(lat2)
    ) * math.sin(dLon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def insert_distance_intro(md: str, distance_km: float) -> str:
    """
    在“人生足迹地图说明”中插入总行程描述。
    """
    if not isinstance(md, str):
        return ""
    lines = md.splitlines()
    out: List[str] = []
    inserted = False
    for line in lines:
        out.append(line)
        if line.strip().startswith("## 二、人生足迹地图说明"):
            continue
        if not inserted and line.strip().startswith("- 🌟 **重要节点数量**"):
            out.append(f"- 🚶 **总行程估算**：约 {distance_km:.0f} 公里")
            inserted = True
    return "\n".join(out)

import argparse
import datetime as _dt
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import extract_historical_figures as ehf


def _safe_stem(name: str) -> str:
    s = re.sub(r"\s+", "_", name.strip())
    s = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "_", s)
    s = s.strip("._-")
    return s or "unknown"


def _load_env() -> None:
    repo_root = Path(__file__).resolve().parent
    ehf._load_env_from_dotenv_if_needed(repo_root / ".env")


def _http_get_json(url: str, *, timeout_seconds: float) -> Any:
    req = urllib.request.Request(
        url=url,
        method="GET",
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; footprint-map/1.0)",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8", errors="replace"))


def _load_json_file(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json_file(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _geocode_nominatim(
    *,
    query: str,
    countrycodes: Optional[str],
    timeout_seconds: float,
) -> Optional[Tuple[float, float, str]]:
    qs = urllib.parse.urlencode(
        {
            "q": query,
            "format": "jsonv2",
            "limit": "1",
            **({"countrycodes": countrycodes} if countrycodes else {}),
        }
    )
    url = f"https://nominatim.openstreetmap.org/search?{qs}"
    try:
        data = _http_get_json(url, timeout_seconds=timeout_seconds)
    except Exception:
        return None
    if not isinstance(data, list) or not data:
        return None
    item = data[0]
    try:
        lat = float(item["lat"])
        lon = float(item["lon"])
        display_name = str(item.get("display_name") or "")
        return lat, lon, display_name
    except Exception:
        return None


def _load_geocode_cache(cache_path: Path) -> Dict[str, Dict[str, Any]]:
    if not cache_path.exists():
        return {}
    try:
        data = _load_json_file(cache_path)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for k, v in data.items():
        if isinstance(k, str) and isinstance(v, dict):
            out[k] = v
    return out


def _save_geocode_cache(cache_path: Path, cache: Dict[str, Dict[str, Any]]) -> None:
    _save_json_file(cache_path, cache)


def extract_footprint_candidates(
    *,
    person_name: str,
    api_key: str,
    base_url: str,
    auth_mode: str,
    model: str,
    max_places: int,
    max_completion_tokens: int,
    temperature: float,
    top_p: float,
    timeout_seconds: float,
    max_retries: int,
) -> List[Dict[str, Any]]:
    system = "你只输出JSON数组。"
    user = (
        f"给定历史人物：{person_name}。"
        f"输出{max_places}条足迹地点JSON数组，按时间顺序。"
        "每条包含place,event,time。"
    )
    token_trials = [
        int(max_completion_tokens),
        int(max(2048, max_completion_tokens * 2)),
        int(max(4096, max_completion_tokens * 3)),
    ]
    seen_tokens = set()
    for t in token_trials:
        if t in seen_tokens:
            continue
        seen_tokens.add(t)
        content = ehf._mimo_chat_completions(
            api_key=api_key,
            base_url=base_url,
            auth_mode=auth_mode,
            model=model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_completion_tokens=t,
            temperature=temperature,
            top_p=top_p,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )
        s = content.strip()
        if not s:
            continue
        try:
            data = json.loads(s)
        except Exception:
            continue
        if isinstance(data, list):
            out: List[Dict[str, Any]] = []
            for item in data:
                if isinstance(item, dict):
                    out.append(item)
            if out:
                return out[:max_places]

    return []


def build_geojson(
    *,
    person_name: str,
    candidates: Sequence[Dict[str, Any]],
    geocode_cache_path: Path,
    timeout_seconds: float,
    min_delay_seconds: float,
    max_places: int,
    countrycodes: Optional[str],
) -> Dict[str, Any]:
    cache = _load_geocode_cache(geocode_cache_path)
    features: List[Dict[str, Any]] = []
    used = 0

    def is_cjk(s: str) -> bool:
        return bool(re.search(r"[\u4e00-\u9fff]", s))

    def normalize_queries(place: str, place_hint: str) -> List[str]:
        out: List[str] = []
        if place_hint:
            out.append(place_hint)

        raw = place
        out.append(raw)

        paren = re.findall(r"[（(]([^）)]+)[）)]", raw)
        for p in paren:
            p = p.strip()
            if not p:
                continue
            out.append(p)
            if p.startswith("今") and len(p) > 1:
                out.append(p[1:].strip())

        no_paren = re.sub(r"[（(][^）)]+[）)]", "", raw).strip()
        if no_paren and no_paren != raw:
            out.append(no_paren)

        if "长安" in raw and all("西安" not in q for q in out):
            out.insert(0, "西安 长安")
        if ("华清池" in raw or "华清宫" in raw) and all("临潼" not in q for q in out):
            out.insert(0, "华清池 临潼 西安 陕西")
        if "马嵬" in raw and all("兴平" not in q for q in out):
            out.insert(0, "马嵬驿 兴平 咸阳 陕西")

        seen = set()
        deduped: List[str] = []
        for q in out:
            q = q.strip()
            if not q or q in seen:
                continue
            seen.add(q)
            deduped.append(q)
        return deduped

    for item in candidates:
        if used >= max_places:
            break
        place = str(item.get("place") or "").strip()
        if not place:
            continue
        place_hint = str(item.get("place_hint") or "").strip()
        event = str(item.get("event") or "").strip()
        time_str = str(item.get("time") or "").strip()
        queries = normalize_queries(place, place_hint)

        lat: float | None = None
        lon: float | None = None
        display_name = ""
        for query in queries:
            cached = cache.get(query)
            if cached and isinstance(cached.get("lat"), (int, float)) and isinstance(cached.get("lon"), (int, float)):
                lat = float(cached["lat"])
                lon = float(cached["lon"])
                display_name = str(cached.get("display_name") or "")
                break
            cc = countrycodes
            if cc is None and is_cjk(query):
                cc = "cn"
            res = _geocode_nominatim(query=query, countrycodes=cc, timeout_seconds=timeout_seconds)
            if res is None:
                cache[query] = {"lat": None, "lon": None, "display_name": "", "q": query}
                _save_geocode_cache(geocode_cache_path, cache)
                continue
            lat, lon, display_name = res
            cache[query] = {"lat": lat, "lon": lon, "display_name": display_name, "q": query}
            _save_geocode_cache(geocode_cache_path, cache)
            time.sleep(min_delay_seconds)
            break

        if lat is None or lon is None:
            continue

        props = {
            "person": person_name,
            "place": place,
            "place_hint": place_hint,
            "time": time_str,
            "event": event,
            "display_name": display_name,
        }
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": props,
            }
        )
        used += 1

    return {"type": "FeatureCollection", "features": features}


def build_html(*, person_name: str, geojson: Dict[str, Any]) -> str:
    safe_title = person_name.replace("<", "").replace(">", "").replace("&", "")
    data = json.dumps(geojson, ensure_ascii=False)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{safe_title} 足迹地图</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <style>
    html, body, #map {{ height: 100%; margin: 0; }}
    .popup-title {{ font-weight: 600; margin-bottom: 4px; }}
    .popup-line {{ margin: 2px 0; }}
  </style>
</head>
<body>
  <div id="map"></div>
  <script>
    const geojson = {data};
    const map = L.map('map');
    const tiles = L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
      maxZoom: 18,
      attribution: '&copy; OpenStreetMap contributors'
    }});
    tiles.addTo(map);

    function popupHtml(p) {{
      const esc = (s) => (s || '').toString().replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
      return `
        <div class="popup-title">${{esc(p.place || '')}}</div>
        <div class="popup-line">${{esc(p.time || '')}}</div>
        <div class="popup-line">${{esc(p.event || '')}}</div>
        <div class="popup-line" style="color:#666;font-size:12px;">${{esc(p.display_name || '')}}</div>
      `;
    }}

    const layer = L.geoJSON(geojson, {{
      pointToLayer: (feature, latlng) => L.marker(latlng),
      onEachFeature: (feature, l) => {{
        l.bindPopup(popupHtml(feature.properties || {{}}));
      }}
    }}).addTo(map);

    const bounds = layer.getBounds();
    if (bounds.isValid()) {{
      map.fitBounds(bounds.pad(0.2));
    }} else {{
      map.setView([35.0, 103.0], 4);
    }}
  </script>
</body>
</html>
"""


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="输入历史人物姓名，输出足迹地图（GeoJSON + HTML）。")
    parser.add_argument("--name", required=True, help="历史人物姓名")
    parser.add_argument("--output_dir", default="output", help="输出目录，默认: output")
    parser.add_argument("--cache_dir", default=".cache", help="缓存目录，默认: .cache")
    parser.add_argument("--max_places", type=int, default=12, help="最多地点数，默认: 12")
    parser.add_argument("--min_delay_seconds", type=float, default=1.0, help="地理编码最小间隔秒数，默认: 1.0")
    parser.add_argument(
        "--countrycodes",
        default="",
        help="地理编码国家过滤（Nominatim countrycodes，可选，如 cn/us；留空表示自动：中文查询默认 cn）",
    )

    parser.add_argument("--base_url", default="https://api.xiaomimimo.com/v1", help="MiMo API Base URL")
    parser.add_argument("--auth_mode", default="api-key", choices=["api-key", "bearer"], help="MiMo 认证方式")
    parser.add_argument("--model", default="mimo-v2-omni", help="用于推断足迹地点的模型，默认: mimo-v2-omni")
    parser.add_argument("--max_completion_tokens", type=int, default=1024, help="模型输出token上限")
    parser.add_argument("--temperature", type=float, default=0.2, help="温度")
    parser.add_argument("--top_p", type=float, default=0.95, help="top_p")
    parser.add_argument("--timeout_seconds", type=float, default=120.0, help="请求超时秒数")
    parser.add_argument("--max_retries", type=int, default=2, help="MiMo 请求重试次数")

    args = parser.parse_args(list(argv) if argv is not None else None)

    _load_env()
    api_key = os.environ.get("MIMO_API_KEY", "").strip()
    if not api_key:
        print("未检测到 MIMO_API_KEY。请在环境变量或 .env 中配置。", file=sys.stderr)
        return 2

    person_name = str(args.name).strip()
    if not person_name:
        print("name 不能为空", file=sys.stderr)
        return 2

    cache_dir = (Path(__file__).resolve().parent / args.cache_dir).resolve()
    out_dir = (Path(__file__).resolve().parent / args.output_dir).resolve()
    geo_cache_path = cache_dir / "geocode_cache.json"
    llm_cache_path = cache_dir / "footprint_candidates" / f"{_safe_stem(person_name)}.json"

    if llm_cache_path.exists():
        try:
            candidates = _load_json_file(llm_cache_path)
            if not isinstance(candidates, list):
                candidates = []
        except Exception:
            candidates = []
    else:
        candidates = extract_footprint_candidates(
            person_name=person_name,
            api_key=api_key,
            base_url=args.base_url,
            auth_mode=args.auth_mode,
            model=args.model,
            max_places=int(args.max_places),
            max_completion_tokens=int(args.max_completion_tokens),
            temperature=float(args.temperature),
            top_p=float(args.top_p),
            timeout_seconds=float(args.timeout_seconds),
            max_retries=int(args.max_retries),
        )
        _save_json_file(llm_cache_path, candidates)

    if not candidates:
        candidates = extract_footprint_candidates(
            person_name=person_name,
            api_key=api_key,
            base_url=args.base_url,
            auth_mode=args.auth_mode,
            model=args.model,
            max_places=int(args.max_places),
            max_completion_tokens=max(1024, int(args.max_completion_tokens)),
            temperature=float(args.temperature),
            top_p=float(args.top_p),
            timeout_seconds=float(args.timeout_seconds),
            max_retries=int(args.max_retries),
        )
        _save_json_file(llm_cache_path, candidates)

    if not candidates and args.model != "mimo-v2-omni":
        candidates = extract_footprint_candidates(
            person_name=person_name,
            api_key=api_key,
            base_url=args.base_url,
            auth_mode=args.auth_mode,
            model="mimo-v2-omni",
            max_places=int(args.max_places),
            max_completion_tokens=max(1024, int(args.max_completion_tokens)),
            temperature=float(args.temperature),
            top_p=float(args.top_p),
            timeout_seconds=float(args.timeout_seconds),
            max_retries=int(args.max_retries),
        )
        _save_json_file(llm_cache_path, candidates)

    geojson = build_geojson(
        person_name=person_name,
        candidates=candidates,
        geocode_cache_path=geo_cache_path,
        timeout_seconds=float(args.timeout_seconds),
        min_delay_seconds=float(args.min_delay_seconds),
        max_places=int(args.max_places),
        countrycodes=(str(args.countrycodes).strip() or None),
    )

    stem = _safe_stem(person_name)
    out_dir.mkdir(parents=True, exist_ok=True)
    geo_path = out_dir / f"footprint_{stem}.geojson"
    html_path = out_dir / f"footprint_{stem}.html"
    _save_json_file(geo_path, geojson)
    html_path.write_text(build_html(person_name=person_name, geojson=geojson), encoding="utf-8")

    print(f"已写入: {geo_path}")
    print(f"已写入: {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

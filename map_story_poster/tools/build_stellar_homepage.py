#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote as url_quote
from urllib.request import Request, urlopen


REPO_ROOT = Path(__file__).resolve().parent.parent
try:
    from dotenv import load_dotenv  # type: ignore
except Exception:
    load_dotenv = None

if load_dotenv:
    load_dotenv(dotenv_path=str((REPO_ROOT / ".env").resolve()))
    load_dotenv(dotenv_path=str((REPO_ROOT.parent / ".env").resolve()))
    load_dotenv(dotenv_path=str((REPO_ROOT / "data" / ".env").resolve()))
STORY_MD_DIR = REPO_ROOT / "storymap" / "examples" / "story"
STORY_MAP_DIR = REPO_ROOT / "storymap" / "examples" / "story_map"
SPOTLIGHT_JSON = REPO_ROOT / "data" / "pep_people_spotlight.json"
KNOWLEDGE_GRAPH_JSON = REPO_ROOT / "data" / "people_knowledge_graph.json"
BIRTH_COORDS_WGS84_JSON = REPO_ROOT / "data" / "people_birth_coords_wgs84.json"
MIN_YEAR = -800
MAX_YEAR = 2000

BAD_PERSON_NAMES = {
    "人物",
    "母亲",
    "刘某",
    "人物 生平传记与足迹",
}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _is_inside_china(lat: float, lng: float) -> bool:
    return 17.5 <= lat <= 55.5 and 72.0 <= lng <= 136.5


_PI = math.pi
_A = 6378245.0
_EE = 0.00669342162296594323


def _transform_lat(x: float, y: float) -> float:
    ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * _PI) + 20.0 * math.sin(2.0 * x * _PI)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * _PI) + 40.0 * math.sin(y / 3.0 * _PI)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * _PI) + 320.0 * math.sin(y * _PI / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lng(x: float, y: float) -> float:
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * _PI) + 20.0 * math.sin(2.0 * x * _PI)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * _PI) + 40.0 * math.sin(x / 3.0 * _PI)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * _PI) + 300.0 * math.sin(x / 30.0 * _PI)) * 2.0 / 3.0
    return ret


def _wgs84_to_gcj02(lat: float, lng: float) -> Tuple[float, float]:
    if not _is_inside_china(lat, lng):
        return lat, lng
    d_lat = _transform_lat(lng - 105.0, lat - 35.0)
    d_lng = _transform_lng(lng - 105.0, lat - 35.0)
    rad_lat = lat / 180.0 * _PI
    magic = math.sin(rad_lat)
    magic = 1 - _EE * magic * magic
    sqrt_magic = math.sqrt(magic)
    d_lat = (d_lat * 180.0) / (((_A * (1 - _EE)) / (magic * sqrt_magic)) * _PI)
    d_lng = (d_lng * 180.0) / ((_A / sqrt_magic) * math.cos(rad_lat) * _PI)
    return lat + d_lat, lng + d_lng


def _gcj02_to_wgs84(lat: float, lng: float) -> Tuple[float, float]:
    if not _is_inside_china(lat, lng):
        return lat, lng
    mg_lat, mg_lng = _wgs84_to_gcj02(lat, lng)
    return lat * 2.0 - mg_lat, lng * 2.0 - mg_lng


def _sha1_int(s: str) -> int:
    h = hashlib.sha1(s.encode("utf-8")).hexdigest()
    return int(h[:12], 16)


def _person_from_filename(name: str) -> str:
    stem = Path(name).stem
    if "__pure__" in stem:
        return stem.split("__pure__", 1)[0]
    return stem


def _is_valid_person_name(name: str) -> bool:
    s = str(name or "").strip()
    if not s:
        return False
    if s in BAD_PERSON_NAMES:
        return False
    return True


@dataclass
class HtmlEntry:
    person: str
    file: str
    mtime: float


def _html_birth_has_coords(html_path: Path) -> bool:
    try:
        text = html_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False
    m = re.search(r"\"birth\"\\s*:\\s*\\{([\\s\\S]*?)\\}\\s*,\\s*\"death\"", text)
    if not m:
        m = re.search(r"\"birth\"\\s*:\\s*\\{([\\s\\S]*?)\\}", text)
    if not m:
        return False
    body = m.group(1)
    return bool(re.search(r"\"lat\"\\s*:\\s*-?\\d+(?:\\.\\d+)?", body) and re.search(r"\"lng\"\\s*:\\s*-?\\d+(?:\\.\\d+)?", body))


def _scan_latest_html(story_map_dir: Path) -> Dict[str, HtmlEntry]:
    latest: Dict[str, HtmlEntry] = {}
    for p in story_map_dir.glob("*.html"):
        if not p.is_file():
            continue
        person = _person_from_filename(p.name).strip()
        if not _is_valid_person_name(person):
            continue
        e = HtmlEntry(person=person, file=p.name, mtime=p.stat().st_mtime)
        cur = latest.get(person)
        if cur is None:
            latest[person] = e
            continue
        cur_has = _html_birth_has_coords(story_map_dir / cur.file)
        e_has = _html_birth_has_coords(p)
        if e_has and not cur_has:
            latest[person] = e
            continue
        if e_has == cur_has and e.mtime > cur.mtime:
            latest[person] = e
    return latest


def _extract_birth_from_story_map_html(html_path: Path) -> Tuple[Optional[float], Optional[float], str, str]:
    try:
        text = html_path.read_text(encoding="utf-8")
    except Exception:
        return None, None, "", ""
    m = re.search(r"const data\s*=\s*(\{[\s\S]*?\})\s*;\s*window\.__EXPORT_DATA__", text)
    if not m:
        return None, None, "", ""
    try:
        data = json.loads(m.group(1))
    except Exception:
        return None, None, "", ""
    person = data.get("person") if isinstance(data, dict) else None
    if not isinstance(person, dict):
        return None, None, "", ""
    dynasty = str(person.get("dynasty") or "").strip()
    birthplace = str(person.get("birthplace") or "").strip()
    birth = person.get("birth")
    if not isinstance(birth, dict):
        return None, None, birthplace, dynasty
    lat = birth.get("lat")
    lng = birth.get("lng")
    try:
        lat_f = float(lat) if lat is not None else None
    except Exception:
        lat_f = None
    try:
        lng_f = float(lng) if lng is not None else None
    except Exception:
        lng_f = None
    return lat_f, lng_f, birthplace, dynasty


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _scan_people_from_story_md(story_md_dir: Path) -> List[str]:
    if not story_md_dir.exists():
        return []
    items = [p.stem for p in story_md_dir.glob("*.md") if p.is_file()]
    return sorted({x.strip() for x in items if _is_valid_person_name(x)})


def _scan_people_from_story_map_html(story_map_dir: Path) -> List[str]:
    if not story_map_dir.exists():
        return []
    names: List[str] = []
    for p in story_map_dir.glob("*.html"):
        if not p.is_file():
            continue
        person = _person_from_filename(p.name).strip()
        if _is_valid_person_name(person):
            names.append(person)
    return sorted(set(names))


def _extract_years_from_md(md_text: str) -> Tuple[Optional[int], Optional[int]]:
    text = md_text

    def parse_years(s: str) -> List[int]:
        out: List[int] = []
        src = str(s or "")
        for m in re.finditer(r"(?<!\d)(-?\d{1,4})(?!\d)", src):
            try:
                y = int(m.group(1))
            except Exception:
                continue
            if y < 0:
                out.append(y)
                continue
            suffix = src[m.end() : min(len(src), m.end() + 4)]
            if suffix.startswith("世纪"):
                continue
            if suffix and suffix[0] in ("月", "日", "号"):
                continue
            prefix = src[max(0, m.start() - 8) : m.start()]
            p = prefix.strip()
            if "公元前" in prefix or p.endswith("前") or "BC" in prefix.upper():
                y = -y
            out.append(y)
        return out

    def pick_year(s: str) -> Optional[int]:
        ys = parse_years(s)
        if not ys:
            return None
        return ys[0]

    def pick_two_years(s: str) -> Tuple[Optional[int], Optional[int]]:
        ys = parse_years(s)
        if len(ys) < 2:
            return None, None
        return ys[0], ys[1]

    m = re.search(r"\*\*生卒年\*\*[:：]\s*([^\n]+)", text)
    if not m:
        m = re.search(r"(?:生卒年|生卒)[:：]\s*([^\n]+)", text)
    if m:
        b, d = pick_two_years(m.group(1))
        if b is not None or d is not None:
            return b, d

    birth = None
    death = None
    mb = re.search(r"\*\*出生\*\*[:：]\s*([^\n]+)", text)
    if not mb:
        mb = re.search(r"(?:出生)[:：]\s*([^\n]+)", text)
    if mb:
        birth = pick_year(mb.group(1))

    md = re.search(r"\*\*(去世|逝世)\*\*[:：]\s*([^\n]+)", text)
    if not md:
        md = re.search(r"(去世|逝世)[:：]\s*([^\n]+)", text)
    if md:
        death = pick_year(md.group(2))

    if birth is None and death is None:
        head = text[:420]
        ys: List[int] = []
        for m in re.finditer(r"(公元前|公元|前)\s*(\d{1,4})\s*年", head):
            try:
                y = int(m.group(2))
            except Exception:
                continue
            prefix = m.group(1) or ""
            if prefix in ("公元前", "前"):
                y = -y
            ys.append(y)
        if len(ys) >= 2:
            return ys[0], ys[1]
        if len(ys) == 1:
            return ys[0], None

    if birth is not None and death is not None:
        try:
            if birth > death and birth > 1000 and death > 0 and death < 400:
                death = None
        except Exception:
            pass

    return birth, death


def _extract_birthplace_from_md(md_text: str) -> Tuple[str, str, str]:
    if not isinstance(md_text, str) or not md_text.strip():
        return "", "", ""
    m = re.search(r"\*\*出生\*\*[:：]\s*([^\n]+)", md_text)
    if not m:
        m = re.search(r"(?:出生)[:：]\s*([^\n]+)", md_text)
    if not m:
        head = md_text[:320]
        mm = re.search(r"([^\s，,。]{2,18})人", head)
        if mm:
            cand = mm.group(1).strip()
            allow = [
                "北京",
                "天津",
                "上海",
                "重庆",
                "河北",
                "河南",
                "山东",
                "山西",
                "陕西",
                "江苏",
                "浙江",
                "安徽",
                "江西",
                "福建",
                "广东",
                "广西",
                "湖南",
                "湖北",
                "四川",
                "云南",
                "贵州",
                "甘肃",
                "青海",
                "辽宁",
                "吉林",
                "黑龙江",
                "内蒙古",
                "宁夏",
                "新疆",
                "西藏",
                "海南",
                "香港",
                "澳门",
            ]
            if any(x in cand for x in allow) or re.search(r"(省|市|县|州|郡|国|府|区|镇|乡|村)$", cand):
                return cand, cand, ""
        return "", "", ""
    text = m.group(1).strip()
    if not re.search(r"(一说|或说|又说|另说)", text):
        text = re.sub(
            r"[（(][^）)]*(存疑|不详|未详|未知|无法确认|生年不详|卒年不详)[^）)]*[）)]\s*$",
            "",
            text,
        ).strip()
    text = re.sub(
        r"^\s*(?:约|大约|约于)?\s*(公元前|公元|前)?\s*\d{1,4}\s*年(?:\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*(?:日|号))?)?\s*[?？]?\s*[，,]?\s*",
        "",
        text,
    ).strip()
    text = re.sub(
        r"^\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*(?:日|号))?\s*[?？]?\s*[，,]?\s*",
        "",
        text,
    ).strip()
    text = re.sub(r"^\s*\d{1,2}\s*(?:日|号)\s*[?？]?\s*[，,]?\s*", "", text).strip()
    text = re.sub(r"^\s*(?:约|大约|约于)?\s*\d{1,2}\s*世纪(?:初|中|末)?\s*[，,]?\s*", "", text).strip()
    if not text:
        return "", "", ""
    if re.fullmatch(r"(?:约|大约|约于)?\s*(公元前|公元|前)?\s*\d{1,4}\s*年(?:\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*(?:日|号))?)?\s*", text):
        return "", "", ""
    if re.fullmatch(r"\d{1,2}\s*月(?:\s*\d{1,2}\s*(?:日|号))?\s*", text):
        return "", "", ""
    parts = [p.strip() for p in re.split(r"[，,]", text) if p.strip()]
    bad = re.compile(r"(存疑|不详|未详|未知|无法确认|生年不详|卒年不详|一说|或说|又说|另说|另有|说法|年说)")
    loc = ""
    for p in parts:
        if not p or bad.search(p):
            continue
        loc = p
        break
    if not loc:
        loc = parts[0] if parts else text
    loc = loc.strip()
    loc = re.sub(r"^\s*(?:出生于|出生在|生于|生在|于|在)\s*", "", loc).strip()
    if (loc.startswith("（") and loc.endswith("）")) or (loc.startswith("(") and loc.endswith(")")):
        loc = loc[1:-1].strip()
    if not loc:
        return "", "", ""
    bad_kw = re.compile(r"(小说|虚构|未明确|具体年份|年份|生年|卒年|出生年|出生年份|年号)")
    if bad_kw.search(loc):
        return "", "", ""
    if re.search(r"(公元前|公元)\s*\d{1,4}", loc):
        return "", "", ""
    if re.search(r"\d{2,4}\s*年", loc):
        return "", "", ""
    if re.search(r"[一二三四五六七八九十]{1,4}年", loc) and not re.search(r"(省|市|县|州|郡|国|府|区|镇|乡|村|岛|湾|山|河|湖|海)", loc):
        return "", "", ""
    ancient = loc
    modern = ""
    if "（" in loc and "）" in loc:
        left, right = loc.split("（", 1)
        ancient = left.strip()
        modern = right.split("）", 1)[0].strip()
    elif "(" in loc and ")" in loc:
        left, right = loc.split("(", 1)
        ancient = left.strip()
        modern = right.split(")", 1)[0].strip()
    modern = re.sub(r"^今", "", modern).strip()
    loc = re.sub(r"[（）()]+", "", loc).strip()
    ancient = re.sub(r"[（）()]+", "", ancient).strip()
    modern = re.sub(r"[（）()]+", "", modern).strip()
    return loc, ancient, modern


def _extract_relations(md_text: str) -> Tuple[List[str], List[Dict[str, str]]]:
    text = md_text
    kw_norm = {
        "父": "父亲",
        "父亲": "父亲",
        "母": "母亲",
        "母亲": "母亲",
        "祖父": "祖父",
        "祖母": "祖母",
        "兄": "兄长",
        "兄长": "兄长",
        "弟": "弟弟",
        "弟弟": "弟弟",
        "姐": "姐姐",
        "姐姐": "姐姐",
        "妹": "妹妹",
        "妹妹": "妹妹",
        "子": "子女",
        "儿子": "子女",
        "女儿": "子女",
        "配偶": "配偶",
        "妻子": "配偶",
        "丈夫": "配偶",
        "师从": "师承",
        "师事": "师承",
        "老师": "师承",
        "导师": "师承",
        "友人": "亲友",
        "好友": "亲友",
        "朋友": "亲友",
        "同僚": "同僚",
        "同事": "同僚",
        "盟友": "盟友",
        "对手": "对手",
        "政敌": "政敌",
        "敌人": "政敌",
    }
    patterns: List[Tuple[str, str, int]] = [
        ("父亲", r"(父亲|父)[：:][^\S\n]*([^\n]+)", 2),
        ("母亲", r"(母亲|母)[：:][^\S\n]*([^\n]+)", 2),
        ("祖父", r"(祖父)[：:][^\S\n]*([^\n]+)", 2),
        ("祖母", r"(祖母)[：:][^\S\n]*([^\n]+)", 2),
        ("兄长", r"(兄长|兄)[：:][^\S\n]*([^\n]+)", 2),
        ("弟弟", r"(弟弟|弟)[：:][^\S\n]*([^\n]+)", 2),
        ("姐姐", r"(姐姐|姐)[：:][^\S\n]*([^\n]+)", 2),
        ("妹妹", r"(妹妹|妹)[：:][^\S\n]*([^\n]+)", 2),
        ("子女", r"(子|儿子|女儿)[：:][^\S\n]*([^\n]+)", 2),
        ("配偶", r"(配偶|妻子|丈夫)[：:][^\S\n]*([^\n]+)", 2),
        ("师承", r"(师从|师事|老师|导师)[：:][^\S\n]*([^\n]+)", 2),
        ("亲友", r"(友人|好友|朋友)[：:][^\S\n]*([^\n]+)", 2),
        ("同僚", r"(同僚|同事|盟友|对手|政敌|敌人)[：:][^\S\n]*([^\n]+)", 2),
        ("并称", r"与([^\n，。,。;；]{2,10})并称", 1),
        ("会面", r"与([^\n，。,。;；]{2,10})相会", 1),
        ("交友", r"结交([^\n，。,。;；]{2,10})", 1),
        ("问道", r"问道于([^\n，。,。;；]{2,10})", 1),
        ("问学", r"问学于([^\n，。,。;；]{2,10})", 1),
    ]
    out_meta: List[Dict[str, str]] = []
    seen_name = set()
    for default_label, pat, group_idx in patterns:
        for m in re.finditer(pat, text):
            label = default_label
            if group_idx == 2:
                kw = str(m.group(1) or "").strip()
                if kw:
                    label = kw_norm.get(kw, label)
            s = str(m.group(group_idx) or "").strip()
            if not s:
                continue
            s = re.sub(r"[，。；;].*$", "", s).strip()
            parts = re.split(r"[、,，/｜|]", s)
            for p in parts:
                n = re.sub(r"[\s\(\)（）\[\]【】《》<>\"“”‘’·•]+", "", p).strip()
                if not (1 < len(n) <= 10):
                    continue
                if n in seen_name:
                    continue
                seen_name.add(n)
                out_meta.append({"name": n, "label": label})
    names = [x["name"] for x in out_meta if isinstance(x, dict) and x.get("name")]
    return names[:8], out_meta[:12]


def _extract_disambiguation(md_text: str) -> Tuple[List[str], str, List[str]]:
    text = md_text
    aliases: List[str] = []
    foreign = ""
    domains: List[str] = []

    def pick(field: str) -> str:
        m = re.search(rf"\\*\\*{re.escape(field)}\\*\\*\\s*[:：]\\s*([^\\n]+)", text)
        if m:
            return m.group(1).strip()
        m = re.search(rf"^-\\s*\\*\\*{re.escape(field)}\\*\\*\\s*[:：]\\s*([^\\n]+)", text, flags=re.MULTILINE)
        if m:
            return m.group(1).strip()
        return ""

    a = pick("别名") or pick("又名") or pick("号") or ""
    if a:
        parts = [p.strip() for p in re.split(r"[、,，/｜|；;]", a) if p.strip()]
        for p in parts:
            x = re.sub(r"[\\[\\]（）()“”\"'‘’\\s·•]+", "", p).strip()
            if 1 < len(x) <= 16 and x not in aliases:
                aliases.append(x)
    foreign = pick("外文名") or pick("英文名") or pick("原文名") or pick("外文名称") or ""
    foreign = foreign.strip().strip("“”\"'‘’")
    d = pick("领域标签") or pick("领域") or pick("学科") or pick("职业标签") or ""
    if d:
        parts = [p.strip() for p in re.split(r"[、,，/｜|；;]", d) if p.strip()]
        for p in parts:
            x = re.sub(r"[\\[\\]（）()“”\"'‘’\\s·•]+", "", p).strip()
            if 1 < len(x) <= 18 and x not in domains:
                domains.append(x)
    return aliases[:6], (foreign or ""), domains[:6]


def _dynasty_hint_from_md(md_text: str) -> str:
    m = re.search(r"\*\*时代\*\*[:：]\s*([^\n]+)", md_text)
    if m:
        return m.group(1).strip()
    m = re.search(r"时代[：:]\s*([^\n]+)", md_text)
    if m:
        return m.group(1).strip()
    m = re.search(r"\*\*朝代\*\*[:：]\s*([^\n]+)", md_text)
    if m:
        return m.group(1).strip()
    m = re.search(r"朝代[：:]\s*([^\n]+)", md_text)
    if m:
        return m.group(1).strip()
    return ""


def _dynasty_mid_year(dynasty: str) -> Optional[int]:
    d = str(dynasty or "").strip()
    if not d:
        return None
    t = re.sub(r"\s+", "", d)

    ranges: List[Tuple[str, Tuple[int, int]]] = [
        ("春秋", (-770, -476)),
        ("战国", (-475, -221)),
        ("春秋战国", (-770, -221)),
        ("先秦", (-800, -221)),
        ("秦", (-221, -206)),
        ("西汉", (-206, 8)),
        ("东汉", (25, 220)),
        ("汉", (-206, 220)),
        ("三国", (220, 280)),
        ("魏晋南北朝", (220, 589)),
        ("魏晋", (220, 420)),
        ("南北朝", (420, 589)),
        ("隋", (581, 618)),
        ("唐", (618, 907)),
        ("五代", (907, 960)),
        ("宋", (960, 1279)),
        ("元", (1271, 1368)),
        ("明", (1368, 1644)),
        ("清", (1644, 1911)),
        ("近代", (1840, 1911)),
        ("民国", (1912, 1949)),
        ("现代", (1949, 2000)),
        ("当代", (1949, 2000)),
    ]

    for key, (a, b) in ranges:
        if key and (key in t):
            return int(round((a + b) / 2))
    return None


def _pick_main_dynasty_by_years(birth_year: Optional[int], death_year: Optional[int]) -> str:
    by = birth_year if isinstance(birth_year, int) else None
    dy = death_year if isinstance(death_year, int) else None
    if by is None and dy is None:
        return ""
    if by is None:
        by = dy
    if dy is None:
        dy = by
    if by is None or dy is None:
        return ""
    a = min(by, dy)
    b = max(by, dy)
    if a == b:
        b = a + 1
    bands: List[Tuple[str, Tuple[int, int]]] = [
        ("春秋战国", (-800, -221)),
        ("秦", (-221, -206)),
        ("汉", (-206, 220)),
        ("魏晋南北", (220, 589)),
        ("隋", (581, 618)),
        ("唐", (618, 907)),
        ("宋", (960, 1279)),
        ("元", (1271, 1368)),
        ("明", (1368, 1644)),
        ("清", (1644, 1840)),
        ("近代", (1840, 1911)),
        ("现代", (1911, 2000)),
    ]
    best = ""
    best_ol = 0
    for name, (x, y) in bands:
        ol = max(0, min(b, y) - max(a, x))
        if ol > best_ol:
            best_ol = ol
            best = name
    return best


def _normalize_dynasty_label(*, person: str, dynasty_raw: str, birth_year: Optional[int], death_year: Optional[int]) -> str:
    s = str(dynasty_raw or "").strip()
    main = _pick_main_dynasty_by_years(birth_year, death_year)
    overrides = {
        "李渊": "唐",
        "李世民": "唐",
        "唐太宗": "唐",
        "朱元璋": "明",
        "明太祖": "明",
    }
    if person in overrides:
        return overrides[person]
    if not s:
        return main or ""

    t = re.sub(r"\s+", "", s)
    keys = ["先秦", "春秋", "战国", "秦", "汉", "三国", "魏晋", "南北朝", "隋", "唐", "宋", "元", "明", "清", "民国", "近代", "现代", "当代"]
    hit = 0
    for k in keys:
        if k and k in t:
            hit += 1
    if hit >= 2 and main:
        return main
    if main and main not in t and hit == 1:
        return main
    return s


def _dynasty_range_from_label(dynasty: str) -> Optional[Tuple[int, int]]:
    d = str(dynasty or "").strip()
    if not d:
        return None
    t = re.sub(r"\s+", "", d)
    ranges: List[Tuple[str, Tuple[int, int]]] = [
        ("春秋战国", (-800, -221)),
        ("先秦", (-800, -221)),
        ("秦", (-221, -206)),
        ("汉", (-206, 220)),
        ("魏晋南北", (220, 589)),
        ("魏晋", (220, 420)),
        ("南北朝", (420, 589)),
        ("隋", (581, 618)),
        ("唐", (618, 907)),
        ("宋", (960, 1279)),
        ("元", (1271, 1368)),
        ("明", (1368, 1644)),
        ("清", (1644, 1911)),
        ("近代", (1840, 1911)),
        ("民国", (1912, 1949)),
        ("现代", (1911, 2000)),
        ("当代", (1911, 2000)),
    ]
    for key, (a, b) in ranges:
        if key and key in t:
            return a, b
    return None


def _pick_quote(spot: Dict[str, Any]) -> str:
    s = str(spot.get("spotlight") or "").strip()
    if s:
        return s
    quotes = spot.get("quotes")
    if isinstance(quotes, list) and quotes:
        q = str(quotes[0] or "").strip()
        if q:
            return q
    intro = str(spot.get("intro") or "").strip()
    if intro:
        return intro
    return ""


def _render_index_html(title: str, data_file: str, quality_line: str = "") -> str:
    safe_title = title.strip() or "故事地图"
    # Always render a fresh index.html instead of patching an existing template.
    # This prevents older inline JS/CSS (e.g. outdated AMap style) from lingering.
    amap_key = (
        os.getenv("AMAP_KEY")
        or os.getenv("AMAP_JS_KEY")
        or os.getenv("AMAP_WEB_KEY")
        or os.getenv("Amap_API_Key")
        or os.getenv("AMAP_API_KEY")
        or ""
    ).strip()
    amap_sec = (
        os.getenv("AMAP_SECURITY")
        or os.getenv("AMAP_SECURITY_JSCODE")
        or os.getenv("Amap_API_Security")
        or os.getenv("AMAP_API_SECURITY")
        or ""
    ).strip()
    amap_inline = ""
    if amap_key or amap_sec:
        parts = []
        if amap_key:
            parts.append(f"window.AMAP_KEY={json.dumps(amap_key, ensure_ascii=False)};")
        if amap_sec:
            parts.append(f"window.AMAP_SECURITY={json.dumps(amap_sec, ensure_ascii=False)};")
        amap_inline = "<script>" + "".join(parts) + "</script>"
    qhtml = ""
    return f"""<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{safe_title}</title>
    {amap_inline}
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
      body {{
        background:
          radial-gradient(1000px 680px at 12% 0%, rgba(37,99,235,0.14), transparent 62%),
          radial-gradient(900px 720px at 92% 10%, rgba(236,72,153,0.10), transparent 60%),
          radial-gradient(900px 520px at 50% 100%, rgba(16,185,129,0.08), transparent 60%),
          linear-gradient(180deg, #ffffff 0%, #f7f7fb 100%);
      }}
      .glass {{
        background: rgba(255,255,255,0.85);
        border: 1px solid rgba(225,225,225,0.85);
        backdrop-filter: blur(10px);
      }}
      .card {{
        border-radius: 16px;
        box-shadow: 0 14px 38px rgba(15,23,42,0.08);
      }}
      .graph {{
        background:
          radial-gradient(1200px 640px at 18% 0%, rgba(56,189,248,0.14), transparent 58%),
          radial-gradient(900px 620px at 84% 18%, rgba(244,63,94,0.12), transparent 56%),
          radial-gradient(900px 600px at 40% 100%, rgba(167,139,250,0.12), transparent 62%),
          linear-gradient(135deg, #07112a 0%, #090f2a 55%, #0a0c23 100%);
      }}
      canvas {{ display:block; }}
      .tooltip {{
        position: absolute;
        pointer-events: none;
        background: rgba(15,23,42,0.88);
        color: rgba(255,255,255,0.92);
        border: 1px solid rgba(255,255,255,0.12);
        border-radius: 12px;
        padding: 10px 12px;
        max-width: 280px;
        font-size: 12px;
        line-height: 1.45;
        box-shadow: 0 18px 34px rgba(0,0,0,0.26);
        z-index: 50;
      }}
      input[type="range"] {{
        accent-color: rgba(255,255,255,0.85);
      }}
      button {{
        transition: transform 160ms ease, background-color 160ms ease, box-shadow 160ms ease, border-color 160ms ease;
      }}
      button:active {{
        transform: translateY(1px);
      }}
      .range-rail {{
        height: 64px;
        border-radius: 14px;
        background: linear-gradient(180deg, rgba(255,255,255,0.10), rgba(255,255,255,0.03));
        border: 1px solid rgba(255,255,255,0.16);
        touch-action: none;
        user-select: none;
      }}
      .ticks {{
        background-image: none;
        pointer-events: none;
      }}
      .band {{
        background: rgba(255,255,255,0.035);
        border: 1px solid rgba(255,255,255,0.08);
        pointer-events: none;
      }}
      .range-mask {{
        background: rgba(2,6,23,0.48);
        border: 1px solid rgba(255,255,255,0.06);
        pointer-events: none;
      }}
      .range-sel {{
        background: linear-gradient(90deg, rgba(34,197,94,0.22), rgba(56,189,248,0.16));
        border: 1px solid rgba(255,255,255,0.26);
        box-shadow: 0 10px 24px rgba(0,0,0,0.28), inset 0 0 0 1px rgba(34,197,94,0.18);
        pointer-events: none;
      }}
      .handle {{
        width: 14px;
        height: 42px;
        border-radius: 10px;
        background: rgba(255,255,255,0.88);
        box-shadow: 0 8px 16px rgba(0,0,0,0.25);
        border: 1px solid rgba(15,23,42,0.25);
        cursor: ew-resize;
        touch-action: none;
      }}
      @keyframes twinkle {{
        0% {{ transform: scale(1); opacity: 0.82; }}
        50% {{ transform: scale(1.12); opacity: 1; }}
        100% {{ transform: scale(1); opacity: 0.86; }}
      }}
    </style>
  </head>
  <body class="min-h-screen">
    <div class="max-w-5xl mx-auto px-4 py-6 space-y-4">
      <div class="glass card px-6 py-5">
        <div class="text-xl font-extrabold text-slate-900">故事地图</div>
        <div class="text-xs text-slate-500 mt-1">以人物→时空→事件为主线，探索历史人物的时空关联</div>
        {qhtml}
      </div>

      <div class="glass card px-6 py-5">
        <div class="text-sm font-bold text-slate-800 mb-2">检索人物</div>
        <div class="flex items-center gap-3">
          <input id="q" class="flex-1 px-4 py-2.5 rounded-xl border border-slate-200 bg-white outline-none focus:ring-2 focus:ring-slate-900/10" placeholder="例如：苏轼" />
          <button id="go" class="px-5 py-2.5 rounded-xl bg-white/80 border border-slate-200 text-slate-800 text-sm font-bold hover:bg-white shadow-sm">查看</button>
        </div>
        <div id="genStatus" class="hidden mt-2 text-xs text-slate-600"></div>
      </div>

      <div class="card graph overflow-hidden relative">
        <div class="px-6 py-4 text-sm font-bold text-white/90 flex items-center justify-between">
          <div class="flex items-center gap-3">
            <div>人类群星闪耀时</div>
            <div class="flex items-center gap-1 text-[11px] font-normal">
              <button id="tabGraph" class="px-3 py-1 rounded-lg bg-white/15 border border-white/20 text-white/90">关系图</button>
              <button id="tabMap" class="px-3 py-1 rounded-lg bg-white/5 border border-white/10 text-white/70 hover:bg-white/10">地图视角</button>
            </div>
          </div>
          <div class="text-[11px] font-normal text-white/60 flex items-center gap-2 flex-wrap justify-end">
            窗口内：<span id="activeCount">-</span><span class="text-white/30">|</span>坐标点：<span id="coordCount">-</span>
          </div>
        </div>
        <div class="px-6 pb-2 -mt-2 text-[11px] text-white/60">拖动时间窗筛选人物；悬停查看简介；点击节点进入人物页</div>
        <div class="px-6 pb-2 -mt-1 text-[11px] text-white/55 flex items-center justify-between gap-3 flex-wrap">
          <div class="flex items-center gap-x-4 gap-y-1 flex-wrap">
            <div class="flex items-center gap-2"><span class="inline-block w-2.5 h-2.5 rounded-full" style="background:#22c55e"></span><span>先秦/公元前</span></div>
            <div class="flex items-center gap-2"><span class="inline-block w-2.5 h-2.5 rounded-full" style="background:#ef4444"></span><span>秦汉</span></div>
            <div class="flex items-center gap-2"><span class="inline-block w-2.5 h-2.5 rounded-full" style="background:#60a5fa"></span><span>魏晋南北朝</span></div>
            <div class="flex items-center gap-2"><span class="inline-block w-2.5 h-2.5 rounded-full" style="background:#f59e0b"></span><span>隋</span></div>
            <div class="flex items-center gap-2"><span class="inline-block w-2.5 h-2.5 rounded-full" style="background:#fb7185"></span><span>唐</span></div>
            <div class="flex items-center gap-2"><span class="inline-block w-2.5 h-2.5 rounded-full" style="background:#a855f7"></span><span>宋元</span></div>
            <div class="flex items-center gap-2"><span class="inline-block w-2.5 h-2.5 rounded-full" style="background:#10b981"></span><span>明清</span></div>
            <div class="flex items-center gap-2"><span class="inline-block w-2.5 h-2.5 rounded-full" style="background:#f97316"></span><span>近代</span></div>
            <div class="flex items-center gap-2"><span class="inline-block w-2.5 h-2.5 rounded-full" style="background:#eab308"></span><span>现代</span></div>
          </div>
          <div id="mapToolbar" class="hidden items-center gap-2 text-[11px] text-white/70">
            <label class="inline-flex items-center gap-1 select-none cursor-pointer">
              <input id="onlyActiveMarkers" type="checkbox" class="accent-white/70" />
              <span>仅显示时间窗</span>
            </label>
            <button id="focusPerson" class="px-2 py-1 rounded-lg bg-white/10 border border-white/15 text-white/70 hover:bg-white/15">定位人物</button>
            <button id="resetMap" class="px-2 py-1 rounded-lg bg-white/10 border border-white/15 text-white/70 hover:bg-white/15">重置视图</button>
          </div>
        </div>

        <div class="relative px-3 pb-3 overflow-hidden">
          <div id="tabTrack" class="flex w-[200%]" style="transform: translateX(0%); transition: transform 720ms cubic-bezier(0.22, 1, 0.36, 1); will-change: transform;">
            <div class="w-1/2 pr-3 relative">
              <div class="rounded-xl overflow-hidden border border-white/10">
                <canvas id="c" width="980" height="460"></canvas>
              </div>
              <div class="absolute right-5 top-5 z-10 flex items-center gap-2">
                <button id="resetGraph" class="px-2 py-1 rounded-lg bg-white/10 border border-white/15 text-white/70 hover:bg-white/15 text-[11px]">重置视图</button>
              </div>
              <div class="absolute left-5 top-5 z-10 flex flex-col gap-2 text-[11px] text-white/80">
              </div>
              <div id="tip" class="tooltip hidden"></div>
            </div>
            <div class="w-1/2 pl-3 relative">
              <div id="chinaMap" class="rounded-xl overflow-hidden border border-white/10" style="height:460px;"></div>
            </div>
          </div>
        </div>

        <div class="px-6 pb-6">
          <div id="provinceCurvePanel" class="hidden mb-3 px-3 py-2 rounded-xl bg-black/40 border border-white/10 backdrop-blur-sm">
            <div class="flex items-center justify-between text-[11px]">
              <div class="text-white/80 font-bold">时间窗内省份名人 Top5</div>
              <div class="text-white/55">仅中国｜柱状图</div>
            </div>
            <div id="provinceBars" class="mt-2 max-h-[160px] overflow-y-auto pr-1" style="scrollbar-width: thin;"></div>
          </div>
          <div class="range-rail relative px-3 py-3">
            <div class="absolute left-3 right-3 top-2 h-[12px] rounded-lg band flex items-start justify-between px-2 pt-[1px] text-[10px] text-white/55" id="bands"></div>
            <div id="ticks" class="absolute left-3 right-3 top-1/2 -translate-y-1/2 h-[34px] rounded-xl bg-white/5 border border-white/10 ticks"></div>
            <div id="maskL" class="absolute top-1/2 -translate-y-1/2 h-[34px] rounded-xl range-mask"></div>
            <div id="maskR" class="absolute top-1/2 -translate-y-1/2 h-[34px] rounded-xl range-mask"></div>
            <div id="sel" class="absolute top-1/2 -translate-y-1/2 h-[34px] rounded-xl range-sel"></div>
            <div id="lifeBar" class="absolute top-1/2 -translate-y-1/2 h-[6px] rounded-full bg-white/15 border border-white/20 hidden"></div>
            <div id="mBirth" class="absolute top-1/2 -translate-y-1/2 h-[34px] w-[2px] bg-emerald-300/70 hidden"></div>
            <div id="mDeath" class="absolute top-1/2 -translate-y-1/2 h-[34px] w-[2px] bg-rose-300/70 hidden"></div>
            <div id="h1" class="handle absolute top-1/2 -translate-y-1/2"></div>
            <div id="h2" class="handle absolute top-1/2 -translate-y-1/2"></div>
            <div class="absolute left-5 bottom-2 text-[10px] text-white/55" id="minLabel"></div>
            <div class="absolute right-5 bottom-2 text-[10px] text-white/55 text-right" id="maxLabel"></div>
            <div class="absolute left-1/2 -translate-x-1/2 bottom-2 text-[10px] text-white/55" id="midLabel"></div>
          </div>
          <div class="flex items-center justify-between mt-2 text-[11px] text-white/55">
            <div class="flex items-center gap-2">
              <span>起：</span>
              <input id="startYearInput" class="w-24 px-2 py-1 rounded-lg bg-white/10 border border-white/15 text-white/80 outline-none focus:ring-2 focus:ring-white/10" type="number" />
            </div>
            <div>窗口跨度：约 <span id="spanYear">-</span> 年</div>
            <div class="flex items-center gap-2">
              <span>止：</span>
              <input id="endYearInput" class="w-24 px-2 py-1 rounded-lg bg-white/10 border border-white/15 text-white/80 outline-none focus:ring-2 focus:ring-white/10" type="number" />
            </div>
          </div>
          <div class="flex flex-wrap items-center justify-center gap-2 mt-3 text-[11px] text-white/60" id="presetBar">
            <button data-preset="all" class="px-2 py-1 rounded-lg bg-white/10 border border-white/15 hover:bg-white/15">全部</button>
            <button data-preset="tang" class="px-2 py-1 rounded-lg bg-white/10 border border-white/15 hover:bg-white/15">唐</button>
            <button data-preset="song" class="px-2 py-1 rounded-lg bg-white/10 border border-white/15 hover:bg-white/15">宋</button>
            <button data-preset="mingqing" class="px-2 py-1 rounded-lg bg-white/10 border border-white/15 hover:bg-white/15">明清</button>
            <button data-preset="modern" class="px-2 py-1 rounded-lg bg-white/10 border border-white/15 hover:bg-white/15">近代</button>
            <button data-preset="contemporary" class="px-2 py-1 rounded-lg bg-white/10 border border-white/15 hover:bg-white/15">现代</button>
          </div>
        </div>
      </div>

    </div>

    <script>
      const DATA_FILE = "{data_file}";
      const $q = document.getElementById("q");
      const $go = document.getElementById("go");
      const $c = document.getElementById("c");
      const ctx = $c.getContext("2d");
      const $tip = document.getElementById("tip");
      const $h1 = document.getElementById("h1");
      const $h2 = document.getElementById("h2");
      const $sel = document.getElementById("sel");
      const $maskL = document.getElementById("maskL");
      const $maskR = document.getElementById("maskR");
      const $rail = $sel.parentElement;
      const $bands = document.getElementById("bands");
      const $mBirth = document.getElementById("mBirth");
      const $mDeath = document.getElementById("mDeath");
      const $lifeBar = document.getElementById("lifeBar");
      const $ticks = document.getElementById("ticks");
      const $activeCount = document.getElementById("activeCount");
      const $coordCount = document.getElementById("coordCount");
      const $spanYear = document.getElementById("spanYear");
      const $startYearInput = document.getElementById("startYearInput");
      const $endYearInput = document.getElementById("endYearInput");
      const $minLabel = document.getElementById("minLabel");
      const $maxLabel = document.getElementById("maxLabel");
      const $midLabel = document.getElementById("midLabel");
      const $tabTrack = document.getElementById("tabTrack");
      const $tabGraph = document.getElementById("tabGraph");
      const $tabMap = document.getElementById("tabMap");
      const $chinaMap = document.getElementById("chinaMap");
      const $provinceCurvePanel = document.getElementById("provinceCurvePanel");
      const $provinceBars = document.getElementById("provinceBars");
      const $genStatus = document.getElementById("genStatus");
      const $resetGraph = document.getElementById("resetGraph");
      const $resetMap = document.getElementById("resetMap");
      const $onlyActiveMarkers = document.getElementById("onlyActiveMarkers");
      const $focusPerson = document.getElementById("focusPerson");
      const $mapStyle = document.getElementById("mapStyle");
      const $mapToolbar = document.getElementById("mapToolbar");
      const $presetBar = document.getElementById("presetBar");

      const W = $c.width;
      const H = $c.height;
      const pad = 18;

      const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
      const lerp = (a, b, t) => a + (b - a) * t;
      const hash = (s) => {{
        let h = 2166136261;
        for (let i=0;i<s.length;i++) {{ h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }}
        return (h >>> 0);
      }};
      const rand01 = (seed) => {{
        let x = seed >>> 0;
        x ^= x << 13; x >>>= 0;
        x ^= x >> 17; x >>>= 0;
        x ^= x << 5; x >>>= 0;
        return (x >>> 0) / 4294967296;
      }};

      const colorByYear = (y) => {{
        if (y == null) return "rgba(255,255,255,0.75)";
        if (y < 0) return "#22c55e";
        if (y < 220) return "#ef4444";
        if (y < 589) return "#60a5fa";
        if (y < 618) return "#f59e0b";
        if (y < 907) return "#fb7185";
        if (y < 1279) return "#a855f7";
        if (y < 1644) return "#10b981";
        if (y < 1840) return "#10b981";
        if (y < 1911) return "#f97316";
        return "#eab308";
      }};

      const hexToRgba = (hex, a) => {{
        const s = String(hex || "").trim();
        const alpha = Number.isFinite(Number(a)) ? Number(a) : 1;
        if (!s.startsWith("#")) return s;
        const h = s.slice(1);
        if (!(h.length === 3 || h.length === 6)) return s;
        const full = h.length === 3 ? (h[0] + h[0] + h[1] + h[1] + h[2] + h[2]) : h;
        const r = parseInt(full.slice(0, 2), 16);
        const g = parseInt(full.slice(2, 4), 16);
        const b = parseInt(full.slice(4, 6), 16);
        if (![r, g, b].every((x) => Number.isFinite(x))) return s;
        return `rgba(${{r}},${{g}},${{b}},${{alpha}})`;
      }};

      const _isInsideChina = (lat, lng) => {{
        const la = Number(lat);
        const lo = Number(lng);
        if (!Number.isFinite(la) || !Number.isFinite(lo)) return false;
        return la >= 17.5 && la <= 55.5 && lo >= 72.0 && lo <= 136.5;
      }};
      const _PI = Math.PI;
      const _A = 6378245.0;
      const _EE = 0.00669342162296594323;
      const _transformLat = (x, y) => {{
        let ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * Math.sqrt(Math.abs(x));
        ret += (20.0 * Math.sin(6.0 * x * _PI) + 20.0 * Math.sin(2.0 * x * _PI)) * 2.0 / 3.0;
        ret += (20.0 * Math.sin(y * _PI) + 40.0 * Math.sin(y / 3.0 * _PI)) * 2.0 / 3.0;
        ret += (160.0 * Math.sin(y / 12.0 * _PI) + 320.0 * Math.sin(y * _PI / 30.0)) * 2.0 / 3.0;
        return ret;
      }};
      const _transformLng = (x, y) => {{
        let ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * Math.sqrt(Math.abs(x));
        ret += (20.0 * Math.sin(6.0 * x * _PI) + 20.0 * Math.sin(2.0 * x * _PI)) * 2.0 / 3.0;
        ret += (20.0 * Math.sin(x * _PI) + 40.0 * Math.sin(x / 3.0 * _PI)) * 2.0 / 3.0;
        ret += (150.0 * Math.sin(x / 12.0 * _PI) + 300.0 * Math.sin(x / 30.0 * _PI)) * 2.0 / 3.0;
        return ret;
      }};
      const wgs84ToGcj02 = (lat, lng) => {{
        const la = Number(lat);
        const lo = Number(lng);
        if (!_isInsideChina(la, lo)) return {{ lat: la, lng: lo }};
        let dLat = _transformLat(lo - 105.0, la - 35.0);
        let dLng = _transformLng(lo - 105.0, la - 35.0);
        const radLat = (la / 180.0) * _PI;
        let magic = Math.sin(radLat);
        magic = 1 - _EE * magic * magic;
        const sqrtMagic = Math.sqrt(magic);
        dLat = (dLat * 180.0) / (((_A * (1 - _EE)) / (magic * sqrtMagic)) * _PI);
        dLng = (dLng * 180.0) / ((_A / sqrtMagic) * Math.cos(radLat) * _PI);
        return {{ lat: la + dLat, lng: lo + dLng }};
      }};
      const gcj02ToWgs84 = (lat, lng) => {{
        const la = Number(lat);
        const lo = Number(lng);
        if (!_isInsideChina(la, lo)) return {{ lat: la, lng: lo }};
        const mg = wgs84ToGcj02(la, lo);
        return {{ lat: la * 2.0 - mg.lat, lng: lo * 2.0 - mg.lng }};
      }};

      const esc = (s) => String(s || "").replace(/[&<>\"']/g, (c) => ({{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}})[c]);
      const stripMd = (s) => String(s || "").replace(/\*\*/g, "").replace(/__/g, "");
      const stripOuterQuotes = (s) => {{
        let t = String(s || "").trim();
        t = t.replace(/^[“"‘'「『]+/g, "").replace(/[”"’'」』]+$/g, "");
        return t.trim();
      }};
      const stripParenChars = (s) => String(s || "").replace(/[（）()]/g, "").trim();
      const formatBirthplace = (ancient, modern) => {{
        const a = stripParenChars(String(ancient || "").trim());
        const m0 = stripParenChars(String(modern || "").trim());
        const m = m0.replace(/^今\s*/g, "今").trim();
        if (a && m && a !== m) return a + " · " + m;
        return a || m || "";
      }};

      let nodes = [];
      let edges = [];
      let edgesAll = [];
      let neigh = [];
      let edgeMeta = new Map();
      let minYear = -800;
      let maxYear = 1840;
      let startYear = 0;
      let endYear = 1840;
      let dragMode = "";
      let dragStartX = 0;
      let dragStartA = 0;
      let dragStartB = 0;
      let brushStartX = 0;
      let brushStartYear = 0;
      let hover = null;
      let selectedIdx = -1;
      let selected = null;
      let spotlightIdx = -1;
      let spotlight = null;
      let _clickTimer = null;

      let camScale = 1.0;
      let camOffX = 0.0;
      let camOffY = 0.0;

      const worldToScreen = (x, y) => {{
        return {{
          x: x * camScale + camOffX,
          y: y * camScale + camOffY,
        }};
      }};
      const screenToWorld = (x, y) => {{
        return {{
          x: (x - camOffX) / camScale,
          y: (y - camOffY) / camScale,
        }};
      }};
      const setSelected = (n) => {{
        if (!n || typeof n._idx !== "number") {{
          selectedIdx = -1;
          selected = null;
          spotlightIdx = -1;
          spotlight = null;
          showTip(null);
          setLifeBar(null);
          draw();
          updateMapMarkers();
          return;
        }}
        selectedIdx = n._idx;
        selected = n;
        spotlightIdx = -1;
        spotlight = null;
        showTip(null);
        setLifeBar(selected);
        draw();
        updateMapMarkers();
      }};
      const setSpotlight = (n, clientX, clientY) => {{
        if (!n || typeof n._idx !== "number") {{
          spotlightIdx = -1;
          spotlight = null;
          setLifeBar(selected);
          draw();
          updateMapMarkers();
          return;
        }}
        spotlightIdx = n._idx;
        spotlight = n;
        showTip(n, clientX, clientY);
        setLifeBar(spotlight);
        draw();
        updateMapMarkers();
      }};

      const toT = (year) => (year - minYear) / (maxYear - minYear);
      const fromT = (t) => Math.round(minYear + t * (maxYear - minYear));

      const formatYear = (y) => {{
        const yy = Math.round(Number(y));
        if (!Number.isFinite(yy)) return "";
        if (yy === 0) return "公元1";
        if (yy < 0) return `前${{-yy}}`;
        return String(yy);
      }};

      const formatYearRange = (a, b) => {{
        const hasA = a != null && Number.isFinite(Number(a));
        const hasB = b != null && Number.isFinite(Number(b));
        const dash = " \u2013 ";
        if (hasA && hasB) return `${{formatYear(a)}}${{dash}}${{formatYear(b)}}`;
        if (hasA) return `${{formatYear(a)}}${{dash}}?`;
        if (hasB) return `?${{dash}}${{formatYear(b)}}`;
        return "未知";
      }};

      const mainYear = (n) => {{
        if (!n) return null;
        const ty = n.time_year;
        if (typeof ty === "number" && Number.isFinite(ty)) return ty;
        const by = n.birth_year;
        if (typeof by === "number" && Number.isFinite(by)) return by;
        const dy = n.death_year;
        if (typeof dy === "number" && Number.isFinite(dy)) return dy;
        return null;
      }};

      const pickTickStep = (span) => {{
        const s = Math.max(1, Math.round(Number(span) || 1));
        if (s <= 60) return 5;
        if (s <= 120) return 10;
        if (s <= 240) return 20;
        if (s <= 500) return 50;
        if (s <= 900) return 100;
        if (s <= 1600) return 200;
        return 500;
      }};

      const formatTickLabel = (y, span, step) => {{
        let yy = Math.round(Number(y));
        if (!Number.isFinite(yy)) return "";
        if (yy === 0) yy = 1;
        if (span >= 1200 || step >= 200) {{
          if (yy < 0) {{
            const c = Math.floor(((-yy) - 1) / 100) + 1;
            return `前${{c}}世纪`;
          }}
          const c = Math.floor((yy - 1) / 100) + 1;
          return `${{c}}世纪`;
        }}
        return formatYear(yy);
      }};

      const renderTicks = () => {{
        if (!$ticks) return;
        const span = Math.max(1, endYear - startYear);
        const step = pickTickStep(span);
        const r = $rail.getBoundingClientRect();
        const w = r.width || 1;
        const x0 = clamp(toT(startYear), 0, 1) * w;
        const x1 = clamp(toT(endYear), 0, 1) * w;
        const ww = Math.max(1, x1 - x0);
        const density = Math.max(1, Math.floor((span / step)));
        const pxPerStep = (ww * step) / span;
        const maxLabels = 9;
        const minPxPerLabel = 56;
        let labelEvery = Math.max(1, Math.ceil(density / maxLabels));
        labelEvery = Math.max(labelEvery, Math.ceil(minPxPerLabel / Math.max(1, pxPerStep)));
        let y0 = Math.floor(startYear / step) * step;
        if (y0 > startYear) y0 -= step;
        let html = "";
        let idx = 0;
        for (let y = y0; y <= endYear + step; y += step) {{
          if (y < startYear - step) continue;
          if (y > endYear + step) break;
          const t = clamp((y - startYear) / span, 0, 1);
          const left = x0 + (t * ww);
          const major = (idx % labelEvery) === 0;
          const h = major ? 16 : 10;
          const op = major ? 0.32 : 0.14;
          html += `<div style="position:absolute;left:${{left.toFixed(2)}}px;bottom:6px;width:1px;height:${{h}}px;background:rgba(255,255,255,${{op}})"></div>`;
          if (major) {{
            const lab = formatTickLabel(y, span, step);
            if (lab) {{
              html += `<div style="position:absolute;left:${{left.toFixed(2)}}px;top:2px;transform:translateX(-50%);font-size:10px;color:rgba(255,255,255,0.56);white-space:nowrap">${{esc(lab)}}</div>`;
            }}
          }}
          idx += 1;
        }}
        $ticks.innerHTML = html;
      }};

      const setLifeBar = (n) => {{
        if (!$lifeBar) return;
        const pick = n && typeof n === "object" ? n : null;
        const b = pick ? pick.birth_year : null;
        const d = pick ? pick.death_year : null;
        if (b == null && d == null) {{
          $lifeBar.classList.add("hidden");
          return;
        }}
        const r = $rail.getBoundingClientRect();
        const w = r.width || 1;
        let a = (b != null) ? b : d;
        let z = (d != null) ? d : b;
        if (a == null || z == null) {{
          $lifeBar.classList.add("hidden");
          return;
        }}
        if (a > z) {{ const t = a; a = z; z = t; }}
        const t1 = clamp(toT(a), 0, 1);
        const t2 = clamp(toT(z), 0, 1);
        const minW = 6 / w;
        const tt2 = Math.max(t2, t1 + minW);
        $lifeBar.style.left = `${{(t1 * 100).toFixed(4)}}%`;
        $lifeBar.style.width = `${{((tt2 - t1) * 100).toFixed(4)}}%`;
        $lifeBar.classList.remove("hidden");
      }};

      const zoomToFitWindowNodes = () => {{
        if (!nodes || !nodes.length) return;
        let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
        let c = 0;
        for (const n of nodes) {{
          if (!inWindow(n)) continue;
          if (typeof n.x !== "number" || typeof n.y !== "number") continue;
          minX = Math.min(minX, n.x);
          minY = Math.min(minY, n.y);
          maxX = Math.max(maxX, n.x);
          maxY = Math.max(maxY, n.y);
          c += 1;
        }}
        if (c < 2 || !Number.isFinite(minX) || !Number.isFinite(minY) || !Number.isFinite(maxX) || !Number.isFinite(maxY)) return;
        const bw = Math.max(10, maxX - minX);
        const bh = Math.max(10, maxY - minY);
        const margin = 36;
        const sx = (W - margin * 2) / bw;
        const sy = (H - margin * 2) / bh;
        camScale = clamp(Math.min(sx, sy), 0.35, 3.8);
        camOffX = (W - (minX + maxX) * camScale) / 2;
        camOffY = (H - (minY + maxY) * camScale) / 2;
      }};

      const setYearInputs = () => {{
        if ($startYearInput) $startYearInput.value = String(startYear);
        if ($endYearInput) $endYearInput.value = String(endYear);
      }};
      const applyYearInputs = () => {{
        const a = $startYearInput ? Number($startYearInput.value) : NaN;
        const b = $endYearInput ? Number($endYearInput.value) : NaN;
        if (!Number.isFinite(a) || !Number.isFinite(b)) {{
          setYearInputs();
          return;
        }}
        let na = Math.round(a);
        let nb = Math.round(b);
        if (na > nb) {{ const t = na; na = nb; nb = t; }}
        na = clamp(na, minYear, maxYear);
        nb = clamp(nb, minYear, maxYear);
        if (na === nb) nb = clamp(na + 1, minYear, maxYear);
        startYear = na;
        endYear = nb;
        setHandles();
        updateActiveCount();
        updateCoordCount();
        updateMapMarkers();
        draw();
      }};

      const handlePosPx = () => {{
        const r = $rail.getBoundingClientRect();
        const w = r.width || 1;
        const x1 = toT(startYear) * w;
        const x2 = toT(endYear) * w;
        return {{ x1, x2, w }};
      }};

      const setHoverMarkers = (n) => {{
        if (!n) {{
          $mBirth.classList.add("hidden");
          $mDeath.classList.add("hidden");
          return;
        }}
        const r = $rail.getBoundingClientRect();
        const w = r.width || 1;
        const show = (el, year) => {{
          if (year == null) {{
            el.classList.add("hidden");
            return;
          }}
          const t = clamp(toT(year), 0, 1);
          el.style.left = `calc(${{(t * 100).toFixed(4)}}% + 3px)`;
          el.classList.remove("hidden");
        }};
        show($mBirth, n.birth_year);
        show($mDeath, n.death_year);
      }};

      const setHandles = () => {{
        const t1 = clamp(toT(startYear), 0, 1);
        const t2 = clamp(toT(endYear), 0, 1);
        const leftPct = (t1 * 100).toFixed(4) + "%";
        const rightPct = (t2 * 100).toFixed(4) + "%";
        $h1.style.left = `calc(${{leftPct}} - 7px)`;
        $h2.style.left = `calc(${{rightPct}} - 7px)`;
        $sel.style.left = leftPct;
        $sel.style.width = ((t2 - t1) * 100).toFixed(4) + "%";
        if ($maskL) {{
          $maskL.style.left = "0%";
          $maskL.style.width = leftPct;
        }}
        if ($maskR) {{
          $maskR.style.left = rightPct;
          $maskR.style.width = ((1 - t2) * 100).toFixed(4) + "%";
        }}
        $spanYear.textContent = String(Math.max(0, endYear - startYear));
        setYearInputs();
        $minLabel.textContent = formatYear(minYear);
        $maxLabel.textContent = formatYear(maxYear);
        $midLabel.textContent = formatYear(Math.round((startYear + endYear) / 2));
        renderTicks();
        setLifeBar(spotlight || selected);
        persistTimeWindow();
        scheduleMapFit();
        updateProvinceBars();
      }};

      const inWindow = (n) => {{
        const y = mainYear(n);
        if (y == null) return false;
        return y >= startYear && y <= endYear;
      }};

      const updateActiveCount = () => {{
        let c = 0;
        for (const n of nodes) {{
          if (inWindow(n)) c += 1;
        }}
        if ($activeCount) $activeCount.textContent = String(c);
      }};

      const updateCoordCount = () => {{
        let c = 0;
        for (const n of nodes) {{
          if (typeof n.birth_lat === "number" && typeof n.birth_lng === "number") c += 1;
        }}
        if ($coordCount) $coordCount.textContent = `${{c}}/${{nodes.length}}`;
      }};

      const provinceOf = (n) => {{
        const raw = String((n && (n.birthplace_modern || n.birthplace || n.birthplace_raw)) || "").trim();
        if (!raw) return "";
        const s = raw.replace(/^今\\s*/g, "");
        const m = s.match(/(北京市|天津市|上海市|重庆市|香港特别行政区|澳门特别行政区|台湾省|内蒙古自治区|广西壮族自治区|宁夏回族自治区|新疆维吾尔自治区|西藏自治区|黑龙江省|吉林省|辽宁省|河北省|山西省|陕西省|山东省|河南省|江苏省|浙江省|安徽省|江西省|福建省|广东省|海南省|四川省|贵州省|云南省|湖北省|湖南省|甘肃省|青海省)/);
        if (m) {{
          const t = String(m[1] || "").trim();
          if (t.endsWith("省")) return t.slice(0, -1);
          if (t.endsWith("市")) return t.slice(0, -1);
          if (t.endsWith("特别行政区")) return t.replace(/特别行政区$/, "");
          if (t.endsWith("自治区")) return t.replace(/自治区$/, "");
          return t;
        }}
        const m2 = s.match(/(北京|天津|上海|重庆|香港|澳门|台湾|内蒙古|广西|宁夏|新疆|西藏|黑龙江|吉林|辽宁|河北|山西|陕西|山东|河南|江苏|浙江|安徽|江西|福建|广东|海南|四川|贵州|云南|湖北|湖南|甘肃|青海)/);
        if (m2) return String(m2[1] || "").trim();
        return "";
      }};

      const _curvePathFromPoints = (pts) => {{
        if (!Array.isArray(pts) || pts.length < 2) return "";
        const p = pts.map((x) => [Number(x[0]), Number(x[1])]).filter((x) => Number.isFinite(x[0]) && Number.isFinite(x[1]));
        if (p.length < 2) return "";
        const cr = (p0, p1, p2, p3) => {{
          const x1 = p1[0] + (p2[0] - p0[0]) / 6;
          const y1 = p1[1] + (p2[1] - p0[1]) / 6;
          const x2 = p2[0] - (p3[0] - p1[0]) / 6;
          const y2 = p2[1] - (p3[1] - p1[1]) / 6;
          return [x1, y1, x2, y2, p2[0], p2[1]];
        }};
        let d = `M ${{p[0][0].toFixed(2)}} ${{p[0][1].toFixed(2)}}`;
        for (let i = 0; i < p.length - 1; i += 1) {{
          const p0 = p[Math.max(0, i - 1)];
          const p1 = p[i];
          const p2 = p[i + 1];
          const p3 = p[Math.min(p.length - 1, i + 2)];
          const c = cr(p0, p1, p2, p3);
          d += ` C ${{c[0].toFixed(2)}} ${{c[1].toFixed(2)}}, ${{c[2].toFixed(2)}} ${{c[3].toFixed(2)}}, ${{c[4].toFixed(2)}} ${{c[5].toFixed(2)}}`;
        }}
        return d;
      }};

      const updateProvinceBars = () => {{
        if (!$provinceBars) return;
        const counts = new Map();
        let total = 0;
        for (const n of nodes) {{
          if (!inWindow(n)) continue;
          const prov = provinceOf(n);
          if (!prov) continue;
          total += 1;
          counts.set(prov, (counts.get(prov) || 0) + 1);
        }}
        const items = Array.from(counts.entries()).sort((a, b) => (b[1] - a[1]) || String(a[0]).localeCompare(String(b[0])));
        const top = items.slice(0, 5);
        const maxV = top.reduce((m, it) => Math.max(m, Number(it[1] || 0)), 1) || 1;
        const parts = [];
        for (const [prov, v0] of top) {{
          const v = Number(v0 || 0);
          const pct = Math.max(2, Math.round((v / maxV) * 100));
          parts.push(
            '<div class=\"flex items-center gap-2 mb-1\">' +
              '<div class=\"w-10 text-[11px] text-white/70 truncate\" title=\"' + esc(String(prov)) + '\">' + esc(String(prov)) + '</div>' +
              '<div class=\"flex-1\">' +
                '<div class=\"h-[10px] rounded-full bg-white/10 border border-white/10 overflow-hidden\">' +
                  '<div class=\"h-full rounded-full\" style=\"width:' + String(pct) + '%;background:rgba(34,197,94,0.70)\"></div>' +
                '</div>' +
              '</div>' +
              '<div class=\"w-10 text-right text-[11px] text-white/70\">' + esc(String(v)) + '</div>' +
            '</div>'
          );
        }}
        const footer = '<div class=\"mt-1 text-[10px] text-white/50\">中国范围内：' + esc(String(total)) + ' 人</div>';
        $provinceBars.innerHTML = (parts.join('') || '<div class=\"text-[11px] text-white/55\">当前时间窗无中国人物</div>') + footer;
      }};

      const renderBands = () => {{
        if (!$bands) return;
        const bands = [
          {{ name: "春秋战国", a: -800, b: -221 }},
          {{ name: "秦", a: -221, b: -206 }},
          {{ name: "汉", a: -206, b: 220 }},
          {{ name: "魏晋南北", a: 220, b: 589 }},
          {{ name: "隋", a: 581, b: 618 }},
          {{ name: "唐", a: 618, b: 907 }},
          {{ name: "宋", a: 960, b: 1279 }},
          {{ name: "元", a: 1271, b: 1368 }},
          {{ name: "明", a: 1368, b: 1644 }},
          {{ name: "清", a: 1644, b: 1840 }},
          {{ name: "近代", a: 1840, b: 1911 }},
          {{ name: "现代", a: 1911, b: 2000 }},
        ];
        const bandColors = [
          "rgba(56,189,248,0.12)",
          "rgba(34,197,94,0.10)",
          "rgba(239,68,68,0.10)",
          "rgba(96,165,250,0.10)",
          "rgba(245,158,11,0.10)",
          "rgba(168,85,247,0.10)",
          "rgba(16,185,129,0.10)",
          "rgba(249,115,22,0.10)",
          "rgba(234,179,8,0.10)",
        ];
        const pieces = [];
        for (let i = 0; i < bands.length; i++) {{
          const b = bands[i];
          const l = clamp(toT(b.a), 0, 1);
          const r = clamp(toT(b.b), 0, 1);
          if (r <= 0 || l >= 1) continue;
          const left = (l * 100).toFixed(4) + "%";
          const width = ((r - l) * 100).toFixed(4) + "%";
          const bg = bandColors[i % bandColors.length];
          pieces.push(`<div style="position:absolute;left:${{left}};width:${{width}};top:0;bottom:0;display:flex;align-items:center;justify-content:center;overflow:visible;background:${{bg}};border-right:1px solid rgba(255,255,255,0.12);"><span style="white-space:nowrap;padding:0 6px;text-shadow:0 1px 0 rgba(0,0,0,0.25)">${{esc(b.name)}}</span></div>`);
        }}
        $bands.innerHTML = pieces.join("");
        $bands.style.position = "absolute";
      }};

      const draw = () => {{
        ctx.clearRect(0, 0, W, H);
        ctx.fillStyle = "rgba(0,0,0,0)";
        ctx.fillRect(0, 0, W, H);

        ctx.globalCompositeOperation = "source-over";
        const selectedSet = new Set();
        if (selectedIdx >= 0 && neigh[selectedIdx]) {{
          selectedSet.add(selectedIdx);
          for (const j of (neigh[selectedIdx] || [])) selectedSet.add(j);
        }}
        if (edges.length) {{
          ctx.lineWidth = 1;
          for (const e of edges) {{
            const typ = String((e && e.type) || "bio").trim().toLowerCase();
            const conf = Number((e && (e.confidence ?? e.conf)) ?? 0);
            const c = Number.isFinite(conf) ? Math.max(0, Math.min(1, conf)) : 0;
            const baseA = 0.05 + c * 0.22;
            ctx.globalAlpha = Math.max(0.04, Math.min(0.20, baseA));
            ctx.strokeStyle = typ === "manual"
              ? "rgba(147,197,253,0.70)"
              : (typ === "same_book" ? "rgba(148,163,184,0.55)" : "rgba(255,255,255,0.55)");
            const a = nodes[e.a];
            const b = nodes[e.b];
            if (!a || !b) continue;
            const pa = worldToScreen(a.x, a.y);
            const pb = worldToScreen(b.x, b.y);
            ctx.beginPath();
            ctx.moveTo(pa.x, pa.y);
            ctx.lineTo(pb.x, pb.y);
            ctx.stroke();
          }}
          ctx.globalAlpha = 1.0;

          for (const e of edges) {{
            const typ = String((e && e.type) || "bio").trim().toLowerCase();
            const conf = Number((e && (e.confidence ?? e.conf)) ?? 0);
            const c = Number.isFinite(conf) ? Math.max(0, Math.min(1, conf)) : 0;
            const baseA = 0.12 + c * 0.35;
            ctx.globalAlpha = Math.max(0.10, Math.min(0.38, baseA));
            ctx.strokeStyle = typ === "manual"
              ? "rgba(147,197,253,0.92)"
              : (typ === "same_book" ? "rgba(203,213,225,0.78)" : "rgba(255,255,255,0.78)");
            const a = nodes[e.a];
            const b = nodes[e.b];
            if (!a || !b) continue;
            if (!(inWindow(a) && inWindow(b))) continue;
            const pa = worldToScreen(a.x, a.y);
            const pb = worldToScreen(b.x, b.y);
            ctx.beginPath();
            ctx.moveTo(pa.x, pa.y);
            ctx.lineTo(pb.x, pb.y);
            ctx.stroke();
          }}
          ctx.globalAlpha = 1.0;

          const hiIdx = selectedIdx >= 0
            ? selectedIdx
            : (spotlightIdx >= 0
                ? spotlightIdx
                : (hover && typeof hover._idx === "number" ? hover._idx : -1));
          if (hiIdx >= 0) {{
            const ns = neigh[hiIdx] || [];
            ctx.strokeStyle = "rgba(34,197,94,0.85)";
            ctx.lineWidth = 1.8;
            ctx.globalAlpha = 0.70;
            for (const j of ns) {{
              const a = nodes[hiIdx];
              const b = nodes[j];
              if (!a || !b) continue;
              if (!(inWindow(a) && inWindow(b))) continue;
              const pa = worldToScreen(a.x, a.y);
              const pb = worldToScreen(b.x, b.y);
              ctx.beginPath();
              ctx.moveTo(pa.x, pa.y);
              ctx.lineTo(pb.x, pb.y);
              ctx.stroke();
            }}
            ctx.globalAlpha = 1.0;
            ctx.lineWidth = 1;

            if (selectedIdx >= 0) {{
              ctx.save();
              ctx.font = "11px system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial";
              ctx.textAlign = "center";
              ctx.textBaseline = "middle";
              for (let ii = 0; ii < ns.length; ii += 1) {{
                const j = ns[ii];
                const a = nodes[hiIdx];
                const b = nodes[j];
                if (!a || !b) continue;
                if (!(inWindow(a) && inWindow(b))) continue;
                const lo = Math.min(hiIdx, j);
                const hi = Math.max(hiIdx, j);
                const meta = edgeMeta.get(`${{lo}},${{hi}}`) || null;
                const label = meta && meta.label ? String(meta.label) : "";
                if (!label) continue;
                const pa = worldToScreen(a.x, a.y);
                const pb = worldToScreen(b.x, b.y);
                const mx = (pa.x + pb.x) / 2;
                const my = (pa.y + pb.y) / 2;
                const dx = pb.x - pa.x;
                const dy = pb.y - pa.y;
                const len = Math.hypot(dx, dy) || 1;
                const nx = (-dy) / len;
                const ny = dx / len;
                const sign = (ii % 2 === 0) ? 1 : -1;
                const ox = nx * 10 * sign;
                const oy = ny * 10 * sign;
                const x = mx + ox;
                const y = my + oy;
                ctx.lineWidth = 3;
                ctx.strokeStyle = "rgba(15,23,42,0.55)";
                ctx.fillStyle = "rgba(255,255,255,0.88)";
                try {{ ctx.strokeText(label, x, y); }} catch (_) {{}}
                ctx.fillText(label, x, y);
              }}
              ctx.restore();
            }}
          }}
        }}

        ctx.globalCompositeOperation = "source-over";
        for (const n of nodes) {{
          const p = (typeof n.p === "number") ? clamp(n.p, 0, 1) : (inWindow(n) ? 1 : 0);
          const active = p > 0.55;
          let r = (4.4 + p * 2.8) * camScale;
          let alpha = 0.10 + p * 0.88;
          let col = p > 0 ? colorByYear(mainYear(n)) : "rgba(255,255,255,0.30)";
          const i = n._idx;
          const hovered = hover && hover.person === n.person;
          const selectedHere = selected && selected.person === n.person;
          const spotlightHere = (selectedIdx < 0) && spotlight && spotlight.person === n.person;
          if (selectedIdx >= 0) {{
            if (!selectedSet.has(i)) {{
              alpha *= 0.12;
              col = "rgba(255,255,255,0.22)";
            }} else {{
              alpha = Math.max(alpha, 0.70);
            }}
          }}
          if (hovered) {{
            r = 9.2 * camScale;
            alpha = 1.0;
            col = "#fbbf24";
          }}
          if (selectedHere) {{
            r = 10.5 * camScale;
            alpha = 1.0;
            col = "#fbbf24";
          }}
          if (spotlightHere) {{
            r = 11.0 * camScale;
            alpha = 1.0;
            col = "#fbbf24";
          }}
          const pt = worldToScreen(n.x, n.y);
          ctx.beginPath();
          ctx.fillStyle = col;
          ctx.globalAlpha = alpha;
          ctx.arc(pt.x, pt.y, r, 0, Math.PI * 2);
          ctx.fill();
          if (active) {{
            ctx.beginPath();
            ctx.strokeStyle = "rgba(255,255,255,0.22)";
            ctx.globalAlpha = 0.35 + p * 0.35;
            ctx.lineWidth = 1 * camScale;
            ctx.arc(pt.x, pt.y, r + 2.6 * camScale, 0, Math.PI * 2);
            ctx.stroke();
          }}
        }}
        ctx.globalAlpha = 1.0;
        ctx.lineWidth = 1;

        if (hover || (selectedIdx >= 0 && selected)) {{
          const n = hover || selected;
          const pt = worldToScreen(n.x, n.y);
          ctx.beginPath();
          ctx.strokeStyle = "rgba(255,255,255,0.75)";
          ctx.lineWidth = 2 * camScale;
          ctx.arc(pt.x, pt.y, 10 * camScale, 0, Math.PI * 2);
          ctx.stroke();
        }}
      }};

      const reduceMotion = (() => {{
        try {{
          return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        }} catch (_) {{
          return false;
        }}
      }})();

      const animate = (nowMs) => {{
        if (reduceMotion) return;
        const t = (nowMs || 0) * 0.001;
        for (const n of nodes) {{
          const target = inWindow(n) ? 1 : 0;
          if (typeof n.p !== "number") n.p = target;
          n.p = n.p + (target - n.p) * 0.10;
          const seed = hash(n.person || "");
          const ox = Math.sin(t * 0.55 + (seed % 1000) * 0.01) * 2.0 + Math.sin(t * 0.17 + (seed % 97)) * 0.9;
          const oy = Math.cos(t * 0.50 + (seed % 777) * 0.01) * 1.8 + Math.cos(t * 0.19 + (seed % 83)) * 0.8;
          const bx = n.bx != null ? n.bx : n.x;
          const by = n.by != null ? n.by : n.y;
          n.x = clamp(bx + ox, pad, W - pad);
          n.y = clamp(by + oy, pad, H - pad);
        }}
        draw();
        window.requestAnimationFrame(animate);
      }};

      const pickNode = (mx, my) => {{
        const w = screenToWorld(mx, my);
        let best = null;
        let bestD = 999999;
        for (const n of nodes) {{
          const dx = w.x - n.x;
          const dy = w.y - n.y;
          const d = dx*dx + dy*dy;
          const thr = (16 / camScale);
          if (d < bestD && d < thr*thr) {{
            bestD = d;
            best = n;
          }}
        }}
        return best;
      }};

      const showTip = (n, clientX, clientY) => {{
        if (!n) {{
          $tip.classList.add("hidden");
          setHoverMarkers(null);
          return;
        }}
        const years = formatYearRange(n.birth_year, n.death_year);
        const quote = stripMd(String(n.quote || "").trim());
        const review = stripMd(String(n.review || "").trim());
        const tagline = stripOuterQuotes(review || quote);
        const dynasty = String(n.dynasty || "").trim();
        const dline = dynasty ? `<div class="text-white/70 text-[11px] mt-1">时代：${{esc(dynasty)}}</div>` : "";
        const aka = Array.isArray(n.aliases) ? n.aliases.filter((x) => x && String(x).trim()).slice(0, 3).join(" / ") : "";
        const akaline = aka ? `<div class="text-white/70 text-[11px] mt-1">别名：${{esc(aka)}}</div>` : "";
        const foreign = String(n.foreign_name || "").trim();
        const foreignline = foreign ? `<div class="text-white/70 text-[11px] mt-1">外文：${{esc(foreign)}}</div>` : "";
        const tags = Array.isArray(n.domain_tags) ? n.domain_tags.filter((x) => x && String(x).trim()).slice(0, 4).join(" / ") : "";
        const tagline2 = tags ? `<div class="text-white/70 text-[11px] mt-1">领域：${{esc(tags)}}</div>` : "";
        const bp = formatBirthplace(n.birthplace, n.birthplace_modern);
        const bpline = bp ? `<div class="text-white/70 text-[11px] mt-1">籍贯：${{esc(bp)}}</div>` : "";
        const tline = tagline ? `<div class="text-amber-200/95 text-[11px] mt-1 whitespace-pre-wrap">“${{esc(tagline)}}”</div>` : "";
        $tip.innerHTML = `<div class="font-bold text-white/95">${{esc(n.person)}}</div><div class="text-white/70 text-[11px] mt-1">生卒：${{esc(years)}}</div>${{dline}}${{akaline}}${{foreignline}}${{tagline2}}${{bpline}}${{tline}}`;
        const rect = $c.getBoundingClientRect();
        let left = clientX - rect.left + 10;
        let top = clientY - rect.top + 10;
        const tw = 260;
        const th = 146;
        if (left + tw > rect.width - 8) left = Math.max(8, clientX - rect.left - tw - 10);
        if (top + th > rect.height - 8) top = Math.max(8, clientY - rect.top - th - 10);
        $tip.style.left = left + "px";
        $tip.style.top = top + "px";
        $tip.classList.remove("hidden");
        setHoverMarkers(n);
      }};

      const applyEdgeFilters = () => {{
        edgesAll = [];
        edges = [];
        neigh = [];
        edgeMeta = new Map();
        draw();
      }};

      const setGenStatus = (txt) => {{
        if (!$genStatus) return;
        const t = String(txt || "").trim();
        if (!t) {{
          $genStatus.classList.add("hidden");
          $genStatus.textContent = "";
          return;
        }}
        $genStatus.textContent = t;
        $genStatus.classList.remove("hidden");
      }};
      const clearGenTask = () => {{
        try {{ localStorage.removeItem("stellar_gen_task_v1"); }} catch (_) {{}}
      }};
      const setGeneratingUI = (isGenerating) => {{
        const on = !!isGenerating;
        try {{ $go.disabled = on; }} catch (_) {{}}
        try {{
          if (on) {{
            $go.classList.add("opacity-60");
            $go.classList.add("cursor-not-allowed");
          }} else {{
            $go.classList.remove("opacity-60");
            $go.classList.remove("cursor-not-allowed");
          }}
        }} catch (_) {{}}
      }};
      const fetchWithTimeout = (url, ms) => {{
        const controller = new AbortController();
        const id = setTimeout(() => controller.abort(), ms || 12000);
        return fetch(url, {{ cache: "no-store", signal: controller.signal }}).finally(() => clearTimeout(id));
      }};

      const openPerson = (name) => {{
        const q = String(name || "").trim();
        if (!q) return;
        const found = nodes.find((n) => n && String(n.person || "").trim() === q) || null;
        const file = found && found.file ? String(found.file) : "";
        if (file) {{
          window.location.href = "./" + encodeURIComponent(file);
          return;
        }}
        ensurePersonGenerated(q);
      }};
      window.__openPerson = openPerson;

      const pollTask = (taskId, personName) => {{
        const id = String(taskId || "").trim();
        if (!id) return;
        const person = String(personName || "").trim();
        const tick = async () => {{
          let snapshot = null;
          try {{
            const resp = await fetchWithTimeout("./task?id=" + encodeURIComponent(id), 12000);
            snapshot = await resp.json();
          }} catch (_) {{
            snapshot = null;
          }}
          if (!snapshot || snapshot.ok !== true) {{
            setGenStatus("生成任务查询失败，请稍后重试");
            setGeneratingUI(false);
            clearGenTask();
            return;
          }}
          const st = String(snapshot.status || "").trim();
          const queue = snapshot.queue || {{}};
          const pos = queue.position ? String(queue.position) : "";
          const active = queue.active_at_start ? String(queue.active_at_start) : (queue.active ? String(queue.active) : "");
          const limit = queue.limit ? String(queue.limit) : "";
          const progress = Array.isArray(snapshot.progress) ? snapshot.progress : [];
          const last = progress.length ? progress[progress.length - 1] : null;
          const lastTxt = last && last.label ? String(last.label) : "";
          const lastDetail = last && last.detail ? String(last.detail) : "";
          if (st === "queued") {{
            const qtxt = (pos && limit) ? ("排队中（" + pos + "/" + limit + "）") : "排队中";
            setGenStatus("未找到本地人物「" + person + "」，正在生成，请稍候… " + qtxt);
            setGeneratingUI(true);
            return setTimeout(tick, 900);
          }}
          if (st === "running") {{
            const ptxt = lastDetail ? (lastTxt + "：" + lastDetail) : lastTxt;
            const head = "未找到本地人物「" + person + "」，正在生成，请稍候…";
            const tail = ptxt ? ("（" + ptxt + "）") : (active && limit ? ("（执行中 " + active + "/" + limit + "）") : "");
            setGenStatus(head + tail);
            setGeneratingUI(true);
            return setTimeout(tick, 900);
          }}
          if (st === "failed") {{
            setGenStatus("生成失败：" + String(snapshot.error || "未知错误"));
            setGeneratingUI(false);
            clearGenTask();
            return;
          }}
          if (st === "completed") {{
            const result = snapshot.result || {{}};
            const ok = result && result.ok === true;
            if (!ok) {{
              setGenStatus("生成失败：" + String(result.conclusion || snapshot.error || "未生成成功"));
              setGeneratingUI(false);
              clearGenTask();
              return;
            }}
            clearGenTask();
            setGenStatus("生成完成，正在打开人物页…");
            setGeneratingUI(false);
            window.location.href = "./" + encodeURIComponent(person + ".html");
            return;
          }}
          setGenStatus("生成任务状态异常，请稍后重试");
          setGeneratingUI(false);
          clearGenTask();
        }};
        tick();
      }};

      const ensurePersonGenerated = async (personName) => {{
        const person = String(personName || "").trim();
        if (!person) return;
        try {{
          const headResp = await fetch("./" + encodeURIComponent(person + ".html"), {{ method: "HEAD", cache: "no-store" }});
          if (headResp && headResp.ok) {{
            setGenStatus("");
            setGeneratingUI(false);
            window.location.href = "./" + encodeURIComponent(person + ".html");
            return;
          }}
        }} catch (_) {{}}
        setGeneratingUI(true);
        setGenStatus("未找到本地人物「" + person + "」，正在生成，请稍候…");
        try {{
          const resp = await fetchWithTimeout("./generate?person=" + encodeURIComponent(person), 12000);
          const data = await resp.json();
          if (!data || data.ok !== true || !data.task_id) {{
            const msg = data && data.error ? String(data.error) : "生成任务创建失败";
            setGenStatus(msg);
            setGeneratingUI(false);
            return;
          }}
          const taskId = String(data.task_id || "").trim();
          try {{ localStorage.setItem("stellar_gen_task_v1", JSON.stringify({{ id: taskId, person }})); }} catch (_) {{}}
          pollTask(taskId, person);
        }} catch (e) {{
          setGenStatus("生成请求失败，请稍后重试");
          setGeneratingUI(false);
        }}
      }};

      const resumeGenTask = () => {{
        let raw = "";
        try {{ raw = localStorage.getItem("stellar_gen_task_v1") || ""; }} catch (_) {{}}
        if (!raw) return;
        try {{
          const obj = JSON.parse(raw);
          const id = obj && obj.id ? String(obj.id) : "";
          const person = obj && obj.person ? String(obj.person) : "";
          if (id && person) {{
            pollTask(id, person);
            return;
          }}
        }} catch (_) {{}}
        clearGenTask();
        setGenStatus("");
      }};
      try {{
        document.addEventListener("visibilitychange", () => {{
          if (!document.hidden) resumeGenTask();
        }});
      }} catch (_) {{}}
      setTimeout(resumeGenTask, 300);

      const findPersonNode = (name) => {{
        const q = String(name || "").trim();
        if (!q) return null;
        for (const n of nodes) {{
          if (String(n.person || "").trim() === q) return n;
        }}
        const q2 = q.toLowerCase();
        for (const n of nodes) {{
          const p = String(n.person || "").trim().toLowerCase();
          if (p === q2) return n;
        }}
        for (const n of nodes) {{
          const p = String(n.person || "").trim().toLowerCase();
          if (p && p.includes(q2)) return n;
        }}
        return null;
      }};
      const focusPersonInGraph = (name, clientX, clientY) => {{
        const n = findPersonNode(name);
        if (!n) return false;
        setTab("graph");
        camScale = clamp(1.9, 0.6, 2.4);
        camOffX = (W * 0.50) - n.x * camScale;
        camOffY = (H * 0.50) - n.y * camScale;
        setSpotlight(n, clientX || (window.innerWidth * 0.5), clientY || (window.innerHeight * 0.3));
        return true;
      }};

      const onSearch = (ev) => {{
        const name = $q.value;
        if (focusPersonInGraph(name, ev?.clientX, ev?.clientY)) return;
        openPerson(name);
      }};

      $go.addEventListener("click", (ev) => onSearch(ev));
      $q.addEventListener("keydown", (e) => {{
        if (e.key === "Enter") onSearch(e);
      }});

      let currentTab = "graph";
      let mapInited = false;
      let markers = [];
      let amap = null;
      let amapLoading = false;
      let clusterer = null;
      let onlyActiveMarkers = false;
      let _fitMapTimer = null;
      let _persistTimer = null;
      let mapStyleValue = "amap://styles/macaron";
      let markerStyleValue = "circle";

      const _setMapStyleValue = (style) => {{
        const s = String(style || "").trim();
        mapStyleValue = s === "amap://styles/macaron" ? "amap://styles/macaron" : "amap://styles/macaron";
        if ($mapStyle) {{
          try {{ $mapStyle.value = mapStyleValue; }} catch (_) {{}}
        }}
        try {{ localStorage.setItem("stellar_map_style_v1", mapStyleValue); }} catch (_) {{}}
        if (amap && typeof amap.setMapStyle === "function") {{
          try {{ amap.setMapStyle(mapStyleValue); }} catch (_) {{}}
        }}
      }};

      const _initMapStyleValue = () => {{
        let saved = "";
        try {{ saved = (localStorage.getItem("stellar_map_style_v1") || "").trim(); }} catch (_) {{}}
        if (saved === "amap://styles/macaron") mapStyleValue = "amap://styles/macaron";
        if ($mapStyle) {{
          try {{
            $mapStyle.value = "amap://styles/macaron";
            mapStyleValue = "amap://styles/macaron";
          }} catch (_) {{}}
          $mapStyle.addEventListener("change", () => _setMapStyleValue($mapStyle.value));
        }}
      }};
      _initMapStyleValue();

      const _getAmapKey = () => {{
        let k = "";
        try {{
          k = (new URLSearchParams(window.location.search).get("amapKey") || "").trim();
        }} catch (_) {{}}
        if (!k) k = (window.AMAP_KEY || localStorage.getItem("AMAP_KEY") || "").trim();
        return k;
      }};
      const _getAmapSecurity = () => {{
        let s = "";
        try {{
          s = (new URLSearchParams(window.location.search).get("amapSec") || "").trim();
        }} catch (_) {{}}
        if (!s) s = (window.AMAP_SECURITY || localStorage.getItem("AMAP_SECURITY") || "").trim();
        return s;
      }};
      const _ensureAmap = () => new Promise((resolve, reject) => {{
        if (window.AMap && typeof window.AMap.Map === "function") return resolve(true);
        const key = _getAmapKey();
        if (!key) return reject(new Error("AMAP_KEY_REQUIRED"));
        const sec = _getAmapSecurity();
        if (sec) {{
          window._AMapSecurityConfig = {{ securityJsCode: sec }};
        }}
        if (amapLoading) {{
          const t0 = Date.now();
          const tick = () => {{
            if (window.AMap && typeof window.AMap.Map === "function") return resolve(true);
            if (Date.now() - t0 > 12000) return reject(new Error("AMAP_LOAD_TIMEOUT"));
            setTimeout(tick, 80);
          }};
          return tick();
        }}
        amapLoading = true;
        const sEl = document.createElement("script");
        sEl.async = true;
        // Load AMap JS with clustering + geocoder plugins.
        // Different AMap versions expose different globals (MarkerCluster / MarkerClusterer).
        sEl.src = `https://webapi.amap.com/maps?v=2.0&key=${{encodeURIComponent(key)}}&plugin=AMap.MarkerCluster,AMap.MarkerClusterer,AMap.Geocoder`;
        sEl.onload = () => {{
          amapLoading = false;
          if (window.AMap && typeof window.AMap.Map === "function") resolve(true);
          else reject(new Error("AMAP_LOAD_FAILED"));
        }};
        sEl.onerror = () => {{
          amapLoading = false;
          reject(new Error("AMAP_LOAD_FAILED"));
        }};
        document.head.appendChild(sEl);
      }});

      const _amapKeyGate = () => {{
        if (!$chinaMap) return null;
        const wrap = document.createElement("div");
        wrap.style.position = "absolute";
        wrap.style.left = "0";
        wrap.style.top = "0";
        wrap.style.right = "0";
        wrap.style.bottom = "0";
        wrap.style.zIndex = "10";
        wrap.style.display = "flex";
        wrap.style.alignItems = "center";
        wrap.style.justifyContent = "center";
        wrap.style.background = "rgba(2,6,23,0.55)";
        wrap.innerHTML = `
          <div style="width:min(520px,92vw);border-radius:14px;padding:16px 16px 14px;background:rgba(255,255,255,0.92);border:1px solid rgba(255,255,255,0.35);box-shadow:0 18px 44px rgba(0,0,0,0.28)">
            <div style="font-weight:800;color:#0f172a;font-size:14px">地图需要高德 Web Key</div>
            <div style="margin-top:6px;color:rgba(15,23,42,0.65);font-size:12px;line-height:1.4">
              由于环境 DNS 无法访问 AutoNavi 瓦片域名，这里改用 AMap JS API（webapi.amap.com）。请填入 Key（可选填 securityJsCode）。
            </div>
            <div style="margin-top:12px;display:flex;flex-direction:column;gap:8px">
              <input id="amap-key" placeholder="AMAP_KEY" style="width:100%;padding:10px 12px;border-radius:10px;border:1px solid rgba(15,23,42,0.15);outline:none;font-size:12px" />
              <input id="amap-sec" placeholder="AMAP_SECURITY（可选）" style="width:100%;padding:10px 12px;border-radius:10px;border:1px solid rgba(15,23,42,0.15);outline:none;font-size:12px" />
              <div style="display:flex;gap:8px;justify-content:flex-end">
                <button id="amap-save" style="padding:9px 12px;border-radius:10px;background:#0ea5e9;color:white;font-size:12px;font-weight:700">保存并加载</button>
              </div>
            </div>
          </div>
        `;
        const keyEl = wrap.querySelector("#amap-key");
        const secEl = wrap.querySelector("#amap-sec");
        const saveEl = wrap.querySelector("#amap-save");
        if (keyEl) keyEl.value = _getAmapKey();
        if (secEl) secEl.value = _getAmapSecurity();
        if (saveEl) {{
          saveEl.addEventListener("click", () => {{
            const k = (keyEl && keyEl.value ? String(keyEl.value) : "").trim();
            const s = (secEl && secEl.value ? String(secEl.value) : "").trim();
            if (k) localStorage.setItem("AMAP_KEY", k);
            if (s) localStorage.setItem("AMAP_SECURITY", s);
            wrap.remove();
            initMapOnce();
          }});
        }}
        return wrap;
      }};

      const COORD_CACHE_KEY = "stellar_birth_coords_wgs84_v1";
      const COORD_CACHE_OLD_KEY = "stellar_birth_coords_v1";
      const migrateCoordCache = () => {{
        try {{
          const oldRaw = localStorage.getItem(COORD_CACHE_OLD_KEY);
          if (!oldRaw) return;
          const oldObj = JSON.parse(oldRaw);
          if (!oldObj || typeof oldObj !== "object") return;
          const out = {{}};
          for (const k of Object.keys(oldObj)) {{
            const v = oldObj[k];
            if (!Array.isArray(v) || v.length < 2) continue;
            const latG = Number(v[0]);
            const lngG = Number(v[1]);
            if (!Number.isFinite(latG) || !Number.isFinite(lngG)) continue;
            const w = gcj02ToWgs84(latG, lngG);
            out[k] = [w.lat, w.lng];
          }}
          localStorage.setItem(COORD_CACHE_KEY, JSON.stringify(out));
          localStorage.removeItem(COORD_CACHE_OLD_KEY);
        }} catch (_) {{}}
      }};
      const readCoordCache = () => {{
        try {{
          const raw = localStorage.getItem(COORD_CACHE_KEY);
          const obj = raw ? JSON.parse(raw) : null;
          return (obj && typeof obj === "object") ? obj : {{}};
        }} catch (_) {{
          return {{}};
        }}
      }};
      const writeCoordCache = (cache) => {{
        try {{
          localStorage.setItem(COORD_CACHE_KEY, JSON.stringify(cache));
        }} catch (_) {{}}
      }};
      let _coordDirty = {{}};
      let _coordDirtyCount = 0;
      let _coordFlushTimer = null;
      const _flushCoordsToServer = () => {{
        const items = _coordDirty;
        const n = _coordDirtyCount;
        _coordDirty = {{}};
        _coordDirtyCount = 0;
        if (_coordFlushTimer) {{
          clearTimeout(_coordFlushTimer);
          _coordFlushTimer = null;
        }}
        if (!n) return;
        try {{
          fetch("/coords/bulk", {{
            method: "POST",
            headers: {{ "Content-Type": "application/json" }},
            body: JSON.stringify({{ items }}),
          }}).catch(() => {{}});
        }} catch (_) {{}}
      }};
      const _markCoordDirty = (person, lat, lng) => {{
        const p = String(person || "").trim();
        if (!p) return;
        if (!Number.isFinite(lat) || !Number.isFinite(lng)) return;
        _coordDirty[p] = [lat, lng];
        _coordDirtyCount += 1;
        if (_coordFlushTimer) return;
        _coordFlushTimer = setTimeout(_flushCoordsToServer, 1200);
      }};
      const applyCoordCacheToNodes = (cache) => {{
        if (!cache) return;
        for (const n of nodes) {{
          const k = String(n.person || "").trim();
          const v = k ? cache[k] : null;
          const has = (typeof n.birth_lat_wgs84 === "number" && typeof n.birth_lng_wgs84 === "number") || (typeof n.birth_lat === "number" && typeof n.birth_lng === "number");
          if (v && !has && Array.isArray(v) && v.length >= 2) {{
            const lat = Number(v[0]);
            const lng = Number(v[1]);
            if (Number.isFinite(lat) && Number.isFinite(lng)) {{
              n.birth_lat_wgs84 = lat;
              n.birth_lng_wgs84 = lng;
              n.birth_lat = lat;
              n.birth_lng = lng;
            }}
          }}
        }}
      }};

      const TIME_WINDOW_KEY = "stellar_time_window_v1";
      const readTimeWindow = () => {{
        try {{
          const raw = localStorage.getItem(TIME_WINDOW_KEY);
          const obj = raw ? JSON.parse(raw) : null;
          if (!obj || typeof obj !== "object") return null;
          const a = Number(obj.a);
          const b = Number(obj.b);
          if (!Number.isFinite(a) || !Number.isFinite(b)) return null;
          return {{ a: Math.round(a), b: Math.round(b) }};
        }} catch (_) {{
          return null;
        }}
      }};
      const persistTimeWindow = () => {{
        if (_persistTimer) clearTimeout(_persistTimer);
        _persistTimer = setTimeout(() => {{
          try {{
            localStorage.setItem(TIME_WINDOW_KEY, JSON.stringify({{ a: startYear, b: endYear }}));
          }} catch (_) {{}}
        }}, 260);
      }};

      const scheduleMapFit = () => {{
        if (_fitMapTimer) clearTimeout(_fitMapTimer);
        _fitMapTimer = setTimeout(() => {{
          if (currentTab !== "map" || !mapInited || !amap) return;
          resetMapView();
        }}, 220);
      }};

      const centerMapOnPerson = (n) => {{
        if (!n || !amap) return;
        const latW = (typeof n.birth_lat_wgs84 === "number") ? n.birth_lat_wgs84 : n.birth_lat;
        const lngW = (typeof n.birth_lng_wgs84 === "number") ? n.birth_lng_wgs84 : n.birth_lng;
        const p = wgs84ToGcj02(latW, lngW);
        const lat = p.lat;
        const lng = p.lng;
        if (typeof lat !== "number" || typeof lng !== "number") return;
        try {{
          const z = Math.max(6, Number(amap.getZoom ? amap.getZoom() : 6) || 6);
          amap.setZoomAndCenter(Math.min(10, z), [lng, lat]);
        }} catch (_) {{}}
      }};
      const geocodeText = (n) => {{
        const m = String(n.birthplace_modern || "").trim().replace(/^今\\s*/g, "");
        if (m) return m;
        const raw = String(n.birthplace_raw || "").trim();
        if (raw) {{
          const t = raw.split(/[；;，,]/)[0].replace(/[（(].*?[）)]/g, "").replace(/^约\\s*/g, "").replace(/^公元前?\\d+年\\s*/g, "").trim();
          if (t) return t;
        }}
        const bp = String(n.birthplace || "").trim();
        return bp.split(/[；;，,]/)[0].replace(/[（(].*?[）)]/g, "").trim();
      }};
      const prefillCoordsNoMap = () => {{
        const key = _getAmapKey();
        if (!key) return;
        const cache = readCoordCache();
        applyCoordCacheToNodes(cache);
        updateCoordCount();
        _ensureAmap().then(() => {{
          if (!window.AMap || !window.AMap.Geocoder) return;
          const geocoder = new window.AMap.Geocoder({{ city: "全国" }});
          const pending = nodes.filter((n) => (typeof n.birth_lat_wgs84 !== "number" || typeof n.birth_lng_wgs84 !== "number"));
          if (!pending.length) return;
          const limit = pending.length;
          let idx = 0;
          const tick = () => {{
            if (idx >= pending.length || idx >= limit) {{
              writeCoordCache(cache);
              applyCoordCacheToNodes(cache);
              updateCoordCount();
              _flushCoordsToServer();
              return;
            }}
            const n = pending[idx++];
            const q = geocodeText(n);
            const person = String(n.person || "").trim();
            if (!q || !person) return setTimeout(tick, 80);
            geocoder.getLocation(q, (status, result) => {{
              if (status === "complete" && result && result.geocodes && result.geocodes.length) {{
                const loc = result.geocodes[0].location;
                if (loc && typeof loc.getLng === "function" && typeof loc.getLat === "function") {{
                  const lng = Number(loc.getLng());
                  const lat = Number(loc.getLat());
                  if (Number.isFinite(lat) && Number.isFinite(lng)) {{
                    const w = gcj02ToWgs84(lat, lng);
                    n.birth_lat_wgs84 = w.lat;
                    n.birth_lng_wgs84 = w.lng;
                    n.birth_lat = w.lat;
                    n.birth_lng = w.lng;
                    cache[person] = [w.lat, w.lng];
                    updateCoordCount();
                    _markCoordDirty(person, w.lat, w.lng);
                  }}
                }}
              }}
              setTimeout(tick, 140);
            }});
          }};
          setTimeout(tick, 400);
        }}).catch(() => {{}});
      }};

      const setTab = (tab) => {{
        currentTab = tab;
        if ($tabTrack) {{
          $tabTrack.style.transform = tab === "graph" ? "translateX(0%)" : "translateX(-50%)";
        }}
        if ($tabGraph && $tabMap) {{
          if (tab === "graph") {{
            $tabGraph.className = "px-3 py-1 rounded-lg bg-white/15 border border-white/20 text-white/90";
            $tabMap.className = "px-3 py-1 rounded-lg bg-white/5 border border-white/10 text-white/70 hover:bg-white/10";
          }} else {{
            $tabGraph.className = "px-3 py-1 rounded-lg bg-white/5 border border-white/10 text-white/70 hover:bg-white/10";
            $tabMap.className = "px-3 py-1 rounded-lg bg-white/15 border border-white/20 text-white/90";
          }}
        }}
        if (tab === "map") {{
          initMapOnce();
          scheduleMapFit();
        }}
        if ($mapToolbar) {{
          if (tab === "map") $mapToolbar.classList.remove("hidden");
          else $mapToolbar.classList.add("hidden");
        }}
        if ($provinceCurvePanel) {{
          if (tab === "map") {{
            $provinceCurvePanel.classList.remove("hidden");
            updateProvinceBars();
          }} else {{
            $provinceCurvePanel.classList.add("hidden");
          }}
        }}
      }};

      const initMapOnce = () => {{
        if (mapInited) return;
        if (!$chinaMap) return;
        if (!$chinaMap.style.position) $chinaMap.style.position = "relative";
        mapInited = true;
        _ensureAmap().then(() => {{
          if (!window.AMap) return;
          amap = new window.AMap.Map($chinaMap, {{
            zoom: 4,
            center: [105.0, 35.5],
            viewMode: "2D",
            mapStyle: mapStyleValue || "amap://styles/whitesmoke",
            resizeEnable: true,
          }});
          try {{
            const bounds = new window.AMap.Bounds([72.0, 17.5], [136.5, 55.5]);
            if (amap && typeof amap.setLimitBounds === "function") {{
              amap.setLimitBounds(bounds);
            }}
          }} catch (_) {{}}
          try {{
            const mohe = [122.340, 53.480];
            const tengchong = [98.490, 25.020];
            const mid = [(mohe[0] + tengchong[0]) / 2, (mohe[1] + tengchong[1]) / 2];
            const line = new window.AMap.Polyline({{
              path: [mohe, tengchong],
              strokeColor: "rgba(249,115,22,0.92)",
              strokeWeight: 3,
              strokeStyle: "dashed",
              strokeDasharray: [10, 8],
              zIndex: 300,
            }});
            line.setMap(amap);
            const dx = tengchong[0] - mohe[0];
            const dy = tengchong[1] - mohe[1];
            const len = Math.hypot(dx, dy) || 1;
            let nx = (-dy) / len;
            let ny = dx / len;
            if (ny < 0) {{
              nx = -nx;
              ny = -ny;
            }}
            const offsetDeg = 0.6;
            const labelPos = [mid[0] + nx * offsetDeg, mid[1] + ny * offsetDeg];
            const ang = 0;
            const label = new window.AMap.Marker({{
              position: labelPos,
              anchor: "center",
              offset: new window.AMap.Pixel(0, 0),
              clickable: false,
              content:
                '<div style="transform:rotate(' +
                String(ang.toFixed(2)) +
                'deg);transform-origin:center;background:rgba(15,23,42,0.72);border:1px solid rgba(255,255,255,0.22);color:rgba(255,255,255,0.96);padding:6px 10px;border-radius:999px;font-size:12px;font-weight:700;white-space:nowrap">胡焕庸线</div>',
              zIndex: 320,
            }});
            label.setMap(amap);
            try {{
              const dot = new window.AMap.CircleMarker({{
                center: mid,
                radius: 5,
                strokeColor: "rgba(255,255,255,0.65)",
                strokeWeight: 1,
                fillColor: "rgba(249,115,22,0.92)",
                fillOpacity: 1,
                zIndex: 330,
              }});
              dot.setMap(amap);
            }} catch (_) {{}}

            const mkText = (text, pos) => {{
              const t = new window.AMap.Text({{
                text,
                position: pos,
                offset: new window.AMap.Pixel(0, -16),
                style: {{
                  background: "rgba(255,255,255,0.92)",
                  border: "1px solid rgba(15,23,42,0.18)",
                  color: "rgba(15,23,42,0.92)",
                  padding: "4px 8px",
                  borderRadius: "999px",
                  fontSize: "12px",
                  fontWeight: "700",
                }},
                zIndex: 320,
              }});
              t.setMap(amap);
              return t;
            }};
            mkText("漠河", mohe);
            mkText("腾冲", tengchong);
          }} catch (_) {{}}
          const coordCache = readCoordCache();
          applyCoordCacheToNodes(coordCache);

          let infoWin = null;
          try {{
            infoWin = new window.AMap.InfoWindow({{ offset: new window.AMap.Pixel(0, -22) }});
          }} catch (_) {{
            infoWin = null;
          }}

          // Use markers (+ optional clusterer) to make dense distributions readable.
          const addMarker = (n) => {{
            const latW = (typeof n.birth_lat_wgs84 === "number") ? n.birth_lat_wgs84 : n.birth_lat;
            const lngW = (typeof n.birth_lng_wgs84 === "number") ? n.birth_lng_wgs84 : n.birth_lng;
            if (typeof latW !== "number" || typeof lngW !== "number") return;
            const p = wgs84ToGcj02(latW, lngW);
            const lat = p.lat;
            const lng = p.lng;
            if (!Number.isFinite(lat) || !Number.isFinite(lng)) return;
            const mk = new window.AMap.Marker({{
              position: [lng, lat],
              offset: new window.AMap.Pixel(-5, -5),
              content: '<svg width="10" height="10" viewBox="0 0 24 24" style="filter:drop-shadow(0 0 6px rgba(154,160,166,0.22));"><circle cx="12" cy="12" r="9" fill="rgba(232,234,237,0.70)"></circle></svg>',
              anchor: "center",
              clickable: true,
            }});
            mk.on("click", () => {{
              try {{
                const years = formatYearRange(n.birth_year, n.death_year);
                const dynasty = String(n.dynasty || "").trim();
                const bp = formatBirthplace(n.birthplace, n.birthplace_modern);
                const quote = stripMd(String(n.quote || "").trim());
                const review = stripMd(String(n.review || "").trim());
                const tagline = stripOuterQuotes(review || quote);
                const personJs = String(n.person || "").replace(/'/g, "\\\\'");
                let html = '';
                html += '<div style="min-width:220px;max-width:280px">';
                html += '<div style="font-weight:800;color:#0f172a;font-size:14px">' + esc(n.person) + '</div>';
                html += '<div style="margin-top:4px;color:rgba(15,23,42,0.70);font-size:12px">生卒：' + esc(years) + '</div>';
                if (dynasty) html += '<div style="margin-top:4px;color:rgba(15,23,42,0.70);font-size:12px">时代：' + esc(dynasty) + '</div>';
                if (bp) html += '<div style="margin-top:4px;color:rgba(15,23,42,0.70);font-size:12px">籍贯：' + esc(bp) + '</div>';
                if (tagline) html += '<div style="margin-top:6px;color:rgba(245,158,11,0.95);font-size:12px;line-height:1.4">“' + esc(tagline) + '”</div>';
                html += '<div style="margin-top:8px"><button onclick="window.__openPerson && window.__openPerson(\\'' + personJs + '\\')" style="background:#0f172a;color:#fff;border:0;border-radius:10px;padding:6px 10px;font-size:12px;font-weight:700;cursor:pointer">打开人物页</button></div>';
                html += '</div>';
                if (infoWin) {{
                  infoWin.setContent(html);
                  infoWin.open(amap, [lng, lat]);
                }}
              }} catch (_) {{}}
            }});
            mk.on("dblclick", () => openPerson(n.person));
            try {{ mk.setMap(amap); }} catch (_) {{}}
            markers.push({{ mk, n }});
          }};

          for (const n of nodes) addMarker(n);
          updateCoordCount();
          try {{
            const Cluster = window.AMap.MarkerClusterer || window.AMap.MarkerCluster;
            if (Cluster) {{
              try {{
                clusterer = new Cluster(amap, markers.map((x) => x.mk), {{
                  gridSize: 72,
                  minClusterSize: 2,
                  maxZoom: 7,
                }});
              }} catch (_) {{
                clusterer = null;
              }}
            }}
          }} catch (_) {{}}
          updateMapMarkers();
          resetMapView();
          scheduleMapFit();

          const autoFillCoords = () => {{
            let need = 0;
            for (const n of nodes) {{
              if (typeof n.birth_lat_wgs84 !== "number" || typeof n.birth_lng_wgs84 !== "number") need += 1;
            }}
            if (need <= 0) return;
            if (!window.AMap || !window.AMap.Geocoder) return;
            const geocoder = new window.AMap.Geocoder({{ city: "全国" }});
            const pending = nodes.filter((n) => (typeof n.birth_lat_wgs84 !== "number" || typeof n.birth_lng_wgs84 !== "number"));
            const limit = pending.length;
            let idx = 0;
            const tick = () => {{
              if (idx >= pending.length || idx >= limit) {{
                writeCoordCache(coordCache);
                updateCoordCount();
                updateMapMarkers();
                _flushCoordsToServer();
                return;
              }}
              const n = pending[idx++];
              const q = geocodeText(n);
              const person = String(n.person || "").trim();
              if (!q || !person) return setTimeout(tick, 80);
              geocoder.getLocation(q, (status, result) => {{
                if (status === "complete" && result && result.geocodes && result.geocodes.length) {{
                  const loc = result.geocodes[0].location;
                  if (loc && typeof loc.getLng === "function" && typeof loc.getLat === "function") {{
                    const lng = Number(loc.getLng());
                    const lat = Number(loc.getLat());
                    if (Number.isFinite(lat) && Number.isFinite(lng)) {{
                      const w = gcj02ToWgs84(lat, lng);
                      n.birth_lat_wgs84 = w.lat;
                      n.birth_lng_wgs84 = w.lng;
                      n.birth_lat = w.lat;
                      n.birth_lng = w.lng;
                      coordCache[person] = [w.lat, w.lng];
                      writeCoordCache(coordCache);
                      addMarker(n);
                      try {{
                        if (clusterer && typeof clusterer.addMarker === "function") clusterer.addMarker(markers[markers.length - 1].mk);
                        else if (clusterer && typeof clusterer.addMarkers === "function") clusterer.addMarkers([markers[markers.length - 1].mk]);
                      }} catch (_) {{}}
                      updateCoordCount();
                      updateMapMarkers();
                      _markCoordDirty(person, w.lat, w.lng);
                    }}
                  }}
                }}
                setTimeout(tick, 140);
              }});
            }};
            setTimeout(tick, 400);
          }};
          autoFillCoords();
        }}).catch((e) => {{
          mapInited = false;
          const gate = _amapKeyGate();
          if (gate && $chinaMap && !$chinaMap.querySelector("#amap-key")) {{
            $chinaMap.appendChild(gate);
          }}
        }});
      }};

      const updateMapMarkers = () => {{
        if (!mapInited || !amap) return;
        const hiIdx = selectedIdx >= 0 ? selectedIdx : (spotlightIdx >= 0 ? spotlightIdx : -1);
        const focusSet = hiIdx >= 0 ? (() => {{
          const s = new Set();
          s.add(hiIdx);
          for (const j of (neigh[hiIdx] || [])) s.add(j);
          return s;
        }})() : null;
        for (const it of markers) {{
          const n = it.n;
          const active = inWindow(n);
          if (onlyActiveMarkers && !active) {{
            try {{ it.mk.hide(); }} catch (_) {{}}
            continue;
          }}
          try {{ it.mk.show(); }} catch (_) {{}}
          const idx = typeof n._idx === "number" ? n._idx : -1;
          const dim = focusSet && idx >= 0 && !focusSet.has(idx);
          const sz = dim ? 9 : (active ? 13 : 11);
          const base = colorByYear(n.time_year);
          const accent = base.startsWith("#") ? hexToRgba(base, 0.92) : base;
          const accentSoft = base.startsWith("#") ? hexToRgba(base, 0.62) : base;
          const glowStrong = base.startsWith("#") ? hexToRgba(base, 0.40) : "rgba(154,160,166,0.22)";
          const glowSoft = base.startsWith("#") ? hexToRgba(base, 0.20) : "rgba(154,160,166,0.16)";
          const fill = dim ? "rgba(232,234,237,0.18)" : (active ? accent : (focusSet ? accentSoft : "rgba(232,234,237,0.66)"));
          const glow = dim ? "rgba(154,160,166,0.08)" : (active ? glowStrong : glowSoft);
          const anim = (!dim && active) ? "animation:twinkle 2.2s ease-in-out infinite;" : "";
          it.mk.setContent(`<svg width="${{sz}}" height="${{sz}}" viewBox="0 0 24 24" style="${{anim}}filter:drop-shadow(0 0 ${{active ? 10 : 6}}px ${{glow}});"><circle cx="12" cy="12" r="9" fill="${{fill}}"></circle></svg>`);
          it.mk.setOffset(new window.AMap.Pixel(-Math.round(sz / 2), -Math.round(sz / 2)));
        }}
      }};

      if ($tabGraph) $tabGraph.addEventListener("click", () => setTab("graph"));
      if ($tabMap) $tabMap.addEventListener("click", () => setTab("map"));
      if ($onlyActiveMarkers) {{
        $onlyActiveMarkers.addEventListener("change", () => {{
          onlyActiveMarkers = Boolean($onlyActiveMarkers.checked);
          updateMapMarkers();
          scheduleMapFit();
        }});
      }}
      if ($focusPerson) {{
        $focusPerson.addEventListener("click", () => {{
          const n = spotlight || selected;
          if (!n) return;
          if (currentTab !== "map") {{
            setTab("map");
          }}
          setTimeout(() => {{
            centerMapOnPerson(n);
          }}, 260);
        }});
      }}
      if ($presetBar) {{
        $presetBar.addEventListener("click", (e) => {{
          const t = e && e.target ? e.target : null;
          const btn = t && t.closest ? t.closest("button[data-preset]") : null;
          const key = btn ? String(btn.getAttribute("data-preset") || "") : "";
          if (!key) return;
          const presets = {{
            all: [minYear, maxYear],
            tang: [618, 907],
            song: [960, 1279],
            mingqing: [1368, 1840],
            modern: [1840, 1911],
            contemporary: [1911, maxYear],
          }};
          const r = presets[key];
          if (!r) return;
          startYear = clamp(r[0], minYear, maxYear);
          endYear = clamp(r[1], minYear, maxYear);
          if (startYear >= endYear) endYear = clamp(startYear + 1, minYear, maxYear);
          setHandles();
          updateActiveCount();
          updateCoordCount();
          updateMapMarkers();
          draw();
        }});
      }}
      const resetGraphView = () => {{
        camScale = 1.0;
        camOffX = 0.0;
        camOffY = 0.0;
        setSelected(null);
      }};
      const resetMapView = () => {{
        try {{
          if (amap) amap.setZoomAndCenter(4, [105.0, 35.5]);
        }} catch (_) {{}}
      }};
      if ($resetGraph) $resetGraph.addEventListener("click", resetGraphView);
      if ($resetMap) $resetMap.addEventListener("click", resetMapView);
      setTab("graph");

      const onMouseMove = (e) => {{
        const rect = $c.getBoundingClientRect();
        const mx = (e.clientX - rect.left) * (W / rect.width);
        const my = (e.clientY - rect.top) * (H / rect.height);
        const n = pickNode(mx, my);
        hover = n;
        if (n) showTip(n, e.clientX, e.clientY);
        else showTip(null);
        draw();
      }};

      $c.addEventListener("mousemove", onMouseMove);
      $c.addEventListener("mouseleave", () => {{
        hover = null;
        showTip(null);
        draw();
      }});
      $c.addEventListener("click", (event) => {{
        const rect = $c.getBoundingClientRect();
        const mx = (event.clientX - rect.left) * (W / rect.width);
        const my = (event.clientY - rect.top) * (H / rect.height);
        const n = pickNode(mx, my);
        if (_clickTimer) clearTimeout(_clickTimer);
        _clickTimer = setTimeout(() => {{
          if (n) {{
            setSelected(n);
          }} else {{
            setSelected(null);
          }}
          _clickTimer = null;
        }}, 220);
      }});
      $c.addEventListener("dblclick", (event) => {{
        if (_clickTimer) {{
          clearTimeout(_clickTimer);
          _clickTimer = null;
        }}
        const rect = $c.getBoundingClientRect();
        const mx = (event.clientX - rect.left) * (W / rect.width);
        const my = (event.clientY - rect.top) * (H / rect.height);
        const n = pickNode(mx, my);
        if (n) openPerson(n.person);
      }});

      let isPanning = false;
      let panStartX = 0;
      let panStartY = 0;
      let panStartOffX = 0;
      let panStartOffY = 0;
      $c.addEventListener("mousedown", (e) => {{
        if (!(e.button === 2 || (e.shiftKey && e.button === 0) || e.button === 1)) return;
        isPanning = true;
        panStartX = e.clientX;
        panStartY = e.clientY;
        panStartOffX = camOffX;
        panStartOffY = camOffY;
        try {{ e.preventDefault(); }} catch (_) {{}}
      }});
      $c.addEventListener("contextmenu", (e) => {{
        try {{ e.preventDefault(); }} catch (_) {{}}
      }});
      window.addEventListener("mouseup", () => {{
        isPanning = false;
      }});
      window.addEventListener("mousemove", (e) => {{
        if (!isPanning) return;
        camOffX = panStartOffX + (e.clientX - panStartX) * (W / $c.getBoundingClientRect().width);
        camOffY = panStartOffY + (e.clientY - panStartY) * (H / $c.getBoundingClientRect().height);
        draw();
      }});
      $c.addEventListener("wheel", (e) => {{
        const rect = $c.getBoundingClientRect();
        const mx = (e.clientX - rect.left) * (W / rect.width);
        const my = (e.clientY - rect.top) * (H / rect.height);
        const before = screenToWorld(mx, my);
        const dir = e.deltaY > 0 ? -1 : 1;
        const factor = dir > 0 ? 1.12 : 1 / 1.12;
        camScale = clamp(camScale * factor, 0.35, 3.8);
        camOffX = mx - before.x * camScale;
        camOffY = my - before.y * camScale;
        draw();
        try {{ e.preventDefault(); }} catch (_) {{}}
      }}, {{ passive: false }});

      const railRect = () => $rail.getBoundingClientRect();

      const hitTestHandle = (e) => {{
        const r = railRect();
        const x = e.clientX - r.left;
        const {{x1, x2}} = handlePosPx();
        const px1 = x1;
        const px2 = x2;
        if (Math.abs(x - px1) < 18) return "left";
        if (Math.abs(x - px2) < 18) return "right";
        if (x > px1 && x < px2) return "mid";
        return "";
      }};

      const onDown = (e) => {{
        if (typeof e.button === "number" && e.button !== 0) return;
        const r = railRect();
        const rx = clamp(e.clientX - r.left, 0, r.width || 1);
        const m = hitTestHandle(e);
        if (!m) {{
          dragMode = "brush";
          brushStartX = rx;
          brushStartYear = fromT(clamp(rx / (r.width || 1), 0, 1));
          startYear = brushStartYear;
          endYear = clamp(brushStartYear + 1, minYear, maxYear);
        }} else {{
          dragMode = m;
          dragStartX = e.clientX;
          dragStartA = startYear;
          dragStartB = endYear;
        }}
        if ($rail.setPointerCapture) {{
          try {{ $rail.setPointerCapture(e.pointerId); }} catch (_) {{}}
        }}
        if (e.stopPropagation) e.stopPropagation();
        e.preventDefault();
      }};

      const onMove = (e) => {{
        if (!dragMode) return;
        const r = railRect();
        if (dragMode === "brush") {{
          const rx = clamp(e.clientX - r.left, 0, r.width || 1);
          const y = fromT(clamp(rx / (r.width || 1), 0, 1));
          let a = Math.min(brushStartYear, y);
          let b = Math.max(brushStartYear, y);
          a = clamp(a, minYear, maxYear);
          b = clamp(b, minYear, maxYear);
          if (a === b) b = clamp(a + 1, minYear, maxYear);
          startYear = a;
          endYear = b;
        }} else {{
          const dx = e.clientX - dragStartX;
          const dt = dx / r.width;
          const span = dragStartB - dragStartA;
          if (dragMode === "left") {{
            const t = clamp(toT(dragStartA) + dt, 0, toT(dragStartB) - 0.01);
            startYear = fromT(t);
          }} else if (dragMode === "right") {{
            const t = clamp(toT(dragStartB) + dt, toT(dragStartA) + 0.01, 1);
            endYear = fromT(t);
          }} else if (dragMode === "mid") {{
            let a = dragStartA + Math.round(dt * (maxYear - minYear));
            let b = a + span;
            if (a < minYear) {{ a = minYear; b = a + span; }}
            if (b > maxYear) {{ b = maxYear; a = b - span; }}
            startYear = a;
            endYear = b;
          }}
        }}
        if (startYear >= endYear) {{
          if (dragMode === "left") startYear = endYear - 1;
          else endYear = startYear + 1;
        }}
        setHandles();
        updateActiveCount();
        updateCoordCount();
        updateMapMarkers();
        draw();
      }};

      const onUp = () => {{
        if (!dragMode) return;
        const wasBrush = (dragMode === "brush");
        dragMode = "";
        if (wasBrush && currentTab === "graph") {{
          zoomToFitWindowNodes();
          draw();
        }}
      }};

      $rail.addEventListener("pointerdown", onDown);
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
      window.addEventListener("pointercancel", onUp);
      $rail.addEventListener("mousedown", onDown);
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
      $rail.addEventListener("dblclick", () => {{
        startYear = 0;
        endYear = 1840;
        setHandles();
        updateActiveCount();
        updateCoordCount();
        updateMapMarkers();
        draw();
      }});
      if ($startYearInput) {{
        $startYearInput.addEventListener("keydown", (e) => {{
          if (e.key === "Enter") applyYearInputs();
        }});
        $startYearInput.addEventListener("blur", applyYearInputs);
      }}
      if ($endYearInput) {{
        $endYearInput.addEventListener("keydown", (e) => {{
          if (e.key === "Enter") applyYearInputs();
        }});
        $endYearInput.addEventListener("blur", applyYearInputs);
      }}

      const groupKey = (n) => {{
        const d = String(n.dynasty || "").trim();
        if (d) return d.slice(0, 6);
        const name = String(n.person || "").trim();
        return name ? name.slice(0, 1) : "？";
      }};

      const buildNeigh = () => {{
        neigh = Array.from({{ length: nodes.length }}, () => []);
        edgeMeta = new Map();
        for (const e of edges) {{
          if (!e) continue;
          const a = e.a;
          const b = e.b;
          if (typeof a !== "number" || typeof b !== "number") continue;
          if (!neigh[a]) neigh[a] = [];
          if (!neigh[b]) neigh[b] = [];
          neigh[a].push(b);
          neigh[b].push(a);
          const lo = Math.min(a, b);
          const hi = Math.max(a, b);
          const k = `${{lo}},${{hi}}`;
          const conf = edgeConf(e);
          const label = String((e && e.label) || "").trim();
          const t = edgeType(e);
          const prev = edgeMeta.get(k) || null;
          if (!prev || conf >= (prev.confidence || 0)) {{
            edgeMeta.set(k, {{ label, confidence: conf, type: t }});
          }}
        }}
      }};

      migrateCoordCache();
      fetch(DATA_FILE).then((r) => r.json()).then((data) => {{
        const raw = (data.nodes || []);
        const groups = new Map();
        raw.forEach((n) => {{
          const k = groupKey(n);
          if (!groups.has(k)) groups.set(k, []);
          groups.get(k).push(n);
        }});
        const keys = Array.from(groups.keys()).sort();
        const centers = new Map();
        const cx = W / 2;
        const cy = H / 2;
        const picked = [];
        const minD2 = 160 * 160;
        keys.forEach((k) => {{
          const seed = hash(k);
          let best = null;
          for (let a = 0; a < 32; a++) {{
            const x = pad + rand01(seed + a * 17 + 1) * (W - pad * 2);
            const y = pad + rand01(seed + a * 17 + 2) * (H - pad * 2);
            let ok = true;
            for (const p of picked) {{
              const dx = x - p.x;
              const dy = y - p.y;
              if (dx * dx + dy * dy < minD2) {{ ok = false; break; }}
            }}
            if (ok) {{ best = {{ x, y }}; break; }}
          }}
          if (!best) {{
            best = {{
              x: clamp(cx + (rand01(seed + 3) - 0.5) * (W * 0.7), pad, W - pad),
              y: clamp(cy + (rand01(seed + 4) - 0.5) * (H * 0.7), pad, H - pad),
            }};
          }}
          centers.set(k, best);
          picked.push(best);
        }});

        const laneFor = (n) => {{
          const k = groupKey(n);
          const i = Math.abs(hash(k)) % 7;
          return (i + 0.5) / 7;
        }};

        const cell = 12;
        const occ = new Set();
        const key = (cx, cy) => `${{cx}},${{cy}}`;
        const isFree = (x, y) => {{
          const cx = Math.round(x / cell);
          const cy = Math.round(y / cell);
          const k = key(cx, cy);
          if (occ.has(k)) return false;
          occ.add(k);
          return true;
        }};

        const place = (dx, dy, wantX, wantY) => {{
          const x = clamp(wantX + dx, pad, W - pad);
          const y = clamp(wantY + dy, pad, H - pad);
          if (isFree(x, y)) return [x, y];
          return null;
        }};

        const offsets = [];
        for (let r = 0; r <= 6; r++) {{
          const step = 10;
          for (let a = 0; a < 12; a++) {{
            const ang = (a / 12) * Math.PI * 2;
            offsets.push([Math.cos(ang) * r * step, Math.sin(ang) * r * step]);
          }}
        }}

        nodes = raw.map((n, idx) => {{
          const seed = hash(n.person || "");
          const my = mainYear(n);
          const t = (typeof my === "number" && Number.isFinite(my)) ? clamp(toT(my), 0, 1) : null;
          const x0 = t == null ? (pad + rand01(seed + 1) * (W - pad * 2)) : (pad + t * (W - pad * 2));
          const yLane = laneFor(n);
          const y0 = pad + yLane * (H - pad * 2) + (rand01(seed + 2) - 0.5) * 26;

          let best = null;
          for (const [dx, dy] of offsets) {{
            const p = place(dx, dy, x0, y0);
            if (p) {{ best = p; break; }}
          }}
          if (!best) {{
            best = [clamp(x0, pad, W - pad), clamp(y0, pad, H - pad)];
          }}
          const x = best[0];
          const y = best[1];
          return {{ ...n, x, y, bx: x, by: y, _idx: idx }};
        }});
        edgesAll = [];
        minYear = data.min_year ?? -800;
        maxYear = data.max_year ?? 1840;
        startYear = data.default_start ?? 0;
        endYear = data.default_end ?? 1840;
        const savedWin = readTimeWindow();
        if (savedWin) {{
          const a = clamp(savedWin.a, minYear, maxYear);
          const b = clamp(savedWin.b, minYear, maxYear);
          startYear = Math.min(a, b);
          endYear = Math.max(a, b);
          if (startYear === endYear) endYear = clamp(startYear + 1, minYear, maxYear);
        }}
        applyEdgeFilters();
        renderBands();
        setHandles();
        updateActiveCount();
        updateCoordCount();
        prefillCoordsNoMap();
        window.requestAnimationFrame(animate);
      }});
    </script>
  </body>
</html>
"""


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--story-map-dir", default=str(STORY_MAP_DIR))
    p.add_argument("--story-md-dir", default=str(STORY_MD_DIR))
    p.add_argument("--spotlight", default=str(SPOTLIGHT_JSON))
    p.add_argument("--out-index", default="index.html")
    p.add_argument("--out-data", default="stellar_home_data.json")
    p.add_argument("--title", default="故事地图")
    p.add_argument("--default-start", type=int, default=0)
    p.add_argument("--default-end", type=int, default=MAX_YEAR)
    args = p.parse_args()

    story_map_dir = Path(args.story_map_dir).resolve()
    story_md_dir = Path(args.story_md_dir).resolve()
    spotlight_path = Path(args.spotlight).resolve()

    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv(dotenv_path=str((REPO_ROOT / ".env").resolve()))
        load_dotenv(dotenv_path=str((REPO_ROOT.parent / ".env").resolve()))
        load_dotenv(dotenv_path=str((REPO_ROOT.parent.parent / ".env").resolve()))
        load_dotenv(dotenv_path=str((REPO_ROOT / "data" / ".env").resolve()))
    except Exception:
        pass

    latest_html = _scan_latest_html(story_map_dir)
    geocode_city = None
    try:
        sys.path.insert(0, str((REPO_ROOT / "storymap" / "script").resolve()))
        from map_client import geocode_city as _geocode_city  # type: ignore

        geocode_city = _geocode_city
    except Exception:
        geocode_city = None
    geocode_limit = int(os.getenv("STELLAR_HOME_GEOCODE_LIMIT", "0") or "0")
    geocode_used = 0

    hist_index_path = (REPO_ROOT / "data" / "historical_places_index.jsonl").resolve()
    hist_index: Dict[str, Tuple[float, float]] = {}

    def _norm_place_key(s: str) -> str:
        t = str(s or "").strip()
        if not t:
            return ""
        t = re.sub(r"[\\s\\(\\)（）\\[\\]【】<>《》“”‘’\"'·•,，。；;:：/\\\\-—]+", "", t)
        return t.strip().lower()

    def _load_hist_index() -> Dict[str, Tuple[float, float]]:
        if not hist_index_path.exists():
            return {}
        mapping: Dict[str, Tuple[float, float]] = {}
        try:
            with hist_index_path.open("r", encoding="utf-8") as f:
                for line in f:
                    s = (line or "").strip()
                    if not s:
                        continue
                    try:
                        obj = json.loads(s)
                    except Exception:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    ancient = str(obj.get("ancient_name") or "").strip()
                    modern = str(obj.get("modern_name") or "").strip()
                    lat = obj.get("lat")
                    lon = obj.get("lon")
                    try:
                        lat_f = float(lat)
                        lon_f = float(lon)
                    except Exception:
                        continue
                    if not (-90 <= lat_f <= 90 and -180 <= lon_f <= 180):
                        continue
                    for key in (ancient, modern):
                        nk = _norm_place_key(key)
                        if nk and nk not in mapping:
                            mapping[nk] = (lat_f, lon_f)
        except Exception:
            return {}
        return mapping

    hist_index = _load_hist_index()

    person_birth_coords: Dict[str, Tuple[float, float]] = {}
    try:
        if BIRTH_COORDS_WGS84_JSON.exists():
            raw_pbc = json.loads(BIRTH_COORDS_WGS84_JSON.read_text(encoding="utf-8"))
            if isinstance(raw_pbc, dict):
                for k, v in raw_pbc.items():
                    name = str(k or "").strip()
                    if not name:
                        continue
                    if isinstance(v, list) and len(v) >= 2:
                        try:
                            lat = float(v[0])
                            lng = float(v[1])
                        except Exception:
                            continue
                        if -90 <= lat <= 90 and -180 <= lng <= 180:
                            person_birth_coords[name] = (lat, lng)
    except Exception:
        person_birth_coords = {}

    person_birth_coords_dirty = 0

    def _set_person_birth_coord(person: str, lat: float, lng: float) -> None:
        nonlocal person_birth_coords_dirty
        p = str(person or "").strip()
        if not p:
            return
        try:
            la = float(lat)
            lo = float(lng)
        except Exception:
            return
        if not (-90 <= la <= 90 and -180 <= lo <= 180):
            return
        old = person_birth_coords.get(p)
        if old and abs(old[0] - la) < 1e-7 and abs(old[1] - lo) < 1e-7:
            return
        person_birth_coords[p] = (la, lo)
        person_birth_coords_dirty += 1

    def _hist_lookup(*names: str) -> Optional[Tuple[float, float]]:
        for name in names:
            nk = _norm_place_key(name)
            if not nk:
                continue
            coord = hist_index.get(nk)
            if coord:
                return coord
        return None

    def _parse_coords_table_from_md(md_text: str) -> Dict[str, Tuple[float, float]]:
        if not isinstance(md_text, str) or not md_text.strip():
            return {}
        lines = md_text.splitlines()
        in_section = False
        table_started = False
        idx_name = None
        idx_lat = None
        idx_lng = None
        out: Dict[str, Tuple[float, float]] = {}
        for line in lines:
            s = (line or "").strip()
            if s.startswith("## "):
                title = s.lstrip("#").strip()
                in_section = "地点坐标" in title
                table_started = False
                idx_name = None
                idx_lat = None
                idx_lng = None
                continue
            if not in_section:
                continue
            if s.startswith("|") and (not table_started):
                header = [c.strip() for c in s.strip("|").split("|")]
                for i, c in enumerate(header):
                    cl = c.lower()
                    if ("现称" in c) or ("地点" in c) or ("location" in cl) or ("place" in cl):
                        idx_name = i
                    if ("纬度" in c) or ("lat" in cl):
                        idx_lat = i
                    if ("经度" in c) or ("lng" in cl) or ("lon" in cl) or ("long" in cl):
                        idx_lng = i
                table_started = True
                continue
            if table_started:
                if (not s) or (not s.startswith("|")):
                    break
                cols = [c.strip() for c in s.strip("|").split("|")]
                if idx_name is None or idx_lat is None or idx_lng is None:
                    continue
                if idx_name >= len(cols) or idx_lat >= len(cols) or idx_lng >= len(cols):
                    continue
                name = cols[idx_name]
                if re.fullmatch(r":?-+:?", cols[idx_lat].replace(" ", "")) or re.fullmatch(
                    r":?-+:?", cols[idx_lng].replace(" ", "")
                ):
                    continue
                try:
                    lat = float(cols[idx_lat])
                    lng = float(cols[idx_lng])
                except Exception:
                    continue
                if not (-90 <= lat <= 90 and -180 <= lng <= 180):
                    continue
                variants = [str(name or "").strip()]
                try:
                    stripped = re.sub(r"[（(].*?[）)]", "", str(name or "")).strip()
                    if stripped and stripped not in variants:
                        variants.append(stripped)
                    if "（" in name:
                        left = name.split("（", 1)[0].strip()
                        if left and left not in variants:
                            variants.append(left)
                    if "(" in name:
                        left = name.split("(", 1)[0].strip()
                        if left and left not in variants:
                            variants.append(left)
                except Exception:
                    pass
                for v in variants:
                    nk = _norm_place_key(v)
                    if nk and nk not in out:
                        out[nk] = (lat, lng)
        return out

    amap_key = (
        os.getenv("locaion_api")
        or os.getenv("location_api")
        or os.getenv("LOCATION_API")
        or os.getenv("AMAP_WEBSERVICE_KEY")
        or os.getenv("AMAP_WEB_SERVICE_KEY")
        or os.getenv("AMAP_REST_KEY")
        or ""
    ).strip()
    amap_limit = int(os.getenv("STELLAR_HOME_AMAP_GEOCODE_LIMIT", "5000") or "5000")
    amap_interval_s = float(os.getenv("STELLAR_HOME_AMAP_MIN_INTERVAL", "0.08") or "0.08")
    amap_concurrency = int(os.getenv("STELLAR_HOME_AMAP_CONCURRENCY", "6") or "6")
    amap_qps = float(os.getenv("STELLAR_HOME_AMAP_QPS", "8") or "8")
    if not (amap_concurrency > 0):
        amap_concurrency = 1
    if not (amap_qps > 0):
        amap_qps = 8.0
    amap_min_interval_s = max(amap_interval_s, 1.0 / float(amap_qps))
    amap_req_used = 0
    amap_last_ts = 0.0
    amap_lock = threading.Lock()
    amap_cache_path = (REPO_ROOT / "cache" / "amap_geocode_cache.json").resolve()
    amap_cache: Dict[str, Optional[Tuple[float, float]]] = {}
    try:
        if amap_cache_path.exists():
            raw_cache = json.loads(amap_cache_path.read_text(encoding="utf-8"))
            if isinstance(raw_cache, dict):
                for k, v in raw_cache.items():
                    if not isinstance(k, str) or not k.strip():
                        continue
                    kk = k.strip()
                    if v is None:
                        amap_cache[kk] = None
                        continue
                    if isinstance(v, list) and len(v) >= 2:
                        try:
                            lat = float(v[0])
                            lng = float(v[1])
                        except Exception:
                            continue
                        if -90 <= lat <= 90 and -180 <= lng <= 180:
                            amap_cache[kk] = (lat, lng)
    except Exception:
        amap_cache = {}

    def _amap_geocode(address: str) -> Optional[Tuple[float, float]]:
        nonlocal amap_last_ts, amap_req_used
        addr = str(address or "").strip()
        if not addr or not amap_key:
            return None
        retry_none = str(os.getenv("STELLAR_HOME_AMAP_RETRY_NONE", "") or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "y",
        }
        if addr in amap_cache and (amap_cache.get(addr) is not None or (not retry_none)):
            return amap_cache.get(addr)
        with amap_lock:
            if amap_req_used >= amap_limit:
                return None
            amap_req_used += 1
            now = time.time()
            wait = (amap_last_ts + amap_min_interval_s) - now
            amap_last_ts = max(amap_last_ts, now) + amap_min_interval_s
        if wait > 0:
            time.sleep(wait)
        url = (
            "https://restapi.amap.com/v3/geocode/geo"
            f"?address={url_quote(addr, safe='')}&key={url_quote(amap_key, safe='')}"
        )
        try:
            req = Request(url, headers={"User-Agent": "StoryMap/1.0"})
            with urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="ignore"))
        except Exception:
            amap_cache[addr] = None
            return None
        if not isinstance(data, dict) or str(data.get("status")) != "1":
            amap_cache[addr] = None
            return None
        geocodes = data.get("geocodes")
        if not isinstance(geocodes, list) or not geocodes:
            amap_cache[addr] = None
            return None
        g0 = geocodes[0] if isinstance(geocodes[0], dict) else None
        if not isinstance(g0, dict):
            amap_cache[addr] = None
            return None
        loc = str(g0.get("location") or "").strip()
        if not loc or "," not in loc:
            amap_cache[addr] = None
            return None
        a, b = loc.split(",", 1)
        try:
            lng = float(a.strip())
            lat = float(b.strip())
        except Exception:
            amap_cache[addr] = None
            return None
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            amap_cache[addr] = None
            return None
        res = (lat, lng)
        amap_cache[addr] = res
        return res

    def _looks_foreign_query(q: str) -> bool:
        s = str(q or "").strip()
        if not s:
            return False
        if re.search(r"[A-Za-z]", s):
            return True
        return bool(
            re.search(
                r"(美国|智利|法国|英国|俄罗斯|希腊|乌克兰|西班牙|意大利|德国|日本|韩国|朝鲜|越南|泰国|缅甸|斯里兰卡|印度尼西亚|印度|巴西|阿根廷|墨西哥|古巴|加拿大|澳大利亚|新西兰|南非|埃及|以色列|巴勒斯坦|土耳其|伊朗|伊拉克|叙利亚|阿富汗|巴基斯坦|挪威|瑞典|芬兰|丹麦|冰岛|荷兰|比利时|瑞士|奥地利|葡萄牙|波兰|捷克|匈牙利|罗马尼亚|保加利亚|塞尔维亚|克罗地亚|爱尔兰|苏联)",
                s,
            )
        )

    def _looks_like_geocode_query(q: str) -> bool:
        s = str(q or "").strip()
        if not s:
            return False
        if _looks_foreign_query(s):
            return False
        if re.search(r"(存疑|不详|无法确认|具体地点存疑|未知|待查证|无考|虚构|传说|小说|人物|文学作品|作品|未明确|未记载|记载有限|背景设定)", s):
            return False
        if re.search(r"^\\d{1,2}\\s*月(?:\\s*\\d{1,2}\\s*(?:日|号))?$", s):
            return False
        if re.search(r"^(?:约|大约|约于)?\\s*(公元前|公元|前)?\\s*\\d{1,4}\\s*年(?:\\s*\\d{1,2}\\s*月(?:\\s*\\d{1,2}\\s*(?:日|号))?)?$", s):
            return False
        if re.search(r"^(?:约|大约|约于)?\\s*\\d{1,2}\\s*世纪(?:初|中|末)?$", s):
            return False
        return True

    def _make_geocode_query(birthplace_modern: str, birthplace_ancient: str, birthplace_raw: str) -> str:
        q = (birthplace_modern or birthplace_ancient or birthplace_raw or "").strip()
        q = re.sub(r"^今\\s*", "", q).strip()
        q = re.sub(r"^(?:出生于|出生在|生于|生在|于|在)\\s*", "", q).strip()
        q = re.sub(r"^(?:出生地|出生地是|出生地点|籍贯|祖籍|故里)[:：\\s]*", "", q).strip()
        q = re.sub(r"^(?:今|现)?属\\s*", "", q).strip()
        q = re.sub(r"^(?:今|现)?为\\s*", "", q).strip()
        q = re.sub(r"[（(].*?[）)]", "", q).strip()
        q = re.split(r"(?:当时|现|今|属|位于|位在|坐落于|附近|一带|境内|范围内|大致在)", q, 1)[0].strip()
        q = q.split("，", 1)[0].split(",", 1)[0].split("；", 1)[0].split(";", 1)[0].strip()
        return q

    def _amap_geocode_batch(addresses: List[str]) -> None:
        if not amap_key:
            return
        retry_none = str(os.getenv("STELLAR_HOME_AMAP_RETRY_NONE", "") or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "y",
        }
        uniq: List[str] = []
        seen = set()
        for a in addresses:
            s = str(a or "").strip()
            if not s or s in seen:
                continue
            seen.add(s)
            if s in amap_cache and (amap_cache.get(s) is not None or (not retry_none)):
                continue
            if not _looks_like_geocode_query(s):
                amap_cache[s] = None
                continue
            uniq.append(s)
        if not uniq:
            return

        def worker(addr: str) -> Tuple[str, Optional[Tuple[float, float]]]:
            return (addr, _amap_geocode(addr))

        with ThreadPoolExecutor(max_workers=amap_concurrency) as ex:
            futs = [ex.submit(worker, a) for a in uniq]
            for fut in as_completed(futs):
                try:
                    addr, res = fut.result()
                except Exception:
                    continue
                if not addr:
                    continue
                if addr not in amap_cache:
                    amap_cache[addr] = res
                    continue
                if retry_none and amap_cache.get(addr) is None and res is not None:
                    amap_cache[addr] = res

    foreign_limit = int(os.getenv("STELLAR_HOME_FOREIGN_GEOCODE_LIMIT", "1500") or "1500")
    foreign_concurrency = int(os.getenv("STELLAR_HOME_FOREIGN_CONCURRENCY", "6") or "6")
    foreign_qps = float(os.getenv("STELLAR_HOME_FOREIGN_QPS", "6") or "6")
    if not (foreign_concurrency > 0):
        foreign_concurrency = 1
    if not (foreign_qps > 0):
        foreign_qps = 6.0
    foreign_min_interval_s = max(1.0 / float(foreign_qps), 0.05)
    foreign_req_used = 0
    foreign_last_ts = 0.0
    foreign_lock = threading.Lock()
    foreign_cache_path = (REPO_ROOT / "cache" / "foreign_geocode_cache.json").resolve()
    foreign_cache: Dict[str, Optional[Tuple[float, float]]] = {}
    try:
        if foreign_cache_path.exists():
            raw_cache = json.loads(foreign_cache_path.read_text(encoding="utf-8"))
            if isinstance(raw_cache, dict):
                for k, v in raw_cache.items():
                    if not isinstance(k, str) or not k.strip():
                        continue
                    kk = k.strip()
                    if v is None:
                        foreign_cache[kk] = None
                        continue
                    if isinstance(v, list) and len(v) >= 2:
                        try:
                            lat = float(v[0])
                            lng = float(v[1])
                        except Exception:
                            continue
                        if -90 <= lat <= 90 and -180 <= lng <= 180:
                            foreign_cache[kk] = (lat, lng)
    except Exception:
        foreign_cache = {}

    def _looks_like_foreign_geocode_query(q: str) -> bool:
        s = str(q or "").strip()
        if not s:
            return False
        if not _looks_foreign_query(s):
            return False
        if re.search(r"(存疑|不详|无法确认|具体地点存疑|未知)", s):
            return False
        if re.search(r"^\\d{1,2}\\s*月(?:\\s*\\d{1,2}\\s*(?:日|号))?$", s):
            return False
        if re.search(r"^(?:约|大约|约于)?\\s*(公元前|公元|前)?\\s*\\d{1,4}\\s*年(?:\\s*\\d{1,2}\\s*月(?:\\s*\\d{1,2}\\s*(?:日|号))?)?$", s):
            return False
        if re.search(r"^(?:约|大约|约于)?\\s*\\d{1,2}\\s*世纪(?:初|中|末)?$", s):
            return False
        return True

    def _foreign_geocode(address: str) -> Optional[Tuple[float, float]]:
        nonlocal foreign_last_ts, foreign_req_used
        addr = str(address or "").strip()
        if not addr:
            return None
        retry_none = str(os.getenv("STELLAR_HOME_FOREIGN_RETRY_NONE", "") or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "y",
        }
        if addr in foreign_cache:
            cached = foreign_cache.get(addr)
            if cached is not None or (not retry_none):
                return cached
        with foreign_lock:
            if foreign_req_used >= foreign_limit:
                return None
            foreign_req_used += 1
            now = time.time()
            wait = (foreign_last_ts + foreign_min_interval_s) - now
            foreign_last_ts = max(foreign_last_ts, now) + foreign_min_interval_s
        if wait > 0:
            time.sleep(wait)
        data = None
        try:
            url = f"https://photon.komoot.io/api/?limit=1&q={url_quote(addr, safe='')}"
            req = Request(url, headers={"User-Agent": "StoryMap/1.0"})
            with urlopen(req, timeout=18) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="ignore"))
        except Exception:
            data = None
        lat = None
        lng = None
        if isinstance(data, dict):
            feats = data.get("features")
            if isinstance(feats, list) and feats:
                f0 = feats[0] if isinstance(feats[0], dict) else None
                geom = f0.get("geometry") if isinstance(f0, dict) else None
                coords = geom.get("coordinates") if isinstance(geom, dict) else None
                if isinstance(coords, list) and len(coords) >= 2:
                    try:
                        lng = float(coords[0])
                        lat = float(coords[1])
                    except Exception:
                        lat = None
                        lng = None
        if lat is None or lng is None:
            try:
                url = f"https://nominatim.openstreetmap.org/search?format=json&limit=1&q={url_quote(addr, safe='')}"
                req = Request(url, headers={"User-Agent": "StoryMap/1.0"})
                with urlopen(req, timeout=18) as resp:
                    data2 = json.loads(resp.read().decode("utf-8", errors="ignore"))
                if isinstance(data2, list) and data2:
                    d0 = data2[0] if isinstance(data2[0], dict) else None
                    if isinstance(d0, dict):
                        lat = float(d0.get("lat"))
                        lng = float(d0.get("lon"))
            except Exception:
                lat = None
                lng = None
        if lat is None or lng is None:
            foreign_cache[addr] = None
            return None
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            foreign_cache[addr] = None
            return None
        res = (float(lat), float(lng))
        foreign_cache[addr] = res
        return res

    def _foreign_geocode_batch(addresses: List[str]) -> None:
        retry_none = str(os.getenv("STELLAR_HOME_FOREIGN_RETRY_NONE", "") or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "y",
        }
        uniq: List[str] = []
        seen = set()
        for a in addresses:
            s = str(a or "").strip()
            if not s or s in seen:
                continue
            seen.add(s)
            if s in foreign_cache and (foreign_cache.get(s) is not None or (not retry_none)):
                continue
            if not _looks_like_foreign_geocode_query(s):
                foreign_cache[s] = None
                continue
            uniq.append(s)
        if not uniq:
            return

        def worker(addr: str) -> Tuple[str, Optional[Tuple[float, float]]]:
            return (addr, _foreign_geocode(addr))

        with ThreadPoolExecutor(max_workers=foreign_concurrency) as ex:
            futs = [ex.submit(worker, a) for a in uniq]
            for fut in as_completed(futs):
                try:
                    addr, res = fut.result()
                except Exception:
                    continue
                if not addr:
                    continue
                if addr not in foreign_cache:
                    foreign_cache[addr] = res
                    continue
                if retry_none and foreign_cache.get(addr) is None and res is not None:
                    foreign_cache[addr] = res

    md_names = _scan_people_from_story_md(story_md_dir)
    names = sorted(set(md_names))

    spotlight_data = _read_json(spotlight_path)
    spotlight_items = spotlight_data.get("items") if isinstance(spotlight_data, dict) else {}
    if not isinstance(spotlight_items, dict):
        spotlight_items = {}

    strict_audit_dir = (REPO_ROOT / "data" / "validation_reports" / "strict_audit").resolve()
    quality_line = ""

    nodes: List[Dict[str, Any]] = []
    min_year: Optional[int] = None
    max_year: Optional[int] = None
    pending_amap: Dict[int, str] = {}
    pending_foreign: Dict[int, str] = {}
    for name in names:
        md_path = story_md_dir / f"{name}.md"
        birth_year = None
        death_year = None
        dynasty = ""
        relations: List[str] = []
        aliases: List[str] = []
        foreign_name = ""
        domain_tags: List[str] = []
        birthplace_raw = ""
        birthplace_ancient = ""
        birthplace_modern = ""
        coords_table: Dict[str, Tuple[float, float]] = {}
        audit_risk_level = ""
        audit_overall_pass = None
        audit_uncertain = None
        if md_path.exists():
            md_text = md_path.read_text(encoding="utf-8")
            birth_year, death_year = _extract_years_from_md(md_text)
            dynasty = _dynasty_hint_from_md(md_text)
            relations, relations_meta = _extract_relations(md_text)
            aliases, foreign_name, domain_tags = _extract_disambiguation(md_text)
            birthplace_raw, birthplace_ancient, birthplace_modern = _extract_birthplace_from_md(md_text)
            coords_table = _parse_coords_table_from_md(md_text)
        try:
            rp = strict_audit_dir / f"{name}.json"
            if rp.exists():
                payload = json.loads(rp.read_text(encoding="utf-8"))
                audit = payload.get("audit") if isinstance(payload, dict) else None
                if isinstance(audit, dict):
                    audit_risk_level = str(audit.get("risk_level") or "").strip()
                    audit_overall_pass = audit.get("overall_pass")
                    ent = audit.get("entity_identity")
                    if isinstance(ent, dict):
                        audit_uncertain = ent.get("uncertain")
        except Exception:
            pass
        if birth_year is not None:
            min_year = birth_year if min_year is None else min(min_year, birth_year)
            max_year = birth_year if max_year is None else max(max_year, birth_year)
        if death_year is not None:
            min_year = death_year if min_year is None else min(min_year, death_year)
            max_year = death_year if max_year is None else max(max_year, death_year)

        spot = spotlight_items.get(name)
        quote = ""
        review = ""
        if isinstance(spot, dict):
            quote = _pick_quote(spot)
            review = str(spot.get("review") or "").strip()
        if name == "武则天" and not review:
            review = "千秋功过，后人评说。"

        html_entry = latest_html.get(name)
        birth_lat = None
        birth_lng = None
        cached_birth = person_birth_coords.get(name)
        if cached_birth and isinstance(cached_birth, tuple) and len(cached_birth) >= 2:
            try:
                birth_lat = float(cached_birth[0])
                birth_lng = float(cached_birth[1])
            except Exception:
                birth_lat = None
                birth_lng = None
        if html_entry:
            lat, lng, bp, dyn2 = _extract_birth_from_story_map_html(story_map_dir / html_entry.file)
            if birth_lat is None or birth_lng is None:
                birth_lat = lat
                birth_lng = lng
            if not dynasty and dyn2:
                dynasty = dyn2
            if not birthplace_raw and bp:
                birthplace_raw, birthplace_ancient, birthplace_modern = _extract_birthplace_from_md(f"**出生**：{bp}")
        if (birth_lat is None or birth_lng is None) and coords_table:
            cands = [birthplace_modern, birthplace_ancient, birthplace_raw]
            picked = None
            for c in cands:
                s = str(c or "").strip()
                if not s:
                    continue
                s = re.sub(r"^今\\s*", "", s).strip()
                s = re.sub(r"^(?:出生于|出生在|生于|生在|于|在)\\s*", "", s).strip()
                s2 = re.sub(r"[（(].*?[）)]", "", s).strip()
                for k in (s, s2):
                    nk = _norm_place_key(k)
                    if nk and nk in coords_table:
                        picked = coords_table[nk]
                        break
                    if nk:
                        for ck, cv in coords_table.items():
                            if not ck:
                                continue
                            if (ck in nk) or (nk in ck):
                                picked = cv
                                break
                    if picked:
                        break
                if picked:
                    break
            if picked:
                birth_lat = float(picked[0])
                birth_lng = float(picked[1])
        if (birth_lat is None or birth_lng is None) and hist_index:
            c1 = (birthplace_modern or "").strip()
            c2 = (birthplace_ancient or "").strip()
            c3 = (birthplace_raw or "").strip()
            c1 = re.sub(r"^今\\s*", "", c1).strip()
            c2 = re.sub(r"^今\\s*", "", c2).strip()
            c3 = re.sub(r"^今\\s*", "", c3).strip()
            c1b = re.sub(r"[（(].*?[）)]", "", c1).strip()
            c2b = re.sub(r"[（(].*?[）)]", "", c2).strip()
            c3b = re.sub(r"[（(].*?[）)]", "", c3).strip()
            coord0 = _hist_lookup(c1, c2, c3, c1b, c2b, c3b)
            if coord0:
                birth_lat = float(coord0[0])
                birth_lng = float(coord0[1])
        if birth_lat is None or birth_lng is None:
            q = _make_geocode_query(birthplace_modern, birthplace_ancient, birthplace_raw)
            if amap_key and _looks_like_geocode_query(q):
                pending_amap[len(nodes)] = q
            if _looks_like_foreign_geocode_query(q):
                pending_foreign[len(nodes)] = q
        if geocode_city and geocode_used < geocode_limit and (birth_lat is None or birth_lng is None):
            q = (birthplace_modern or birthplace_ancient or birthplace_raw or "").strip()
            q = re.sub(r"^今\\s*", "", q).strip()
            q = re.sub(r"^(?:出生于|出生在|生于|生在|于|在)\\s*", "", q).strip()
            q = re.sub(r"^(?:祖籍|籍贯|故里|家乡|古称|传说中|传说人物)[:：\\s]*", "", q).strip()
            q = re.split(r"(?:当时|现|今|属|传说|小说|虚构|待查证|无考|不详)", q, 1)[0].strip()
            q = re.sub(r"[（(].*?[）)]", "", q).strip()
            if q and re.search(r"(世纪|年间|年|月|日|号|时期|当时|属|人物|传说|小说)", q) and not re.search(
                r"(省|市|县|区|州|郡|国|府|镇|乡|村|旗|盟|自治区|直辖|特区|都|城|岛|港|湾)",
                q,
            ):
                q = ""
            if q and (not (_looks_like_geocode_query(q) or _looks_like_foreign_geocode_query(q))):
                q = ""
            if q:
                try:
                    coord = geocode_city(q)
                except Exception:
                    coord = None
                if coord and isinstance(coord, tuple) and len(coord) >= 2:
                    birth_lat = float(coord[0])
                    birth_lng = float(coord[1])
                    geocode_used += 1
        if birth_lat is not None and birth_lng is not None:
            _set_person_birth_coord(name, birth_lat, birth_lng)
        dynasty = _normalize_dynasty_label(person=name, dynasty_raw=dynasty, birth_year=birth_year, death_year=death_year)
        time_year = None
        by = birth_year if isinstance(birth_year, int) else None
        dy = death_year if isinstance(death_year, int) else None
        if by is not None and dy is not None:
            a0 = min(by, dy)
            b0 = max(by, dy)
            r = _dynasty_range_from_label(dynasty) or _dynasty_range_from_label(_pick_main_dynasty_by_years(by, dy))
            if r:
                a = max(a0, int(r[0]))
                b = min(b0, int(r[1]))
                if a < b:
                    time_year = int(round((a + b) / 2))
                else:
                    time_year = int(round((a0 + b0) / 2))
            else:
                time_year = int(round((a0 + b0) / 2))
        else:
            time_year = by if by is not None else dy
        if time_year is None and dynasty:
            time_year = _dynasty_mid_year(dynasty)
        nodes.append(
            {
                "person": name,
                "birth_year": birth_year,
                "death_year": death_year,
                "time_year": time_year,
                "dynasty": dynasty,
                "quote": quote,
                "review": review,
                "aliases": aliases,
                "foreign_name": foreign_name,
                "domain_tags": domain_tags,
                "risk_level": audit_risk_level,
                "audit_pass": audit_overall_pass,
                "audit_uncertain": audit_uncertain,
                "birthplace": birthplace_ancient,
                "birthplace_raw": birthplace_raw,
                "birthplace_modern": birthplace_modern,
                "birth_lat_wgs84": birth_lat,
                "birth_lng_wgs84": birth_lng,
                "birth_lat": birth_lat,
                "birth_lng": birth_lng,
                "file": html_entry.file if html_entry else "",
                "seed": _sha1_int(name),
                "relations": relations,
                "relations_meta": relations_meta,
            }
        )

    if amap_key and pending_amap:
        _amap_geocode_batch(list(pending_amap.values()))
        for idx, q in pending_amap.items():
            if idx < 0 or idx >= len(nodes):
                continue
            coord = amap_cache.get(q)
            if coord and isinstance(coord, tuple) and len(coord) >= 2:
                try:
                    lat_g = float(coord[0])
                    lng_g = float(coord[1])
                except Exception:
                    continue
                lat_w, lng_w = _gcj02_to_wgs84(lat_g, lng_g)
                nodes[idx]["birth_lat_wgs84"] = float(lat_w)
                nodes[idx]["birth_lng_wgs84"] = float(lng_w)
                nodes[idx]["birth_lat"] = float(lat_w)
                nodes[idx]["birth_lng"] = float(lng_w)
                try:
                    _set_person_birth_coord(str(nodes[idx].get("person") or ""), float(lat_w), float(lng_w))
                except Exception:
                    pass
    if pending_foreign:
        _foreign_geocode_batch(list(pending_foreign.values()))
        for idx, q in pending_foreign.items():
            if idx < 0 or idx >= len(nodes):
                continue
            coord = foreign_cache.get(q)
            if coord and isinstance(coord, tuple) and len(coord) >= 2:
                try:
                    lat_w = float(coord[0])
                    lng_w = float(coord[1])
                except Exception:
                    continue
                nodes[idx]["birth_lat_wgs84"] = float(lat_w)
                nodes[idx]["birth_lng_wgs84"] = float(lng_w)
                nodes[idx]["birth_lat"] = float(lat_w)
                nodes[idx]["birth_lng"] = float(lng_w)
                try:
                    _set_person_birth_coord(str(nodes[idx].get("person") or ""), float(lat_w), float(lng_w))
                except Exception:
                    pass

    person_to_idx = {n["person"]: i for i, n in enumerate(nodes)}
    edges: List[Dict[str, Any]] = []
    kg_edges: List[Dict[str, int]] = []

    max_edges = 2200
    edge_set: Dict[Tuple[int, int], int] = {}

    def add_edge(i: int, j: int, meta: Optional[Dict[str, Any]] = None) -> None:
        nonlocal edges
        if i == j:
            return
        a, b = (i, j) if i < j else (j, i)
        key = (a, b)
        if key in edge_set:
            idx = edge_set[key]
            cur = edges[idx] if 0 <= idx < len(edges) else None
            if isinstance(cur, dict) and isinstance(meta, dict):
                try:
                    cc = float(cur.get("confidence"))
                except Exception:
                    cc = 0.0
                try:
                    nc = float(meta.get("confidence"))
                except Exception:
                    nc = 0.0
                if nc > cc:
                    cur.update(meta)
            return
        edge_set[key] = len(edges)
        e: Dict[str, Any] = {"a": a, "b": b}
        if isinstance(meta, dict):
            e.update(meta)
        edges.append(e)

    for i, n in enumerate(nodes):
        rels_meta = n.get("relations_meta") if isinstance(n.get("relations_meta"), list) else []
        if rels_meta:
            for r in rels_meta:
                if not isinstance(r, dict):
                    continue
                nm = str(r.get("name") or "").strip()
                if not nm:
                    continue
                j = person_to_idx.get(nm)
                if j is None or j == i:
                    continue
                label = str(r.get("label") or "亲友").strip() or "亲友"
                add_edge(i, j, {"type": "bio", "label": label, "confidence": 0.55})
                if len(edges) >= max_edges:
                    break
        else:
            rels = n.get("relations") if isinstance(n.get("relations"), list) else []
            for r in rels:
                j = person_to_idx.get(r)
                if j is None or j == i:
                    continue
                add_edge(i, j, {"type": "bio", "label": "文本提及", "confidence": 0.55})
                if len(edges) >= max_edges:
                    break
        if len(edges) >= max_edges:
            break

    try:
        kg = _read_json(KNOWLEDGE_GRAPH_JSON)
        raw_edges = kg.get("edges") if isinstance(kg, dict) else None
        if isinstance(raw_edges, list):
            for e in raw_edges:
                if not isinstance(e, dict):
                    continue
                typ = str(e.get("type") or "").strip().lower()
                w = e.get("weight")
                try:
                    if int(w or 0) < 2:
                        continue
                except Exception:
                    continue
                if typ not in {"same_book", "manual"}:
                    continue
                a = str(e.get("source") or "").strip()
                b = str(e.get("target") or "").strip()
                ia = person_to_idx.get(a)
                ib = person_to_idx.get(b)
                if ia is None or ib is None or ia == ib:
                    continue
                conf = None
                try:
                    conf = float(e.get("relation_confidence"))
                except Exception:
                    conf = None
                if conf is None or not (0.0 <= conf <= 1.0):
                    try:
                        ww = int(w or 0)
                    except Exception:
                        ww = 0
                    if typ == "same_book":
                        conf = max(0.15, min(0.60, 0.15 + 0.07 * max(0, ww - 2)))
                    else:
                        conf = 0.90
                label = str(e.get("relation_label") or "").strip()
                if not label:
                    if typ == "same_book":
                        da = str(nodes[ia].get("dynasty") or "").strip()
                        db = str(nodes[ib].get("dynasty") or "").strip()
                        if da and db and da[:2] == db[:2]:
                            label = "同朝共现"
                            conf = min(1.0, float(conf) + 0.10)
                        else:
                            ta = nodes[ia].get("domain_tags") if isinstance(nodes[ia].get("domain_tags"), list) else []
                            tb = nodes[ib].get("domain_tags") if isinstance(nodes[ib].get("domain_tags"), list) else []
                            sa = {str(x).strip() for x in ta if str(x).strip()}
                            sb = {str(x).strip() for x in tb if str(x).strip()}
                            if sa and sb and (sa & sb):
                                label = "同领域共现"
                                conf = min(1.0, float(conf) + 0.08)
                            else:
                                label = "同册共现"
                    else:
                        label = "人工关系"
                add_edge(ia, ib, {"type": typ, "label": label, "confidence": float(conf), "weight": int(w or 0)})
                if len(edges) >= max_edges:
                    break
    except Exception:
        kg_edges = []

    min_year_v = MIN_YEAR
    max_year_v = MAX_YEAR

    out_data = story_map_dir / str(args.out_data)
    out_index = story_map_dir / str(args.out_index)
    payload = {
        "generated_at": _now(),
        "min_year": min_year_v,
        "max_year": max_year_v,
        "default_start": int(args.default_start),
        "default_end": int(args.default_end),
        "nodes": nodes,
        "edges": edges,
        "kg_edges": kg_edges,
    }
    try:
        amap_cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload_cache: Dict[str, Any] = {}
        for k, v in amap_cache.items():
            if not isinstance(k, str) or not k.strip():
                continue
            if v is None:
                payload_cache[k] = None
            else:
                payload_cache[k] = [float(v[0]), float(v[1])]
        amap_cache_path.write_text(json.dumps(payload_cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    try:
        foreign_cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload_cache2: Dict[str, Any] = {}
        for k, v in foreign_cache.items():
            if not isinstance(k, str) or not k.strip():
                continue
            if v is None:
                payload_cache2[k] = None
            else:
                payload_cache2[k] = [float(v[0]), float(v[1])]
        foreign_cache_path.write_text(json.dumps(payload_cache2, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    try:
        if person_birth_coords_dirty > 0:
            BIRTH_COORDS_WGS84_JSON.parent.mkdir(parents=True, exist_ok=True)
            payload_pbc: Dict[str, Any] = {}
            for k in sorted(person_birth_coords.keys()):
                v = person_birth_coords.get(k)
                if not v:
                    continue
                payload_pbc[k] = [float(v[0]), float(v[1])]
            BIRTH_COORDS_WGS84_JSON.write_text(json.dumps(payload_pbc, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    out_data.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    out_index.write_text(_render_index_html(args.title, out_data.name, quality_line=quality_line), encoding="utf-8")
    print(json.dumps({"ok": True, "index": str(out_index), "data": str(out_data), "count": len(nodes)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


REPO_ROOT = Path(__file__).resolve().parent.parent
STORY_MD_DIR = REPO_ROOT / "storymap" / "examples" / "story"
STORY_MAP_DIR = REPO_ROOT / "storymap" / "examples" / "story_map"
SPOTLIGHT_JSON = REPO_ROOT / "data" / "pep_people_spotlight.json"
KNOWLEDGE_GRAPH_JSON = REPO_ROOT / "data" / "people_knowledge_graph.json"
MIN_YEAR = -800
MAX_YEAR = 2000


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _sha1_int(s: str) -> int:
    h = hashlib.sha1(s.encode("utf-8")).hexdigest()
    return int(h[:12], 16)


def _person_from_filename(name: str) -> str:
    stem = Path(name).stem
    if "__pure__" in stem:
        return stem.split("__pure__", 1)[0]
    return stem


@dataclass
class HtmlEntry:
    person: str
    file: str
    mtime: float


def _scan_latest_html(story_map_dir: Path) -> Dict[str, HtmlEntry]:
    latest: Dict[str, HtmlEntry] = {}
    for p in story_map_dir.glob("*.html"):
        if not p.is_file():
            continue
        person = _person_from_filename(p.name).strip()
        if not person:
            continue
        e = HtmlEntry(person=person, file=p.name, mtime=p.stat().st_mtime)
        cur = latest.get(person)
        if cur is None or e.mtime > cur.mtime:
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
    return sorted({x.strip() for x in items if str(x).strip()})


def _scan_people_from_story_map_html(story_map_dir: Path) -> List[str]:
    if not story_map_dir.exists():
        return []
    names: List[str] = []
    for p in story_map_dir.glob("*.html"):
        if not p.is_file():
            continue
        person = _person_from_filename(p.name).strip()
        if person:
            names.append(person)
    return sorted(set(names))


def _extract_years_from_md(md_text: str) -> Tuple[Optional[int], Optional[int]]:
    text = md_text

    def pick_year(s: str) -> Optional[int]:
        ys = re.findall(r"(?<!\d)(-?\d{1,4})(?!\d)", str(s or ""))
        if not ys:
            return None
        try:
            return int(ys[0])
        except Exception:
            return None

    def pick_two_years(s: str) -> Tuple[Optional[int], Optional[int]]:
        ys = re.findall(r"(?<!\d)(-?\d{1,4})(?!\d)", str(s or ""))
        if len(ys) < 2:
            return None, None
        try:
            return int(ys[0]), int(ys[1])
        except Exception:
            return None, None

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

    return birth, death


def _extract_birthplace_from_md(md_text: str) -> Tuple[str, str, str]:
    if not isinstance(md_text, str) or not md_text.strip():
        return "", "", ""
    m = re.search(r"\*\*出生\*\*[:：]\s*([^\n]+)", md_text)
    if not m:
        m = re.search(r"(?:出生)[:：]\s*([^\n]+)", md_text)
    if not m:
        return "", "", ""
    text = m.group(1).strip()
    parts = [p.strip() for p in re.split(r"[，,]", text) if p.strip()]
    loc = parts[-1] if parts else text
    loc = re.sub(r"^一说[^，,]+[，,]\s*", "", loc).strip()
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
    return loc, ancient, modern


def _extract_relations(md_text: str) -> List[str]:
    text = md_text
    patterns = [
        r"(?:父亲|父)[：:\s]+([^\n]+)",
        r"(?:母亲|母)[：:\s]+([^\n]+)",
        r"(?:兄长|兄)[：:\s]+([^\n]+)",
        r"(?:弟弟|弟)[：:\s]+([^\n]+)",
        r"(?:姐姐|姐)[：:\s]+([^\n]+)",
        r"(?:妹妹|妹)[：:\s]+([^\n]+)",
        r"(?:子|儿子|女儿)[：:\s]+([^\n]+)",
        r"(?:配偶|妻子|丈夫)[：:\s]+([^\n]+)",
        r"(?:师从|师事|老师|导师)[：:\s]+([^\n]+)",
    ]
    out: List[str] = []
    for pat in patterns:
        for m in re.finditer(pat, text):
            s = str(m.group(1) or "").strip()
            if not s:
                continue
            s = re.sub(r"[，。；;].*$", "", s).strip()
            parts = re.split(r"[、,，/｜|]", s)
            for p in parts:
                n = re.sub(r"[\s\(\)（）\[\]【】《》<>\"“”‘’·•]+", "", p).strip()
                if 1 < len(n) <= 10:
                    out.append(n)
    seen = set()
    dedup: List[str] = []
    for x in out:
        if x in seen:
            continue
        seen.add(x)
        dedup.append(x)
    return dedup[:8]


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


def _render_index_html(title: str, data_file: str) -> str:
    safe_title = title.strip() or "故事地图"
    template_path = STORY_MAP_DIR / "index.html"
    if template_path.exists():
        html = template_path.read_text(encoding="utf-8")
        html = re.sub(r"<title>[^<]*</title>", f"<title>{safe_title}</title>", html, flags=re.I)
        html = re.sub(r'const DATA_FILE = "[^"]*";', f'const DATA_FILE = "{data_file}";', html)
        return html
    return f"""<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{safe_title}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css" onerror="if(!this.dataset.f){{this.dataset.f='1';this.href='https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';}}else if(this.dataset.f==='1'){{this.dataset.f='2';this.href='https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.css';}}" />
    <script src="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js" onerror="if(!this.dataset.f){{this.dataset.f='1';this.src='https://unpkg.com/leaflet@1.9.4/dist/leaflet.js';}}else if(this.dataset.f==='1'){{this.dataset.f='2';this.src='https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.js';}}"></script>
    <style>
      body {{
        background: radial-gradient(900px 600px at 10% 0%, rgba(59,130,246,0.15), transparent 60%),
                    radial-gradient(900px 700px at 90% 10%, rgba(244,63,94,0.12), transparent 55%),
                    linear-gradient(180deg, #fff 0%, #f6f6f6 100%);
      }}
      .glass {{
        background: rgba(255,255,255,0.85);
        border: 1px solid rgba(225,225,225,0.85);
        backdrop-filter: blur(10px);
      }}
      .card {{
        border-radius: 16px;
        box-shadow: 0 10px 28px rgba(15,23,42,0.08);
      }}
      .graph {{
        background: radial-gradient(1200px 600px at 20% 0%, rgba(56,189,248,0.12), transparent 55%),
                    radial-gradient(900px 600px at 80% 20%, rgba(244,63,94,0.10), transparent 55%),
                    linear-gradient(135deg, #0b1b3a 0%, #0a1530 55%, #0c1130 100%);
      }}
      canvas {{ display:block; }}
      .tooltip {{
        position: absolute;
        pointer-events: none;
        background: rgba(15,23,42,0.88);
        color: rgba(255,255,255,0.92);
        border: 1px solid rgba(255,255,255,0.12);
        border-radius: 10px;
        padding: 10px 12px;
        max-width: 280px;
        font-size: 12px;
        line-height: 1.45;
        box-shadow: 0 12px 24px rgba(0,0,0,0.24);
        z-index: 50;
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
        background-image:
          repeating-linear-gradient(to right,
            rgba(255,255,255,0.14) 0px,
            rgba(255,255,255,0.14) 1px,
            rgba(0,0,0,0) 1px,
            rgba(0,0,0,0) 20px);
        pointer-events: none;
      }}
      .band {{
        background: rgba(255,255,255,0.06);
        border: 1px solid rgba(255,255,255,0.10);
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
    </style>
  </head>
  <body class="min-h-screen">
    <div class="max-w-5xl mx-auto px-4 py-6 space-y-4">
      <div class="glass card px-6 py-5">
        <div class="text-xl font-extrabold text-slate-900">故事地图</div>
        <div class="text-xs text-slate-500 mt-1">以人物→时空→事件为主线，探索历史人物的时空关联</div>
      </div>

      <div class="glass card px-6 py-5">
        <div class="text-sm font-bold text-slate-800 mb-2">检索人物</div>
        <div class="flex items-center gap-3">
          <input id="q" class="flex-1 px-4 py-2.5 rounded-xl border border-slate-200 bg-white outline-none focus:ring-2 focus:ring-slate-900/10" placeholder="例如：苏轼" />
          <button id="go" class="px-5 py-2.5 rounded-xl bg-slate-900 text-white text-sm font-bold hover:bg-slate-800">查看</button>
        </div>
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
          <div class="text-[11px] font-normal text-white/60 flex items-center gap-3">窗口内：<span id="activeCount">-</span><a class="underline hover:text-white/80" href="./echarts_home.html">ECharts 版</a></div>
        </div>
        <div class="px-6 pb-2 -mt-2 text-[11px] text-white/60">拖动时间窗筛选人物；悬停查看简介；点击节点进入人物页</div>

        <div class="relative px-3 pb-3 overflow-hidden">
          <div id="tabTrack" class="flex w-[200%]" style="transform: translateX(0%); transition: transform 720ms cubic-bezier(0.22, 1, 0.36, 1); will-change: transform;">
            <div class="w-1/2 pr-3 relative">
              <div class="rounded-xl overflow-hidden border border-white/10">
                <canvas id="c" width="980" height="460"></canvas>
              </div>
              <div id="tip" class="tooltip hidden"></div>
            </div>
            <div class="w-1/2 pl-3">
              <div id="chinaMap" class="rounded-xl overflow-hidden border border-white/10" style="height:460px;"></div>
            </div>
          </div>
        </div>

        <div class="px-6 pb-6">
          <div class="range-rail relative px-3 py-3">
            <div class="absolute left-3 right-3 top-3 h-[12px] rounded-lg band flex items-center justify-between px-2 text-[10px] text-white/60" id="bands"></div>
            <div class="absolute left-3 right-3 top-1/2 -translate-y-1/2 h-[34px] rounded-xl bg-white/5 border border-white/10 ticks"></div>
            <div id="sel" class="absolute top-1/2 -translate-y-1/2 h-[34px] rounded-xl bg-white/10 border border-white/15"></div>
            <div id="mBirth" class="absolute top-1/2 -translate-y-1/2 h-[34px] w-[2px] bg-emerald-300/70 hidden"></div>
            <div id="mDeath" class="absolute top-1/2 -translate-y-1/2 h-[34px] w-[2px] bg-rose-300/70 hidden"></div>
            <div id="h1" class="handle absolute top-1/2 -translate-y-1/2"></div>
            <div id="h2" class="handle absolute top-1/2 -translate-y-1/2"></div>
            <div class="absolute left-5 bottom-2 text-[10px] text-white/55" id="minLabel"></div>
            <div class="absolute right-5 bottom-2 text-[10px] text-white/55 text-right" id="maxLabel"></div>
            <div class="absolute left-1/2 -translate-x-1/2 bottom-2 text-[10px] text-white/55" id="midLabel"></div>
          </div>
          <div class="flex items-center justify-between mt-2 text-[11px] text-white/55">
            <div>起：<span id="startYear">-</span></div>
            <div>窗口跨度：约 <span id="spanYear">-</span> 年</div>
            <div>止：<span id="endYear">-</span></div>
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
      const $rail = $sel.parentElement;
      const $bands = document.getElementById("bands");
      const $mBirth = document.getElementById("mBirth");
      const $mDeath = document.getElementById("mDeath");
      const $activeCount = document.getElementById("activeCount");
      const $startYear = document.getElementById("startYear");
      const $endYear = document.getElementById("endYear");
      const $spanYear = document.getElementById("spanYear");
      const $minLabel = document.getElementById("minLabel");
      const $maxLabel = document.getElementById("maxLabel");
      const $midLabel = document.getElementById("midLabel");
      const $tabTrack = document.getElementById("tabTrack");
      const $tabGraph = document.getElementById("tabGraph");
      const $tabMap = document.getElementById("tabMap");
      const $chinaMap = document.getElementById("chinaMap");

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
        if (y < 600) return "#60a5fa";
        if (y < 907) return "#f59e0b";
        if (y < 1279) return "#a855f7";
        if (y < 1644) return "#10b981";
        if (y < 1911) return "#f97316";
        return "#eab308";
      }};

      const esc = (s) => String(s || "").replace(/[&<>\"']/g, (c) => ({{"&":"&amp;","<":"&lt;",">":"&gt;","\\\"":"&quot;","'":"&#39;"}})[c]);

      let nodes = [];
      let edges = [];
      let neigh = [];
      let minYear = -800;
      let maxYear = 1840;
      let startYear = 0;
      let endYear = 1840;
      let dragMode = "";
      let dragStartX = 0;
      let dragStartA = 0;
      let dragStartB = 0;
      let hover = null;

      const toT = (year) => (year - minYear) / (maxYear - minYear);
      const fromT = (t) => Math.round(minYear + t * (maxYear - minYear));

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
        $startYear.textContent = String(startYear);
        $endYear.textContent = String(endYear);
        $spanYear.textContent = String(Math.max(0, endYear - startYear));
        $minLabel.textContent = "前800";
        $maxLabel.textContent = String(maxYear);
        $midLabel.textContent = "0";
      }};

      const inWindow = (n) => {{
        const b = n.birth_year;
        if (b == null) return false;
        return b >= startYear && b <= endYear;
      }};

      const updateActiveCount = () => {{
        let c = 0;
        for (const n of nodes) {{
          if (inWindow(n)) c += 1;
        }}
        if ($activeCount) $activeCount.textContent = String(c);
      }};

      const renderBands = () => {{
        if (!$bands) return;
        const bands = [
          {{ name: "春秋战国", a: -800, b: -221 }},
          {{ name: "秦", a: -221, b: -206 }},
          {{ name: "汉", a: -206, b: 220 }},
          {{ name: "魏晋南北", a: 220, b: 589 }},
          {{ name: "隋唐", a: 589, b: 907 }},
          {{ name: "宋", a: 960, b: 1279 }},
          {{ name: "元", a: 1271, b: 1368 }},
          {{ name: "明", a: 1368, b: 1644 }},
          {{ name: "清", a: 1644, b: 1840 }},
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
          pieces.push(`<div style="position:absolute;left:${{left}};width:${{width}};top:0;bottom:0;display:flex;align-items:center;justify-content:center;overflow:hidden;background:${{bg}};border-right:1px solid rgba(255,255,255,0.12);">${{esc(b.name)}}</div>`);
        }}
        $bands.innerHTML = pieces.join("");
        $bands.style.position = "absolute";
      }};

      const draw = () => {{
        ctx.clearRect(0, 0, W, H);
        ctx.fillStyle = "rgba(0,0,0,0)";
        ctx.fillRect(0, 0, W, H);

        ctx.globalCompositeOperation = "source-over";
        if (edges.length) {{
          ctx.lineWidth = 1;
          ctx.strokeStyle = "rgba(255,255,255,0.10)";
          ctx.globalAlpha = 0.10;
          for (const e of edges) {{
            const a = nodes[e.a];
            const b = nodes[e.b];
            if (!a || !b) continue;
            ctx.beginPath();
            ctx.moveTo(a.x, a.y);
            ctx.lineTo(b.x, b.y);
            ctx.stroke();
          }}
          ctx.globalAlpha = 1.0;

          ctx.strokeStyle = "rgba(147,197,253,0.60)";
          ctx.globalAlpha = 0.25;
          for (const e of edges) {{
            const a = nodes[e.a];
            const b = nodes[e.b];
            if (!a || !b) continue;
            if (!(inWindow(a) && inWindow(b))) continue;
            ctx.beginPath();
            ctx.moveTo(a.x, a.y);
            ctx.lineTo(b.x, b.y);
            ctx.stroke();
          }}
          ctx.globalAlpha = 1.0;

          if (hover && typeof hover._idx === "number") {{
            const i = hover._idx;
            const ns = neigh[i] || [];
            ctx.strokeStyle = "rgba(34,197,94,0.85)";
            ctx.lineWidth = 1.5;
            ctx.globalAlpha = 0.55;
            for (const j of ns) {{
              const a = nodes[i];
              const b = nodes[j];
              if (!a || !b) continue;
              if (!(inWindow(a) && inWindow(b))) continue;
              ctx.beginPath();
              ctx.moveTo(a.x, a.y);
              ctx.lineTo(b.x, b.y);
              ctx.stroke();
            }}
            ctx.globalAlpha = 1.0;
            ctx.lineWidth = 1;
          }}
        }}

        ctx.globalCompositeOperation = "source-over";
        for (const n of nodes) {{
          const p = (typeof n.p === "number") ? clamp(n.p, 0, 1) : (inWindow(n) ? 1 : 0);
          const active = p > 0.55;
          let r = 4.4 + p * 2.8;
          let alpha = 0.10 + p * 0.88;
          let col = p > 0 ? colorByYear(n.birth_year) : "rgba(255,255,255,0.30)";
          if (hover && hover.person === n.person) {{
            r = 9.2;
            alpha = 1.0;
            col = "#fbbf24";
          }}
          ctx.beginPath();
          ctx.fillStyle = col;
          ctx.globalAlpha = alpha;
          ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
          ctx.fill();
          if (active) {{
            ctx.beginPath();
            ctx.strokeStyle = "rgba(255,255,255,0.22)";
            ctx.globalAlpha = 0.35 + p * 0.35;
            ctx.lineWidth = 1;
            ctx.arc(n.x, n.y, r + 2.6, 0, Math.PI * 2);
            ctx.stroke();
          }}
        }}
        ctx.globalAlpha = 1.0;
        ctx.lineWidth = 1;

        if (hover) {{
          ctx.beginPath();
          ctx.strokeStyle = "rgba(255,255,255,0.75)";
          ctx.lineWidth = 2;
          ctx.arc(hover.x, hover.y, 9, 0, Math.PI * 2);
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
        let best = null;
        let bestD = 999999;
        for (const n of nodes) {{
          const dx = mx - n.x;
          const dy = my - n.y;
          const d = dx*dx + dy*dy;
          if (d < bestD && d < 16*16) {{
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
        const years = (n.birth_year != null && n.death_year != null) ? `${{n.birth_year}}-${{n.death_year}}` : (n.birth_year != null ? `${{n.birth_year}}-?` : (n.death_year != null ? `?- ${{n.death_year}}` : "未知"));
        const quote = n.quote ? `\\n${{n.quote}}` : "";
        const dynasty = String(n.dynasty || "").trim();
        const dline = dynasty ? `<div class="text-white/70 text-[11px] mt-1">时代：${{esc(dynasty)}}</div>` : "";
        const bp = String(n.birthplace || "").trim();
        const bpm = String(n.birthplace_modern || "").trim();
        const bpline = bp ? `<div class="text-white/70 text-[11px] mt-1">籍贯：${{esc(bp)}}${{bpm ? `（${{esc(bpm)}}）` : ""}}</div>` : "";
        $tip.innerHTML = `<div class="font-bold text-white/95">${{esc(n.person)}}</div><div class="text-white/70 text-[11px] mt-1">生卒：${{esc(years)}}</div>${{dline}}${{bpline}}<div class="text-white/85 text-[11px] mt-1 whitespace-pre-wrap">${{esc(quote).replace(/^\\n/,'')}}</div>`;
        const rect = $c.getBoundingClientRect();
        let left = clientX - rect.left + 10;
        let top = clientY - rect.top + 10;
        const tw = 260;
        const th = 92;
        if (left + tw > rect.width - 8) left = Math.max(8, clientX - rect.left - tw - 10);
        if (top + th > rect.height - 8) top = Math.max(8, clientY - rect.top - th - 10);
        $tip.style.left = left + "px";
        $tip.style.top = top + "px";
        $tip.classList.remove("hidden");
        setHoverMarkers(n);
      }};

      const openPerson = (name) => {{
        const q = String(name || "").trim();
        if (!q) return;
        const n = nodes.find((x) => x.person === q);
        if (n && n.file) {{
          const href = "./" + encodeURIComponent(n.file).replace(/%2F/g, "/");
          window.location.href = href;
          return;
        }}
        alert("该人物暂无已生成的人物页（HTML）。");
      }};

      $go.addEventListener("click", () => openPerson($q.value));
      $q.addEventListener("keydown", (e) => {{
        if (e.key === "Enter") openPerson($q.value);
      }});

      let currentTab = "graph";
      let mapInited = false;
      let map = null;
      let geoLayer = null;
      let markers = [];
      let mapRenderer = null;

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
        }}
      }};

      const initMapOnce = () => {{
        if (mapInited) return;
        if (!$chinaMap) return;
        if (typeof L === "undefined") return;
        mapInited = true;
        mapRenderer = L.canvas({{ padding: 0.5 }});
        map = L.map($chinaMap, {{
          zoomControl: false,
          attributionControl: false,
          scrollWheelZoom: true,
          preferCanvas: true,
          renderer: mapRenderer,
        }}).setView([35.5, 105.0], 4);

        fetch("https://geo.datav.aliyun.com/areas_v3/bound/100000_full.json").then((r) => r.json()).then((gj) => {{
          try {{
            geoLayer = L.geoJSON(gj, {{
              style: () => ({{
                color: "rgba(255,255,255,0.28)",
                weight: 1,
                fillColor: "rgba(59,130,246,0.06)",
                fillOpacity: 1,
              }})
            }}).addTo(map);
            map.fitBounds(geoLayer.getBounds(), {{ padding: [12, 12] }});
          }} catch (_) {{}}
        }}).catch(() => {{}});

        const addMarker = (n) => {{
          const lat = n.birth_lat;
          const lng = n.birth_lng;
          if (typeof lat !== "number" || typeof lng !== "number") return;
          const mk = L.circleMarker([lat, lng], {{
            radius: 4.5,
            color: "rgba(255,255,255,0.35)",
            weight: 1,
            fillColor: "rgba(255,255,255,0.28)",
            fillOpacity: 0.4,
            renderer: mapRenderer,
          }});
          mk.on("click", () => openPerson(n.person));
          mk.addTo(map);
          markers.push({{ mk, n }});
        }};

        for (const n of nodes) addMarker(n);
        updateMapMarkers();
      }};

      const updateMapMarkers = () => {{
        if (!mapInited) return;
        for (const it of markers) {{
          const n = it.n;
          const active = inWindow(n);
          it.mk.setStyle({{
            radius: active ? 6.2 : 4.2,
            color: active ? "rgba(34,197,94,0.65)" : "rgba(255,255,255,0.20)",
            weight: active ? 1.2 : 1,
            fillColor: active ? "rgba(34,197,94,0.55)" : "rgba(255,255,255,0.20)",
            fillOpacity: active ? 0.85 : 0.30,
          }});
        }}
      }};

      if ($tabGraph) $tabGraph.addEventListener("click", () => setTab("graph"));
      if ($tabMap) $tabMap.addEventListener("click", () => setTab("map"));
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
        if (n) openPerson(n.person);
      }});

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
        const m = hitTestHandle(e);
        if (!m) return;
        dragMode = m;
        dragStartX = e.clientX;
        dragStartA = startYear;
        dragStartB = endYear;
        if ($rail.setPointerCapture) {{
          try {{ $rail.setPointerCapture(e.pointerId); }} catch (_) {{}}
        }}
        if (e.stopPropagation) e.stopPropagation();
        e.preventDefault();
      }};

      const onMove = (e) => {{
        if (!dragMode) return;
        const r = railRect();
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
        if (startYear >= endYear) {{
          if (dragMode === "left") startYear = endYear - 1;
          else endYear = startYear + 1;
        }}
        setHandles();
        updateActiveCount();
        updateMapMarkers();
        draw();
      }};

      const onUp = () => {{
        if (!dragMode) return;
        dragMode = "";
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
        updateMapMarkers();
        draw();
      }});

      const groupKey = (n) => {{
        const d = String(n.dynasty || "").trim();
        if (d) return d.slice(0, 6);
        const name = String(n.person || "").trim();
        return name ? name.slice(0, 1) : "？";
      }};

      const buildNeigh = () => {{
        neigh = Array.from({{ length: nodes.length }}, () => []);
        for (const e of edges) {{
          if (!e) continue;
          const a = e.a;
          const b = e.b;
          if (typeof a !== "number" || typeof b !== "number") continue;
          if (!neigh[a]) neigh[a] = [];
          if (!neigh[b]) neigh[b] = [];
          neigh[a].push(b);
          neigh[b].push(a);
        }}
      }};

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

        const tFor = (n) => {{
          const y = n.birth_year;
          if (typeof y !== "number") return null;
          return clamp((y - minYear) / (maxYear - minYear), 0, 1);
        }};

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
          const t = tFor(n);
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
        edges = (data.edges || []).filter((e) => e && typeof e.a === "number" && typeof e.b === "number");
        minYear = data.min_year ?? -800;
        maxYear = data.max_year ?? 1840;
        startYear = data.default_start ?? 0;
        endYear = data.default_end ?? 1840;
        buildNeigh();
        renderBands();
        setHandles();
        updateActiveCount();
        draw();
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

    latest_html = _scan_latest_html(story_map_dir)

    md_names = _scan_people_from_story_md(story_md_dir)
    html_names = _scan_people_from_story_map_html(story_map_dir)
    names = sorted(set(md_names) | set(html_names))

    spotlight_data = _read_json(spotlight_path)
    spotlight_items = spotlight_data.get("items") if isinstance(spotlight_data, dict) else {}
    if not isinstance(spotlight_items, dict):
        spotlight_items = {}

    nodes: List[Dict[str, Any]] = []
    min_year: Optional[int] = None
    max_year: Optional[int] = None
    for name in names:
        md_path = story_md_dir / f"{name}.md"
        birth_year = None
        death_year = None
        dynasty = ""
        relations: List[str] = []
        birthplace_raw = ""
        birthplace_ancient = ""
        birthplace_modern = ""
        if md_path.exists():
            md_text = md_path.read_text(encoding="utf-8")
            birth_year, death_year = _extract_years_from_md(md_text)
            dynasty = _dynasty_hint_from_md(md_text)
            relations = _extract_relations(md_text)
            birthplace_raw, birthplace_ancient, birthplace_modern = _extract_birthplace_from_md(md_text)
        if birth_year is not None:
            min_year = birth_year if min_year is None else min(min_year, birth_year)
            max_year = birth_year if max_year is None else max(max_year, birth_year)
        if death_year is not None:
            min_year = death_year if min_year is None else min(min_year, death_year)
            max_year = death_year if max_year is None else max(max_year, death_year)

        spot = spotlight_items.get(name)
        quote = ""
        if isinstance(spot, dict):
            quote = _pick_quote(spot)

        html_entry = latest_html.get(name)
        birth_lat = None
        birth_lng = None
        if html_entry:
            lat, lng, bp, dyn2 = _extract_birth_from_story_map_html(story_map_dir / html_entry.file)
            birth_lat = lat
            birth_lng = lng
            if not dynasty and dyn2:
                dynasty = dyn2
            if not birthplace_raw and bp:
                birthplace_raw, birthplace_ancient, birthplace_modern = _extract_birthplace_from_md(f"**出生**：{bp}")
        nodes.append(
            {
                "person": name,
                "birth_year": birth_year,
                "death_year": death_year,
                "dynasty": dynasty,
                "quote": quote,
                "birthplace": birthplace_ancient,
                "birthplace_raw": birthplace_raw,
                "birthplace_modern": birthplace_modern,
                "birth_lat": birth_lat,
                "birth_lng": birth_lng,
                "file": html_entry.file if html_entry else "",
                "seed": _sha1_int(name),
                "relations": relations,
            }
        )

    person_to_idx = {n["person"]: i for i, n in enumerate(nodes)}
    edges: List[Dict[str, int]] = []
    kg_edges: List[Dict[str, int]] = []

    def add_links(keys: List[int], k: int, max_edges: int) -> None:
        nonlocal edges
        for i, a in enumerate(keys):
            for j in range(1, k + 1):
                if i + j >= len(keys):
                    break
                b = keys[i + j]
                edges.append({"a": a, "b": b})
                if len(edges) >= max_edges:
                    return

    by_dyn: Dict[str, List[int]] = {}
    by_surname: Dict[str, List[int]] = {}
    for i, n in enumerate(nodes):
        d = str(n.get("dynasty") or "").strip()
        if d:
            dk = d[:6]
            by_dyn.setdefault(dk, []).append(i)
        p = str(n.get("person") or "").strip()
        if p:
            by_surname.setdefault(p[0], []).append(i)

    def sort_key(i: int) -> Tuple[int, int, str]:
        n = nodes[i]
        by = n.get("birth_year")
        dy = n.get("death_year")
        a = int(by) if isinstance(by, int) else 999999
        b = int(dy) if isinstance(dy, int) else 999999
        return (a, b, str(n.get("person") or ""))

    max_edges = 2200
    for dk, arr in sorted(by_dyn.items(), key=lambda x: x[0]):
        ids = sorted(arr, key=sort_key)
        add_links(ids[:80], k=6, max_edges=max_edges)
        if len(edges) >= max_edges:
            break

    if len(edges) < max_edges:
        for sk, arr in sorted(by_surname.items(), key=lambda x: x[0]):
            ids = sorted(arr, key=sort_key)
            add_links(ids[:40], k=3, max_edges=max_edges)
            if len(edges) >= max_edges:
                break

    rel_edges = 0
    for i, n in enumerate(nodes):
        rels = n.get("relations") if isinstance(n.get("relations"), list) else []
        for r in rels:
            j = person_to_idx.get(r)
            if j is None or j == i:
                continue
            edges.append({"a": i, "b": j})
            rel_edges += 1
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
                a = str(e.get("source") or "").strip()
                b = str(e.get("target") or "").strip()
                ia = person_to_idx.get(a)
                ib = person_to_idx.get(b)
                if ia is None or ib is None or ia == ib:
                    continue
                kg_edges.append({"a": ia, "b": ib})
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
    out_data.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    out_index.write_text(_render_index_html(args.title, out_data.name), encoding="utf-8")
    print(json.dumps({"ok": True, "index": str(out_index), "data": str(out_data), "count": len(nodes)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

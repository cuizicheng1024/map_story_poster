import json
from typing import Dict, List




def build_info_panel_html(title: str, fields: Dict[str, str]) -> str:
    """
    构建基础地图页左上角的信息面板。
    """
    wrap = ['<div class="bio-panel"><h3>人物简介</h3><div class="bio-body">']
    order = ["朝代", "身份", "生卒年", "主要事件", "主要作品", "历史地位", "一生行程"]
    for k in order:
        val = fields.get(k, "")
        if val:
            esc = val.replace("<", "&lt;").replace(">", "&gt;")
            wrap.append(f'<div class="bio-row"><span class="bio-label">{k}：</span>{esc}</div>')
    wrap.append("</div></div>")
    css = """
<style>
.bio-panel{position:fixed;top:12px;left:12px;z-index:9999;max-width:380px;background:#ffffffee;padding:12px 14px;border-radius:8px;box-shadow:0 2px 12px rgba(0,0,0,.15);font:14px/1.4 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial;}
.bio-panel h3{margin:0 0 8px 0;font-size:16px;}
.bio-row{margin:4px 0;}
.bio-label{color:#666;margin-right:4px;}
</style>
"""
    return css + "".join(wrap)


def render_profile_html(data: Dict[str, object]) -> str:
    """
    渲染完整人物页（头像 + 统计卡片 + 足迹时间轴 + 地图）。
    """
    payload = json.dumps(data, ensure_ascii=False).replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    name = (data.get("person", {}) or {}).get("name", "")
    title = f"{name}的人生足迹地图" if name else "人生足迹地图"
    html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__TITLE__</title>
<script src="https://cdn.tailwindcss.com"></script>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css" onerror="if(!this.dataset.f){this.dataset.f='1';this.href='https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';}else if(this.dataset.f==='1'){this.dataset.f='2';this.href='https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.css';}" />
<script src="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js" onerror="if(!this.dataset.f){this.dataset.f='1';this.src='https://unpkg.com/leaflet@1.9.4/dist/leaflet.js';}else if(this.dataset.f==='1'){this.dataset.f='2';this.src='https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.js';}"></script>
<script src="https://cdn.jsdelivr.net/npm/react@18/umd/react.production.min.js" onerror="if(!this.dataset.f){this.dataset.f='1';this.src='https://unpkg.com/react@18/umd/react.production.min.js';}else if(this.dataset.f==='1'){this.dataset.f='2';this.src='https://cdnjs.cloudflare.com/ajax/libs/react/18.2.0/umd/react.production.min.js';}"></script>
<script src="https://cdn.jsdelivr.net/npm/react-dom@18/umd/react-dom.production.min.js" onerror="if(!this.dataset.f){this.dataset.f='1';this.src='https://unpkg.com/react-dom@18/umd/react-dom.production.min.js';}else if(this.dataset.f==='1'){this.dataset.f='2';this.src='https://cdnjs.cloudflare.com/ajax/libs/react-dom/18.2.0/umd/react-dom.production.min.js';}"></script>
<script src="https://cdn.jsdelivr.net/npm/@babel/standalone@7.24.7/babel.min.js" onerror="if(!this.dataset.f){this.dataset.f='1';this.src='https://unpkg.com/@babel/standalone@7.24.7/babel.min.js';}else if(this.dataset.f==='1'){this.dataset.f='2';this.src='https://cdnjs.cloudflare.com/ajax/libs/babel-standalone/7.24.7/babel.min.js';}"></script>
<style>
body {
  font-family: 'Noto Serif SC', serif;
  background-color: #fdf6e3;
  color: #2c3e50;
}
#map {
  height: 620px;
  width: 100%;
  border-radius: 8px;
  box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
  z-index: 1;
}
.custom-scrollbar::-webkit-scrollbar {
  width: 6px;
}
.custom-scrollbar::-webkit-scrollbar-track {
  background: #f1f1f1;
}
.custom-scrollbar::-webkit-scrollbar-thumb {
  background: #c0392b;
  border-radius: 10px;
}
.glass-panel {
  background: rgba(255, 255, 255, 0.8);
  backdrop-filter: blur(4px);
  border: 1px solid rgba(200, 180, 150, 0.3);
}
.desc-clamp {
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.age-marker {
  background: transparent;
  border: none;
}
.age-badge {
  background: rgba(255, 255, 255, 0.65);
  color: #7c2d12;
  padding: 2px 6px;
  border-radius: 10px;
  font-size: 10px;
  border: 1px solid rgba(200, 180, 150, 0.5);
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.12);
  white-space: nowrap;
}
.export-bar {
  position: fixed;
  top: 14px;
  right: 16px;
  display: flex;
  gap: 8px;
  z-index: 9999;
}
.export-btn {
  background: rgba(255, 255, 255, 0.88);
  border: 1px solid rgba(200, 180, 150, 0.5);
  padding: 6px 10px;
  border-radius: 999px;
  font-size: 12px;
  color: #7c2d12;
  cursor: pointer;
}
.export-btn:hover {
  background: rgba(255, 255, 255, 0.98);
}
</style>
</head>
<body class="p-4 md:p-8">
<div class="export-bar">
  <button class="export-btn" data-export="geojson">GeoJSON</button>
  <button class="export-btn" data-export="csv">CSV</button>
  <button class="export-btn" data-export="markdown">Markdown</button>
  <button class="export-btn" data-export="share">分享链接</button>
    </div>
    <div id="root"></div>
    <script type="text/babel" data-presets="env,react">
      const { useState, useEffect, useRef, useMemo } = React;
      const data = __DATA__;
      window.__EXPORT_DATA__ = data;
const locations = data.locations || [];
const textbookPoints = String(data.textbookPoints || '').trim();
const examPoints = String(data.examPoints || '').trim();
const mapStyle = data.mapStyle || {};
const mergedTeachingPoints = [textbookPoints, examPoints].filter(Boolean).join('\n\n');
const markerStyles = mapStyle.markers || {};
const defaultMarkerStyles = {
  normal: {
    iconUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png',
    color: '#3498db'
  },
  birth: {
    iconUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png',
    color: '#2ecc71'
  },
  death: {
    iconUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png',
    color: '#e74c3c'
  }
};
const highlights = data.person?.highlights || {};
const calculateDistance = (lat1, lon1, lat2, lon2) => {
  const R = 6371;
  const dLat = (lat2 - lat1) * Math.PI / 180;
  const dLon = (lon2 - lon1) * Math.PI / 180;
  const a = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
            Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
            Math.sin(dLon / 2) * Math.sin(dLon / 2);
  const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  return R * c;
};
const extractYear = (text) => {
  if (!text) return null;
  const match = String(text).match(/(\\d{3,4})\\s*年/);
  return match ? parseInt(match[1], 10) : null;
};

const renderInline = (text) => {
  const raw = String(text || '');
  // 支持 Markdown 的 **加粗**（非贪婪匹配，避免跨段落/跨多段加粗误吞）
  const parts = raw.split(/(\*\*.*?\*\*)/g).filter(Boolean);
  return parts.map((p, idx) => {
    const m = p.match(/^\*\*(.+?)\*\*$/);
    if (m) {
      return <strong key={idx} className="font-semibold text-gray-800">{m[1]}</strong>;
    }
    return <span key={idx}>{p}</span>;
  });
};

const renderTextbookPoints = (raw) => {
  // 兼容两种换行输入：
  // 1) 真实换行：LF / CRLF / CR
  // 2) 字面量换行：反斜杠 + n（可能出现 1 个或多个反斜杠）
  // 说明：这段 JS 位于 Python 三引号字符串里，为避免 Python 对转义序列做预解析，
  // 这里用 charCode 构造换行/反斜杠字符，再用正则 split 一次性切分。
  const LF = String.fromCharCode(10); // line feed
  const CR = String.fromCharCode(13); // carriage return
  const BS = String.fromCharCode(92); // backslash

  // 用 RegExp 构造器 split：匹配 CRLF / CR / LF / (一个或多个反斜杠 + n)
  const BS_RE = BS + BS;
  const splitRe = new RegExp(`${CR}${LF}|${CR}|${LF}|${BS_RE}+n`, 'g');

  const lines = String(raw || '').split(splitRe);
  return lines.map((line, idx) => {
    const rawLine = String(line || '');
    const leadingSpaces = rawLine.match(/^\\s*/)[0].length;
    const t = rawLine.trim();
    if (!t) return <div key={idx} className="h-2" />;

    // Markdown 列表层级：0-1 空格为一级，2-3 空格为二级，4+ 空格为三级
    const level = leadingSpaces >= 4 ? 3 : (leadingSpaces >= 2 ? 2 : 1);
    const indentClass = level === 1 ? 'ml-0' : (level === 2 ? 'ml-4' : 'ml-8');

    if (t.startsWith('### ')) {
      const heading = t.replace(/^###\s*/, '');
      return (
        <h3 key={idx} className="mt-2 text-base font-bold text-[#7c2d12]">
          {renderInline(heading)}
        </h3>
      );
    }

    if (t.startsWith('#### ')) {
      const heading = t.replace(/^####\s*/, '');
      return (
        <h4 key={idx} className="mt-2 text-sm font-semibold text-gray-700">
          {renderInline(heading)}
        </h4>
      );
    }

    if (t.startsWith('- ')) {
      const body = t.slice(2).trim();
      const bullet = level === 1 ? '•' : (level === 2 ? '◦' : '▪');
      return (
        <div key={idx} className={`flex ${indentClass} gap-2 text-sm leading-relaxed text-gray-700`}>
          <span className="mt-[2px] text-[#c0392b]">{bullet}</span>
          <div>{renderInline(body)}</div>
        </div>
      );
    }

    const ordered = t.match(/^(\d+)\.\s+(.*)$/);
    if (ordered) {
      return (
        <div key={idx} className={`flex ${indentClass} gap-2 text-sm leading-relaxed text-gray-700`}>
          <span className="mt-[2px] text-gray-500">{ordered[1]}.</span>
          <div>{renderInline(ordered[2])}</div>
        </div>
      );
    }

    return (
      <p key={idx} className="text-sm leading-relaxed text-gray-700">
        {renderInline(t)}
      </p>
    );
  });
};

const App = () => {
  const [selectedLoc, setSelectedLoc] = useState(locations[0] || null);
  const [activeIndex, setActiveIndex] = useState(0);
  const [showFullDesc, setShowFullDesc] = useState(false);
  const [splitPct, setSplitPct] = useState(30);
  const mapRef = useRef(null);
  const splitRef = useRef(null);
  const draggingRef = useRef(false);
  const totalEvents = locations.length;
  const avatarUrl = data.person?.avatar || '';
  const description = data.person?.description || '';
  const quoteText = data.person?.quote || data.person?.title || '';
  const relatedWorks = Array.isArray(highlights.works) ? highlights.works : [];
  const relatedReviews = Array.isArray(highlights.reviews) ? highlights.reviews : [];
  const relatedHonor = String(highlights.honor || data.person?.title || '').trim();
  const relatedStatus = String(highlights.status || '').trim();
  const relatedIdentities = String(highlights.identities || '').trim();
  const descSegments = useMemo(() => {
    if (!description) return [];
    const parts = description.split(/([。！？])/);
    const segs = [];
    for (let i = 0; i < parts.length; i += 2) {
      const seg = `${parts[i] || ''}${parts[i + 1] || ''}`.trim();
      if (seg) segs.push(seg);
    }
    return segs;
  }, [description]);
  const isLongDesc = useMemo(() => description.length > 120 || descSegments.length > 3, [description, descSegments]);
  const stats = useMemo(() => {
    let totalDist = 0;
    for (let i = 0; i < locations.length - 1; i++) {
      totalDist += calculateDistance(
        locations[i].lat, locations[i].lng,
        locations[i+1].lat, locations[i+1].lng
      );
    }
    const regions = new Set(locations.map(l => (l.modernName || l.name || '').split(/[\\s/]/)[0]).filter(Boolean));
    const lifespanDigits = parseInt((data.person?.lifespan || '').replace(/\\D/g, ''), 10);
    const b = extractYear(data.person?.birth?.date || '');
    const d = extractYear(data.person?.death?.date || '');
    let yearsValue = Number.isFinite(lifespanDigits) && lifespanDigits > 0 ? lifespanDigits : null;
    if (yearsValue === null && b && d && d >= b && (d - b) < 200) {
      yearsValue = d - b + 1;
    }
    const yearsLabel = yearsValue === null ? '存疑' : String(yearsValue);
    return {
      distance: Math.round(totalDist),
      regions: regions.size,
      events: locations.length,
      yearsValue,
      yearsLabel
    };
  }, []);
  const birthDate = data.person?.birth?.date || '';
  const deathDate = data.person?.death?.date || '';
  const lifeDates = birthDate || deathDate
    ? `${birthDate}${birthDate && deathDate ? '-' : ''}${deathDate}`
    : (data.person?.lifespan || '');
  const birthYear = useMemo(() => extractYear(birthDate), [birthDate]);
  const getAgeText = (loc) => {
    const year = extractYear(loc.time || '');
    if (!birthYear || !year) return '';
    const age = year - birthYear + 1;
    if (!Number.isFinite(age) || age <= 0 || age > 150) return '';
    return `${age}岁`;
  };
  const renderDescription = () => {
    if (!description) return null;
    return (
      <div className="mb-4">
        <div className={`text-gray-600 leading-relaxed ${showFullDesc ? '' : 'desc-clamp'}`}>
          {descSegments.map((seg, idx) => (
            <span key={idx} className="block">{seg}</span>
          ))}
        </div>
        {isLongDesc ? (
          <button
            onClick={() => setShowFullDesc(!showFullDesc)}
            className="text-xs text-[#c0392b] mt-1"
          >
            {showFullDesc ? '收起' : '展开'}
          </button>
        ) : null}
      </div>
    );
  };
  const changeEvent = (nextIndex) => {
    if (nextIndex < 0 || nextIndex >= totalEvents || nextIndex === activeIndex) return;
    const nextLoc = locations[nextIndex] || null;
    setActiveIndex(nextIndex);
    setSelectedLoc(nextLoc);
    if (mapRef.current && nextLoc) {
      mapRef.current.setView([nextLoc.lat, nextLoc.lng], 7);
    }
  };
  useEffect(() => {
    if (!mapRef.current) {
      const first = locations[0] || { lat: 35, lng: 105 };
      const map = L.map('map', { zoomControl: false }).setView([first.lat, first.lng], locations.length ? 4 : 4);
      L.control.zoom({ position: 'topright' }).addTo(map);
      const tileSources = [
        {
          url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
          options: { attribution: '&copy; OpenStreetMap contributors' }
        },
        {
          url: 'https://{s}.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png',
          options: { attribution: '&copy; OpenStreetMap contributors' }
        },
        {
          url: 'https://{s}.tile.openstreetmap.de/{z}/{x}/{y}.png',
          options: { attribution: '&copy; OpenStreetMap contributors' }
        },
        {
          url: 'https://webrd{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=7&x={x}&y={y}&z={z}',
          options: { subdomains: ['0', '1', '2', '3'], attribution: '&copy; 高德地图' }
        },
        {
          url: 'https://webst{s}.is.autonavi.com/appmaptile?style=7&x={x}&y={y}&z={z}',
          options: { subdomains: ['0', '1', '2', '3'], attribution: '&copy; 高德地图' }
        }
      ];
      const addTileLayer = (mapInstance) => {
        let idx = 0;
        let errorCount = 0;
        let layer = L.tileLayer(tileSources[idx].url, tileSources[idx].options);
        const handleError = () => {
          errorCount += 1;
          if (errorCount >= 8) {
            idx += 1;
            if (idx >= tileSources.length) {
              return;
            }
            mapInstance.removeLayer(layer);
            layer = L.tileLayer(tileSources[idx].url, tileSources[idx].options);
            layer.addTo(mapInstance);
            errorCount = 0;
            layer.on('tileerror', handleError);
          }
        };
        layer.on('tileerror', handleError);
        layer.addTo(mapInstance);
      };
      addTileLayer(map);
      const resolveMarkerStyle = (type) => {
        if (markerStyles[type]) return markerStyles[type];
        if (markerStyles.normal) return markerStyles.normal;
        return defaultMarkerStyles[type] || defaultMarkerStyles.normal;
      };
      const markerIcon = (type) => {
        const iconUrl = resolveMarkerStyle(type).iconUrl || defaultMarkerStyles.normal.iconUrl;
        return L.icon({
          iconUrl,
          iconSize: [25, 34],
          iconAnchor: [12, 34],
          popupAnchor: [0, -30]
        });
      };
      const pathCoords = locations.map(l => [l.lat, l.lng]);
      for (let i = 0; i < pathCoords.length - 1; i++) {
        const opacity = 0.25 + (i / pathCoords.length) * 0.6;
        L.polyline([pathCoords[i], pathCoords[i+1]], {
          color: mapStyle.pathColor || '#1e40af',
          weight: 3,
          opacity: opacity
        }).addTo(map);
      }
      locations.forEach((loc, idx) => {
        const style = resolveMarkerStyle(loc.type || 'normal');
        L.circleMarker([loc.lat, loc.lng], {
          radius: 10,
          color: style.color || defaultMarkerStyles.normal.color,
          fillColor: style.color || defaultMarkerStyles.normal.color,
          fillOpacity: 0.35,
          weight: 2
        }).addTo(map);
        const marker = L.marker([loc.lat, loc.lng], { icon: markerIcon(loc.type || 'normal') }).addTo(map);
        marker.on('click', () => {
          setSelectedLoc(loc);
          setActiveIndex(idx);
          map.setView([loc.lat, loc.lng], 7);
        });
        const ageText = getAgeText(loc);
        if (ageText) {
          const ageIcon = L.divIcon({
            className: 'age-marker',
            html: `<div class="age-badge">${ageText}</div>`,
            iconSize: [44, 20],
            iconAnchor: [22, 30]
          });
          L.marker([loc.lat, loc.lng], { icon: ageIcon, interactive: false }).addTo(map);
        }
      });
      if (pathCoords.length > 1) {
        const bounds = L.latLngBounds(pathCoords);
        map.fitBounds(bounds, { padding: [48, 48], maxZoom: 6 });
      } else if (pathCoords.length === 1) {
        map.setView(pathCoords[0], 6);
      }
      mapRef.current = map;
    }
  }, []);
  useEffect(() => {
    const onMove = (e) => {
      if (!draggingRef.current || !splitRef.current) return;
      const rect = splitRef.current.getBoundingClientRect();
      const next = ((e.clientX - rect.left) / rect.width) * 100;
      if (!Number.isFinite(next)) return;
      const clamped = Math.min(60, Math.max(20, next));
      setSplitPct(clamped);
    };
    const onUp = () => {
      draggingRef.current = false;
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
  }, []);
  const handleLocClick = (loc, idx) => {
    setSelectedLoc(loc);
    if (typeof idx === 'number') {
      setActiveIndex(idx);
    }
    if (mapRef.current) {
      mapRef.current.setView([loc.lat, loc.lng], 7);
    }
  };
  return (
    <div className="max-w-screen-2xl mx-auto space-y-6">
      <header className="glass-panel p-6 rounded-xl shadow-sm border-l-8 border-[#c0392b] flex flex-col md:flex-row gap-6 items-center">
        <div className="w-32 h-32 bg-[#e5e7eb] rounded-full flex items-center justify-center border-4 border-white shadow-inner overflow-hidden">
          {avatarUrl ? (
            <img src={avatarUrl} alt={data.person?.name || ''} className="w-full h-full object-cover" />
          ) : (
            <span className="text-gray-600 text-2xl">{data.person?.name || ''}</span>
          )}
        </div>
        <div className="flex-1 text-center md:text-left">
          <h1 className="text-4xl font-bold">{data.person.name}</h1>
          {quoteText ? (
            <p className="text-xs text-gray-500 mt-1 mb-2">{quoteText}</p>
          ) : null}
          {renderDescription()}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-center">
            <div>
              <p className="text-gray-400 text-sm">朝代</p>
              <p className="font-bold">{data.person?.dynasty || ''}</p>
            </div>
            <div>
              <p className="text-gray-400 text-sm">籍贯</p>
              <p className="font-bold">{data.person?.birthplace || ''}</p>
            </div>
            <div>
              <p className="text-gray-400 text-sm">生卒</p>
              <p className="font-bold">{lifeDates}</p>
            </div>
            <div>
              <p className="text-gray-400 text-sm">享年</p>
              <p className="font-bold">{data.person?.lifespan || ''}</p>
            </div>
          </div>
        </div>
      </header>
      <div className="space-y-6">
        <div ref={splitRef} className="flex flex-col lg:flex-row gap-6 items-stretch">
          <div
            className="glass-panel rounded-xl overflow-hidden flex flex-col h-[620px]"
            style={{ flexBasis: `${splitPct}%`, flexGrow: 0 }}
          >
            <div className="p-4 bg-[#c0392b] text-white font-bold flex items-center justify-between">
              <span>足迹时间轴</span>
              <div className="flex items-center gap-2 text-xs">
                <button
                  onClick={() => changeEvent(activeIndex - 1)}
                  disabled={activeIndex === 0}
                  className="px-2 py-1 rounded bg-white/20 disabled:opacity-40"
                >上一事件</button>
                <span className="opacity-80">{totalEvents ? activeIndex + 1 : 0} / {totalEvents}</span>
                <button
                  onClick={() => changeEvent(activeIndex + 1)}
                  disabled={activeIndex + 1 >= totalEvents}
                  className="px-2 py-1 rounded bg-white/20 disabled:opacity-40"
                >下一事件</button>
              </div>
            </div>
            <div className="flex-1 overflow-y-auto custom-scrollbar p-3 space-y-3">
              {locations.length ? (
                locations.map((loc, idx) => (
                  <div
                    key={idx}
                    onClick={() => handleLocClick(loc, idx)}
                    className={`p-3 rounded-lg cursor-pointer transition-all border-l-4 ${
                      idx === activeIndex
                        ? 'bg-white shadow-md border-[#c0392b]'
                        : 'bg-white/70 border-transparent hover:bg-white'
                    }`}
                  >
                    <div className="flex justify-between items-start mb-2">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="font-bold text-sm truncate">{loc.name}</span>
                          {loc.type === 'birth' ? (
                            <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-50 text-emerald-700 border border-emerald-200">出生</span>
                          ) : loc.type === 'death' ? (
                            <span className="text-[10px] px-1.5 py-0.5 rounded bg-rose-50 text-rose-700 border border-rose-200">去世</span>
                          ) : null}
                        </div>
                        <div className="text-[10px] text-gray-400 truncate">{loc.ancientName} → {loc.modernName}</div>
                      </div>
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-gray-100 text-gray-500">
                        {loc.time || '未知'}
                      </span>
                    </div>
                    <div className="space-y-1 text-[11px] text-gray-500 mb-2">
                      <div className="flex justify-between">
                        <span>公元纪年</span>
                        <span className="text-gray-700">{loc.time || '未知'}</span>
                      </div>
                      <div className="flex justify-between">
                        <span>停留时间</span>
                        <span className="text-gray-700">{loc.duration || '未知'}</span>
                      </div>
                    </div>
                    <p className="text-xs text-gray-500">{loc.event}</p>
                  </div>
                ))
              ) : (
                <div className="text-xs text-gray-400">暂无事件</div>
              )}
            </div>
          </div>
          <div
            className="hidden lg:block w-2 rounded bg-[#c8b496]/50 hover:bg-[#c8b496] cursor-col-resize"
            onMouseDown={(e) => {
              e.preventDefault();
              draggingRef.current = true;
            }}
          ></div>
          <div className="relative flex-1">
            <div id="map"></div>
            {selectedLoc && (
              <div className="absolute top-4 left-4 z-[1000] w-72 glass-panel p-4 rounded-xl shadow-xl border-t-4 border-[#c0392b]">
                <button
                  onClick={() => setSelectedLoc(null)}
                  className="absolute top-2 right-2 text-gray-400 hover:text-gray-600"
                >✕</button>
                <h3 className="text-xl font-bold text-[#c0392b] mb-1">{selectedLoc.name}</h3>
                <p className="text-xs text-gray-400 mb-3">{selectedLoc.ancientName} → {selectedLoc.modernName}</p>
                <div className="space-y-3 text-sm">
                  <div>
                    <p className="text-gray-400 text-[10px] uppercase font-bold">公元纪年</p>
                    <p>{selectedLoc.time || '未知'}</p>
                  </div>
                  <div>
                    <p className="text-gray-400 text-[10px] uppercase font-bold">停留时间</p>
                    <p>{selectedLoc.duration || '未知'}</p>
                  </div>
                  <div>
                    <p className="text-gray-400 text-[10px] uppercase font-bold">事迹描述</p>
                    <p className="leading-relaxed">{selectedLoc.event}</p>
                  </div>
                  {selectedLoc.poster?.png ? (
                    <div>
                      <p className="text-gray-400 text-[10px] uppercase font-bold">海报</p>
                      <a href={selectedLoc.poster.png} target="_blank" rel="noreferrer">
                        <img src={selectedLoc.poster.png} className="w-full rounded-lg border border-[#c8b496]/50" />
                      </a>
                    </div>
                  ) : null}
                  <div className="bg-[#fdf6e3] p-2 rounded border border-dashed border-[#c8b496]">
                    <p className="text-[#c0392b] text-[10px] uppercase font-bold">历史意义</p>
                    <p className="italic text-xs">{selectedLoc.significance}</p>
                  </div>
                  {selectedLoc.quoteLines?.length ? (
                    <div>
                      <p className="text-gray-400 text-[10px] uppercase font-bold">名篇名句</p>
                      <ul className="space-y-1">
                        {selectedLoc.quoteLines.map((q, idx) => (
                          <li key={idx} className="text-xs text-gray-700">{q}</li>
                        ))}
                      </ul>
                    </div>
                  ) : selectedLoc.works?.length ? (
                    <div>
                      <p className="text-gray-400 text-[10px] uppercase font-bold">名篇名句</p>
                      <ul className="list-disc pl-4 space-y-1">
                        {selectedLoc.works.map((w, idx) => (
                          <li key={idx} className="text-xs text-gray-700">《{w}》</li>
                        ))}
                      </ul>
                    </div>
                  ) : null}
                </div>
              </div>
            )}
          </div>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div className="glass-panel p-4 rounded-xl text-center">
            <p className="text-2xl mb-1">🗺️</p>
            <p className="text-xl font-bold">{stats.distance} <span className="text-xs font-normal">km</span></p>
            <p className="text-[10px] text-gray-400 uppercase">总行程估算</p>
          </div>
          <div className="glass-panel p-4 rounded-xl text-center">
            <p className="text-2xl mb-1">⏱️</p>
            <p className="text-xl font-bold">
              {stats.yearsLabel}{stats.yearsValue === null ? null : <span className="text-xs font-normal">年</span>}
            </p>
            <p className="text-[10px] text-gray-400 uppercase">生命跨度</p>
          </div>
          <div className="glass-panel p-4 rounded-xl text-center">
            <p className="text-2xl mb-1">📍</p>
            <p className="text-xl font-bold">{stats.regions} <span className="text-xs font-normal">个</span></p>
            <p className="text-[10px] text-gray-400 uppercase">覆盖地区</p>
          </div>
          <div className="glass-panel p-4 rounded-xl text-center">
            <p className="text-2xl mb-1">🌟</p>
            <p className="text-xl font-bold">{stats.events} <span className="text-xs font-normal">件</span></p>
            <p className="text-[10px] text-gray-400 uppercase">重要事迹</p>
          </div>
        </div>

        {mergedTeachingPoints ? (
          <section className="glass-panel p-6 rounded-xl shadow-sm border border-[#c8b496]/40 bg-amber-50/40">
            <div className="flex items-center justify-between gap-4 mb-3">
              <h2 className="text-lg font-bold text-[#7c2d12]">教材知识点与考点</h2>
              <span className="text-[10px] text-gray-500">面向教学</span>
            </div>
            <div className="space-y-1">
              {renderTextbookPoints(mergedTeachingPoints)}
            </div>
          </section>
        ) : null}

        {(relatedHonor || relatedStatus || relatedIdentities || relatedWorks.length || relatedReviews.length) ? (
          <section className="glass-panel p-6 rounded-xl shadow-sm border border-[#c8b496]/40 bg-white/70">
            <div className="flex items-center justify-between gap-4 mb-3">
              <h2 className="text-lg font-bold text-[#7c2d12]">人物要点</h2>
              <span className="text-[10px] text-gray-500">强相关速览</span>
            </div>
            <div className="flex flex-wrap gap-2 mb-4">
              {relatedHonor ? <span className="text-[11px] px-2 py-1 rounded-full bg-[#fdf6e3] border border-[#c8b496]/50">{relatedHonor}</span> : null}
              {data.person?.dynasty ? <span className="text-[11px] px-2 py-1 rounded-full bg-[#fdf6e3] border border-[#c8b496]/50">{data.person.dynasty}</span> : null}
              {data.person?.lifespan ? <span className="text-[11px] px-2 py-1 rounded-full bg-[#fdf6e3] border border-[#c8b496]/50">{data.person.lifespan}</span> : null}
            </div>
            {relatedStatus ? (
              <div className="mb-3">
                <p className="text-[10px] text-gray-400 uppercase font-bold mb-1">历史地位</p>
                <p className="text-sm text-gray-700 leading-relaxed">{relatedStatus}</p>
              </div>
            ) : null}
            {relatedIdentities ? (
              <div className="mb-3">
                <p className="text-[10px] text-gray-400 uppercase font-bold mb-1">身份</p>
                <p className="text-sm text-gray-700 leading-relaxed">{relatedIdentities}</p>
              </div>
            ) : null}
            {relatedWorks.length ? (
              <div className="mb-3">
                <p className="text-[10px] text-gray-400 uppercase font-bold mb-1">代表作</p>
                <div className="flex flex-wrap gap-2">
                  {relatedWorks.slice(0, 6).map((w, idx) => (
                    <span key={idx} className="text-[11px] px-2 py-1 rounded bg-gray-50 border border-gray-200">《{w}》</span>
                  ))}
                </div>
              </div>
            ) : null}
            {relatedReviews.length ? (
              <div>
                <p className="text-[10px] text-gray-400 uppercase font-bold mb-1">他人评价 / 史料</p>
                <ul className="space-y-1">
                  {relatedReviews.slice(0, 3).map((t, idx) => (
                    <li key={idx} className="text-sm text-gray-700 leading-relaxed">- {t}</li>
                  ))}
                </ul>
              </div>
            ) : null}
          </section>
        ) : null}
      </div>
      <footer className="text-center text-gray-400 text-[10px] py-8 border-t border-gray-200">
        <p>
          built by cuicheng (
          <a className="underline hover:text-gray-600" href="mailto:cuizicheng.1024@gmail.com">cuizicheng.1024@gmail.com</a>
          )
        </p>
      </footer>
    </div>
  );
};
const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(<App />);
const downloadText = (filename, content, type) => {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
};
const buildGeoJSON = (payload) => {
  const person = payload.person || {};
  const locations = payload.locations || [];
  const features = locations.map(loc => ({
    type: 'Feature',
    geometry: { type: 'Point', coordinates: [loc.lng, loc.lat] },
    properties: {
      person: person.name || '',
      name: loc.name || '',
      type: loc.type || '',
      time: loc.time || '',
      modernName: loc.modernName || '',
      ancientName: loc.ancientName || ''
    }
  }));
  if (locations.length > 1) {
    features.push({
      type: 'Feature',
      geometry: {
        type: 'LineString',
        coordinates: locations.map(loc => [loc.lng, loc.lat])
      },
      properties: { person: person.name || '', name: '轨迹' }
    });
  }
  return { type: 'FeatureCollection', features };
};
const csvEscape = (value) => `"${String(value || '').replace(/"/g, '""')}"`;
const buildCSV = (payload) => {
  const person = payload.person || {};
  const locations = payload.locations || [];
  const header = ['person','name','lat','lng','type','time','modernName','ancientName'];
  const rows = locations.map(loc => [
    person.name || '',
    loc.name || '',
    loc.lat || '',
    loc.lng || '',
    loc.type || '',
    loc.time || '',
    loc.modernName || '',
    loc.ancientName || ''
  ]);
  return [header.join(','), ...rows.map(r => r.map(csvEscape).join(','))].join('\\n');
};
const setupExports = () => {
  document.querySelectorAll('[data-export]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const type = btn.getAttribute('data-export');
      const payload = window.__EXPORT_DATA__ || {};
      if (type === 'geojson') {
        const geo = buildGeoJSON(payload);
        downloadText(`${payload.person?.name || 'map'}.geojson`, JSON.stringify(geo, null, 2), 'application/json');
      } else if (type === 'csv') {
        const csv = buildCSV(payload);
        downloadText(`${payload.person?.name || 'map'}.csv`, csv, 'text/csv');
      } else if (type === 'markdown') {
        const md = payload.markdown || '';
        if (!md) {
          alert('Markdown 导出不可用');
          return;
        }
        downloadText(`${payload.person?.name || 'map'}.md`, md, 'text/markdown');
      } else if (type === 'share') {
        const link = location.href;
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(link);
        } else {
          prompt('复制链接', link);
        }
      }
    });
  });
};
setTimeout(setupExports, 0);
</script>
</body>
</html>"""
    return html.replace("__TITLE__", title).replace("__DATA__", payload.replace('</script>', '<\\/script>'))


def render_multi_html(data: Dict[str, object]) -> str:
    payload = json.dumps(data, ensure_ascii=False).replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    title = data.get("title") or "多人物合并视图"
    html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__TITLE__</title>
<script src="https://cdn.tailwindcss.com"></script>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css" onerror="if(!this.dataset.f){this.dataset.f='1';this.href='https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';}else if(this.dataset.f==='1'){this.dataset.f='2';this.href='https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.css';}" />
<script src="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js" onerror="if(!this.dataset.f){this.dataset.f='1';this.src='https://unpkg.com/leaflet@1.9.4/dist/leaflet.js';}else if(this.dataset.f==='1'){this.dataset.f='2';this.src='https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.js';}"></script>
<style>
body{font-family:'Noto Serif SC',serif;background-color:#fdf6e3;color:#2c3e50;}
#map{height:80vh;width:100%;border-radius:12px;box-shadow:0 6px 12px rgba(0,0,0,0.12);}
.legend{position:fixed;left:16px;top:16px;background:rgba(255,255,255,0.9);border:1px solid rgba(200,180,150,0.5);border-radius:10px;padding:10px 12px;z-index:9999;}
.legend-item{display:flex;align-items:center;gap:8px;font-size:12px;margin-top:6px;}
.legend-color{width:10px;height:10px;border-radius:999px;}
.export-bar{position:fixed;top:14px;right:16px;display:flex;gap:8px;z-index:9999;}
.export-btn{background:rgba(255,255,255,0.88);border:1px solid rgba(200,180,150,0.5);padding:6px 10px;border-radius:999px;font-size:12px;color:#7c2d12;cursor:pointer;}
.export-btn:hover{background:rgba(255,255,255,0.98);}
</style>
</head>
<body class="p-4 md:p-8">
<div class="export-bar">
  <button class="export-btn" data-export="geojson">GeoJSON</button>
  <button class="export-btn" data-export="csv">CSV</button>
  <button class="export-btn" data-export="share">分享链接</button>
</div>
<div id="legend" class="legend"></div>
<div id="map"></div>
<script>
const data = __DATA__;
window.__EXPORT_DATA__ = data;
const people = data.people || [];
const map = L.map('map', { zoomControl: false }).setView([35, 105], 4);
L.control.zoom({ position: 'topright' }).addTo(map);
const tileSources = [
  { url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', options: { attribution: '&copy; OpenStreetMap contributors' } },
  { url: 'https://{s}.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png', options: { attribution: '&copy; OpenStreetMap contributors' } },
  { url: 'https://{s}.tile.openstreetmap.de/{z}/{x}/{y}.png', options: { attribution: '&copy; OpenStreetMap contributors' } },
  { url: 'https://webrd{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=7&x={x}&y={y}&z={z}', options: { subdomains: ['0','1','2','3'], attribution: '&copy; 高德地图' } },
  { url: 'https://webst{s}.is.autonavi.com/appmaptile?style=7&x={x}&y={y}&z={z}', options: { subdomains: ['0','1','2','3'], attribution: '&copy; 高德地图' } }
];
const addTileLayer = (mapInstance) => {
  let idx = 0;
  let errorCount = 0;
  let layer = L.tileLayer(tileSources[idx].url, tileSources[idx].options);
  const handleError = () => {
    errorCount += 1;
    if (errorCount >= 8) {
      idx += 1;
      if (idx >= tileSources.length) {
        return;
      }
      mapInstance.removeLayer(layer);
      layer = L.tileLayer(tileSources[idx].url, tileSources[idx].options);
      layer.addTo(mapInstance);
      errorCount = 0;
      layer.on('tileerror', handleError);
    }
  };
  layer.on('tileerror', handleError);
  layer.addTo(mapInstance);
};
addTileLayer(map);
const bounds = [];
people.forEach((p) => {
  const color = p.color || '#1e40af';
  const locations = p.locations || [];
  const line = locations.map(loc => [loc.lat, loc.lng]);
  if (line.length > 1) {
    L.polyline(line, { color, weight: 3, opacity: 0.7 }).addTo(map);
  }
  locations.forEach((loc) => {
    bounds.push([loc.lat, loc.lng]);
    L.circleMarker([loc.lat, loc.lng], {
      radius: 8,
      color,
      fillColor: color,
      fillOpacity: 0.4,
      weight: 2
    }).addTo(map).bindPopup(`${p.person?.name || ''} · ${loc.name || ''}`);
  });
});
if (bounds.length > 0) {
  map.fitBounds(bounds, { padding: [48, 48], maxZoom: 6 });
}
const legend = document.getElementById('legend');
const overlap = data.overlaps || [];
const overlapText = overlap.length ? overlap.map(o => o.name).join('、') : '暂无';
legend.innerHTML = `<div class="text-sm font-semibold">人物轨迹</div>` + people.map(p => `
  <div class="legend-item">
    <span class="legend-color" style="background:${p.color || '#1e40af'}"></span>
    <span>${p.person?.name || ''}</span>
  </div>
`).join('') + `<div class="text-[11px] text-slate-500 mt-2">交集地点：${overlapText}</div>`;
const downloadText = (filename, content, type) => {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
};
const buildGeoJSON = (payload) => {
  const features = [];
  (payload.people || []).forEach(p => {
    const locations = p.locations || [];
    locations.forEach(loc => {
      features.push({
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [loc.lng, loc.lat] },
        properties: {
          person: p.person?.name || '',
          name: loc.name || '',
          type: loc.type || '',
          time: loc.time || '',
          modernName: loc.modernName || '',
          ancientName: loc.ancientName || ''
        }
      });
    });
    if (locations.length > 1) {
      features.push({
        type: 'Feature',
        geometry: { type: 'LineString', coordinates: locations.map(loc => [loc.lng, loc.lat]) },
        properties: { person: p.person?.name || '', name: '轨迹' }
      });
    }
  });
  return { type: 'FeatureCollection', features };
};
const csvEscape = (value) => `"${String(value || '').replace(/"/g, '""')}"`;
const buildCSV = (payload) => {
  const header = ['person','name','lat','lng','type','time','modernName','ancientName'];
  const rows = [];
  (payload.people || []).forEach(p => {
    (p.locations || []).forEach(loc => {
      rows.push([
        p.person?.name || '',
        loc.name || '',
        loc.lat || '',
        loc.lng || '',
        loc.type || '',
        loc.time || '',
        loc.modernName || '',
        loc.ancientName || ''
      ]);
    });
  });
  return [header.join(','), ...rows.map(r => r.map(csvEscape).join(','))].join('\\n');
};
document.querySelectorAll('[data-export]').forEach(btn => {
  btn.addEventListener('click', async () => {
    const type = btn.getAttribute('data-export');
    const payload = window.__EXPORT_DATA__ || {};
    if (type === 'geojson') {
      const geo = buildGeoJSON(payload);
      downloadText(`storymap.geojson`, JSON.stringify(geo, null, 2), 'application/json');
    } else if (type === 'csv') {
      const csv = buildCSV(payload);
      downloadText(`storymap.csv`, csv, 'text/csv');
    } else if (type === 'share') {
      const link = location.href;
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(link);
      } else {
        prompt('复制链接', link);
      }
    }
  });
});
</script>
</body>
</html>"""
    return html.replace("__TITLE__", title).replace("__DATA__", payload.replace('</script>', '<\\/script>'))


def render_osm_html(title: str, points: List[Dict[str, object]], info_panel_html: str = "") -> str:
    """
    渲染基础地图页（点位与连线）。
    """
    center = {"lat": 35.0, "lon": 105.0, "zoom": 4}
    if points:
        lat = float(points[0]["lat"])
        lon = float(points[0]["lon"])
        center = {"lat": lat, "lon": lon, "zoom": 6}
    pts_json = json.dumps(points, ensure_ascii=False)
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>{title} - 生平地图</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<style>html,body,#map{{height:100%;margin:0;padding:0}}</style>
</head>
<body>
<div id="map"></div>
{info_panel_html}
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<script>
const map = L.map('map').setView([{center["lat"]},{center["lon"]}], {center["zoom"]});
const tileSources = [
  {{
    url: 'https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',
    options: {{ maxZoom: 20, attribution: '&copy; OpenStreetMap contributors' }}
  }},
  {{
    url: 'https://{{s}}.tile.openstreetmap.fr/hot/{{z}}/{{x}}/{{y}}.png',
    options: {{ maxZoom: 20, attribution: '&copy; OpenStreetMap contributors' }}
  }},
  {{
    url: 'https://{{s}}.tile.openstreetmap.de/{{z}}/{{x}}/{{y}}.png',
    options: {{ maxZoom: 20, attribution: '&copy; OpenStreetMap contributors' }}
  }},
  {{
    url: 'https://webrd{{s}}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=7&x={{x}}&y={{y}}&z={{z}}',
    options: {{ maxZoom: 20, subdomains: ['0', '1', '2', '3'], attribution: '&copy; 高德地图' }}
  }},
  {{
    url: 'https://webst{{s}}.is.autonavi.com/appmaptile?style=7&x={{x}}&y={{y}}&z={{z}}',
    options: {{ maxZoom: 20, subdomains: ['0', '1', '2', '3'], attribution: '&copy; 高德地图' }}
  }}
];
const addTileLayer = (mapInstance) => {{
  let idx = 0;
  let errorCount = 0;
  let layer = L.tileLayer(tileSources[idx].url, tileSources[idx].options);
  const handleError = () => {{
    errorCount += 1;
    if (errorCount >= 8) {{
      idx += 1;
      if (idx >= tileSources.length) {{
        return;
      }}
      mapInstance.removeLayer(layer);
      layer = L.tileLayer(tileSources[idx].url, tileSources[idx].options);
      layer.addTo(mapInstance);
      errorCount = 0;
      layer.on('tileerror', handleError);
    }}
  }};
  layer.on('tileerror', handleError);
  layer.addTo(mapInstance);
}};
addTileLayer(map);
const pts = {pts_json};
const latlngs = pts.map(p => [p.lat, p.lon]);
if (latlngs.length > 1) {{
  L.polyline(latlngs, {{color:'#555', weight:2, opacity:0.7}}).addTo(map);
}}
pts.forEach((p, i) => {{
  let style = {{radius: 7, color: '#3498db', fillColor: '#3498db', fillOpacity: 0.9}};
  if (i === 0) style = {{radius: 8, color: '#2ecc71', fillColor: '#2ecc71', fillOpacity: 1.0}};
  if (i === pts.length - 1) style = {{radius: 8, color: '#e74c3c', fillColor: '#e74c3c', fillOpacity: 1.0}};
  const m = L.circleMarker([p.lat, p.lon], style).addTo(map);
  const html = marked.parse(p.md || '');
  m.bindPopup(html);
}});
</script>
</body>
</html>"""
    return html

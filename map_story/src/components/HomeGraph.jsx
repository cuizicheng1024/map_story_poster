import { useMemo } from "react";

const W = 980;
const H = 460;
const CX = W / 2;
const CY = H / 2;
const R = 170;

function getSurnameKey(name) {
  const s = String(name || "").trim();
  if (!s) return "";
  return s[0];
}

export default function HomeGraph({ names, query, onSelect }) {
  const displayNames = useMemo(() => {
    const all = Array.isArray(names) ? names : [];
    if (!query.trim()) return all.slice(0, 36);
    return all.filter((n) => String(n).includes(query.trim())).slice(0, 36);
  }, [names, query]);

  const nodes = useMemo(() => {
    const n = displayNames.length || 1;
    return displayNames.map((name, idx) => {
      const angle = (2 * Math.PI * idx) / n - Math.PI / 2;
      const radius = R + (idx % 3) * 20;
      return {
        id: name,
        label: name,
        x: CX + Math.cos(angle) * radius,
        y: CY + Math.sin(angle) * radius,
        key: getSurnameKey(name),
      };
    });
  }, [displayNames]);

  const edges = useMemo(() => {
    const byKey = new Map();
    for (const n of nodes) {
      if (!n.key) continue;
      if (!byKey.has(n.key)) byKey.set(n.key, []);
      byKey.get(n.key).push(n);
    }
    const out = [];
    for (const arr of byKey.values()) {
      for (let i = 0; i < arr.length - 1; i++) {
        out.push([arr[i], arr[i + 1]]);
      }
    }
    return out.slice(0, 80);
  }, [nodes]);

  return (
    <div className="rounded-xl border border-amber-200 bg-amber-50/40 p-3 overflow-x-auto">
      <div className="text-xs text-gray-500 mb-2">
        人物知识图谱（按姓氏聚合连线，点击人物即可生成地图）
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full min-w-[760px] h-[360px] bg-white rounded-lg border border-gray-100">
        {edges.map(([a, b], idx) => (
          <line key={idx} x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="#f59e0b" strokeOpacity="0.35" />
        ))}
        {nodes.map((n) => (
          <g key={n.id} transform={`translate(${n.x}, ${n.y})`} onClick={() => onSelect(n.id)} style={{ cursor: "pointer" }}>
            <circle r="16" fill="#fff7ed" stroke="#f59e0b" />
            <text textAnchor="middle" dy="4" fontSize="11" fill="#7c2d12">{n.label.length > 4 ? `${n.label.slice(0, 4)}…` : n.label}</text>
          </g>
        ))}
      </svg>
    </div>
  );
}


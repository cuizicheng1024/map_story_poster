"""
为已生成的 storymap_*.html 批量修复底图加载兜底逻辑。

背景：
- 部分网络环境下 OpenStreetMap 瓦片会失败，导致地图区域空白。
- 新版渲染模板已内置更稳健的 tile fallback，但旧输出 HTML 需要一次性补丁。

作用：
- 将旧版 `addTileLayer(map);` 替换为“优先中国范围使用高德 + 2 秒无 tileload 自动切换”的版本。
"""

import re
from pathlib import Path
from typing import Tuple


PATTERN = re.compile(
    r"const addTileLayer = \(mapInstance\) => \{[\s\S]*?\};\s*addTileLayer\(map\);",
    re.M,
)

TILES_PATTERN = re.compile(r"const tileSources = \[\s*([\s\S]*?)\s*\];", re.M)
IDX_PATTERN = re.compile(r"let idx = isInChina\(center\.lat, center\.lng\) \? 3 : 0;")
NEW_BLOCK_PATTERN = re.compile(
    r"const isInChina = \(lat, lng\) =>[\s\S]*?addTileLayer\(map, first\);",
    re.M,
)

REPLACEMENT = """const isInChina = (lat, lng) => lat >= 18 && lat <= 54 && lng >= 73 && lng <= 135;
const addTileLayer = (mapInstance, center) => {
  let idx = isInChina(center.lat, center.lng) ? 5 : 0;
  let errorCount = 0;
  let tileLoadCount = 0;
  let layer = null;
  let timer = null;
  let blankAdded = false;

  const addBlankBase = () => {
    if (blankAdded) return;
    blankAdded = true;
    const el = document.getElementById('map');
    if (el) el.style.background = '#f6f4ee';
    const blank = L.gridLayer({ attribution: '' });
    blank.createTile = () => {
      const tile = document.createElement('div');
      tile.style.background = 'transparent';
      return tile;
    };
    blank.addTo(mapInstance);
  };

  const attach = () => {
    if (layer) mapInstance.removeLayer(layer);
    errorCount = 0;
    tileLoadCount = 0;
    if (idx >= tileSources.length) {
      addBlankBase();
      return;
    }
    layer = L.tileLayer(tileSources[idx].url, tileSources[idx].options);
    const handleError = () => {
      errorCount += 1;
      if (errorCount >= 6) {
        idx += 1;
        attach();
      }
    };
    const handleLoad = () => { tileLoadCount += 1; };
    layer.on('tileerror', handleError);
    layer.on('tileload', handleLoad);
    layer.addTo(mapInstance);
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => {
      if (tileLoadCount === 0) {
        idx += 1;
        attach();
      }
    }, 2000);
  };
  attach();
};
addTileLayer(map, first);"""

EXTRA_TILE_SOURCES = """,
        {
          url: 'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png',
          options: { subdomains: ['a', 'b', 'c', 'd'], attribution: '&copy; OpenStreetMap contributors &copy; CARTO' }
        },
        {
          url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}',
          options: { attribution: 'Tiles &copy; Esri' }
        }"""


def patch_one(path: Path) -> Tuple[bool, bool]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    changed = False
    if "basemaps.cartocdn.com" not in text:
        m = TILES_PATTERN.search(text)
        if m:
            content = m.group(1).rstrip()
            if content.endswith("}"):
                injected = content + EXTRA_TILE_SOURCES
                text = text[: m.start(1)] + injected + text[m.end(1) :]
                changed = True

    if PATTERN.search(text):
        next_text = PATTERN.sub(REPLACEMENT, text)
        if next_text != text:
            text = next_text
            changed = True
    elif NEW_BLOCK_PATTERN.search(text):
        next_text = NEW_BLOCK_PATTERN.sub(REPLACEMENT, text)
        if next_text != text:
            text = next_text
            changed = True
    else:
        next_text = IDX_PATTERN.sub("let idx = isInChina(center.lat, center.lng) ? 5 : 0;", text)
        if next_text != text:
            text = next_text
            changed = True

    if changed:
        path.write_text(text, encoding="utf-8")
    return True, changed


def main() -> int:
    root = Path(__file__).resolve().parents[1] / "outputs" / "output_batch_storymap_pep_history"
    files = sorted(root.glob("storymap_*.html"))
    matched = 0
    patched = 0
    for f in files:
        has, did = patch_one(f)
        if has:
            matched += 1
        if did:
            patched += 1
    print({"files": len(files), "matched": matched, "patched": patched, "dir": str(root)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

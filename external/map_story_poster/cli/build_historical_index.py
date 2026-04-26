#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_historical_index.py

生成古地名坐标索引库（JSONL）。

输出文件：historical_places_index.jsonl（追加写入，每行一个 JSON 对象）
字段：
- ancient_name: 古称
- modern_name: 现代名称（用于地理编码）
- lat: 纬度
- lon: 经度

坐标获取策略：
1) 优先尝试使用 geopy 的 Nominatim 对 modern_name 进行地理编码
2) 任何异常（网络超时、被拒、无结果、无 geopy 依赖等）一律使用硬编码备用坐标兜底

额外特性：
- 若 historical_places_index.jsonl 已存在，会先读取已存在的 ancient_name，避免重复追加。
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Set, Tuple


OUT_FILE = "historical_places_index.jsonl"


@dataclass(frozen=True)
class Place:
    ancient_name: str
    modern_name: str


def build_high_frequency_places() -> List[Place]:
    """预置 30-50 个中小学课本高频古地名（偏李白/苏轼/唐三藏轨迹相关）。"""

    # 说明：
    # - ancient_name 尽量使用教材中常见写法
    # - modern_name 尽量写得具体一点，以提升 Nominatim 成功率
    return [
        # 中原/关中
        Place("长安", "Xi'an, Shaanxi, China"),
        Place("咸阳", "Xianyang, Shaanxi, China"),
        Place("洛阳", "Luoyang, Henan, China"),
        Place("汴京", "Kaifeng, Henan, China"),
        Place("开封", "Kaifeng, Henan, China"),
        Place("大梁", "Kaifeng, Henan, China"),
        Place("邺城", "Linzhang County, Handan, Hebei, China"),
        Place("并州", "Taiyuan, Shanxi, China"),
        Place("太原", "Taiyuan, Shanxi, China"),
        Place("燕京", "Beijing, China"),
        Place("幽州", "Beijing, China"),
        Place("涿郡", "Zhuozhou, Hebei, China"),

        # 江南/两宋
        Place("临安", "Hangzhou, Zhejiang, China"),
        Place("钱塘", "Hangzhou, Zhejiang, China"),
        Place("建康", "Nanjing, Jiangsu, China"),
        Place("江宁", "Nanjing, Jiangsu, China"),
        Place("金陵", "Nanjing, Jiangsu, China"),
        Place("建业", "Nanjing, Jiangsu, China"),
        Place("姑苏", "Suzhou, Jiangsu, China"),
        Place("会稽", "Shaoxing, Zhejiang, China"),
        Place("扬州", "Yangzhou, Jiangsu, China"),
        Place("润州", "Zhenjiang, Jiangsu, China"),

        # 巴蜀/三峡
        Place("益州", "Chengdu, Sichuan, China"),
        Place("成都", "Chengdu, Sichuan, China"),
        Place("蜀中", "Chengdu, Sichuan, China"),
        Place("夔州", "Fengjie County, Chongqing, China"),
        Place("白帝城", "Fengjie County, Chongqing, China"),
        Place("夷陵", "Yichang, Hubei, China"),

        # 荆楚/两广/海南（苏轼）
        Place("江陵", "Jingzhou, Hubei, China"),
        Place("荆州", "Jingzhou, Hubei, China"),
        Place("黄州", "Huanggang, Hubei, China"),
        Place("惠州", "Huizhou, Guangdong, China"),
        Place("儋州", "Danzhou, Hainan, China"),
        Place("广州", "Guangzhou, Guangdong, China"),

        # 丝路/西域（唐三藏/李白）
        Place("敦煌", "Dunhuang, Gansu, China"),
        Place("玉门关", "Yumen Pass, Dunhuang, Gansu, China"),
        Place("阳关", "Yangguan Pass, Dunhuang, Gansu, China"),
        Place("凉州", "Wuwei, Gansu, China"),
        Place("兰州", "Lanzhou, Gansu, China"),
        Place("高昌", "Turpan, Xinjiang, China"),
        Place("交河", "Jiaohe Ruins, Turpan, Xinjiang, China"),
        Place("龟兹", "Kuqa, Xinjiang, China"),
        Place("于阗", "Hotan, Xinjiang, China"),
        Place("疏勒", "Kashgar, Xinjiang, China"),
        Place("碎叶城", "Tokmok, Kyrgyzstan"),

        # 远行目的地
        Place("天竺", "India"),
    ]


def fallback_coords() -> Dict[str, Tuple[float, float]]:
    """备用坐标（Mock 数据）。

    重要：必须覆盖所有 modern_name，确保即使网络失败也能完整产出。
    """

    # 注：以下经纬度为常见公开坐标的近似值（用于兜底/Mock）。
    return {
        "Xi'an, Shaanxi, China": (34.3416, 108.9398),
        "Xianyang, Shaanxi, China": (34.3296, 108.7093),
        "Luoyang, Henan, China": (34.6186, 112.4540),
        "Kaifeng, Henan, China": (34.7986, 114.3076),
        "Linzhang County, Handan, Hebei, China": (36.3350, 114.6190),
        "Taiyuan, Shanxi, China": (37.8706, 112.5489),
        "Beijing, China": (39.9042, 116.4074),
        "Zhuozhou, Hebei, China": (39.4856, 115.9744),
        "Hangzhou, Zhejiang, China": (30.2741, 120.1551),
        "Nanjing, Jiangsu, China": (32.0603, 118.7969),
        "Suzhou, Jiangsu, China": (31.2989, 120.5853),
        "Shaoxing, Zhejiang, China": (30.0000, 120.5800),
        "Yangzhou, Jiangsu, China": (32.3942, 119.4129),
        "Zhenjiang, Jiangsu, China": (32.1878, 119.4250),
        "Chengdu, Sichuan, China": (30.5728, 104.0668),
        "Fengjie County, Chongqing, China": (31.0185, 109.4643),
        "Yichang, Hubei, China": (30.6919, 111.2865),
        "Jingzhou, Hubei, China": (30.3348, 112.2400),
        "Huanggang, Hubei, China": (30.4537, 114.8724),
        "Huizhou, Guangdong, China": (23.1115, 114.4168),
        "Danzhou, Hainan, China": (19.5209, 109.5807),
        "Guangzhou, Guangdong, China": (23.1291, 113.2644),
        "Dunhuang, Gansu, China": (40.1421, 94.6620),
        "Yumen Pass, Dunhuang, Gansu, China": (40.3517, 93.7720),
        "Yangguan Pass, Dunhuang, Gansu, China": (39.9220, 94.0840),
        "Wuwei, Gansu, China": (37.9283, 102.6370),
        "Lanzhou, Gansu, China": (36.0611, 103.8343),
        "Turpan, Xinjiang, China": (42.9476, 89.1780),
        "Jiaohe Ruins, Turpan, Xinjiang, China": (42.9510, 89.1740),
        "Kuqa, Xinjiang, China": (41.7179, 82.9630),
        "Hotan, Xinjiang, China": (37.1110, 79.9220),
        "Kashgar, Xinjiang, China": (39.4704, 75.9898),
        "Tokmok, Kyrgyzstan": (42.8419, 75.3015),
        "India": (20.5937, 78.9629),
    }


def safe_load_existing_ancient_names(path: str) -> Set[str]:
    """读取既有 JSONL，提取已存在的 ancient_name，避免重复追加。"""

    if not os.path.exists(path):
        return set()

    existed: Set[str] = set()
    with open(path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            s = line.strip()
            if not s:
                continue
            try:
                obj = json.loads(s)
                name = obj.get("ancient_name")
                if isinstance(name, str) and name:
                    existed.add(name)
            except Exception:
                # 容错：遇到脏行不要中断
                continue
    return existed


def try_geocode(modern_name: str, timeout_seconds: int = 3) -> Optional[Tuple[float, float]]:
    """尝试用 Nominatim 获取坐标。

    任何失败都返回 None（由上层兜底）。
    """

    try:
        from geopy.geocoders import Nominatim
    except Exception:
        return None

    try:
        geolocator = Nominatim(user_agent="map_story_poster_historical_index/1.0")
        location = geolocator.geocode(modern_name, timeout=timeout_seconds)
        if location is None:
            return None
        return (float(location.latitude), float(location.longitude))
    except Exception:
        return None


def resolve_coords(
    modern_name: str,
    fallback: Dict[str, Tuple[float, float]],
) -> Tuple[float, float]:
    """获取坐标：优先 geocode，失败则 fallback。"""

    coords = try_geocode(modern_name)
    if coords is not None:
        return coords

    if modern_name in fallback:
        print(f"Warning: Geocoding failed for '{modern_name}', using hardcoded fallback.")
        return fallback[modern_name]

    # 极限兜底：如果某条遗漏了 fallback，也给出一个“有效但明显是兜底”的坐标
    # 这样可以保证文件不为空/任务不崩。
    print(f"Warning: No fallback for '{modern_name}', using (0.0, 0.0).")
    return (0.0, 0.0)


def main() -> int:
    places = build_high_frequency_places()
    fb = fallback_coords()

    # 输出文件放在脚本同目录下
    script_dir = os.path.dirname(os.path.abspath(__file__))
    out_path = os.path.join(script_dir, OUT_FILE)

    existed = safe_load_existing_ancient_names(out_path)

    written = 0
    total = 0

    with open(out_path, "a", encoding="utf-8") as out:
        for p in places:
            total += 1
            if p.ancient_name in existed:
                continue

            lat, lon = resolve_coords(p.modern_name, fb)

            obj = {
                "ancient_name": p.ancient_name,
                "modern_name": p.modern_name,
                "lat": lat,
                "lon": lon,
            }
            out.write(json.dumps(obj, ensure_ascii=False) + "\n")
            written += 1

            # 轻微节流：即使网络可用也别太快（对 Nominatim 更友好）
            time.sleep(0.2)

    print(f"[OK] 预置地点数: {total}")
    print(f"[OK] 本次新增写入: {written}")
    print(f"[OK] 输出文件: {out_path}")

    # 额外校验：确保文件非空
    try:
        size = os.path.getsize(out_path)
        print(f"[OK] 输出文件大小: {size} bytes")
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

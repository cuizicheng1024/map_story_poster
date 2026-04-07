# StoryMap作品介绍

## 🧾 项目信息
- 作者：崔成（cuizicheng.1024@gmail.com）
- 版本：v1.0.0
- 创作时间：2026-02-20

## 📌 项目定位
MapStory 以“人物—时空—事件”为主线，聚焦历史人物的空间叙事。该技能可根据用户输入的人物名称，自动生成该人物的到访城市、年龄与典型事件，并可视化为交互式足迹地图，便于从空间视角理解人物生平。

## 🧭 核心流程
- 📚 生平研究：调用 LLM 生成生平叙述，提取 6–20 个关键地点与对应事件
- 📍 地理编码：通过高德工具与公共地理编码服务解析城市经纬度
- 🧩 数据整合：抽取地点、事件与停留时间并整理为结构化数据
- 🗺️ 地图可视化：渲染交互式足迹地图，支持路线、弹窗与时间轴
- 📖 传记输出：生成包含人物简介、足迹与影响的结构化文档

## 🔁 输入与输出
- 输入：包含人物名称的自然语言请求，如“李白的一生足迹”“苏轼去过哪些地方”
- 输出：到访地点、年龄与典型事件摘要，并生成可交互的足迹地图 HTML
运行时间约3-10分钟，主要依赖于人物生平 Markdown 与 地图渲染时间。


## 🧩 技术架构
- 生成层：人物名抽取与生平生成，入口为 .github/skills/map-story/script/story_agents.py
- 地理能力层：地理编码、坐标与距离统计，核心逻辑在 .github/skills/map-story/script/map_client.py
- 表达层：解析 Markdown 与渲染交互式地图，入口为 .github/skills/map-story/script/story_map.py
- 页面层：交互式地图模板与布局在 .github/skills/map-story/script/map_html_renderer.py
- 资源与产物
  - .github/skills/map-story/docs/：提示词模板
  - story/：人物生平 Markdown
  - story_map/：交互式地图 HTML

## 🛠️ 使用说明
### 🔐 环境变量
- LLM_MODEL_ID、LLM_API_KEY、LLM_BASE_URL、LLM_TIMEOUT（秒）
- QVERIS_API_URL 或 QVERIS_BASE_URL、QVERIS_API_KEY（可选，仅用于高德地理编码）

### ✍️ 生成人物生平 Markdown
直接生成并保存 Markdown 文件：

```bash
python .github/skills/map-story/script/story_agents.py -p "李白"
```

交互式输入：

```bash
python .github/skills/map-story/script/story_agents.py
```

产物输出在 story/ 目录。

### 🗺️ 生成人物故事地图 HTML
指定人物：

```bash
python .github/skills/map-story/script/story_map.py -p "李白"
```

交互式模式：

```bash
python .github/skills/map-story/script/story_map.py
```

产物输出在 story_map/ 目录。底图在页面内由 Leaflet 多源自动回退加载。

## 👥 目标用户
- 地理历史爱好者、历史教学人员、文史研究者

## 🎯 适配场景
- 历史教育：展示人物生平与地理迁徙路径
- 文史研究与可视化：将文本材料转为可交互地图辅助分析
- 文化旅游策划：以人物足迹形成主题路线

## ✅ 小测试
1. 人物一：峨眉山月半轮秋，影入平羌江水流
2. 人物二：问余平生事业，黄州惠州儋州
3. 人物三：关东有义士，兴兵讨群凶

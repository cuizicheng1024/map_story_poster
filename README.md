# 地图故事（面向中学生与中学教师）

打开网页，输入一个历史人物名字，就能看到这个人的“人生足迹地图”：他去过哪些地方、发生了什么事、以及对应的教材知识点与考点。

## 功能概览
- 首页「人类群星闪耀时」：提供“关系图/地图视角”，并用“时间窗”筛选人物；悬停可看人物简介，点击可进入人物页
- 人物页：人物卡片（时代/籍贯/简介）、生命轨迹地图（点线+事件）、人物对话（MiMo）、对应教材考点/考点要点

## 快速开始（本地运行）

### 1) 准备
- 安装 Python 3.11
- 配置 MiMo：可在项目根目录 `.env` 或 `external/map_story_poster/.env` 中写入 `MIMO_API_KEY=...`（推荐放在 `external/map_story_poster/.env`，服务启动时会读取）

### 2) 生成并查看人物地图（示例 3-5 个）
```bash
python3 scripts/batch_test_map_story_poster.py --names data/sample_5_names.json --out_dir outputs/output_batch_storymap_pep_history
python3 -m http.server 8000
```
打开目录页：
`http://localhost:8000/outputs/output_batch_storymap_pep_history/`

## 全量人物页（约 518）与对话功能

### 1) 批量重渲人物页（不调用模型）
```bash
python3 external/map_story_poster/cli/generate_pure_story_map.py --render-all --all-mode nogeocode
```
命令行会输出 `[i/total] OK xxx`，可作为实时进度。

### 2) 启动本地服务（用于打开人物页 + 对话接口）
```bash
python3 external/map_story_poster/storymap/script/story_map.py --serve --port 8765
```
打开：
`http://localhost:8765/`

人物页里的“开始对话”会请求本地的 `/api/ai/proxy`，因此建议通过该服务打开页面（而不是直接双击 file:// 打开）。

### 3) 人物页能看到什么？
- 基本信息卡：姓名、称号/摘要、时代、生卒、籍贯（古称/今称）
- 生命轨迹地图：按行程地点绘制点线，支持缩放/拖拽；点击地点可查看事件与材料引用
- 对话：点击“开始对话”后可向人物提问（走本地服务的 `/api/ai/proxy`，由服务端调用 MiMo）
- 教材与考点：页面下方展示与该人物相关的教材知识点、可考点要点（便于中学教学与复习）

### 4) 提交前冒烟检查（推荐）
```bash
# Python 语法检查
python3 -m py_compile external/map_story_poster/storymap/script/*.py external/map_story_poster/tools/build_stellar_homepage.py

# Web 前端构建检查（可选）
cd external/map_story_poster/web && npm run build

# 服务健康检查
python3 -c "import urllib.request; r=urllib.request.urlopen('http://localhost:8765/health',timeout=10); print(r.status, r.read().decode('utf-8','ignore'))"
```

对话接口会返回 `meta.used_fallback`：
- `false`：已调用大模型（MiMo）
- `true`：走了本地兜底回复（通常是模型配置/网络/超时问题）

## 首页（人类群星闪耀时）
```bash
python3 external/map_story_poster/tools/build_stellar_homepage.py
```
生成产物在：
`external/map_story_poster/storymap/examples/story_map/index.html`

### 首页能看到什么？
- 关系图：以节点展示人物群像；可拖拽/缩放；悬停显示人物简介；点击进入人物页
- 地图视角：按人物籍贯/出生地的现代地理位置展示分布（用于对比“人才地理分布”）
- 时间窗：通过拖动时间窗筛选人物范围；并展示“时间窗内省份名人 Top5”等统计

### 检索未收录人物（自动生成）
- 在首页搜索框输入“当前库中没有的人物”时，会提示“正在生成，请稍候…”，并在后台通过本地服务生成对应人物页，完成后自动打开
- 该功能需要通过本地服务访问首页（`http://localhost:8765/`），并正确配置模型接口（用于生成 Markdown/人物页）

## 人物真实性核查（web_search 产物）
- 报告目录：`external/map_story_poster/data/validation_reports/web_search_truth_audit/`
- 索引文件：`external/map_story_poster/data/validation_reports/index_web_search_truth.json`

## 部署与访问（GitHub）
- 仅静态页面（GitHub Pages）：可以把 `external/map_story_poster/storymap/examples/story_map/` 作为静态站点部署，让别人直接浏览首页与人物页
- 对话/模型能力：GitHub Pages 不能运行本项目的 Python 服务，因此无法提供 `/api/ai/proxy`；如果要让别人也能对话，需要你单独部署后端服务并在服务端配置你的 `MIMO_API_KEY`（不建议把 key 暴露到前端）
- 访问者行为/查询记录：只有在你部署后端服务并做日志/埋点时才能看到访客查询了什么、访问了哪些页面；纯静态托管一般只能看到页面访问量（且粒度有限）

## 目录说明
- `external/map_story_poster/`：主产品仓库（StoryMap 生成与渲染）
- `scripts/`：本仓库的批量/数据构建脚本
- `data/`：抽取的人名名单、知识图谱、本地古今地名索引
- `outputs/`：本地生成的 HTML/报告输出（不建议提交）

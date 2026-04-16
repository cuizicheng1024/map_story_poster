# StoryMap 技术说明（README_TECH）

本文件面向开发者，说明项目从“输入人物名”到“生成可交互 HTML 地图课件”的完整链路、目录结构、以及为稳定性做的关键工程设计。

## 1. 一句话架构
- **LLM 生成 Markdown 生平**（结构化模板）  
- **解析 Markdown → 结构化 profile**（人物档案/地点/事件/主要作品/教学要点）  
- **地名→坐标**（本地历史词典优先 + 缓存 + 在线兜底）  
- **渲染 HTML**（单文件离线可打开，含时间线与地图交互）

## 2. 最重要的入口

### 2.1 单人一键生成（推荐）
```bash
python3 cli/auto_generate.py --name "辛弃疾"
```
- 产物：
  - Markdown：`map_story/storymap/examples/story/<人物>.md`
  - HTML：`map_story/storymap/examples/story_map/`（或脚本输出目录）

### 2.2 纯渲染：Markdown → HTML
```bash
python3 cli/generate_pure_story_map.py --md_path map_story/storymap/examples/story/辛弃疾.md
```

### 2.3 批量跑数（工程回归/体检）
```bash
python3 cli/batch_run_mimo_autogen_v2.py
```
- 用于：批量调用模型、结构检查、地理编码统计与失败样本汇总、生成报表。

## 3. 主干数据流（输入人名 → HTML）
1. **输入人物名**：命令行 `--name` 或上层服务/前端输入。
2. **生成结构化 Markdown**：
   - `auto_generate.py`：以 OpenAI 兼容接口调用模型，生成 Markdown 并做基础兜底。
   - `story_agents.py`：读取 `map_story/storymap/docs/story_system_prompt.md` 作为“真规范”，生成 Markdown。
3. **Markdown 解析与质量兜底**：
   - `story_map.py`：解析人物档案、时间线、地点段落，生成 `profile`。
   - 内部会对 Markdown 表格做容错（例如补齐 `| --- |` 分隔线）。
4. **地理编码（古今地名→坐标）**：
   - 优先：Markdown 自带坐标表/现代搜索地名。
   - 其次：本地历史坐标词典 `historical_places_index.jsonl`。
   - 再次：地理编码缓存（减少重复请求）。
   - 最后：在线 geocode 兜底。
5. **渲染 HTML**：
   - `map_html_renderer.py` 负责渲染单文件 HTML（Leaflet）。

## 4. 目录结构（建议只关心这些）
```
map_story/
  src/                       # 网页前端（Vite/React）
  storymap/
    docs/                    # Prompt/规则文档（生成 Markdown 的“真规范”）
    examples/
      story/                 # 人物 Markdown（可手工/批量生成）
      story_map/             # 渲染后的 HTML/导出文件
    script/                  # 核心 Python 链路（解析/地名/渲染/服务）
```

## 5. 教学相关字段（页面底部）
页面底部的教学区域由 Markdown 解析得到：
- `textbookPoints`：教材知识点
- `examPoints`：考点（若缺失，会从 `textbookPoints` 派生兜底）
- 页面展示合并为“**教材知识点与考点**”

另外新增“人物要点”模块（强相关速览）：
- 历史地位/身份/称号
- 代表作（从全文抽取《…》）
- 他人评价/史料（默认取“历史评价”段落前 3 条）

## 6. 稳定性与三道防线（重要）
1. **Prompt 与解析一致**  
   - `map_story/storymap/docs/story_system_prompt.md` 是生成 Markdown 的“真规范”。
2. **Markdown 表格容错**  
   - 自动修复常见的表格分隔线缺失，避免解析链路崩溃。
3. **地名解析降级策略**  
   - 本地历史词典优先，其次缓存，最后在线 geocode。

## 7. 配置与环境变量
在项目根目录创建 `.env`（示例）：
```bash
API_KEY=你的key
BASE_URL=https://api.openai.com/v1
MODEL=gpt-4o-mini
```
不同脚本可能还支持 MiMo/QVeris 等配置，建议优先阅读对应脚本顶部的说明与 `map_story/storymap/docs/`。

## 8. 提交内置数据（评审可直接复现）
- `data/pep_people_merged.json`：人教版人名合并去重结果（推荐入口）
- `data/pep_history_figures_sample.json`：历史人物名单
- `data/pep_junior_all_people.json`：初中阶段全人物名单
- `data/pep_history_figures_sample_by_book.json` / `data/pep_junior_all_people_by_book.json`：按教材分组

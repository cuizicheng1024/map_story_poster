# readme2：技术路线说明（给开发者）

## 1. 目标与范围
- 输入：历史人物姓名（单个或批量）。
- 输出：可直接打开的 StoryMap HTML（含人物档案、时间线、地点标注、路线、教材知识点与考点）。
- 场景：中学历史/语文教学、学生项目展示、文史地理可视化。

## 2. 总体架构
- `story_agents.py`：调用大模型生成标准化人物传记 Markdown。
- `story_map.py`：解析 Markdown，抽取人物档案、足迹地点、时间、事件、知识点与考点。
- `map_client.py`：古今地名归一 + 地理编码（本地索引优先，在线服务兜底，结果缓存）。
- `map_html_renderer.py`：将结构化数据渲染为单文件 HTML（Leaflet 交互地图）。
- `generate_pure_story_map.py`：单人流程入口（md -> html）。
- `batch_test_map_story_poster.py`：批量回归入口（多人批量生成 + 报告）。

## 3. 数据流（单人物）
1. 输入人物名（如 `吴道子`）。
2. `story_agents.py` 生成 `map_story/storymap/examples/story/<人物>.md`。
3. `story_map.py` 读取 Markdown，产出 `data`（person/locations/mapStyle/textbookPoints/examPoints）。
4. `map_client.py` 对每个地点做坐标解析：
   - 先查 `historical_places_index.jsonl`（古今地名本地库）；
   - 再查地理编码缓存；
   - 最后在线 geocode 兜底。
5. `map_html_renderer.py` 生成 `storymap_<人物>.html`。

## 4. 关键优化（本次）
- **知识点与考点合并展示**：页面底部统一为“教材知识点与考点”。
- **生命跨度修复**：优先用享年；若缺失则由出生/去世年份计算；仍无法确认显示“存疑”。
- **地理编码稳态化**：
  - 增加落盘缓存（默认 `.cache/map_story_geocode_cache.json`）；
  - 增强候选地名拆解（括号、`今XX`、多地点分隔符）。
- **本地古今地名库**：
  - 文件：`historical_places_index.jsonl`；
  - 生成脚本：`build_local_historical_places_index.py`；
  - 作用：减少在线 geocode，提升命中率与稳定性。

## 5. 批量流程（教材人物回归）
- 名单来源：`pep_history_figures_sample.json`（或教材全量抽取结果）。
- 执行：
  - `python3 batch_test_map_story_poster.py --names pep_history_figures_sample.json --out_dir output_batch_storymap_pep_history`
- 产物：
  - HTML：`output_batch_storymap_pep_history/storymap_*.html`
  - 报告：`output_batch_storymap_pep_history/batch_report.json`

## 6. 人教版教材人物抽取链路
- 脚本：`smartedu_tchmaterial_extract.py`
- 数据源：国家智慧教育平台教材资源分片。
- 两种抽取模式：
  - `--extract_mode historical`：历史人物；
  - `--extract_mode person`：所有人物（含现代著名人物）。

## 7. 目录约定（便于后续 push）
- 业务核心改动保持在仓库原结构内：
  - `map_story/storymap/script/*.py`
  - `map_story/storymap/docs/*.md`
- 批量产物统一在输出目录：
  - `output_batch_storymap_pep_history/`
- 缓存统一在：
  - `.cache/` 或 `external/.cache/`

## 8. 后续建议
- 增加“考点等级（基础/提升/拓展）”标签，支持分层作业。
- 将“古地名->现代坐标”库拆分成可维护词典（按朝代/区域版本化）。
- 增加离线底图方案，避免课堂网络波动影响展示。

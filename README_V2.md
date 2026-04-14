# StoryMap 工程视角 README_V2

本文件站在工程视角，把项目里几条主要脚本怎么协同、以及从“输入一个人名”到“生成 HTML 地图”的完整链路，讲清楚一遍，并重点说明现在已经加上的三道工程防线。

## 一、从人名到 HTML 地图：一条主干链路

不管是单人一键生成，还是批量跑数、HTTP 服务调用，底层其实走的是同一条管线，可以粗略拆成四步：

第一步，拿到人物姓名。入口可能是命令行参数 `--name`，也可能是用户输入的一句话文本，最终都会被规整成一个或多个“待生成的人物名”。

第二步，调用大模型生成结构化 Markdown 生平。这里有两套实现：
- `auto_generate.py` 直接通过 OpenAI 兼容接口调用模型，使用内嵌的 `build_story_system_prompt()`。
- `story_agents.py` 读取 `storymap/docs/story_system_prompt.md`，通过 QVeris/MiMo 的工具接口调用模型。
两条路最终都产出一份带固定章节结构的 Markdown，落盘到 `map_story/storymap/examples/story/<人物>.md`。

第三步，对 Markdown 做一次工程化清洗和自检。包括：补齐必备章节、修 Markdown 表格、补坐标表、给“人生足迹地图说明”插入总行程描述等，这一步主要在 `auto_generate.ensure_required_sections()` 和 `story_map._normalize_markdown_tables()`、`map_client.append_coords_section()` 里完成。

第四步，把 Markdown 变成可交互 HTML 地图。`story_map.py` 会解析 Markdown：抽人物档案、时间线、重要地点和教材知识点，结合 `map_client.geocode_city()` 算出的经纬度，组装成内部的 `profile` 结构，再交给 `map_html_renderer` 渲染成 HTML，最终写到 `map_story/storymap/examples/story_map/<人物>__pure__时间戳.html`。

后面提到的所有脚本，其实都是围绕这四步做封装和加防线。

## 二、auto_generate.py / batch_run_mimo_autogen_v2.py 在链路里的位置

### 1. auto_generate.py：单人一键链路

`auto_generate.py` 就是老师向外暴露的“一键脚本”，负责把上面四步串起来跑完一遍：

1. 解析命令行参数 `--name`，确保人物名非空。
2. 用 `build_story_system_prompt()` 拼出一整块系统提示词，硬性约束章节结构（人物档案 / 足迹说明 / 人生历程 / 生平时间线 / 人教版教材知识点 / 地点坐标等），并要求所有表格带 `| --- |` 分隔线。
3. 通过 `call_openai_compatible()` 调用 OpenAI 兼容接口，请求体就是标准的 `chat/completions`，从 `.env` 里读 `API_KEY/BASE_URL/MODEL`，失败会直接抛异常，让问题暴露在终端日志里。
4. 用 `_strip_md_fence()` 去掉大模型可能加上的 ```md 围栏，只保留纯 Markdown 文本。
5. 用 `ensure_required_sections()` 做一次兜底校验和补全：
   - 如果没有任何 H1 标题，就补一个默认标题。
   - 如果缺少 `## 人教版教材知识点`，在文末补一个“存疑”版本。
   - 如果缺少 `## 地点坐标`，自动补一个带表头和 `| --- |` 分隔线的空表。
6. 把整理好的 Markdown 写到 `map_story/storymap/examples/story/<name>.md`。
7. 通过 `generate_pure_story_map.generate_pure_html(md_path=...)` 复用 `story_map` 的解析+渲染能力生成 HTML 文件，并在终端打印 Markdown / HTML 路径，最后尝试自动用浏览器打开 HTML 地图。

可以简单理解成：`auto_generate.py` 是“单个名字的一键胶水层”，它本身不解析时间线、不画地图，只负责“叫模型 + 保证 Markdown 合格 + 丢给下游渲染”。

### 2. batch_run_mimo_autogen_v2.py：批量跑数 + 工程诊断

`batch_run_mimo_autogen_v2.py` 做的是同一条链路的“批量版本 + 报表诊断”，核心职责有三块：

1. 批量调用模型并记录 API 健康状况：
   - 脚本从仓库根 `.env` 和 `map_story/storymap/script/.env` 里加载 MiMo/QVeris 相关配置。
   - 每个人物用 `auto_generate.build_story_system_prompt()` 拿到同一份系统 Prompt，再经 `call_openai_compatible_with_meta()` 调接口。
   - 把原始返回（去掉 ``` 围栏后）写到 `runs/<person>/raw_markdown.md`，把 HTTP 状态码、超时信息、`usage.total_tokens` 等写到 `api_attempt.json`。

2. 复用 pipeline，只做“下游链路”验证：
   - 不再重新叫模型，而是调用 `run_pipeline_only(person, raw_md, run_dir)`。
   - 内部使用前面同一个 `ensure_required_sections()` 标准化 Markdown，再写入统一的 story 目录。
   - 临时替换 `story_map.geocode_city` 和 `map_client.geocode_city` 为包装函数，记录总调用次数、成功数、失败样本和实际使用的后端（`qveris(amap)` 或 `osm(fallback)`），同时生成 `pipeline.log`。
   - 用 `story_map._load_profile_from_md()` 优先走“结构化 profile”路径，失败时才退回老的 `parse_places/parse_events/build_points/render_html` 组合，生成对应 HTML 文件。

3. 汇总工程信号，生成排障报告：
   - `markdown_checks()` 会对原始 Markdown 和补完后的 Markdown 分别做结构检查，包括：
     - 是否完全缺表头分隔线。
     - 是否缺少 auto_generate 期望的几个 H2 章节。
     - 是否和 `story_system_prompt.md` 规定的 H2 章节对齐（多了/少了哪些）。
     - 坐标表头是否是 `| 现称 | 现代搜索地名 | 纬度 | 经度 |` 这个家族。
   - 所有结果聚合成 `RunResult` 写入 `runs/<person>/result.json`，最后再由 `build_report()` 汇总成 `summary.json` + `report.md`，里面会直接给出“端到端成功率、HTTP 429/504/超时、Markdown 结构问题、地理编码失败率、token 消耗”等信息。

整体上，`batch_run_mimo_autogen_v2.py` 不改变业务逻辑，只是把“模型调用 + Markdown 校验 + story_map 渲染 + geocode 行为”全跑一遍，然后给出一份工程视角的体检报告。

## 三、story_agents.py 和 story_map.py：谁负责什么，怎么配合

这两个脚本是“人物故事生成 + 地图渲染”的核心协作单元，责任边界很清晰。

### 1. story_agents.py：只管“写故事”的 LLM 代理

`story_agents.py` 主要做三件事：

1. 封装大模型客户端 `StoryAgentLLM`：
   - 支持 QVeris 和 MiMo 两种 provider，根据 `LLM_BASE_URL` 自动判断。
   - 统一管理 `LLM_MODEL_ID/LLM_API_KEY/LLM_BASE_URL/LLM_TIMEOUT` 等配置，并带有简单重试与事件回调能力。

2. 根据 docs 里的 Prompt 生成 Markdown：
   - `_read_prompt("story_system_prompt.md")` 从 `storymap/docs` 目录加载提示词文件，**这个文件就是人名 → 生平 Markdown 的“真规范”**。
   - `generate_historical_markdown()` 把系统 Prompt 和 `"请整理历史人物『XXX』的生平信息，并按要求输出"` 这类用户内容一起丢给 LLM，拿回 Markdown 文本。

3. 提供基础工具函数：
   - `extract_historical_figures()`：从一句话中抽出可能出现的历史人物名，方便用户随便输入一句话也能触发多人物生成。
   - `save_markdown()`：把人物 Markdown 写到 `storymap/examples/story` 目录。
   - `run_interactive()` / `main()`：命令行下交互式循环输入人物名字，不涉及地图逻辑。

可以理解为：`story_agents.py` 只负责“把人名变成一份结构化的 Markdown 生平”，完全不关心坐标、地图、HTML。

### 2. story_map.py：把 Markdown 变成地图和服务的中枢

`story_map.py` 是整个项目的中控，既负责命令行模式，也负责 HTTP 服务模式：

1. 在命令行模式下：
   - 如果带 `--person`，则用 `StoryAgentLLM` + `extract_historical_figures()` 把输入拆成若干人物。
   - 对每个人调用 `_generate_for_person()`：
     - 先用 `generate_historical_markdown()` 拉一份生平 Markdown。
     - 然后 `_normalize_markdown_tables()` 自动修正时间线表和坐标表里缺失的 `| --- |` 分隔线。
     - 用 `compute_total_distance_km()` 估算总行程，再用 `insert_distance_intro()` 把“总共走了多少公里”写回“人生足迹地图说明”小节。
     - 用 `append_coords_section()` 解析时间线表，按顺序对地点做地理编码，自动追加 `## 地点坐标（自动地理编码）` 表。
     - 调 `_print_quality_report()` 打一份 Markdown 质量小结。
     - 调 `save_markdown()` 落盘 Markdown。
     - 用 `parse_places()/parse_events()/build_points()` 把 Markdown 解析成点集，最后 `render_html()` 渲染成单人地图 HTML，写入文件。
     - 同时调用 `_load_profile_from_md()` 得到标准化的 `profile` 结构，后续可以导出 GeoJSON/CSV。

2. 在 HTTP 服务模式下（`--serve`）：
   - 启动 `StoryMapServerHandler`，暴露 `/generate` 和 `/task` 两个 API：
     - `/generate` 接收人物文本，创建异步任务，立即返回 `task_id` 和排队信息。
     - 后台线程里 `_run_task()` 会复用 `_generate_for_person()` 跑完整套流程，并为多个人物生成合并视图，调用 `render_multi_html()` 画出多人物叠加地图。
     - `/task?id=...` 用于前端轮询任务进度，能看到“人物识别 → 生平生成 → 地理编码 → 渲染 → 导出”等阶段事件。
   - 还提供 `/api/ai/proxy`，作为前端直连 LLM 的代理通道，同样走 `StoryAgentLLM`，统一日志和异常处理。

3. 除了 HTML，`story_map.py` 还负责导出分析用数据：
   - `_ensure_profile_exports()` 和 `_ensure_multi_exports()` 会把单人/多人的 `profile` 导出成 GeoJSON 和 CSV。
   - `_build_geojson_for_profile()` / `_build_geojson_for_multi()` 把人物轨迹拼成点+线的 FeatureCollection，方便做后续地理分析或可视化。

一句话概括：`story_agents.py` 把“故事写出来”，`story_map.py` 把“故事搬到地图上并提供服务”。二者通过 Markdown 文本和 `profile` 数据结构做边界解耦。

## 四、map_client.py：地理编码和地图计算的通用底座

`map_client.py` 是所有“跟坐标相关”的公共能力层，主要负责三件事：

1. 地理编码 `geocode_city()`：
   - 根据地点字符串构造一系列候选（例如自动加上“中国”前后缀）。
   - 如果配置了 `QVERIS_API_URL/QVERIS_API_KEY`，优先调用 QVeris 的高德 WebService 工具，拿到 GCJ-02 坐标后用 `_gcj02_to_wgs84()` 统一转成 WGS84。
   - 如果本地没有 QVeris 配置，则走 OSM 公共地理编码回退链路（`nominatim.openstreetmap.org` / `geocode.maps.co` / `photon.komoot.io`），直接获取 WGS84 坐标。
   - 全程带 `_GEOCODE_CACHE` 缓存，避免重复请求；对于明显是国外地名的情况，也会通过 `_looks_foreign_location()` 降低“中国 XXX”这种误加前缀带来的干扰。

2. 基于 Markdown 的地点提取和自动补坐标表：
   - `extract_places_in_order()` 会从“生平时间线”表里按顺序抽出地点，优先用“现代搜索地名”列，没有时再退回“现称”列，保证顺序和去重。
   - `append_coords_section()` 会并发调用 `geocode_city()` 对这些地点进行地理编码，把成功的结果以 `## 地点坐标（自动地理编码）` 表的形式追加到 Markdown 文末。

3. 行程距离统计和文案插入：
   - `compute_total_distance_km()` 从“地点坐标”表中读出经纬度，按顺序用 Haversine 公式累加直线距离。
   - `insert_distance_intro()` 在“人生足迹地图说明”里追加一行类似“总行程约 XXX 公里”的描述，让老师一眼能看到行程量级。

`story_map.py` 自己不直接碰具体地图 SDK，所有“坐标怎么算、GCJ-02 怎么纠偏、公共 API 怎么回退”都集中在 `map_client.py`，方便统一维护和排障。

## 五、三道刚加上的工程防线

这部分是最近补齐的几个“物理防线”，目的是做到：只要大模型大致遵守模板，链路就尽量不因为格式细节或外部服务不稳定而崩。

### 1. Prompt 与代码模板校验统一

过去容易出现的问题是：Prompt 文档和解析代码各自维护一套“章节规范”，一旦有人只改了提示词没同步更新解析逻辑，就会出现“模型看一个模板，代码按另一个模板解析”的情况。

现在这块做了统一：

1. 单一“真规范”：
   - `storymap/docs/story_system_prompt.md` 定义了人名 → Markdown 的完整结构（人物档案 / 人生足迹地图说明 / 人生历程与重要地点（按时间顺序）/ 生平时间线 / 历史影响 / 人教版教材知识点 / 地点坐标），并规定了表头和 `| --- |` 分隔线的写法。
   - `story_agents.py` 在生成 Markdown 时直接读取这个文件，不再在代码里硬编码章节名。

2. auto_generate 的 Prompt 与解析要求对齐：
   - `auto_generate.build_story_system_prompt()` 虽然是单独的一套 Prompt，但章节设计和 `story_system_prompt.md` 保持同构，尤其是 `## 人教版教材知识点` 和 `## 地点坐标` 两块。
   - `ensure_required_sections()` 会在 LLM 输出缺失关键章节时自动补空壳章节，保证后面的解析逻辑永远能找到这些标题。

3. 用批量脚本做结构校验：
   - `batch_run_mimo_autogen_v2.markdown_checks()` 同时维护两组“必备章节”列表：一组对应 auto_generate 的设计，一组对应 `story_system_prompt.md`。
   - 对每个人物的原始 Markdown 和补完后的 Markdown 分别检查缺失章节，结果写进 `markdown_raw_checks` / `markdown_after_ensure_checks`。
   - `build_report()` 会统计“缺章节样本数”和示例人物名，让我们一眼看到 Prompt 是否真的被模型遵守，以及 `ensure_required_sections()` 是否兜住了坑。

这样，Prompt 和解析模板不再是两套“各说各话”的东西，而是通过统一的文档和批量校验工具绑定在一起。

### 2. Markdown 表格容错与自动补 `| --- |`

第二个常见坑是：大模型经常忘记在 Markdown 表头后面写 `| --- |` 分隔线，导致解析逻辑把整张表当成普通文本，后面所有依赖表格的逻辑都会被带崩。

这块的核心改动集中在 `story_map._normalize_markdown_tables()`：

1. 函数会在进入解析前跑一遍 Markdown：
   - 按行扫描，记录当前所在的 H2 标题（`current_h2`）。
   - 只对两个区域做修复：`## 生平时间线`（或“年份”开头的章节）和 `## 地点坐标`。

2. 修复规则很简单直接：
   - 如果在“生平时间线”章节里看到一行以 `|` 开头，并且表头里既有“年份”列也有“事件”列，就认为这是时间线表的表头。
   - 紧看下一行，如果下一行既以 `|` 开头又不是合法分隔线（`_is_table_separator()` 返回 False），就根据表头列数自动插入一行
     `| --- | --- | ... |`。
   - 对“地点坐标”章节同理，只不过检查的是“现称/地点 + 纬度 + 经度”三类列名。

3. 只修一次，不重复插线：
   - `timeline_fixed` / `coords_fixed` 标记确保每个章节最多补一条分隔线，不会把正常的表格改坏。

这个防线的效果是：哪怕模型漏写了一行 `| --- |`，`_parse_timeline_table()`、`_parse_coords_table()`、`compute_total_distance_km()` 等后续逻辑也都能正常工作，避免因为“小格式错误”导致整个人物解析失败。

### 3. 双地名解析法与本地历史坐标词典降级策略

地名解析原来最大的风险点有两个：一是古地名本身含糊，二是外部地理编码服务（高德/OSM）有频控和网络波动。现在的策略是“三层兜底”：

1. 先从 Markdown 中榨干能用的信息：
   - `_parse_location_sections()` 把“人生历程与重要地点”里每个地点段落解析成结构化字段，包括时间、位置描述、事件、意义、名篇名句等。
   - `_batch_split_ancient_modern()` / `_split_ancient_modern()` 负责“拆解古今地名”：
     - 优先调 LLM，根据规则只返回 `{"ancient":"","modern":""}`。
     - LLM 不可用或失败时，退回纯正则启发式（例如识别“今某地”的模式，把括号里的现代地名拆出来）。
   - `_pick_geocode_name()` 根据“今 XX”“/”“、”“括号”等模式，从原始位置描述里选一个最适合丢给地理编码的字符串。

2. 充分利用 Markdown 自带的坐标和本地历史坐标词典：
   - `_parse_coords_table()` / `_parse_coords_search_map()` 会把 `## 地点坐标` 表拆成两个缓存：
     - `coords_cache`：`标准地点名 → (lat, lon)`。
     - `coords_search_map`：`标准地点名 → 现代搜索地名`。
   - `_load_historical_places_index()` 会按一系列候选路径（环境变量 `HISTORICAL_PLACES_INDEX`、当前脚本同目录及若干父目录、当前工作目录）去找本地的历史坐标索引文件（例如 `historical_places_index.jsonl`），逐行加载，基于 `_normalize_place_key()` 做多种写法的归一化索引。
   - `_lookup_coords_from_historical_index()` 就是这个本地“历史坐标词典”的查询入口，会同时尝试 ancient 名、modern 名、原始位置全文以及搜索候选名，命中就直接返回坐标。

3. 在线地理编码作为最后一层降级：
   - 在 `_build_profile_data()` 里，出生地/去世地的坐标获取顺序是：先看 Markdown 坐标表 → 再查历史坐标词典 → 最后才调用 `geocode_city()`。
   - 对每一个地点段落也是同样顺序：先试 `coords_cache`，再试 `coords_search_map` + 历史坐标词典，最后才用 `geocode_city()` 走在线服务。
   - 在更底层的 `build_points()` 等函数里，也统一优先用 `_lookup_coords_from_historical_index()`，未命中才调用在线 geocode。

配合 `map_client.geocode_city()` 本身的 QVeris/高德 → OSM 多级回退，这套“双地名解析 + 本地历史坐标词典 + 在线 geocode” 的组合，能做到：

- 常见历史地名优先走本地 JSON 词典，几乎不打外部 API；
- 只有当 Markdown 没坐标、本地词典也没覆盖时，才会触发网络请求；
- 地理编码失败的样本会在 `batch_run_mimo_autogen_v2` 的 `geocode_summary` 里统一汇总，方便后续对词典和 Prompt 再迭代。

---

整体来看，现在这套工程防线的目标是：

- Prompt、Markdown 模板和解析逻辑三者有一份“共同真相”，不会再各自漂移；
- 大模型小失误（少一行 `| --- |` 这类）不会直接把整条链路跑挂；
- 地名解析优先用本地知识和已有坐标，把外部服务的不确定性挡在外面。

后续如果你计划再加新的人物模板或地名处理规则，可以继续沿用这三个思路：先统一规范，再加运行期校验和本地降级手段，而不是直接把复杂度压到 Prompt 或单点服务上。

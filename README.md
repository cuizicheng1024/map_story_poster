# StoryMap：历史人物轨迹交互地图生成器

这是一个为教学与知识梳理场景打造的历史人物 **StoryMap** 工具。

### 项目目标
其核心目标是将繁杂的人物生平资料，通过自动化流程转化为 **交互式历史足迹地图**。老师或研究者只需输入人物姓名，即可生成一个无后端依赖、可直接在浏览器打开的单体纯净 HTML 课件，极大地降低了历史地理可视化教学的门槛。

---

### 内部工作流程 (Engineering Pipeline)

项目采用模块化设计，核心处理流程如下：

1.  **起点输入**：接收用户输入的人物姓名（例如：`辛弃疾`）。
2.  **ai_generator (故事生成)**：调用大语言模型（LLM）基于高质量 System Prompt 生成结构化的 Markdown 文本，包含人物档案、人生重要足迹（时间、地点、事件、意义）以及教学知识点。
3.  **story_map (地理编码)**：解析 Markdown 中的地点信息，结合本地历史地名索引（`historical_places_index.jsonl`）与地理编码算法，将古地名精准转化为 WGS84 经纬度坐标。
4.  **map_html_renderer (静态渲染)**：基于 React 和 Leaflet 构建地图组件，将处理后的数据注入 HTML 模板，生成包含时间轴交互、路线连线、事件弹窗的单体 HTML 页面。

---

### 如何使用

#### 1. 环境准备
项目推荐使用 **Python 3.11** 环境。首先安装必要的依赖：

```bash
pip install -r maptoposter/requirements.txt
```

#### 2. 配置 API Key (可选)
为了使用 AI 生成功能，请在根目录创建 `.env` 文件并配置：
```bash
API_KEY=your_openai_api_key
BASE_URL=https://api.openai.com/v1
MODEL=gpt-4o-mini
```
*注：若未配置 API Key，系统将自动回退到内置的“辛弃疾”Mock 数据，确保演示流程可跑通。*

#### 3. 核心生成命令
只需一条命令即可完成从生成到渲染的全过程：

```bash
python3 auto_generate.py --name "辛弃疾"
```

#### 4. 查看结果
生成的 HTML 文件将保存在：
`map_story/storymap/examples/story_map/`

脚本执行完成后，系统会自动在默认浏览器中打开该地图页面。

---

### 关键特性
- **纯净单体 HTML**：输出文件不依赖任何后端服务器，通过 CDN 加载 React/Leaflet 库，方便课件分发与离线演示。
- **历史地名自适应**：内置本地索引，优先匹配古今地名对应关系，解决古地名难定位的问题。
- **教学知识点集成**：生成的 Markdown 自动包含“人教版教材知识点”章节，贴合实际教学需求。
- **数据导出支持**：生成的地图页面支持导出 GeoJSON 或 CSV 格式，方便进一步进行地理分析。

---

### 参考来源
README 产品定位及部分逻辑参考自：[map_story](https://github.com/cuizicheng1024/map_story)

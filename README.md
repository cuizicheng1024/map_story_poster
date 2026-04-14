# StoryMap：历史人物轨迹交互地图生成器

这是一个给学校教学场景准备的历史人物 StoryMap 工具。

它的目标很直接：把人物生平资料整理成一份简单的 Markdown，然后一键生成可交互的 HTML 地图课件。对历史课、语文课、校本课程、公开课都比较实用。老师不需要先做复杂可视化，也不用手工在地图上一个点一个点去标人物轨迹，准备好文本就能出结果。

当前仓库默认推荐使用**极速版纯 HTML 地图模式**：直接生成交互地图页面，适合快速备课、课堂展示、课后分享，也方便继续嵌入网页、课件或教学平台。

## 这个项目适合谁

- 历史老师：把人物生平、迁徙路线、重要地点讲得更直观
- 语文老师：把李白、苏轼、杜甫这类人物的作品与人生轨迹连起来讲
- 学生社团或课程项目：把文本资料快速整理成互动地图作品
- 做课件的人：希望拿到一个可以直接打开、直接展示的 HTML 页面

## 你能得到什么

输入是一份人物 Markdown，输出是一个可交互的 HTML 地图课件。

生成后的页面通常会包含这些内容：人物简介、关键地点、时间顺序、路线连线、地点事件说明，以及可以直接在浏览器里打开的交互地图页面。

如果 Markdown 结构完整，页面会直接渲染成较完整的人物故事地图；如果内容比较简化，也能回退成基础地图页，保证先把课件跑出来。

## 推荐用法：极速版纯 HTML 地图

这个模式对应脚本：`generate_pure_story_map.py`

它的作用很明确：**只生成交互式 HTML 地图，不走额外海报流程，适合快速出教学课件。**

### 1）准备人物 Markdown

仓库里已经带了几个可直接跑的例子：

- `map_story/storymap/examples/story/李白.md`
- `map_story/storymap/examples/story/苏轼.md`
- `map_story/storymap/examples/story/唐三藏.md`

Markdown 推荐结构很简单，照着写就行：

```md
# 李白

## 人物档案

### 基本信息
- **姓名**：李白
- **时代**：唐
- **身份**：诗人
- **历史地位**：被后世称为“诗仙”。

### 生平概述
李白一生多在游历中写诗结交，足迹遍布巴蜀、关中、洛阳与江淮一带。

## 人生历程（重要地点）

### 长安
- **时间**：742年—744年
- **地点**：陕西西安
- **事件**：受召入京，供奉翰林。
- **意义**：名声达到顶峰，也埋下仕途失意的伏笔。

### 洛阳
- **时间**：744年—746年
- **地点**：河南洛阳
- **事件**：离开长安后继续漫游。
- **意义**：诗歌主题更转向人生感慨与自由精神。

## 地点坐标

| 现称 | 纬度 | 经度 |
| --- | ---: | ---: |
| 陕西西安 | 34.3416 | 108.9398 |
| 河南洛阳 | 34.6197 | 112.4540 |
```

如果你只是想先跑通，直接用仓库现成的 `李白.md` 或 `苏轼.md` 就够了。

### 2）执行脚本生成地图课件

按人物名生成：

```bash
python3 generate_pure_story_map.py --person 李白
```

或者直接指定 Markdown 文件：

```bash
python3 generate_pure_story_map.py --md map_story/storymap/examples/story/苏轼.md
```

也可以自己指定输出路径：

```bash
python3 generate_pure_story_map.py \
  --md map_story/storymap/examples/story/李白.md \
  --out output/libai_storymap.html
```

### 3）查看输出结果

默认输出目录：

```bash
map_story/storymap/examples/story_map/
```

脚本执行后会打印两类信息：

- 生成出来的 HTML 文件路径
- 可以直接打开的 `file://` 地址

当前环境里，执行下面这条命令：

```bash
python3 generate_pure_story_map.py --person 李白
```

实测已经可以直接生成 HTML，命令行输出会类似这样：

```text
HTML: .../map_story/storymap/examples/story_map/李白__pure__时间戳.html
Open: file:///.../李白__pure__时间戳.html
耗时：解析 1.5ms，渲染 0.1ms，写入 0.2ms，总计 1.9ms
```

对教学场景来说，这种方式的好处很直接：改一段 Markdown，再跑一次脚本，新的课件页面就出来了。

## 最短上手路径

如果你今天就要备课，最省事的流程就是下面这三步：

```bash
cd map_story_poster
python3 generate_pure_story_map.py --person 李白
```

然后打开输出的 HTML 文件，就能看到可交互地图课件。

如果你要讲苏轼，同样直接执行：

```bash
python3 generate_pure_story_map.py --person 苏轼
```

## 关键文件说明

- `generate_pure_story_map.py`：极速版入口，直接输出纯 HTML 地图
- `map_story/storymap/script/story_map.py`：Markdown 解析、地点整理、地图数据组装
- `historical_places_index.jsonl`：内置历史地点索引数据
- `build_historical_index.py`：生成或补充历史地点索引
- `map_story/storymap/examples/story/`：人物 Markdown 示例
- `map_story/storymap/examples/story_map/`：生成后的 HTML 地图页面

## Markdown 怎么写更稳

想让课件效果更完整，建议优先保证这几部分：

1. `人物档案 / 基本信息`
2. `人物档案 / 生平概述`
3. `人生历程（重要地点）`
4. 可选的 `地点坐标` 表

其中最关键的是“重要地点”部分。每个地点最好带上时间、地点、事件、意义，页面展示会更完整。

如果你已经有比较规范的坐标表，脚本会优先使用这些坐标，生成结果会更稳定。

## 一个适合老师的实际使用方式

比较实用的做法是：

先让老师或备课同学把人物生平整理成一份 Markdown，再在这个仓库里运行 `generate_pure_story_map.py`，拿到 HTML 后直接本地打开预览。确认内容没问题后，可以把这个 HTML 页面放进班级展示网页、电子课件、校内资源平台，或者上课时直接浏览器全屏展示。

## 后续可以继续做什么

这个仓库当前最适合做的，是把历史人物内容快速转成互动课件。后续如果你要继续扩展，通常会沿着这些方向走：补充更多人物 Markdown、整理统一模板、扩充地点索引、调整页面风格，或者把生成后的 HTML 接入自己的教学站点。

## 参考来源

README 的产品定位和部分表达方式参考了原始仓库：

- https://github.com/cuizicheng1024/map_story

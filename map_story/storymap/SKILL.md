---
name: StoryMap
description: StoryMap 以“人物—时空—事件”为主线生成历史人物足迹地图并支持空间叙事分析
argument-hint: "[人物名称]"
user-invokable: true
disable-model-invocation: false
---

# Skill Instructions

用于组织与执行人物足迹地图的生成与排查流程，覆盖人物生平、地理编码、结构化数据与 HTML 产出。
运行时间约 3-10 分钟，主要取决于人物生平 Markdown 与地图渲染时间。

## 输入与输出
- 输入：人物名称、人物生平 Markdown、地理编码配置
- 输出：结构化坐标表与 story_map/*.html 页面

## 使用场景
- 生成历史人物足迹地图 HTML 页面
- 排查点位、路线、时间轴或弹窗渲染异常
- 调整人物 Markdown 与提示词结构

## 步骤说明
1. 生成或准备人物生平 Markdown
2. 执行地理编码并生成结构化坐标表
3. 运行 story_map.py 生成 HTML
4. 打开 story_map/*.html 验证点位与排版

## 关键文件
- .github/skills/map-story/script/story_agents.py
- .github/skills/map-story/script/story_map.py
- .github/skills/map-story/docs/story_system_prompt.md

## 示例
人物 Markdown 生成：

```bash
python .github/skills/map-story/script/story_agents.py -p "李白"
```

地图 HTML 生成：

```bash
python .github/skills/map-story/script/story_map.py -p "李白"
```

## 参考资源
- [StoryMapProject.md](./docs/StoryMapProject.md)

---
name: "storymap"
description: "Packages and runs the StoryMap project (serve pages, generate missing people). Invoke when user asks to demo/ship StoryMap as a portable skill bundle under size limits."
---

# StoryMap（时空人物课件生成与交互地图）

## 这个 Skill 是什么

把 StoryMap 的核心代码（Python 服务端 + 生成/渲染脚本 + 最小演示资源）组织成一个可提交的轻量压缩包，用于作品提交（压缩后可控制在 10MB 以内），并提供最小可运行方式。

## 何时调用

- 需要把 StoryMap “打包成一个 Skill/作品附件”提交，且对压缩包体积有限制（如 ≤10MB）。
- 需要在干净环境快速演示：启动服务、打开主页、生成未收录人物页、调用对话代理。
- 需要对包内内容做瘦身：只保留核心代码与必要示例，剔除大体积离线人物产物（大量 .html/.geojson/.csv）。

## 包内应包含的核心内容（建议最小集）

- `storymap/script/`：服务端与渲染核心（`story_map.py`, `map_client.py`, `map_html_renderer.py`, `story_agents.py`）
- `tools/`、`cli/`：批量生成/校验与常用入口（可选）
- `storymap/examples/story_map/`：最小主页资源（`index.html`, `stellar_home_data.json`, `world.json`）
- `storymap/examples/story/`：少量示例人物 Markdown（建议 1–5 个即可）
- `.trae/skills/storymap/SKILL.md`：本说明

## 不应包含的内容（体积与安全）

- `.env` 或任何密钥文件（必须由使用方自行提供环境变量/配置文件）
- 大规模人物页产物：`storymap/examples/story_map/*.html/*.geojson/*.csv`（除非只保留 1–2 个示例）
- `web/` 前端构建目录、`node_modules/`、`.git/`、大体积数据集（除非评审明确要求）

## 最小运行方式（示例）

在压缩包解压后目录（项目根）执行：

```bash
python3 storymap/script/story_map.py --serve --port 8766
```

浏览器打开：

- `http://localhost:8766/`（主页）
- `http://localhost:8766/health`（健康检查）

如需启用大模型/地理编码等在线能力，请通过环境变量或 `.env`（不随包提交）提供必要配置。

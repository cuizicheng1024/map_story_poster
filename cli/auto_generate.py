#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""auto_generate.py

一键入口：输入人名 -> （LLM 生成 Markdown）-> 生成整合版交互 HTML StoryMap。

用法：
  python3 cli/auto_generate.py --name "辛弃疾"

说明：
- 默认使用 OpenAI 兼容接口（读取环境变量 API_KEY）。
- 如果 API_KEY 未配置或调用失败，程序将直接抛出异常，显式报错。

输出：
- Markdown: storymap/examples/story/<name>.md
- HTML:     storymap/examples/story_map/<name>__pure__<timestamp>.html
"""

from __future__ import annotations

import argparse
import os
import re
import webbrowser
from pathlib import Path
from typing import Dict, List, Optional

import requests


# -----------------------------------------------------------------------------
# High-quality System Prompt (核心：强制包含“## 人教版教材知识点”章节)
# -----------------------------------------------------------------------------

def build_story_system_prompt() -> str:
    return (
        "你是一名严谨的历史与文学研究助理与人物传记整理助手。\n"
        "你的目标是：根据给定人名，生成一份可直接用于‘人物足迹交互地图’的 Markdown 文本。\n"
        "请严格遵守格式要求，不要输出任何额外解释、前后缀、免责声明或引用链接。\n"
        "\n"
        "【最高优先级规则】\n"
        "1) 大标题（H1）必须与输入的人物姓名完全一致，例如输入“李白”则标题必须是 `# 李白`。\n"
        "   - 禁止在标题中添加任何别名、字、号、外文名、朝代信息或括号说明（如“（字太白）”“（李太白）”等）。\n"
        "2) 除标题外，可以在正文中介绍字、号等信息。\n"
        "\n"
        "【总要求】\n"
        "1) 只输出 Markdown 正文，不要代码块包裹（不要输出 ```md）。\n"
        "2) 使用中文。信息不确定时必须标注：‘存疑’或‘说法不一’，不要编造具体年份/地点细节。\n"
        "3) 地点描述必须尽量包含‘古称（今XX省XX市）’这种结构，便于地理编码。\n"
        "4) ‘人生历程与重要地点’必须按时间顺序，地点总数不少于 8 个、不多于 25 个。\n"
        "5) 必须提供‘地点坐标’表格：至少覆盖 8 个地点；经纬度用小数；尽量使用现代城市坐标。\n"
        "6) 所有 Markdown 表格必须包含表头分隔线行，形式类似：`| --- | --- | --- |`。\n"
        "\n"
        "【输出版式（必须严格包含以下章节标题，标题级别也必须一致）】\n"
        "# <人物姓名>\n"
        "\n"
        "## 人物档案\n"
        "\n"
        "### 基本信息\n"
        "- **姓名**：...\n"
        "- **时代**：...\n"
        "- **出生**：公元XXXX年，古称（今XX省XX市）...（不确定则存疑）\n"
        "- **去世**：公元XXXX年，古称（今XX省XX市）...（不确定则存疑）\n"
        "- **享年**：...\n"
        "- **主要身份**：...（用‘、’分隔）\n"
        "- **历史地位**：一句话总结\n"
        "- **主要成就**：2-5 条要点\n"
        "\n"
        "### 生平概述\n"
        "150-220 字，一段话概述人物生平与时代背景。\n"
        "\n"
        "---\n"
        "\n"
        "## 人生足迹地图说明\n"
        "- 🗺️ **行程概览**：一句话概括行程范围\n"
        "- ⏱️ **时间跨度**：起止年份与总年数\n"
        "- 📍 **地理范围**：大致覆盖地区\n"
        "- 🌟 **重要节点数量**：核心地点数量（不少于 8 个）\n"
        "\n"
        "---\n"
        "\n"
        "## 人生历程与重要地点（按时间顺序）\n"
        "按时间顺序，为每个地点写一个三级标题。必须至少包含：出生地、去世地，总地点数不少于 8 个、不多于 25 个。\n"
        "\n"
        "### 🟢 出生地：<地点名称>\n"
        "- **时间**：公元XXXX年或范围\n"
        "- **停留时间**：...\n"
        "- **地点**：古称（今XX省XX市）\n"
        "- **事件**：...\n"
        "- **意义**：...\n"
        "\n"
        "### 📍 重要地点：<地点名称>\n"
        "- **时间**：公元XXXX年或范围\n"
        "- **停留时间**：...\n"
        "- **地点**：古称（今XX省XX市）\n"
        "- **事件**：...\n"
        "- **意义**：...\n"
        "- **名篇名句**：列出 1-3 条，格式为“《作品》：句子”，多条用“；”分隔（若人物无作品可省略该字段）\n"
        "\n"
        "### 🔴 去世地：<地点名称>\n"
        "- **时间**：公元XXXX年或范围\n"
        "- **停留时间**：...\n"
        "- **地点**：古称（今XX省XX市）\n"
        "- **事件**：...\n"
        "- **意义**：...\n"
        "- **名篇名句**：同上（可选）\n"
        "\n"
        "---\n"
        "\n"
        "## 生平时间线\n"
        "输出表格：| 年份 | 年龄 | 关键事件 |（必须有表头分隔线），按时间从早到晚。\n"
        "\n"
        "---\n"
        "\n"
        "## 人教版教材知识点\n"
        "【强制要求：必须单列此章节】\n"
        "只从文学与历史理解的角度，梳理该人物在教材/经典叙述中常见的核心作品与史实节点，以及关键概念。\n"
        "禁止出现‘考点/考试/备课/应试/刷题/中学考试’等话术；不要把内容写成‘考点梳理’或‘备课提纲’，面向一般读者即可。\n"
        "要求：用 Markdown 的 **加粗** 标记突出核心作品名、重要术语与关键词；并用 **重点** 来标记最核心的信息。\n"
        "重点标记规则：在每个小节（语文/历史）的要点列表中，至少有 3 条以 ‘- **重点**：...’ 的格式输出。\n"
        "请按如下结构输出：\n"
        "### 语文（课文/词作）\n"
        "- 课文/词作：...（列 2-5 个，核心作品名请 **加粗**）\n"
        "- 核心要点：...（列 4-8 条：主题思想、意象、手法、风格、典故、表达技巧、情感线索等；每条至少包含 1 个 **加粗** 关键词；其中至少 3 条用 ‘- **重点**：...’ 标注）\n"
        "\n"
        "### 历史（史实/人物定位）\n"
        "- 关键史实：...（列 3-6 条；每条至少包含 1 个 **加粗** 关键词；其中至少 2 条用 ‘- **重点**：...’ 标注）\n"
        "- 核心要点：...（列 3-6 条：时代背景、立场、功过评价、与重大事件关系等；每条至少包含 1 个 **加粗** 关键词；其中至少 3 条用 ‘- **重点**：...’ 标注）\n"
        "\n"
        "---\n"
        "\n"
        "## 地点坐标\n"
        "输出表格：| 现称 | 纬度 | 经度 |（必须有表头分隔线）。\n"
    )


# -----------------------------------------------------------------------------
# OpenAI compatible client (reads env API_KEY)
# -----------------------------------------------------------------------------

def _resolve_openai_endpoint(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        base = "https://api.openai.com"
    # allow passing .../v1
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def call_openai_compatible(
    *,
    messages: List[Dict[str, str]],
    api_key: str,
    model: str,
    base_url: str,
    timeout: int = 120,
    temperature: float = 0.2,
) -> str:
    url = _resolve_openai_endpoint(base_url)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload: Dict[str, object] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()

    # OpenAI standard: choices[0].message.content
    if isinstance(data, dict):
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            msg = choices[0].get("message") if isinstance(choices[0], dict) else None
            if isinstance(msg, dict) and isinstance(msg.get("content"), str):
                return msg.get("content") or ""

    raise RuntimeError(f"无法解析模型返回：{type(data)}")


def _strip_md_fence(text: str) -> str:
    s = (text or "").strip()
    if not s:
        return ""
    # remove ```md ... ``` wrappers if present
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z0-9_-]*\n", "", s)
        s = re.sub(r"\n```\s*$", "", s)
    return s.strip()


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _default_story_md_path(name: str) -> Path:
    return _repo_root() / "storymap" / "examples" / "story" / f"{name}.md"


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def generate_story_markdown(name: str) -> str:
    from dotenv import load_dotenv
    load_dotenv(".env")
    api_key = (os.getenv("API_KEY") or "").strip()
    base_url = (os.getenv("BASE_URL") or os.getenv("OPENAI_BASE_URL") or "").strip()
    model = (os.getenv("MODEL") or os.getenv("OPENAI_MODEL") or "gpt-4o-mini").strip()
    timeout = int(os.getenv("TIMEOUT", "120"))

    sys_prompt = build_story_system_prompt()
    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": f"人物姓名：{name}"},
    ]

    if not api_key:
        raise RuntimeError("未配置 API_KEY，请在 .env 文件中设置。")

    raw = call_openai_compatible(
        messages=messages,
        api_key=api_key,
        model=model,
        base_url=base_url,
        timeout=timeout,
        temperature=0.2,
    )
    md = _strip_md_fence(raw)
    if not md:
        raise RuntimeError("模型返回内容为空")
    return md


def ensure_required_sections(md: str) -> str:
    s = (md or "").strip()
    if not s:
        return ""

    # Ensure title.
    if not re.search(r"^#\s+", s, flags=re.M):
        s = "# 人物\n\n" + s

    # Ensure required chapter exists.
    if "\n## 人教版教材知识点\n" not in "\n" + s + "\n":
        s += (
            "\n\n## 人教版教材知识点\n\n"
            "### 语文（课文/词作）\n"
            "- 课文/词作：存疑\n"
            "- 核心要点：存疑\n\n"
            "### 历史（史实/人物定位）\n"
            "- 关键史实：存疑\n"
            "- 核心要点：存疑\n"
        )

    # Ensure coords section exists.
    if "\n## 地点坐标\n" not in "\n" + s + "\n":
        s += (
            "\n\n## 地点坐标\n\n"
            "| 现称 | 现代搜索地名 | 纬度 | 经度 |\n"
            "| --- | --- | --- | --- |\n"
        )

    return s.strip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="一键人名生成地图与教材知识点")
    parser.add_argument("--name", required=True, type=str, help='人物姓名，例如："辛弃疾"')
    args = parser.parse_args()

    name = str(args.name or "").strip()
    if not name:
        raise SystemExit("--name 不能为空")

    md = generate_story_markdown(name)
    md = ensure_required_sections(md)

    md_path = _default_story_md_path(name)
    _write_text(md_path, md)

    # Reuse existing pure-html generator.
    from generate_pure_story_map import generate_pure_html

    result = generate_pure_html(md_path=str(md_path))
    print(f"✅ Markdown: {md_path}")
    print(f"✅ HTML: {result['html_path']}")

    # 自动在默认浏览器中打开 HTML 地图
    try:
        html_path = result['html_path']
        webbrowser.open(f"file://{os.path.abspath(html_path)}")
    except Exception:
        pass


if __name__ == "__main__":
    main()

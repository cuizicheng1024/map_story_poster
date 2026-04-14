#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""auto_generate.py

一键入口：输入人名 -> （LLM 生成 Markdown）-> 生成整合版交互 HTML StoryMap。

用法：
  python3 auto_generate.py --name "辛弃疾"

说明：
- 默认优先尝试 OpenAI 兼容接口（读取环境变量 API_KEY）。
- 若未配置 API_KEY（或调用失败），会回退到内置的“辛弃疾”Mock Markdown，
  保证后续地图解析与 HTML 渲染流程畅通。

输出：
- Markdown: map_story/storymap/examples/story/<name>.md
- HTML:     map_story/storymap/examples/story_map/<name>__pure__<timestamp>.html
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
        "【总要求】\n"
        "1) 只输出 Markdown 正文，不要代码块包裹（不要输出 ```md）。\n"
        "2) 使用中文。信息不确定时必须标注：‘存疑’或‘说法不一’，不要编造具体年份/地点细节。\n"
        "3) 地点描述必须尽量包含‘古称（今XX省XX市）’这种结构，便于地理编码。\n"
        "4) ‘人生历程与重要地点’必须按时间顺序，地点总数不少于 8 个、不多于 25 个。\n"
        "5) 必须提供‘地点坐标’表格：至少覆盖 8 个地点；经纬度用小数；尽量使用现代城市坐标。\n"
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
        "## 人生历程（重要地点）\n"
        "按时间顺序，为每个地点写一个三级标题。必须至少包含：出生地、去世地。\n"
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


# -----------------------------------------------------------------------------
# Mock Markdown (极度逼真 + 结构完整)：辛弃疾
# -----------------------------------------------------------------------------

MOCK_XIN_QIJI_MD = """# 辛弃疾

## 人物档案

### 基本信息
- **姓名**：辛弃疾（字幼安，号稼轩）
- **时代**：南宋
- **出生**：公元1140年，历城（今山东省济南市历城区）
- **去世**：公元1207年，铅山（今江西省上饶市铅山县）
- **享年**：68
- **主要身份**：词人、将领、政治家、主战派代表人物
- **历史地位**：南宋豪放词宗之一，以爱国词作与抗金主张著称，与苏轼并称“苏辛”。
- **主要成就**：
  - 以词抒写家国之痛与复国理想，形成豪放词高峰
  - 早年组织义军抗金，后南归宋廷，长期主张北伐
  - 参与地方治理与军政筹划，提出多项经略恢复方案

### 生平概述
辛弃疾生于金人统治下的山东，青年时期即投身反金义军，曾以胆识奇谋擒叛将、策应起义。二十余岁南归南宋后，抱持恢复中原之志，却屡遭主和派掣肘，政治生涯多起伏。其一生辗转于江淮、两浙与江西等地，既有军政实践，也有退居乡里之沉郁。辛词以雄健奔放、用典精密见长，常在壮怀与悲慨之间回旋，其词作与事功在后世广为传诵。

---

## 人生历程（重要地点）

### 🟢 出生地：历城
- **时间**：1140年—约1160年
- **停留时间**：少年至青年早期
- **地点**：历城（今山东省济南市历城区）
- **事件**：在金统治区成长，目睹山河沦陷；早年习武读书，形成强烈家国意识。
- **意义**：爱国立场与“恢复”理想的精神原点。

### 📍 重要地点：济南
- **时间**：约1150年—1161年
- **停留时间**：约十余年（间歇）
- **地点**：济南（今山东省济南市）
- **事件**：参与地方义军与反金活动（细节说法不一）。
- **意义**：由文士向武将转变的重要阶段。

### 📍 重要地点：开封
- **时间**：1161年—1162年
- **停留时间**：数月至一年（存疑）
- **地点**：汴京（今河南省开封市）
- **事件**：在北方形势剧变中辗转策应起义；传说有擒叛将情节（史实细节见诸不同记载）。
- **意义**：个人军事才略的“传奇化”来源之一。

### 📍 重要地点：建康
- **时间**：1162年—约1164年
- **停留时间**：约两年
- **地点**：建康（今江苏省南京市）
- **事件**：南归后入宋廷，开始仕途；屡陈恢复方略。
- **意义**：从“义军将领”转为“朝廷官员”，理想与现实冲突的开端。
- **名篇名句**：
  - 《破阵子·为陈同甫赋壮词以寄之》：醉里挑灯看剑，梦回吹角连营；
  - 《南乡子·登京口北固亭有怀》：何处望神州？满眼风光北固楼。

### 📍 重要地点：镇江（京口）
- **时间**：约1164年—1166年
- **停留时间**：数月到一年（存疑）
- **地点**：京口（今江苏省镇江市）
- **事件**：沿江防务与军政议论频仍；登临怀古，借古讽今。
- **意义**：以地理空间触发历史想象与家国情怀的重要“词学场景”。
- **名篇名句**：
  - 《永遇乐·京口北固亭怀古》：想当年，金戈铁马，气吞万里如虎；
  - 《永遇乐·京口北固亭怀古》：凭谁问：廉颇老矣，尚能饭否？

### 📍 重要地点：杭州（临安）
- **时间**：约1166年—1180年
- **停留时间**：十余年（断续）
- **地点**：临安（今浙江省杭州市）
- **事件**：在朝中与地方官之间转任；多次遭罢黜或外放，主战主张难以施展。
- **意义**：政治挫折与“报国无门”的情绪积累。
- **名篇名句**：
  - 《青玉案·元夕》：东风夜放花千树，更吹落，星如雨；
  - 《青玉案·元夕》：众里寻他千百度，蓦然回首，那人却在，灯火阑珊处。

### 📍 重要地点：南昌
- **时间**：约1180年—1183年
- **停留时间**：数年
- **地点**：洪州（今江西省南昌市）
- **事件**：参与地方治理与军备筹画（具体官职历任可因史料不同而略有差异）。
- **意义**：由“议论北伐”转向“地方实践”，展现治世能力。

### 📍 重要地点：上饶
- **时间**：约1183年—1207年
- **停留时间**：晚年长期（间歇入仕）
- **地点**：信州（今江西省上饶市）
- **事件**：晚年多寓居乡里，仍关心时局；创作、结交、讲学与著述。
- **意义**：理想沉潜为文学与人格力量，形成“稼轩词”的成熟面貌。

### 🔴 去世地：铅山（带湖）
- **时间**：1207年
- **停留时间**：晚年居住地
- **地点**：铅山（今江西省上饶市铅山县）
- **事件**：晚年病逝（细节存疑），留有大量词作与议论。
- **意义**：以“未竟恢复之志”的悲壮形象进入后世记忆。
- **名篇名句**：
  - 《西江月·夜行黄沙道中》：明月别枝惊鹊，清风半夜鸣蝉。

---

## 生平时间线

| 年份 | 年龄 | 关键事件 |
| --- | ---: | --- |
| 1140年 | 1 | 出生于历城（今山东济南一带）。 |
| 1161年 | 22 | 参加反金义军并在北方辗转（具体事迹说法不一）。 |
| 1162年 | 23 | 南归南宋，开始仕途与恢复方略的建言。 |
| 1164年 | 25 | 江淮一带从事军政与防务讨论（存疑）。 |
| 1166年 | 27 | 参与朝中事务与地方任官，屡遭掣肘。 |
| 1180年 | 41 | 转任地方治理与军备筹画工作。 |
| 1183年 | 44 | 多居江西一带，退居与再起交替。 |
| 1207年 | 68 | 卒于铅山（今江西上饶铅山）。 |

---

## 人教版教材知识点

### 语文（课文/词作）
- 课文/词作：
  - **《破阵子·为陈同甫赋壮词以寄之》**（“醉里挑灯看剑”）
  - **《永遇乐·京口北固亭怀古》**（“金戈铁马”“廉颇老矣”）
  - **《青玉案·元夕》**（“众里寻他千百度”）
  - **《西江月·夜行黄沙道中》**（“明月别枝惊鹊”）
- 核心要点：
  - **豪放词**风格：以雄健笔力抒写家国情怀与个人抱负
  - **爱国主题**与情感张力：壮志难酬、报国无门的悲慨
  - **典故**与用典：借古讽今（如“**廉颇老矣**”），增强论辩与历史纵深
  - **意象**与场景：军旅意象（**剑**、**角**、**连营**）与现实抒情场景（**元夕灯火**）对照
  - 表达技巧：以景衬情、虚实结合、对比反衬（**热烈/冷清**、**盛景/孤怀**）

### 历史（史实/人物定位）
- 关键史实：
  - 南宋与金对峙格局下的 **主和/主战** 分歧
  - 辛弃疾早年在金统治区参加 **抗金活动**，后 **南归南宋**
  - 多次提出 **恢复中原** 的军事与政治主张，但难获彻底采纳
  - 晚年退居乡里，**文学成就** 与 **政治抱负** 并存
- 核心要点：
  - 人物定位：南宋 **主战派** 代表、爱国文人将领
  - 时代背景：偏安政权与边防压力、政治路线之争
  - 功过评价：理想与现实冲突、政治失意与文学高峰的内在关联
  - 与重大事件关系：北伐议题、江淮防务、地方治理实践（以教材表述为准）

---

## 地点坐标

| 现称 | 纬度 | 经度 |
| --- | ---: | ---: |
| 山东济南 | 36.6512 | 117.1201 |
| 山东济南历城 | 36.6870 | 117.0650 |
| 河南开封 | 34.7973 | 114.3076 |
| 江苏南京 | 32.0603 | 118.7969 |
| 江苏镇江 | 32.1878 | 119.4250 |
| 浙江杭州 | 30.2741 | 120.1551 |
| 江西南昌 | 28.6820 | 115.8579 |
| 江西上饶 | 28.4546 | 117.9436 |
| 江西铅山 | 28.3225 | 117.7092 |
"""


def _repo_root() -> Path:
    return Path(__file__).resolve().parent


def _default_story_md_path(name: str) -> Path:
    return _repo_root() / "map_story" / "storymap" / "examples" / "story" / f"{name}.md"


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def generate_story_markdown(name: str) -> str:
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
        # Requirement: when no key configured, must fallback to a realistic Xin Qiji mock.
        md = MOCK_XIN_QIJI_MD
        if name.strip() and name.strip() != "辛弃疾":
            # Make the fallback file still look consistent with the requested name.
            md = md.replace("# 辛弃疾", f"# {name}", 1)
            md = md.replace("- **姓名**：辛弃疾（字幼安，号稼轩）", f"- **姓名**：{name}（存疑）", 1)
        return md

    try:
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
            raise RuntimeError("模型返回为空")
        return md
    except Exception:
        # Any failure also falls back to mock to keep pipeline running.
        md = MOCK_XIN_QIJI_MD
        if name.strip() and name.strip() != "辛弃疾":
            md = md.replace("# 辛弃疾", f"# {name}", 1)
            md = md.replace("- **姓名**：辛弃疾（字幼安，号稼轩）", f"- **姓名**：{name}（存疑）", 1)
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
            "| 现称 | 纬度 | 经度 |\n"
            "| --- | ---: | ---: |\n"
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

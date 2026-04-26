"""
从公开网页/文本中抽取“历史人物姓名”的工具脚本（辅助构建人物名单）。

用途：
- 在早期数据准备阶段，快速从网页文本中抽取候选人物名，减少手工整理成本。
- 产出的候选名单建议人工抽检后再进入批量生成链路。
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import List, Optional, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import extract_historical_figures as ehf


def _load_env() -> None:
    ehf._load_env_from_dotenv_if_needed(_REPO_ROOT / ".env")


def _download_url(url: str, timeout_seconds: float) -> str:
    req = urllib.request.Request(
        url=url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; educator-tool/1.0; +https://platform.xiaomimimo.com/)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            content_type = (resp.headers.get("Content-Type") or "").lower()
            data = resp.read()
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        raise RuntimeError(f"下载失败 HTTP {e.code}: {url}\n{body[:1000]}") from e
    except Exception as e:
        raise RuntimeError(f"下载失败: {url}\n{e}") from e

    enc = "utf-8"
    m = re.search(r"charset=([a-zA-Z0-9_-]+)", content_type)
    if m:
        enc = m.group(1).strip().lower()
    try:
        return data.decode(enc, errors="replace")
    except Exception:
        return data.decode("utf-8", errors="replace")


def _html_to_text(html: str) -> str:
    s = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    s = re.sub(r"(?is)<br\\s*/?>", "\n", s)
    s = re.sub(r"(?is)</p\\s*>", "\n\n", s)
    s = re.sub(r"(?is)</li\\s*>", "\n", s)
    s = re.sub(r"(?is)<[^>]+>", " ", s)
    s = s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def collect_from_sources(
    *,
    sources: Sequence[str],
    output_path: Path,
    model: str,
    base_url: str,
    auth_mode: str,
    max_chars_per_chunk: int,
    max_completion_tokens: int,
    temperature: float,
    top_p: float,
    timeout_seconds: float,
    max_retries: int,
    validate: bool,
    validate_batch_size: int,
) -> List[str]:
    _load_env()
    api_key = os.environ.get("MIMO_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("未检测到 MIMO_API_KEY。请在环境变量或 .env 中配置。")

    all_names: List[str] = []
    for url in sources:
        raw = _download_url(url, timeout_seconds=timeout_seconds)
        text = _html_to_text(raw)
        if not text:
            continue
        chunks = ehf._chunk_text(text, max_chars=max_chars_per_chunk)
        for chunk in chunks:
            names = ehf._extract_json_array(
                ehf._mimo_chat_completions(
                    api_key=api_key,
                    base_url=base_url,
                    auth_mode=auth_mode,
                    model=model,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "你是MiMo（中文名称也是MiMo），是小米公司研发的AI智能助手。"
                                "你是一个严谨的信息抽取器。你只输出严格的JSON，不输出任何额外文字。"
                            ),
                        },
                        {
                            "role": "user",
                            "content": (
                                "从下面网页文本中提取所有出现的历史人物姓名（中国与世界历史人物均可）。"
                                "只输出一个JSON数组（字符串数组），姓名去重，保持首次出现的顺序。"
                                "如果没有人物姓名，输出空数组[]。"
                                "\n\n网页文本：\n'''"
                                + chunk
                                + "'''"
                            ),
                        },
                    ],
                    max_completion_tokens=max_completion_tokens,
                    temperature=temperature,
                    top_p=top_p,
                    timeout_seconds=timeout_seconds,
                    max_retries=max_retries,
                )
            )
            all_names.extend(names)

    names = ehf._dedupe_preserve_order(all_names)

    if validate and names:
        names = ehf.validate_historical_figures(
            names=names,
            api_key=api_key,
            base_url=base_url,
            auth_mode=auth_mode,
            model=model,
            max_completion_tokens=max_completion_tokens,
            temperature=min(temperature, 0.2),
            top_p=top_p,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            batch_size=max(1, validate_batch_size),
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(names, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return names


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="从公开网页中抽取历史人物姓名，并调用 MiMo 进行复核后输出 JSON。")
    parser.add_argument("--sources_file", help="包含URL列表的文本文件（每行一个URL）")
    parser.add_argument("--source", action="append", default=[], help="单个URL（可重复传入多次）")
    parser.add_argument("--output", default="web_figures.json", help="输出JSON文件，默认: web_figures.json")
    parser.add_argument("--model", default="mimo-v2-pro", help="模型名，默认: mimo-v2-pro")
    parser.add_argument("--base_url", default="https://api.xiaomimimo.com/v1", help="API Base URL")
    parser.add_argument("--auth_mode", default="api-key", choices=["api-key", "bearer"], help="认证方式")
    parser.add_argument("--max_chars_per_chunk", type=int, default=3500, help="分块最大字符数")
    parser.add_argument("--max_completion_tokens", type=int, default=1024, help="模型输出token上限")
    parser.add_argument("--temperature", type=float, default=0.2, help="温度")
    parser.add_argument("--top_p", type=float, default=0.95, help="top_p")
    parser.add_argument("--timeout_seconds", type=float, default=120.0, help="下载与请求超时（秒）")
    parser.add_argument("--max_retries", type=int, default=3, help="MiMo 请求重试次数")
    parser.add_argument("--validate", action="store_true", help="复核是否为历史人物姓名（额外调用）")
    parser.add_argument("--validate_batch_size", type=int, default=40, help="复核批大小")

    args = parser.parse_args(list(argv) if argv is not None else None)
    sources: List[str] = []
    if args.sources_file:
        p = Path(args.sources_file)
        if not p.exists():
            print(f"sources_file 不存在: {p}", file=sys.stderr)
            return 2
        sources.extend([line.strip() for line in p.read_text(encoding="utf-8", errors="ignore").splitlines() if line.strip()])
    sources.extend([s.strip() for s in args.source if s and s.strip()])
    sources = [s for s in sources if s.startswith("http://") or s.startswith("https://")]
    if not sources:
        print("未提供任何可用URL。请使用 --source 或 --sources_file。", file=sys.stderr)
        return 2

    repo_root = _REPO_ROOT
    out = (repo_root / args.output).resolve()
    try:
        names = collect_from_sources(
            sources=sources,
            output_path=out,
            model=args.model,
            base_url=args.base_url,
            auth_mode=args.auth_mode,
            max_chars_per_chunk=args.max_chars_per_chunk,
            max_completion_tokens=args.max_completion_tokens,
            temperature=args.temperature,
            top_p=args.top_p,
            timeout_seconds=args.timeout_seconds,
            max_retries=args.max_retries,
            validate=args.validate,
            validate_batch_size=args.validate_batch_size,
        )
    except Exception as e:
        print(str(e), file=sys.stderr)
        return 1

    print(f"已写入: {out}（共 {len(names)} 个姓名，来源URL数 {len(sources)}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

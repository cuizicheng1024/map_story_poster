import argparse
import datetime as _dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple


def _parse_dotenv(dotenv_path: Path) -> List[Tuple[str, str]]:
    if not dotenv_path.exists():
        return []
    pairs: List[Tuple[str, str]] = []
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        key = k.strip()
        val = v.strip()
        if val and ("#" in val):
            val = val.split("#", 1)[0].rstrip()
        val = val.strip().strip('"').strip("'")
        if key:
            pairs.append((key, val))
    return pairs


def _load_env_from_dotenv_if_needed(dotenv_path: Path) -> None:
    for k, v in _parse_dotenv(dotenv_path):
        if k and v and os.environ.get(k) in (None, ""):
            os.environ[k] = v


def _iter_text_files(input_dir: Path) -> List[Path]:
    if not input_dir.exists():
        return []
    files: List[Path] = []
    for p in input_dir.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() in {".txt", ".md"}:
            files.append(p)
    return sorted(files)


def _chunk_text(text: str, max_chars: int) -> List[str]:
    cleaned = text.replace("\r\n", "\n")
    if len(cleaned) <= max_chars:
        return [cleaned]
    parts: List[str] = []
    buf: List[str] = []
    buf_len = 0
    for para in re.split(r"\n{2,}", cleaned):
        piece = para.strip()
        if not piece:
            continue
        if buf_len + len(piece) + (2 if buf else 0) <= max_chars:
            if buf:
                buf.append("\n\n")
                buf_len += 2
            buf.append(piece)
            buf_len += len(piece)
            continue
        if buf:
            parts.append("".join(buf))
            buf = []
            buf_len = 0
        if len(piece) <= max_chars:
            buf = [piece]
            buf_len = len(piece)
            continue
        for i in range(0, len(piece), max_chars):
            parts.append(piece[i : i + max_chars])
    if buf:
        parts.append("".join(buf))
    return parts


def _mimo_chat_completions(
    *,
    api_key: str,
    base_url: str,
    auth_mode: str,
    model: str,
    messages: Sequence[dict],
    max_completion_tokens: int,
    temperature: float,
    top_p: float,
    timeout_seconds: float,
    max_retries: int,
) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": list(messages),
        "max_completion_tokens": max_completion_tokens,
        "temperature": temperature,
        "top_p": top_p,
        "stream": False,
        "stop": None,
        "frequency_penalty": 0,
        "presence_penalty": 0,
    }

    auth_mode_normalized = auth_mode.strip().lower()
    headers = {"Content-Type": "application/json"}
    if auth_mode_normalized == "api-key":
        headers["api-key"] = api_key
    elif auth_mode_normalized == "bearer":
        headers["Authorization"] = f"Bearer {api_key}"
    else:
        raise ValueError("auth_mode 必须是 api-key 或 bearer")

    last_err: Exception | None = None
    for attempt in range(max_retries + 1):
        req = urllib.request.Request(
            url=url,
            method="POST",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                data = resp.read()
            break
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:
                body = ""
            if attempt < max_retries and (e.code == 429 or 500 <= e.code <= 599):
                time.sleep(min(2**attempt, 20) + (0.1 * attempt))
                last_err = RuntimeError(f"MiMo API HTTP {e.code}: {body[:2000]}")
                continue
            raise RuntimeError(f"MiMo API HTTP {e.code}: {body[:2000]}") from e
        except Exception as e:
            if attempt < max_retries:
                time.sleep(min(2**attempt, 20) + (0.1 * attempt))
                last_err = e
                continue
            raise RuntimeError(f"MiMo API request failed: {e}") from e
    else:
        raise RuntimeError(f"MiMo API request failed: {last_err}") from last_err

    try:
        obj = json.loads(data.decode("utf-8"))
        return obj["choices"][0]["message"]["content"]
    except Exception as e:
        raise RuntimeError(f"MiMo API response parse failed: {e}") from e


def _extract_json_array(text: str) -> List[str]:
    s = text.strip()
    try:
        data = json.loads(s)
        if isinstance(data, list):
            return [str(x).strip() for x in data if str(x).strip()]
    except Exception:
        pass

    m = re.search(r"\[[\s\S]*\]", s)
    if not m:
        return []
    snippet = m.group(0)
    try:
        data = json.loads(snippet)
        if isinstance(data, list):
            return [str(x).strip() for x in data if str(x).strip()]
    except Exception:
        return []
    return []


def _dedupe_preserve_order(items: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for x in items:
        k = x.strip()
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(k)
    return out


def extract_historical_figures_from_files(
    *,
    input_files: Sequence[Path],
    api_key: str,
    base_url: str,
    auth_mode: str,
    model: str,
    max_chars_per_chunk: int,
    max_completion_tokens: int,
    temperature: float,
    top_p: float,
    timeout_seconds: float,
    max_retries: int,
) -> List[str]:
    today = _dt.date.today().isoformat()
    system = (
        "你是MiMo（中文名称也是MiMo），是小米公司研发的AI智能助手。"
        f"今天的日期：{today}。你的知识截止日期是2024年12月。"
        "你是一个严谨的信息抽取器。你只输出严格的JSON，不输出任何额外文字。"
    )

    all_names: List[str] = []
    for file_path in input_files:
        raw = file_path.read_text(encoding="utf-8", errors="ignore")
        for chunk in _chunk_text(raw, max_chars=max_chars_per_chunk):
            user = (
                "从下面教材节选中提取所有出现的历史人物姓名（中国与世界历史人物均可）。"
                "要求：只输出一个JSON数组（字符串数组），姓名去重，保持首次出现的顺序。"
                "如果没有人物姓名，输出空数组[]。"
                "\n\n教材节选：\n"
                "'''"
                + chunk
                + "'''"
            )
            content = _mimo_chat_completions(
                api_key=api_key,
                base_url=base_url,
                auth_mode=auth_mode,
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                max_completion_tokens=max_completion_tokens,
                temperature=temperature,
                top_p=top_p,
                timeout_seconds=timeout_seconds,
                max_retries=max_retries,
            )
            names = _extract_json_array(content)
            all_names.extend(names)

    return _dedupe_preserve_order(all_names)


def validate_historical_figures(
    *,
    names: Sequence[str],
    api_key: str,
    base_url: str,
    auth_mode: str,
    model: str,
    max_completion_tokens: int,
    temperature: float,
    top_p: float,
    timeout_seconds: float,
    max_retries: int,
    batch_size: int,
) -> List[str]:
    today = _dt.date.today().isoformat()
    system = (
        "你是MiMo（中文名称也是MiMo），是小米公司研发的AI智能助手。"
        f"今天的日期：{today}。你的知识截止日期是2024年12月。"
        "你是一个严格的分类器。你只输出严格的JSON，不输出任何额外文字。"
    )
    items = [n.strip() for n in names if n and n.strip()]
    removed_all: List[str] = []

    for i in range(0, len(items), batch_size):
        batch = items[i : i + batch_size]
        user = (
            "下面是一些从教材中抽取的候选条目，大多数应为历史人物姓名。"
            "请找出其中“明显不是历史人物姓名”的条目（例如：朝代/国家/地名/机构/官职/术语/书名/事件名/纯数字）。"
            "只输出一个JSON数组（字符串数组），数组里是需要剔除的条目，保持原顺序。"
            "如果都像历史人物姓名，输出空数组[]。"
            "注意：不要输出解释；不要输出新增的名字。"
            "\n\n候选列表：\n"
            + json.dumps(batch, ensure_ascii=False)
        )
        content = _mimo_chat_completions(
            api_key=api_key,
            base_url=base_url,
            auth_mode=auth_mode,
            model=model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_completion_tokens=max_completion_tokens,
            temperature=temperature,
            top_p=top_p,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )
        removed = _extract_json_array(content)
        removed_set = set(removed)
        removed_in_batch = [x for x in batch if x in removed_set]
        if len(removed_in_batch) > int(len(batch) * 0.5):
            continue
        removed_all.extend(removed_in_batch)

    removed_set_all = set(removed_all)
    kept = [x for x in items if x not in removed_set_all]
    return _dedupe_preserve_order(kept)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="从人教版教材文本（.txt/.md）中抽取历史人物姓名，调用小米 MiMo OpenAI 兼容接口，并保存为JSON文件。"
    )
    parser.add_argument(
        "--input_dir",
        default="textbooks",
        help="教材文本目录（递归读取 .txt/.md 文件），默认: textbooks",
    )
    parser.add_argument(
        "--output",
        default="historical_figures.json",
        help="输出JSON文件路径，默认: historical_figures.json",
    )
    parser.add_argument("--base_url", default="https://api.xiaomimimo.com/v1", help="API Base URL，默认: https://api.xiaomimimo.com/v1")
    parser.add_argument(
        "--auth_mode",
        default="api-key",
        choices=["api-key", "bearer"],
        help="认证方式，默认: api-key（也可选 bearer）",
    )
    parser.add_argument("--model", default="mimo-v2-pro", help="模型名，默认: mimo-v2-pro")
    parser.add_argument(
        "--max_chars_per_chunk",
        type=int,
        default=3500,
        help="每次发送给模型的最大字符数（按段落切分），默认: 3500",
    )
    parser.add_argument(
        "--max_completion_tokens",
        type=int,
        default=1024,
        help="模型输出token上限，默认: 1024",
    )
    parser.add_argument("--temperature", type=float, default=0.2, help="默认: 0.2")
    parser.add_argument("--top_p", type=float, default=0.95, help="默认: 0.95")
    parser.add_argument("--timeout_seconds", type=float, default=120.0, help="单次请求超时秒数，默认: 120")
    parser.add_argument("--max_retries", type=int, default=3, help="请求重试次数（429/5xx/网络错误），默认: 3")
    parser.add_argument(
        "--validate",
        action="store_true",
        help="对抽取出的名字逐条复核为“历史人物姓名”（会产生额外调用）",
    )
    parser.add_argument("--validate_batch_size", type=int, default=40, help="复核批大小，默认: 40")

    args = parser.parse_args(list(argv) if argv is not None else None)

    repo_root = Path(__file__).resolve().parent
    _load_env_from_dotenv_if_needed(repo_root / ".env")

    api_key = os.environ.get("MIMO_API_KEY", "").strip()
    if not api_key:
        print("未检测到 MIMO_API_KEY。请在环境变量或 .env 中配置。", file=sys.stderr)
        return 2

    input_dir = (repo_root / args.input_dir).resolve()
    input_files = _iter_text_files(input_dir)
    if not input_files:
        print(
            f"未在目录中找到可处理的教材文本文件: {input_dir}\n"
            "请将人教版教材内容转换为 .txt 或 .md 后放入该目录，再运行本脚本。",
            file=sys.stderr,
        )
        return 2

    names = extract_historical_figures_from_files(
        input_files=input_files,
        api_key=api_key,
        base_url=args.base_url,
        auth_mode=args.auth_mode,
        model=args.model,
        max_chars_per_chunk=args.max_chars_per_chunk,
        max_completion_tokens=args.max_completion_tokens,
        temperature=args.temperature,
        top_p=args.top_p,
        timeout_seconds=args.timeout_seconds,
        max_retries=args.max_retries,
    )

    if args.validate and names:
        names = validate_historical_figures(
            names=names,
            api_key=api_key,
            base_url=args.base_url,
            auth_mode=args.auth_mode,
            model=args.model,
            max_completion_tokens=args.max_completion_tokens,
            temperature=min(args.temperature, 0.2),
            top_p=args.top_p,
            timeout_seconds=args.timeout_seconds,
            max_retries=args.max_retries,
            batch_size=max(1, args.validate_batch_size),
        )

    output_path = (repo_root / args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(names, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已写入: {output_path}（共 {len(names)} 个姓名）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

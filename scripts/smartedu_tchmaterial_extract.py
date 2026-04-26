"""
从国家智慧教育平台（tchMaterial）抽取教材页面中的人物姓名。

用途：
- 从“人教版”等教材资源的预览页图片中抽取人物姓名（历史人物、现代人物等）。
- 输出汇总名单与按教材分组名单，供后续生成 StoryMap/知识图谱使用。

说明：
- 脚本会访问远端 JSON 分片与教材图片链接，并调用多模态模型做 OCR+实体抽取。
- 请在仓库根目录配置 MIMO_API_KEY（或使用环境变量）。
"""

import argparse
import json
import os
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import extract_historical_figures as ehf


TCH_MATERIAL_TAG_URL = "https://bdcs-file-2.ykt.cbern.com.cn/zxx_secondary/ndrs/tags/tch_material_tag.json"
TCH_MATERIAL_VERSION_URL = "https://bdcs-file-2.ykt.cbern.com.cn/zxx_secondary/ndrs/resources/tch_material/version/data_version.json"


def _http_get_json(url: str, timeout_seconds: float) -> object:
    req = urllib.request.Request(
        url=url,
        method="GET",
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8"))


def _iter_strings(obj: object) -> Iterable[str]:
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _iter_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_strings(v)
    elif isinstance(obj, str):
        yield obj


def _dedupe(items: Iterable[str]) -> List[str]:
    return ehf._dedupe_preserve_order(items)


def _load_env() -> None:
    ehf._load_env_from_dotenv_if_needed(_REPO_ROOT / ".env")


@dataclass(frozen=True)
class Textbook:
    id: str
    title: str
    label_text: str
    tag_list: Sequence[dict]
    preview_images: Sequence[str]


def _find_in_tag_list(tag_list: Sequence[dict], dimension_id: str) -> List[str]:
    out: List[str] = []
    for t in tag_list:
        if t.get("tag_dimension_id") == dimension_id:
            name = t.get("tag_name")
            if isinstance(name, str) and name.strip():
                out.append(name.strip())
    return out


def _load_all_textbooks(timeout_seconds: float) -> List[dict]:
    ver = _http_get_json(TCH_MATERIAL_VERSION_URL, timeout_seconds=timeout_seconds)
    urls = str(ver.get("urls") or "").split(",")
    items: List[dict] = []
    for u in [x.strip() for x in urls if x.strip()]:
        part = _http_get_json(u, timeout_seconds=timeout_seconds)
        if isinstance(part, list):
            items.extend([x for x in part if isinstance(x, dict)])
    return items


def _to_textbook(item: dict) -> Optional[Textbook]:
    book_id = item.get("id")
    if not isinstance(book_id, str) or not book_id.strip():
        return None
    title = (item.get("global_title") or {}).get("zh-CN") or item.get("title") or ""
    if not isinstance(title, str):
        title = str(title)
    label_text = ""
    gl = (item.get("global_label") or {}).get("zh-CN")
    if isinstance(gl, list):
        label_text = " ".join([x for x in gl if isinstance(x, str)])
    elif isinstance(gl, str):
        label_text = gl
    if not label_text:
        lbl = item.get("label")
        if isinstance(lbl, list):
            label_text = " ".join([x for x in lbl if isinstance(x, str)])
        elif isinstance(lbl, str):
            label_text = lbl

    tag_list = item.get("tag_list") or []
    if not isinstance(tag_list, list):
        tag_list = []

    cp = item.get("custom_properties") or {}
    preview = cp.get("preview") if isinstance(cp, dict) else None
    preview_images: List[str] = []
    if isinstance(preview, dict):
        pairs: List[Tuple[int, str]] = []
        for k, v in preview.items():
            if not isinstance(v, str):
                continue
            n = 0
            if isinstance(k, str) and k.lower().startswith("slide"):
                try:
                    n = int(k[5:])
                except Exception:
                    n = 0
            pairs.append((n, v))
        preview_images = [u for _, u in sorted(pairs, key=lambda x: (x[0], x[1]))]

    return Textbook(
        id=book_id.strip(),
        title=title.strip(),
        label_text=label_text.strip(),
        tag_list=tag_list,
        preview_images=preview_images,
    )


def _filter_textbooks(
    items: Sequence[dict],
    *,
    publisher_keyword: str,
    subject_keyword: str,
    stage_keyword: Optional[str],
) -> List[Textbook]:
    out: List[Textbook] = []
    for item in items:
        tb = _to_textbook(item)
        if tb is None:
            continue
        if publisher_keyword and publisher_keyword not in tb.label_text:
            continue
        if subject_keyword and (subject_keyword not in tb.title and subject_keyword not in tb.label_text):
            tag_names = " ".join(_find_in_tag_list(tb.tag_list, "zxxxk") + _find_in_tag_list(tb.tag_list, "zxxnj"))
            if subject_keyword not in tag_names:
                continue
        if stage_keyword:
            stage_names = " ".join(_find_in_tag_list(tb.tag_list, "zxxxd"))
            if stage_keyword not in stage_names and stage_keyword not in tb.title and stage_keyword not in tb.label_text:
                continue
        if not tb.preview_images:
            continue
        out.append(tb)
    return out


def _extract_names_from_image(
    *,
    image_url: str,
    extract_mode: str,
    api_key: str,
    base_url: str,
    auth_mode: str,
    model: str,
    max_completion_tokens: int,
    temperature: float,
    top_p: float,
    timeout_seconds: float,
    max_retries: int,
) -> List[str]:
    system = "你是一个严谨的信息抽取器。你只输出严格的JSON，不输出任何额外文字。"
    mode = extract_mode.strip().lower()
    if mode == "historical":
        user_text = (
            "请阅读图片中的教材页面内容，提取页面内出现的所有“历史人物姓名”。"
            "要求：只输出一个JSON数组（字符串数组），姓名去重，保持首次出现顺序。"
            "如果没有历史人物姓名，输出空数组[]。"
        )
    elif mode == "person":
        user_text = (
            "请阅读图片中的教材页面内容，提取页面内出现的所有“人物姓名”（包括历史人物、现代著名人物等）。"
            "要求：只输出一个JSON数组（字符串数组），姓名去重，保持首次出现顺序。"
            "如果没有人物姓名，输出空数组[]。"
        )
    else:
        raise ValueError("extract_mode 必须是 historical 或 person")
    content = ehf._mimo_chat_completions(
        api_key=api_key,
        base_url=base_url,
        auth_mode=auth_mode,
        model=model,
        messages=[
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            },
        ],
        max_completion_tokens=max_completion_tokens,
        temperature=temperature,
        top_p=top_p,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
    )
    return ehf._extract_json_array(content)


def _load_cache(cache_path: Path) -> Dict[str, List[str]]:
    if not cache_path.exists():
        return {}
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            out: Dict[str, List[str]] = {}
            for k, v in data.items():
                if isinstance(k, str) and isinstance(v, list):
                    out[k] = [str(x).strip() for x in v if str(x).strip()]
            return out
    except Exception:
        return {}
    return {}


def _save_cache(cache_path: Path, cache: Dict[str, List[str]]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="从国家智慧教育平台的电子教材（tchMaterial）抽取历史人物姓名。")
    parser.add_argument("--publisher", default="人民教育出版社", help="出版社关键字（匹配教材 label），默认: 人民教育出版社")
    parser.add_argument("--subject", default="历史", help="学科关键字（留空表示不过滤），默认: 历史")
    parser.add_argument("--stage", default="初中", help="学段关键字（可为空字符串表示不过滤），默认: 初中")
    parser.add_argument("--output", default="pep_history_figures.json", help="输出文件，默认: pep_history_figures.json")
    parser.add_argument("--by_book_output", default="pep_history_figures_by_book.json", help="按教材分组输出，默认: pep_history_figures_by_book.json")
    parser.add_argument("--cache", default=".cache/smartedu_tchmaterial_cache.json", help="缓存文件（避免重复调用），默认: .cache/smartedu_tchmaterial_cache.json")
    parser.add_argument(
        "--extract_mode",
        default="historical",
        choices=["historical", "person"],
        help="抽取模式：historical=历史人物；person=所有人物（含现代著名人物），默认: historical",
    )

    parser.add_argument("--base_url", default="https://api.xiaomimimo.com/v1", help="MiMo API Base URL")
    parser.add_argument("--auth_mode", default="api-key", choices=["api-key", "bearer"], help="MiMo 认证方式")
    parser.add_argument("--model", default="mimo-v2-omni", help="抽取用模型（支持图片），默认: mimo-v2-omni")
    parser.add_argument("--max_completion_tokens", type=int, default=1024, help="模型输出token上限")
    parser.add_argument("--temperature", type=float, default=0.2, help="温度")
    parser.add_argument("--top_p", type=float, default=0.95, help="top_p")
    parser.add_argument("--timeout_seconds", type=float, default=120.0, help="单次请求超时秒数")
    parser.add_argument("--max_retries", type=int, default=3, help="MiMo 请求重试次数")

    parser.add_argument("--max_books", type=int, default=0, help="最多处理多少本书（0表示不限制）")
    parser.add_argument("--max_pages_per_book", type=int, default=0, help="每本书最多处理多少页（0表示不限制）")
    parser.add_argument("--validate", action="store_true", help="对最终汇总名单逐条复核为历史人物姓名（额外调用）")
    parser.add_argument("--validate_batch_size", type=int, default=40, help="复核批大小")

    args = parser.parse_args(list(argv) if argv is not None else None)

    _load_env()
    api_key = os.environ.get("MIMO_API_KEY", "").strip()
    if not api_key:
        print("未检测到 MIMO_API_KEY。请在环境变量或 .env 中配置。", file=sys.stderr)
        return 2

    stage_keyword = args.stage.strip() if args.stage is not None else ""
    stage_keyword = stage_keyword or None
    subject_keyword = str(args.subject).strip()
    if subject_keyword == "":
        subject_keyword = ""

    items = _load_all_textbooks(timeout_seconds=float(args.timeout_seconds))
    books = _filter_textbooks(
        items,
        publisher_keyword=str(args.publisher).strip(),
        subject_keyword=subject_keyword,
        stage_keyword=stage_keyword,
    )
    if not books:
        print("未找到匹配教材。", file=sys.stderr)
        return 1

    if args.max_books and args.max_books > 0:
        books = books[: int(args.max_books)]

    cache_path = (Path(__file__).resolve().parent / args.cache).resolve()
    cache = _load_cache(cache_path)

    by_book: Dict[str, List[str]] = {}
    all_names: List[str] = []

    for idx, book in enumerate(books, start=1):
        images = list(book.preview_images)
        if args.max_pages_per_book and args.max_pages_per_book > 0:
            images = images[: int(args.max_pages_per_book)]

        book_names: List[str] = []
        for image_url in images:
            cache_key = f"{args.extract_mode}:{image_url}"
            cached = cache.get(cache_key)
            if cached is not None:
                book_names.extend(cached)
                continue
            names = _extract_names_from_image(
                image_url=image_url,
                extract_mode=args.extract_mode,
                api_key=api_key,
                base_url=args.base_url,
                auth_mode=args.auth_mode,
                model=args.model,
                max_completion_tokens=int(args.max_completion_tokens),
                temperature=float(args.temperature),
                top_p=float(args.top_p),
                timeout_seconds=float(args.timeout_seconds),
                max_retries=int(args.max_retries),
            )
            cache[cache_key] = names
            _save_cache(cache_path, cache)
            book_names.extend(names)

        book_names = _dedupe(book_names)
        by_book[f"{book.title} ({book.id})"] = book_names
        all_names.extend(book_names)
        print(f"[{idx}/{len(books)}] {book.title}：{len(book_names)}")

    all_names = _dedupe(all_names)

    if args.validate and all_names and args.extract_mode == "historical":
        all_names = ehf.validate_historical_figures(
            names=all_names,
            api_key=api_key,
            base_url=args.base_url,
            auth_mode=args.auth_mode,
            model="mimo-v2-pro",
            max_completion_tokens=int(args.max_completion_tokens),
            temperature=min(float(args.temperature), 0.2),
            top_p=float(args.top_p),
            timeout_seconds=float(args.timeout_seconds),
            max_retries=int(args.max_retries),
            batch_size=max(1, int(args.validate_batch_size)),
        )

    repo_root = _REPO_ROOT
    out_all = (repo_root / args.output).resolve()
    out_by_book = (repo_root / args.by_book_output).resolve()
    out_all.parent.mkdir(parents=True, exist_ok=True)
    out_by_book.parent.mkdir(parents=True, exist_ok=True)
    out_all.write_text(json.dumps(all_names, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out_by_book.write_text(json.dumps(by_book, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已写入: {out_all}（共 {len(all_names)} 个姓名）")
    print(f"已写入: {out_by_book}（共 {len(by_book)} 本教材）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

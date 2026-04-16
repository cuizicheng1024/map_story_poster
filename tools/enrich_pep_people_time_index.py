import argparse
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if not k:
            continue
        os.environ.setdefault(k, v)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _extract_first_int(text: str) -> Optional[int]:
    if not text:
        return None
    m = re.search(r"(?<!\d)(-?\d{1,4})(?!\d)", str(text))
    if not m:
        return None
    y = int(m.group(1))
    if y == 0:
        return None
    if y < -3000 or y > 2100:
        return None
    return y


def _mimo_chat(api_key: str, model: str, messages: List[Dict[str, str]], base_url: str) -> str:
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {"api-key": api_key, "Content-Type": "application/json"}
    payload: Dict[str, Any] = {"model": model, "messages": messages, "temperature": 0.0, "stream": False}
    last_err: Optional[Exception] = None
    for attempt in range(3):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=240)
            resp.raise_for_status()
            data = resp.json()
            choices = data.get("choices", []) if isinstance(data, dict) else []
            if not choices:
                return ""
            msg = choices[0].get("message") if isinstance(choices[0], dict) else {}
            return str((msg or {}).get("content") or "")
        except Exception as e:
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    raise last_err or RuntimeError("mimo request failed")


def _ask_person_meta(api_key: str, model: str, base_url: str, name: str) -> Dict[str, Any]:
    sys = (
        "你是严谨的历史人物信息抽取器。"
        "只返回严格 JSON，不要输出多余文字。"
        "字段：dynasty, birthYear, deathYear。"
        "birthYear/deathYear 为整数（公元前用负数），未知则为 null。"
    )
    user = (
        f"人物：{name}\n"
        "请给出最常见、最可靠的：朝代（尽量精简如“晚唐”“五代后梁”“北宋”“明”）"
        "以及出生年、死亡年。无法确定则填 null。"
    )
    text = _mimo_chat(api_key, model, [{"role": "system", "content": sys}, {"role": "user", "content": user}], base_url)
    text = text.strip()
    text = re.sub(r"^```json\s*|\s*```$", "", text, flags=re.I | re.M).strip()
    try:
        obj = json.loads(text)
        if not isinstance(obj, dict):
            return {}
        dynasty = str(obj.get("dynasty") or "").strip()
        by = obj.get("birthYear")
        dy = obj.get("deathYear")
        birth = None if by is None else _extract_first_int(str(by))
        death = None if dy is None else _extract_first_int(str(dy))
        return {"dynasty": dynasty, "birthYear": birth, "deathYear": death}
    except Exception:
        return {}


def _merge_item(old: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(old)
    if new.get("dynasty"):
        out["dynasty"] = new["dynasty"]
    if out.get("birthYear") is None and new.get("birthYear") is not None:
        out["birthYear"] = new["birthYear"]
    if out.get("deathYear") is None and new.get("deathYear") is not None:
        out["deathYear"] = new["deathYear"]
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--only", type=str, default="")
    args = parser.parse_args()

    root = _repo_root()
    _load_env_file(root.parent.parent / ".env")
    data_dir = root / "data"
    merged_path = data_dir / "pep_people_merged.json"
    time_path = data_dir / "pep_people_time_index.json"
    out_path = data_dir / "pep_people_time_index_enriched.json"

    api_key = (os.environ.get("MIMO_API_KEY") or os.environ.get("LLM_API_KEY") or "").strip()
    base_url = (os.environ.get("MIMO_BASE_URL") or os.environ.get("LLM_BASE_URL") or "https://api.xiaomimimo.com/v1").strip()
    model = (os.environ.get("LLM_MODEL_ID") or os.environ.get("MODEL") or "mimo-v2-pro").strip()
    if not api_key:
        raise SystemExit("missing MIMO_API_KEY/LLM_API_KEY")

    names = _load_json(merged_path)
    names = [str(x).strip() for x in names if str(x).strip()]
    only = [x.strip() for x in args.only.split(",") if x.strip()]
    if only:
        names = [n for n in names if n in set(only)]

    time_obj = _load_json(time_path)
    items = time_obj.get("items", []) if isinstance(time_obj, dict) else []
    by_name: Dict[str, Dict[str, Any]] = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        n = str(it.get("name") or "").strip()
        if not n:
            continue
        by_name[n] = it

    targets: List[str] = []
    for n in names:
        it = by_name.get(n)
        if not it:
            targets.append(n)
            continue
        if not str(it.get("dynasty") or "").strip():
            targets.append(n)
            continue
        if it.get("birthYear") is None or it.get("deathYear") is None:
            targets.append(n)
            continue

    targets = targets[: max(0, int(args.limit))]
    updated = 0
    for n in targets:
        meta = _ask_person_meta(api_key=api_key, model=model, base_url=base_url, name=n)
        if not meta:
            continue
        if n in by_name:
            by_name[n] = _merge_item(by_name[n], meta)
        else:
            by_name[n] = {"name": n, "dynasty": meta.get("dynasty", ""), "birthYear": meta.get("birthYear"), "deathYear": meta.get("deathYear"), "source": "mimo_enrich"}
        updated += 1
        time.sleep(0.25)

    merged_items = [by_name[n] for n in sorted(by_name.keys())]
    out = {"items": merged_items, "meta": {"count": len(merged_items), "updated": updated, "generated_at": int(time.time())}}
    _write_json(out_path, out)
    print(str(out_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

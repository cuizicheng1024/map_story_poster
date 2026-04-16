#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv


def _load_env() -> None:
    repo = Path(__file__).resolve().parent.parent
    load_dotenv(repo / "data" / ".env")
    load_dotenv(repo / ".env")


def _endpoint(base_url: str) -> str:
    b = (base_url or "").strip().rstrip("/")
    if not b:
        b = "https://api.xiaomimimo.com/v1"
    if b.endswith("/v1"):
        return f"{b}/chat/completions"
    return f"{b}/v1/chat/completions"


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _quantile(xs: List[float], q: float) -> Optional[float]:
    if not xs:
        return None
    xs2 = sorted(xs)
    idx = int(round((len(xs2) - 1) * q))
    idx = max(0, min(len(xs2) - 1, idx))
    return xs2[idx]


def _one_call(
    *,
    url: str,
    api_key: str,
    model: str,
    timeout_s: int,
) -> Dict[str, Any]:
    t0 = time.perf_counter()
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "只回复 ok"}],
        "temperature": 0,
        "stream": False,
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=(5, timeout_s))
        dt = time.perf_counter() - t0
        try:
            data = resp.json()
        except Exception:
            data = None
        ok = resp.status_code < 400
        return {
            "ok": ok,
            "status": resp.status_code,
            "dt": dt,
            "error_type": "" if ok else "http_error",
            "error": "" if ok else (resp.text[:200] if isinstance(resp.text, str) else ""),
            "has_choices": bool(isinstance(data, dict) and isinstance(data.get("choices"), list) and data.get("choices")),
        }
    except requests.exceptions.Timeout as exc:
        dt = time.perf_counter() - t0
        return {"ok": False, "status": None, "dt": dt, "error_type": "timeout", "error": str(exc), "has_choices": False}
    except requests.exceptions.RequestException as exc:
        dt = time.perf_counter() - t0
        return {
            "ok": False,
            "status": None,
            "dt": dt,
            "error_type": "request_exception",
            "error": str(exc),
            "has_choices": False,
        }


def main() -> int:
    p = argparse.ArgumentParser(description="Probe MiMo API concurrency by sending lightweight requests.")
    p.add_argument("--concurrency", type=int, default=20)
    p.add_argument("--total", type=int, default=100)
    p.add_argument("--timeout", type=int, default=20)
    p.add_argument("--base-url", type=str, default="")
    p.add_argument("--model", type=str, default="")
    args = p.parse_args()

    _load_env()
    api_key = (os.getenv("MIMO_API_KEY") or os.getenv("API_KEY") or os.getenv("LLM_API_KEY") or "").strip()
    if not api_key:
        raise SystemExit("missing api key: set MIMO_API_KEY or API_KEY")

    base_url = args.base_url.strip() or (os.getenv("MIMO_BASE_URL") or os.getenv("BASE_URL") or os.getenv("LLM_BASE_URL") or "https://api.xiaomimimo.com/v1")
    model = args.model.strip() or (os.getenv("MODEL") or os.getenv("LLM_MODEL_ID") or "mimo-v2-pro")
    url = _endpoint(base_url)

    conc = max(1, int(args.concurrency))
    total = max(1, int(args.total))

    t_all0 = time.perf_counter()
    rows: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=conc) as ex:
        futs = [
            ex.submit(_one_call, url=url, api_key=api_key, model=model, timeout_s=int(args.timeout))
            for _ in range(total)
        ]
        for fut in as_completed(futs):
            rows.append(fut.result())

    dt_all = time.perf_counter() - t_all0
    ok = [r for r in rows if r.get("ok")]
    fail = [r for r in rows if not r.get("ok")]
    lat = [float(r.get("dt") or 0.0) for r in rows]
    ok_lat = [float(r.get("dt") or 0.0) for r in ok]

    by_status: Dict[str, int] = {}
    by_err: Dict[str, int] = {}
    for r in rows:
        st = r.get("status")
        k = str(st)
        by_status[k] = by_status.get(k, 0) + 1
        et = r.get("error_type") or ""
        if et:
            by_err[et] = by_err.get(et, 0) + 1

    report = {
        "concurrency": conc,
        "total": total,
        "elapsed_s": dt_all,
        "ok": len(ok),
        "fail": len(fail),
        "ok_rate": len(ok) / total,
        "lat_all_p50": _quantile(lat, 0.50),
        "lat_all_p90": _quantile(lat, 0.90),
        "lat_all_p95": _quantile(lat, 0.95),
        "lat_ok_p50": _quantile(ok_lat, 0.50),
        "lat_ok_p90": _quantile(ok_lat, 0.90),
        "lat_ok_p95": _quantile(ok_lat, 0.95),
        "status_counts": dict(sorted(by_status.items(), key=lambda kv: (-kv[1], kv[0]))),
        "error_type_counts": dict(sorted(by_err.items(), key=lambda kv: (-kv[1], kv[0]))),
    }

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


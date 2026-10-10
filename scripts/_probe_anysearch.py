"""探针：AnySearch 原始返回结构（v2.3.81e 后，为"查得更广"探路）

目的：弄清 AnySearch 到底能返回什么——
  1) 每条 result 有哪些字段（是否有 content 正文，还是只有 snippet 摘要）
  2) count 能不能调大（一次拿更多条）
  3) 有没有 freshness / time_range / locale 之类的可选参数

不写任何业务数据，只打印结构。用法（/root/bot 下）：
    python3 scripts/_probe_anysearch.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_ROOT / "config" / ".env")

import requests  # noqa: E402

URL = "https://api.anysearch.com/v1/search"
KEY = os.getenv("ANYSEARCH_KEY", "")


def call(payload: dict, timeout: float = 15.0) -> dict | None:
    if not KEY:
        print("!! 未配置 ANYSEARCH_KEY")
        return None
    try:
        r = requests.post(
            URL,
            headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=timeout,
        )
        print(f"--- POST {json.dumps(payload, ensure_ascii=False)} → HTTP {r.status_code} ({len(r.content)}B)")
        if r.status_code != 200:
            print("   body:", r.text[:300])
            return None
        return r.json()
    except Exception as e:
        print("   !! 异常:", e)
        return None


def dump(data: dict, label: str) -> None:
    print(f"=== {label} ===")
    print("  顶层 keys:", list(data.keys()))
    print("  code:", data.get("code"), "| message:", data.get("message"))
    d = data.get("data") or {}
    print("  data keys:", list(d.keys()) if isinstance(d, dict) else type(d).__name__)
    results = (d.get("results") if isinstance(d, dict) else None) or []
    print("  results 条数:", len(results))
    for i, it in enumerate(results[:3]):
        if not isinstance(it, dict):
            print(f"  [{i}] 非 dict: {type(it).__name__}")
            continue
        print(f"  [{i}] keys={list(it.keys())}")
        for k, v in it.items():
            if isinstance(v, str):
                print(f"       {k}: {len(v)}字 | {v[:80].replace(chr(10),' ')}")
            else:
                print(f"       {k}: {type(v).__name__} = {str(v)[:80]}")


def main() -> None:
    q = "今日新闻 国内国际要闻"

    # 1) 默认 count=5，看单条字段
    d = call({"query": q, "count": 5})
    if d:
        dump(d, "count=5")

    # 2) count=20，看能否一次拿更多
    d = call({"query": q, "count": 20})
    if d:
        dump(d, "count=20")

    # 3) 试几个可能的分页/时间参数，看接口是否接受（被忽略也无妨）
    for extra in ({"page": 2, "count": 5}, {"count": 30}):
        d = call({"query": q, **extra})
        if d:
            dump(d, f"extra={extra}")


if __name__ == "__main__":
    main()

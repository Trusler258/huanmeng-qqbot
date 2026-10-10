"""探针 3：实测 AnySearch 官方文档里的过滤参数真实行为

官方文档（anysearch.com/docs/api-endpoints/v1-search）列出的参数：
  max_results      结果数（文档写 1–10，另一处写 1–100，需实测）
  constraint.freshness   day/week/month/year   ← 时效窗口
  content_types     ["web","news"]
  zone              cn / intl
  language          zh-CN / en
  format            json / markdown
  tag / domains / providers / params

逐个实测，确认哪些真的影响返回，为"查得更广/更准"选参数。
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


def call(payload: dict) -> tuple[int, dict | None, str]:
    r = requests.post(URL,
                      headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
                      json=payload, timeout=20.0)
    try:
        return r.status_code, r.json(), r.text[:200]
    except Exception:
        return r.status_code, None, r.text[:200]


def brief(payload: dict, tag: str) -> None:
    sc, j, raw = call(payload)
    if not j:
        print(f"[{tag}] HTTP {sc} | 非JSON | {raw}")
        return
    results = ((j.get("data") or {}).get("results") or [])
    n = len(results)
    # 单条 content 长度中位数
    lens = [len(it.get("content") or "") for it in results]
    md = sorted(lens)[len(lens) // 2] if lens else 0
    print(f"[{tag}] HTTP {sc} | 条数={n} | content中位{md}字 | 首条: {((results[0] if results else {}).get('title') or '')[:40]}")


def main() -> None:
    q = "今日新闻 国内国际要闻"
    print("== max_results 范围实测 ==")
    for mr in (1, 3, 10, 20, 50, 100):
        brief({"query": q, "max_results": mr}, f"max_results={mr}")

    print("\n== 时效窗口 constraint.freshness ==")
    for fr in ("day", "week", "month", "year"):
        brief({"query": q, "max_results": 10, "constraint": {"freshness": fr}}, f"freshness={fr}")

    print("\n== content_types / zone / language ==")
    brief({"query": q, "max_results": 10, "content_types": ["news"]}, "content_types=[news]")
    brief({"query": q, "max_results": 10, "zone": "cn"}, "zone=cn")
    brief({"query": q, "max_results": 10, "language": "zh-CN"}, "language=zh-CN")
    brief({"query": q, "max_results": 10, "content_types": ["news"], "constraint": {"freshness": "day"}},
          "news+freshness=day")

    print("\n== format=markdown ==")
    brief({"query": q, "max_results": 10, "format": "markdown"}, "format=markdown")

    print("\n== 对照：当前代码的参数（count=5，非法名） ==")
    brief({"query": q, "count": 5}, "count=5（现状）")


if __name__ == "__main__":
    main()

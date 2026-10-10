"""探针 2：AnySearch 的 metadata + 试其它参数名（count 之外）

AnySearch 固定返回 10 条，count 5/20/30 都一样。这里确认：
  - data.metadata 里有什么（总数？分页？）
  - 是否接受 limit / size / top_k / num / page_size / offset 等参数名
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
                      json=payload, timeout=15.0)
    try:
        j = r.json()
    except Exception:
        j = None
    return r.status_code, j, r.text[:200]


def main() -> None:
    q = "今日新闻"

    # metadata 全貌
    sc, j, raw = call({"query": q, "count": 10})
    print("metadata =", json.dumps((j or {}).get("data", {}).get("metadata"), ensure_ascii=False))
    print("request_id =", (j or {}).get("request_id"))

    # 试各种"多返回"参数名：看哪个能让条数 > 10，或报 400（= 参数被识别）
    for pname in ("limit", "size", "top_k", "num", "page_size", "max_results", "offset", "start"):
        sc, j, raw = call({"query": q, pname: 30})
        n = len(((j or {}).get("data") or {}).get("results") or []) if j else -1
        print(f"  {pname}=30 → HTTP {sc} | results={n}" + ("" if sc == 200 else f" | {raw}"))


if __name__ == "__main__":
    main()

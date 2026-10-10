"""探针：过滤参数 + 结果体量（v2.3.81f）

验证：
  1) search_anysearch 把 freshness/content_type/zone/language/max_results 真的发出去了
  2) agent_search 带过滤参数能跑通，且结果体量比改前更大（max_total_results 5→10、
     单条预览 150→300、截断 1600→4000）
  3) perform_search 带过滤参数跑通

用法（/root/bot 下）：python3 scripts/_probe_search_filters.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_ROOT / "config" / ".env")


def main() -> None:
    from modules.web_search import search_anysearch, agent_search

    print("== 1) search_anysearch 带过滤参数 ==")
    r = search_anysearch("今日新闻", limit=10, freshness="day",
                         content_type="news", zone="cn", language="zh-CN")
    print(f"   返回 {len(r)} 条")
    for it in r[:3]:
        print(f"   - {it['title'][:36]} | {len(it['snippet'])}字 | {it['link'][:50]}")

    print("\n== 2) agent_search 带过滤参数（结果体量） ==")
    for tag, kw in (("无过滤", {}), ("news+day", {"freshness": "day", "content_type": "news"})):
        txt = agent_search("今日新闻 国内国际要闻", limit=8, **kw)
        print(f"   [{tag}] {len(txt)} 字 | 行数 {txt.count(chr(10))}")
        print(f"        预览: {txt[:160].replace(chr(10),' | ')}")

    print("\n== 3) perform_search 带过滤参数 ==")
    from modules.search import perform_search
    out = asyncio.run(perform_search("今日要闻", user_id=0, chat_id=0, limit=8,
                                     freshness="day", content_type="news"))
    print(f"   返回 {len(out or '')} 字")


if __name__ == "__main__":
    main()

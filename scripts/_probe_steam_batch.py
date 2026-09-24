# -*- coding: utf-8 -*-
"""测 appdetails 批量查价的最优批量大小（为什么 25 个一批全失败）"""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "/root/bot")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from services import steam_api as S

async def main():
    cache = json.loads(Path("data/steam_cache/76561199427581023.json").read_text(encoding="utf-8"))
    ids = [str(a) for a in (cache.get("appids") or [])]
    prices = S._price_cache()
    miss = [k for k in ids if not (prices.get(k) or {}).get("t")]
    print("总 appid =", len(ids), "| 未查过 =", len(miss))
    print()

    for size in (5, 10, 25):
        chunk = miss[:size]
        if not chunk:
            continue
        t0 = time.perf_counter()
        try:
            d = await S._get_json("https://store.steampowered.com/api/appdetails",
                                  {"appids": ",".join(chunk), "filters": "price_overview",
                                   "cc": "cn", "l": "schinese"}, timeout=40)
            n = len(d) if isinstance(d, dict) else 0
            withp = sum(1 for k in chunk
                        if (((d.get(k) or {}).get("data") or {}).get("price_overview")))
            print("批量 %-3d -> OK  %.1fs  返回 %d 条  其中有价格 %d"
                  % (size, time.perf_counter() - t0, n, withp))
        except Exception as e:
            print("批量 %-3d -> 失败 %.1fs  %s: %r"
                  % (size, time.perf_counter() - t0, type(e).__name__, str(e)[:70]))
        await asyncio.sleep(1)

    # 并发小批（10 个一批，同时发 3 批）
    print()
    print("=== 并发 3 批 x 10 个 ===")
    chunks = [miss[i:i + 10] for i in range(0, min(30, len(miss)), 10)]
    t0 = time.perf_counter()


    async def one(ch):
        try:
            d = await S._get_json("https://store.steampowered.com/api/appdetails",
                                  {"appids": ",".join(ch), "filters": "price_overview",
                                   "cc": "cn", "l": "schinese"}, timeout=40)
            return len(d) if isinstance(d, dict) else 0
        except Exception as e:
            return "%s" % type(e).__name__


    res = await asyncio.gather(*(one(c) for c in chunks))
    print("耗时 %.1fs  各批返回: %s" % (time.perf_counter() - t0, res))


if __name__ == '__main__':
    asyncio.run(main())

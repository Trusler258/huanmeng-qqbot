# -*- coding: utf-8 -*-
"""Steam 卡片速度对比（冷态 / SWR 热态）

用法（服务器上）：python3 scripts/_probe_steam_speed.py
"""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "/root/bot")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from services import steam_card as SC   # noqa: E402

SID = "76561199427581023"
CF = Path("/root/bot/data/steam_cache/%s.json" % SID)


async def main():
    # 把快照改老，制造冷态（不然 SWR 会直接命中，测不出真取数耗时）
    if CF.exists():
        d = json.loads(CF.read_text(encoding="utf-8"))
        d["_cached_at"] = float(d.get("_cached_at") or 0) - 600
        CF.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        print("已把快照改成 10 分钟前 -> 下一次是冷态")
        print()

    t = time.perf_counter()
    await SC.render_card(SID, "/tmp/_sp_cold.png")
    print("  冷态（真取数）        %5.2f s" % (time.perf_counter() - t))

    for i in (1, 2):
        t = time.perf_counter()
        await SC.render_card(SID, "/tmp/_sp_warm%d.png" % i)
        print("  热态第 %d 次（SWR 直出） %5.2f s" % (i, time.perf_counter() - t))

    print()
    print("（等待后台刷新落地…）")
    await asyncio.sleep(8)
    d = json.loads(CF.read_text(encoding="utf-8"))
    age = time.time() - float(d.get("_cached_at") or 0)
    print("  后台刷新已落地 =", age < 60, "（快照年龄 %.0f 秒）" % age)
    print("  缓存价格条数 =", len(json.loads(Path(
        "/root/bot/data/steam_assets/prices.json").read_text(encoding="utf-8"))))
    print("  中文名缓存条数 =", len(json.loads(Path(
        "/root/bot/data/steam_assets/games/names_zh.json").read_text(encoding="utf-8"))))


if __name__ == "__main__":
    asyncio.run(main())

# -*- coding: utf-8 -*-
"""Steam 卡片耗时剖析：逐步计时，找出真正的慢点

用法（服务器上）：python3 scripts/_probe_steam_timing.py [steamid]
"""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "/root/bot")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from services import steam_api as S          # noqa: E402
from services import steam_card as SC        # noqa: E402

SID = sys.argv[1] if len(sys.argv) > 1 else "76561199427581023"
T0 = time.perf_counter()


def mark(label, t0):
    print("  %-34s %7.0f ms" % (label, (time.perf_counter() - t0) * 1000), flush=True)


async def main():
    print("=== 缓存状态 ===")
    prices = json.loads((Path("/root/bot/data/steam_assets/prices.json")).read_text(encoding="utf-8")) \
        if Path("/root/bot/data/steam_assets/prices.json").exists() else {}
    names_f = Path("/root/bot/data/steam_assets/games/names_zh.json")
    names = json.loads(names_f.read_text(encoding="utf-8")) if names_f.exists() else {}
    cf = Path("/root/bot/data/steam_cache/%s.json" % SID)
    if cf.exists():
        age = (time.time() - cf.stat().st_mtime) / 60.0
        print("  快照: 存在, %.1f 分钟前" % age)
        _c = json.loads(cf.read_text(encoding="utf-8"))
        print("  快照里的 appids =", len(_c.get("appids") or []),
              "| value.priced =", (_c.get("value") or {}).get("priced"))
    else:
        print("  快照: 无")
    print("  价格缓存 =", len(prices), "条（有价 %d）" % sum(1 for v in prices.values() if v.get("p")))
    print("  中文名缓存 =", len(names), "条")
    print()

    print("=== 逐步计时 ===")
    t = time.perf_counter()
    ok = await S.reachable()
    mark("reachable() = %s" % ok, t)

    t = time.perf_counter()
    prof = await S.player_summary(SID)
    mark("player_summary", t)
    if not prof:
        print("  资料拿不到，后续跳过")
        return

    t = time.perf_counter()
    owned, recent_raw, level, badges, friends = await asyncio.gather(
        S.owned_games(SID), S.recent_games(SID, count=8), S.steam_level(SID),
        S.profile_badges(SID), S.friend_count(SID))
    mark("owned/recent/level/badges/friends 并发", t)

    t = time.perf_counter()
    ach = await S.achievements(SID, SC.ACH_APPID)
    mark("achievements", t)

    t = time.perf_counter()
    ach_recent = await S.recent_achievements(SID, SC.ACH_APPID, 4)
    mark("recent_achievements（内部又查 achievements+schema）", t)

    games = owned.get("games") or []
    appids = [g.get("appid") for g in games if g.get("appid")]
    print("  游戏数 =", len(appids))

    t = time.perf_counter()
    value = await S.library_value(appids)
    mark("library_value（%d 款，%d 待查）" % (len(appids),
         sum(1 for a in appids if str(a) not in prices)), t)

    top = sorted(games, key=lambda g: g.get("playtime_forever", 0), reverse=True)[:10]
    recent = sorted(recent_raw, key=lambda g: g.get("playtime_2weeks", 0), reverse=True)[:8]

    t = time.perf_counter()
    await asyncio.gather(*[SC._enrich(g, "playtime_forever") for g in top])
    mark("_enrich top 10（中文名+横幅）", t)

    t = time.perf_counter()
    await asyncio.gather(*[SC._enrich(g, "playtime_2weeks") for g in recent])
    mark("_enrich recent 8（中文名+横幅）", t)

    t = time.perf_counter()
    await asyncio.gather(*[S.ach_icon(SC.ACH_APPID, a.get("apiname", ""), a.get("icon_url", ""))
                           for a in (ach_recent or [])])
    mark("成就图标 x %d" % len(ach_recent or []), t)

    t = time.perf_counter()
    await S.avatar_file(SID, prof.get("avatar", ""))
    mark("avatar_file", t)
    print()


async def full():
    print("=== 完整 build_payload（含探测）===")
    t = time.perf_counter()
    p = await SC.build_payload(SID)
    mark("build_payload 总耗时", t)
    if p:
        stale = "（缓存）" if p.get("stale") else "（实时）"
        print("  payload %s  字段=%d" % (stale, len(p)))
    print()
    if p:
        t = time.perf_counter()
        from services import steam_card_pillow as CP
        img = CP.render_steam_card(p)
        mark("Pillow 渲染", t)
        print("  尺寸 =", img.size)


if __name__ == "__main__":
    asyncio.run(main())
    asyncio.run(full())
    print("总耗时 %.1f s" % (time.perf_counter() - T0))

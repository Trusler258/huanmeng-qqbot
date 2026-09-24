# -*- coding: utf-8 -*-
"""验证 CF Worker 代理 Steam 的实际收益

对比对象：直连时 library_value(92 款) 实测 75013 ms（75 秒，撞满预算）

用法（服务器上）：
    STEAM_PROXY_TOKEN=xxx python3 scripts/_probe_steam_proxy.py
"""
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, "/root/bot")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SID = "76561199427581023"
BASE = os.environ.get("STEAM_PROXY", "https://steamapi.truslerweb.dpdns.org")
TOKEN = os.environ.get("STEAM_PROXY_TOKEN", "")
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def post(ops, timeout=180):
    body = json.dumps({"ops": ops}).encode("utf-8")
    # ⚠️ 必须带正常 UA：CF 边缘的 Bot Fight Mode 会拦 Python 默认的
    #    `Python-urllib/3.x`（实测 403），但 `python-httpx/...` 与浏览器 UA 都能过。
    #    bot 侧用 httpx，天然没问题；这里是 urllib 才需要显式设置。
    req = urllib.request.Request(
        BASE + "/", data=body, method="POST",
        headers={"Content-Type": "application/json", "X-Proxy-Token": TOKEN,
                 "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) "
                               "Chrome/120.0.0.0 Safari/537.36"})
    t = time.perf_counter()
    raw = OPENER.open(req, timeout=timeout).read()
    return json.loads(raw.decode("utf-8")), (time.perf_counter() - t) * 1000.0


def main():
    if not TOKEN:
        print("缺少 STEAM_PROXY_TOKEN 环境变量")
        return 1

    print("=== 目标 ===", BASE)
    cf = Path("/root/bot/data/steam_cache/%s.json" % SID)
    appids = []
    if cf.exists():
        appids = [str(a) for a in (json.loads(cf.read_text(encoding="utf-8")).get("appids") or [])]
    print("  快照里的 appid 数 =", len(appids))
    print()

    print("=== 单 op ===")
    d, ms = post([{"k": "api", "p": "ISteamUser/GetPlayerSummaries/v2/", "q": {"steamids": SID}}])
    ok = d["r"][0].get("ok")
    name = (((d["r"][0].get("data") or {}).get("response") or {}).get("players") or [{}])[0].get("personaname")
    print("  summary   %6.0f ms  ok=%s name=%s" % (ms, ok, name))
    print()

    if appids:
        print("=== %d 款游戏价格（一次请求）===" % len(appids))
        d, ms = post([{"k": "store", "p": "api/appdetails",
                       "q": {"appids": ",".join(appids), "filters": "price_overview"}}])
        rr = d["r"][0]
        data = rr.get("data") or {}
        priced = 0
        o = f = 0
        for v in data.values():
            po = ((v or {}).get("data") or {}).get("price_overview") or {}
            if po:
                priced += 1
                o += int(po.get("initial") or 0)
                f += int(po.get("final") or 0)
        print("  第 1 次   %6.0f ms  ok=%s batches=%s 有价 %d/%d"
              % (ms, rr.get("ok"), rr.get("batches"), priced, len(appids)))
        print("            原价 ¥%.2f  现价 ¥%.2f" % ((o or 0) / 100.0, (f or 0) / 100.0))
        d2, ms2 = post([{"k": "store", "p": "api/appdetails",
                         "q": {"appids": ",".join(appids), "filters": "price_overview"}}])
        print("  第 2 次   %6.0f ms  （命中 CF 边缘缓存应显著更快）" % ms2)
        print()

    print("=== 全流程 8 个 op 合并成一次请求 ===")
    ops = [
        {"k": "api", "p": "ISteamUser/GetPlayerSummaries/v2/", "q": {"steamids": SID}},
        {"k": "api", "p": "IPlayerService/GetOwnedGames/v1/",
         "q": {"steamid": SID, "include_played_free_games": 1}},
        {"k": "api", "p": "IPlayerService/GetRecentlyPlayedGames/v1/", "q": {"steamid": SID}},
        {"k": "api", "p": "IPlayerService/GetSteamLevel/v1/", "q": {"steamid": SID}},
        {"k": "api", "p": "IPlayerService/GetBadges/v1/", "q": {"steamid": SID}},
        {"k": "api", "p": "ISteamUser/GetFriendList/v1/", "q": {"steamid": SID, "relationship": "friend"}},
        {"k": "api", "p": "ISteamUserStats/GetPlayerAchievements/v1/",
         "q": {"steamid": SID, "appid": 977950}},
        {"k": "store", "p": "api/appdetails",
         "q": {"appids": ",".join(appids or ["730"]), "filters": "price_overview"}},
    ]
    d, ms = post(ops)
    print("  总耗时 %6.0f ms  （Worker 内并发执行 8 个 op）" % ms)
    for r in d.get("r", []):
        if not r.get("ok"):
            print("     op%-2d FAIL  %s" % (r.get("i"), r.get("error")))
        else:
            dd = r.get("data")
            n = len(dd) if isinstance(dd, dict) else ("list %d" % len(dd) if isinstance(dd, list) else "?")
            print("     op%-2d ok    %d 个顶层键" % (r.get("i"), n))
    return 0


if __name__ == "__main__":
    sys.exit(main())

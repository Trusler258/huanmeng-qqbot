# -*- coding: utf-8 -*-
"""探测 Steam 扩展数据源（为把卡片内容塞满做准备）

要回答三个问题：
  1) 愿望单 / 好友 / 徽章 / XP 能不能拿到（决定 stats 条能放几格）
  2) 成就能不能拿到"解锁时间"（决定能否做"最近解锁"）
  3) 拿不到时错误是什么（区分"没公开"和"接口不对"）
"""
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ENV = Path("/root/bot/config/.env")
KEY = ""
if ENV.exists():
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if line.startswith("STEAM_KEY="):
            KEY = line.split("=", 1)[1].strip()

SID = "76561199427581023"
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get(path, **kw):
    kw["key"] = KEY
    url = "https://api.steampowered.com/" + path + "?" + urllib.parse.urlencode(kw)
    try:
        raw = OP.open(url, timeout=30).read().decode("utf-8")
        return json.loads(raw)
    except Exception as e:
        return {"_err": "%s %s" % (type(e).__name__, e)}


print("KEY 已读 =", bool(KEY))
print()

r = get("IWishlistService/GetWishlist/v1/", steamid=SID)
items = (r.get("response") or {}).get("items") or []
print("[愿望单] keys=%s items=%d err=%s"
      % (list(r.keys())[:4], len(items), r.get("_err", "")))
if items:
    print("         样例 =", json.dumps(items[:3], ensure_ascii=False))

r = get("ISteamUser/GetFriendList/v1/", steamid=SID, relationship="friend")
fl = (r.get("friendslist") or {}).get("friends") or []
print("[好友]   数量=%d err=%s 样例=%s"
      % (len(fl), r.get("_err", ""), json.dumps(fl[:2], ensure_ascii=False)))

r = get("IPlayerService/GetBadges/v1/", steamid=SID)
b = r.get("response") or {}
print("[徽章]   keys=%s 徽章数=%d xp=%s level=%s err=%s"
      % (list(b.keys()), len(b.get("badges") or []), b.get("player_xp"),
         b.get("player_level"), r.get("_err", "")))

r = get("IPlayerService/GetSteamLevel/v1/", steamid=SID)
print("[等级]   =", (r.get("response") or {}).get("player_level"))
print()

r = get("ISteamUserStats/GetPlayerAchievements/v1/", steamid=SID, appid=977950)
ps = r.get("playerstats") or {}
ach = ps.get("achievements") or []
got = [x for x in ach if x.get("achieved")]
print("[ADOFAI 成就] %d / %d  含 unlocktime=%s"
      % (len(got), len(ach), ("unlocktime" in (got[0] if got else {}))))
if got:
    print("          样例 =", json.dumps(got[0], ensure_ascii=False))
    top = sorted(got, key=lambda x: x.get("unlocktime", 0), reverse=True)[:3]
    print("          最近解锁 =",
          [(x.get("apiname"), x.get("unlocktime")) for x in top])

r = get("ISteamUserStats/GetSchemaForGame/v2/", appid=977950)
st = ((r.get("game") or {}).get("availableGameStats") or {})
sach = st.get("achievements") or []
print("[schema] 成就定义数 = %d" % len(sach))
if sach:
    print("         样例 =", json.dumps(sach[:2], ensure_ascii=False)[:260])

# 最近玩过的游戏（决定"最近活跃"展示）
r = get("IPlayerService/GetRecentlyPlayedGames/v1/", steamid=SID, count=10)
rg = (r.get("response") or {}).get("games") or []
print()
print("[最近两周] %d 款 -> appid %s" % (len(rg), [g.get("appid") for g in rg]))

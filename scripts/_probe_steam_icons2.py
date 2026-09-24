# -*- coding: utf-8 -*-
"""探 Steam 官方图标：① GetBadges 里的徽章图标 ② CDN 上的其他官方素材"""
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

KEY = ""
for line in Path("/root/bot/config/.env").read_text(encoding="utf-8").splitlines():
    if line.startswith("STEAM_KEY="):
        KEY = line.split("=", 1)[1].strip()
SID = "76561199427581023"
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
UA = {"User-Agent": "Mozilla/5.0"}


def api(path, **kw):
    kw["key"] = KEY
    u = "https://api.steampowered.com/" + path + "?" + urllib.parse.urlencode(kw)
    try:
        return json.loads(OP.open(urllib.request.Request(u, headers=UA), timeout=30)
                          .read().decode("utf-8"))
    except Exception as e:
        return {"_err": "%s %s" % (type(e).__name__, e)}


print("=== ① GetBadges（Steam 等级徽章 = 官方图标来源）===")
b = (api("IPlayerService/GetBadges/v1/", steamid=SID).get("response") or {})
print("  顶层字段:", list(b.keys()))
print("  player_level =", b.get("player_level"), " player_xp =", b.get("player_xp"))
bl = b.get("badges") or []
print("  徽章数 =", len(bl))
for x in bl[:6]:
    print("   ", json.dumps(x, ensure_ascii=False)[:190])
lv = [x for x in bl if x.get("badgeid") == 0]
if lv:
    print("  ★ 等级徽章(badgeid=0):", json.dumps(lv[0], ensure_ascii=False)[:220])

print()
print("=== ② Steam 官方 CDN 素材（试更多路径）===")
CANDS = [
    "https://store.akamai.steamstatic.com/public/shared/images/header/logo_steam.svg",
    "https://store.akamai.steamstatic.com/public/shared/images/responsive/logo_valve_footer.png",
    "https://store.akamai.steamstatic.com/public/shared/images/responsive/logo_valve_footer_2x.png",
    "https://store.akamai.steamstatic.com/public/images/v6/logo_steam.png",
    "https://store.akamai.steamstatic.com/public/shared/images/header/globalheader_logo.png",
    "https://cdn.cloudflare.steamstatic.com/steam/apps/977950/logo.png",
    "https://cdn.cloudflare.steamstatic.com/steam/apps/977950/logo_2x.png",
    "https://cdn.cloudflare.steamstatic.com/steam/community/tf2/steam_logo.png",
    "https://avatars.fastly.steamstatic.com/e9af1b78526aad8c21115c48a3610d3bf3e141db_full.jpg",
]
for u in CANDS:
    try:
        with OP.open(urllib.request.Request(u, headers=UA), timeout=15) as r:
            raw = r.read(3000)
        kind = ("SVG" if b"<svg" in raw or raw.lstrip().startswith(b"<?xml")
                else "PNG" if raw.startswith(b"\x89PNG") else "JPG" if raw.startswith(b"\xff\xd8") else "?")
        print("  OK   %-6s %6d+B  %s" % (kind, len(raw), u.split("/public/")[-1] if "/public/" in u else u[-60:]))
    except Exception as e:
        print("  FAIL       %s  (%s)" % (u[-64:], str(e)[:36]))

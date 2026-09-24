# -*- coding: utf-8 -*-
"""探测：① 批量查价 ② Steam 官方图形资源

要回答：
  1) appdetails 能否一次查多个 appid（决定"仓库价值"要不要 92 次请求）
  2) price_overview 里 initial / final 的语义（原价 vs 现价）
  3) Steam 官方 logo 等图形资源能否抓到、什么格式
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

OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
STORE = "https://store.steampowered.com"


def get(url, timeout=25, binary=False):
    try:
        req = urllib.request.Request(url, headers=UA)
        raw = OP.open(req, timeout=timeout).read()
        return raw if binary else raw.decode("utf-8", "replace")
    except Exception as e:
        return ("_ERR %s %s" % (type(e).__name__, e)) if not binary else b""


print("=" * 62)
print("① 批量 appdetails（5 个 appid 一次）")
ids = "730,570,440,977950,1426210"
url = (STORE + "/api/appdetails?appids=" + ids
       + "&filters=price_overview&cc=cn&l=schinese")
r = get(url)
if isinstance(r, str) and not r.startswith("_ERR"):
    try:
        d = json.loads(r)
        print("  返回条目 =", len(d))
        for k, v in d.items():
            dd = (v or {}).get("data") or {}
            po = dd.get("price_overview") or {}
            print("  %-10s success=%s name=%s" % (k, v.get("success"),
                                                  (dd.get("name") or "")[:22]))
            if po:
                print("      原价 %s(%s) 现价 %s(%s) 折扣 %s%%"
                      % (po.get("initial_formatted"), po.get("initial"),
                         po.get("final_formatted"), po.get("final"),
                         po.get("discount_percent")))
            else:
                print("      (无 price_overview —— 免费游戏或该区未售)")
    except Exception as e:
        print("  解析失败:", e, "| 原文前 200:", r[:200])
else:
    print("  请求失败:", r)

print()
print("② 100 个 appid 大批量（测上限）")
big = ",".join(str(x) for x in range(730, 830))
r2 = get(STORE + "/api/appdetails?appids=" + big + "&filters=price_overview&cc=cn")
if isinstance(r2, str) and not r2.startswith("_ERR"):
    print("  请求长度 =", len(r2), "字符")
    try:
        print("  返回条目 =", len(json.loads(r2)))
    except Exception as e:
        print("  解析失败:", e)
else:
    print("  失败:", r2)

print()
print("③ Steam 官方图形资源")
assets = [
    ("Steam logo SVG", "https://store.akamai.steamstatic.com/public/shared/images/header/logo_steam.svg?t=962016"),
    ("Steam logo (footer)", "https://store.akamai.steamstatic.com/public/shared/images/header/logo_steam_footer.png"),
    ("Steam 图标(小)", "https://store.akamai.steamstatic.com/public/shared/images/responsive/logo_valve_footer.png"),
    ("成就图标示例", "https://cdn.cloudflare.steamstatic.com/steamcommunity/public/images/apps/977950/6f0e8a2b0d0e5d7e2b6b6a4c8e9d1f2a3b4c5d6e.jpg"),
]
for name, u in assets:
    raw = get(u, timeout=20, binary=True)
    if raw:
        head = raw[:80]
        print("  %-22s %6d B  %s" % (name, len(raw), head[:40]))
    else:
        print("  %-22s 下载失败" % name)

print()
print("④ 头像/图标域名可用性（仓库价值要查 92 个，先确认 CDN）")
for host in ("store.steampowered.com", "cdn.cloudflare.steamstatic.com",
             "cdn.akamai.steamstatic.com", "shared.akamai.steamstatic.com"):
    raw = get("https://%s/favicon.ico" % host, timeout=15, binary=True)
    print("  %-34s %s" % (host, ("%d B" % len(raw)) if raw else "不可达"))

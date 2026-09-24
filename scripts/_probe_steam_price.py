#!/usr/bin/env python3
"""Steam 价格/搜索接口可行性探针（服务器上跑）

验证 /~steam price 需要的三件事：
  1. 按名字搜到 appid          store/api/storesearch
  2. 拿到当前价 + 折扣          store/api/appdetails 的 price_overview
  3. 多地区比价                appdetails 换 cc=
"""
import json
import sys
import urllib.parse
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
UA = {"User-Agent": "Mozilla/5.0"}


def g(url):
    return json.loads(OP.open(urllib.request.Request(url, headers=UA), timeout=25)
                      .read().decode("utf-8"))


def search(term, cc="cn"):
    u = ("https://store.steampowered.com/api/storesearch/?"
         + urllib.parse.urlencode({"term": term, "cc": cc, "l": "schinese"}))
    return g(u)


def details(appid, cc="cn", lang="schinese"):
    u = ("https://store.steampowered.com/api/appdetails/?"
         + urllib.parse.urlencode({"appids": appid, "cc": cc, "l": lang}))
    return (g(u).get(str(appid)) or {}).get("data") or {}


def main():
    print("=" * 64)
    print("[1] 名字搜索（storesearch）")
    for term in ("A Dance of Fire and Ice", "冰与火之舞", "双人成行", "Hades"):
        try:
            s = search(term)
            items = s.get("items") or []
            print("  %-28s 命中 %s 条" % (term, s.get("total")))
            for it in items[:2]:
                pr = it.get("price") or {}
                print("      appid=%-8s %-34s %s" % (
                    it.get("id"), (it.get("name") or "")[:34],
                    pr.get("final_formatted") or ("免费" if it.get("is_free") else "无价")))
        except Exception as e:
            print("  %-28s 失败: %s" % (term, e))

    print("")
    print("[2] 当前价 / 折扣（appdetails.price_overview）")
    d = details(977950)
    po = d.get("price_overview") or {}
    print("  游戏名     =", d.get("name"))
    print("  字段       =", sorted(po.keys()) if po else "（无 price_overview：免费游戏或未发售）")
    if po:
        print("  原价/现价  = %s → %s  折扣 %s%%  货币 %s" % (
            po.get("initial_formatted"), po.get("final_formatted"),
            po.get("discount_percent"), po.get("currency")))
    free = details(1905180)   # OBS Studio 免费
    print("  OBS is_free =", free.get("is_free"), "| 有 price_overview =", bool(free.get("price_overview")))
    paid = details(1426210)   # 双人成行（付费）
    ppo = paid.get("price_overview") or {}
    print("  双人成行   = %s → %s (%s%%)" % (ppo.get("initial_formatted"), ppo.get("final_formatted"),
                                            ppo.get("discount_percent")))

    print("")
    print("[3] 地区比价（同 appid 换 cc）")
    for cc in ("cn", "us", "ar", "tr", "ru"):
        try:
            dd = details(977950, cc=cc, lang="english")
            p = dd.get("price_overview") or {}
            print("  cc=%-3s %-12s %s" % (cc, p.get("currency", "?"),
                                          p.get("final_formatted") or ("免费" if dd.get("is_free") else "-")))
        except Exception as e:
            print("  cc=%-3s 失败: %s" % (cc, e))

    print("")
    print("[4] 从商店链接里抠 appid（/~steam price <链接> 用）")
    import re
    for u in ("https://store.steampowered.com/app/977950/A_Dance_of_Fire_and_Ice/",
              "https://store.steampowered.com/app/730?snr=1_7_7_151_150_1",
              "https://store.steampowered.com/app/1144400/"):
        m = re.search(r"/app/(\d+)", u)
        print("  %-58s → appid=%s" % (u[:56], m.group(1) if m else "未匹配"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

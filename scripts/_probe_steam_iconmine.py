# -*- coding: utf-8 -*-
"""从 Steam 官方页面里挖图标资源（自己能猜的路径基本都 404，得从 HTML 里找）"""
import re
import sys
import urllib.request
from collections import Counter

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

PAGES = [
    ("商店首页", "https://store.steampowered.com/"),
    ("关于页", "https://store.steampowered.com/about/"),
    ("登录页", "https://store.steampowered.com/login/"),
]

seen = Counter()
for name, u in PAGES:
    try:
        req = urllib.request.Request(u, headers=UA)
        html = OP.open(req, timeout=30).read().decode("utf-8", "replace")
    except Exception as e:
        print("%s  抓取失败: %s" % (name, e))
        continue
    print("=== %s (%.0f KB) ===" % (name, len(html) / 1024))
    # 收集 svg / png 资源链接
    for m in re.finditer(r'["\'(]([^"\'()]+?\.(?:svg|png))(?:\?[^"\'()]*)?["\')]', html):
        url = m.group(1)
        if "steamstatic" in url or "steampowered" in url:
            seen[url] += 1
    icons = sorted(set(seen))
    print("  已累计 %d 个资源" % len(icons))

print()
print("=== 与图标/logo 相关的候选（按出现次数）===")
KEY = ("logo", "icon", "svg", "btn_", "wishlist", "cart", "library",
       "controller", "steamdeck", "achievement", "badge", "clock", "friends")
hits = [(u, c) for u, c in seen.items() if any(k in u.lower() for k in KEY)]
for u, c in sorted(hits, key=lambda x: -x[1])[:40]:
    print("  %-3d %s" % (c, u))
if not hits:
    print("  （没有匹配的，打印全部前 30）")
    for u, c in sorted(seen.items(), key=lambda x: -x[1])[:30]:
        print("  %-3d %s" % (c, u))

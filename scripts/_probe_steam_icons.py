# -*- coding: utf-8 -*-
"""探测 Steam 官方图标资源 + SVG 转 PNG 能力（卡片要用真 logo）"""
import sys
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
BASE = "https://store.akamai.steamstatic.com/public/shared/images/"

CANDS = [
    ("steam logo svg", BASE + "header/logo_steam.svg"),
    ("steam logo png?", BASE + "header/logo_steam.png"),
    ("steam logo 2x", BASE + "header/logo_steam_2x.png"),
    ("valve footer png", BASE + "responsive/logo_valve_footer.png"),
    ("cart svg", BASE + "header/btn_header_cart.svg"),
    ("search svg", BASE + "header/btn_header_search.svg"),
    ("menu svg", BASE + "header/btn_header_menu.svg"),
    ("wishlist svg", BASE + "header/btn_header_wishlist.svg"),
    ("steam logo old", BASE + "header/logo_steam_footer.png"),
    ("steam icon 32", "https://store.akamai.steamstatic.com/public/images/steam_icon.png"),
]


def head(url, timeout=15):
    try:
        req = urllib.request.Request(url, headers=UA)
        with OP.open(req, timeout=timeout) as r:
            raw = r.read(4000)
            return len(raw), raw[:16], r.headers.get("Content-Type", "")
    except Exception as e:
        return 0, b"", "%s %s" % (type(e).__name__, str(e)[:50])


print("=== Steam 官方图形资源 ===")
for name, u in CANDS:
    n, head_b, ct = head(u)
    if n:
        kind = "SVG" if head_b.lstrip().startswith(b"<?xml") or b"<svg" in head_b else (
            "PNG" if head_b.startswith(b"\x89PNG") else "?")
        print("  %-20s %5d+ B  %-14s %s" % (name, n, kind, ct))
    else:
        print("  %-20s 不可用 (%s)" % (name, ct))

print()
print("=== SVG -> PNG 转换能力（Pillow 不认 SVG，需要转换器）===")
for mod in ("cairosvg", "svglib", "wand"):
    try:
        __import__(mod)
        print("  python-%s  可用" % mod)
    except ImportError:
        print("  python-%s  未安装" % mod)
import shutil
for exe in ("rsvg-convert", "inkscape", "convert", "chromium-browser", "chromium"):
    p = shutil.which(exe)
    print("  %-18s %s" % (exe, p or "无"))

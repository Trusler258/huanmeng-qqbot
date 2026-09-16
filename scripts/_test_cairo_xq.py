# -*- coding: utf-8 -*-
"""象棋棋盘 cairosvg 热态速度 + 真实盘面（lastmove/check）验证。"""
import sys
import time

sys.path.insert(0, '/root/bot')

import cairosvg  # noqa: E402
import cchess  # noqa: E402
import cchess.svg  # noqa: E402

# 与 modules/chinese_chess._board_to_svg 完全一致的裁剪
import re as _re  # noqa: E402

def patched_svg(board, lastmove=None, check=None, arrows=None):
    kwargs = {"board": board, "orientation": cchess.RED}
    if lastmove is not None:
        kwargs["lastmove"] = lastmove
    if check is not None:
        kwargs["check"] = check
    if arrows is not None:
        kwargs["arrows"] = arrows
    svg = cchess.svg.board(**kwargs)
    svg = _re.sub(r'viewBox="-600 -600 1200 1200"', 'viewBox="-500 -520 1000 1040"', svg, count=1)
    svg = _re.sub(r'width="\d+" height="\d+"', 'width="1000" height="1040"', svg, count=1)
    return svg

BG = '#eebb55'

# ── 初始盘 ──
b0 = cchess.Board()
s0 = patched_svg(b0)

# ── 中局盘：走几步 + lastmove + check ──
b1 = cchess.Board()
moves = ["h2e2", "h9g7", "h0g2", "i9h9", "i0h0", "h9h4"]
for u in moves:
    try:
        b1.push(cchess.Move.from_uci(u))
    except Exception as e:
        print("走子失败", u, e)
last = cchess.Move.from_uci(moves[-1])
s1 = patched_svg(b1, lastmove=last)
print("盘面1 棋子数:", len(b1.piece_map()), " 最后一步:", last)

# ── 冷启动一次 ──
t0 = time.time()
cairosvg.svg2png(bytestring=s0.encode(), write_to='/tmp/_xq_cold.png',
                 output_width=1000, output_height=1040, background_color=BG)
print("冷启动(含首次解析): %.0f ms" % ((time.time() - t0) * 1000))

# ── 热态：初始盘 ×5 ──
ts = []
for i in range(5):
    t0 = time.time()
    cairosvg.svg2png(bytestring=s0.encode(), write_to='/tmp/_xq_hot%d.png' % i,
                     output_width=1000, output_height=1040, background_color=BG)
    ts.append((time.time() - t0) * 1000)
print("热态 初始盘 ×5: " + " ".join("%.0f" % x for x in ts) + " ms  中位 %.0f" % sorted(ts)[2])

# ── 热态：中局盘（带 lastmove）×5 ──
ts = []
for i in range(5):
    t0 = time.time()
    cairosvg.svg2png(bytestring=s1.encode(), write_to='/tmp/_xq_mid%d.png' % i,
                     output_width=1000, output_height=1040, background_color=BG)
    ts.append((time.time() - t0) * 1000)
print("热态 中局盘 ×5: " + " ".join("%.0f" % x for x in ts) + " ms  中位 %.0f" % sorted(ts)[2])

# ── 中局盘与 Chromium 对比 ──
import asyncio  # noqa: E402
from modules.chinese_chess import _svg_to_png  # noqa: E402

t0 = time.time()
asyncio.get_event_loop().run_until_complete(_svg_to_png(s1, '/tmp/_xq_mid_chrome.png'))
print("Chromium 中局盘: %.0f ms" % ((time.time() - t0) * 1000))

from PIL import Image, ImageChops  # noqa: E402

a = Image.open('/tmp/_xq_mid0.png').convert('RGB')
b = Image.open('/tmp/_xq_mid_chrome.png').convert('RGB')
if a.size != b.size:
    b = b.resize(a.size)
d = ImageChops.difference(a, b).convert('L')
h = d.histogram()
tot = sum(h)
print("中局盘 平均差: %.2f/255   差值>15 占比: %.2f%%"
      % (sum(i * c for i, c in enumerate(h)) / tot, sum(h[16:]) * 100.0 / tot))

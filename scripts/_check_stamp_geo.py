# -*- coding: utf-8 -*-
"""页脚「渲染时间」几何校验：确认不与右侧药丸 / 右对齐文字重叠。

图片我这边看不见，所以用坐标算：把页脚每个元素的文字宽度和位置重算一遍，
断言时间串的右边界在下一个元素左边界之前。
"""
import sys

sys.path.insert(0, '/root/bot')

from PIL import Image, ImageDraw  # noqa: E402

from services.wdsj_card_pillow import (  # noqa: E402
    _cjk_font, _mono_font, _hash8,
    CONTENT_X, CONTENT_W, FOOT_MT, FOOT_H,
    ARENA_CONTENT_W, BODY_PAD,
)
from services.card_base import render_stamp  # noqa: E402
from services.card_dark import (  # noqa: E402
    CARD_W, FOOT_PX, FOOT_L_FS, FOOT_R_FS, mono,
)

_probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))

STAMP = render_stamp(128)   # 固定长度样本：2026-09-16 22:52 · 128ms
print("时间串样本: %r  宽=%.1f px" % (STAMP, _probe.textlength(STAMP, font=_cjk_font(11))))


def check_daily(name: str, content_w: int, next_time: str):
    cw = content_w
    brand = "由 幻梦Bot 生成"
    brand_w = _probe.textlength(brand, font=_cjk_font(12))
    stamp_w = _probe.textlength(STAMP, font=_cjk_font(11))
    stamp_x = CONTENT_X + brand_w + 7
    stamp_end = stamp_x + stamp_w

    hash_txt = "#" + _hash8("2026-09-16|")
    hw = _probe.textlength(hash_txt, font=_mono_font(12)) + 14
    hx = CONTENT_X + cw - hw
    nx2 = hx
    nt = ""
    if next_time:
        nt = "下一轮 %s" % next_time
        nw = _probe.textlength(nt, font=_cjk_font(12)) + 14
        nx2 = hx - 8 - nw
    sx0 = int(stamp_end + 8)
    sx1 = int(nx2 - 8)

    ok1 = stamp_end + 4 <= nx2          # 时间不压到左侧药丸
    ok2 = sx1 > sx0 + 4                 # 渐变分隔线还剩得下
    print("\n[%s] 内容宽=%d" % (name, cw))
    print("  品牌右边界   %.1f" % (CONTENT_X + brand_w))
    print("  时间 x=%.1f  右边界=%.1f" % (stamp_x, stamp_end))
    print("  左药丸左边界 %d  (%s)" % (nx2, nt or "无下一轮药丸"))
    print("  分隔线 %d → %d  (留白 %d px)" % (sx0, sx1, sx1 - sx0))
    print("  不与药丸重叠: %s   分隔线可绘: %s" % ("OK" if ok1 else "!!! 重叠", "OK" if ok2 else "!! 被挤没"))
    return ok1


def check_leaderboard():
    stamp = STAMP
    card_x = 16
    left_end = card_x + FOOT_PX + 6 + 6                    # 绿点
    left_end += _probe.textlength("幻梦 Bot", font=mono(FOOT_L_FS))
    left_end += _probe.textlength(" · ", font=mono(FOOT_L_FS))
    left_end += _probe.textlength(stamp, font=mono(FOOT_L_FS))
    right_w = _probe.textlength("HUANMENG", font=mono(FOOT_R_FS))
    right_start = card_x + CARD_W - FOOT_PX - right_w
    ok = left_end + 6 <= right_start
    print("\n[排行榜] 卡片宽=%d  页脚内边距=%d" % (CARD_W, FOOT_PX))
    print("  左栏右边界   %.1f" % left_end)
    print("  HUANMENG 左边界 %.1f" % right_start)
    print("  间隙 %.1f px  → %s" % (right_start - left_end, "OK" if ok else "!!! 重叠"))
    return ok


r1 = check_daily("起床日榜(暖橙)", CONTENT_W, "00:01")
r2 = check_daily("竞技日榜(冷蓝)", ARENA_CONTENT_W, "00:01")
r3 = check_daily("起床日榜·无下一轮", CONTENT_W, "")
r4 = check_leaderboard()

print("\n=== 汇总 ===")
print("起床日榜:", "OK" if r1 else "FAIL")
print("竞技日榜:", "OK" if r2 else "FAIL")
print("起床(无下轮):", "OK" if r3 else "FAIL")
print("排行榜:", "OK" if r4 else "FAIL")

# -*- coding: utf-8 -*-
"""wdsj 各卡「渲染时间」实测。

要点：每张 HTML 卡必须「构建 → 立刻渲染」，否则 t0 会被前面的活儿
（Pillow 出图、上一张卡的浏览器渲染）污染，ms 虚高到十几秒。
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, '/root/bot')

OUT = '/tmp/stamp_test'
os.makedirs(OUT, exist_ok=True)

# ══════════════════════════════════════════════════════════
#  1. Pillow：起床日榜 / 竞技场日榜 / 排行榜
# ══════════════════════════════════════════════════════════
from services.wdsj_card_pillow import (  # noqa: E402
    render_daily_rank_card, ARENA_COLS, ARENA_PALETTE, ARENA_W,
    ARENA_CONTENT_W, ARENA_TITLE,
)
from services.leaderboard_card import build_payload, render_leaderboard_card  # noqa: E402

payload = {
    "date": "2026-09-16",
    "range": "00:01 → 20:00",
    "when": "洛花星雨 Nexus",
    "rows": [
        {"rank": 1, "name": "dongtians", "cells": {"kills": 128, "final": 42, "wins": 9}},
        {"rank": 2, "name": "Trusler", "cells": {"kills": 96, "final": 31, "wins": 6}},
        {"rank": 3, "name": "啊这", "cells": {"kills": 77, "final": 25, "wins": 4}},
    ],
    "newPlayers": ["新来的"],
    "nextTime": "00:01",
    "brand": "幻梦Bot",
}

t0 = time.perf_counter()
img = render_daily_rank_card(payload)
img.save(f'{OUT}/daily_pillow.jpg', 'JPEG', quality=95)
print("[Pillow] 起床日榜 %dx%d  %.0fms" % (img.size[0], img.size[1], (time.perf_counter() - t0) * 1000))

t0 = time.perf_counter()
aimg = render_daily_rank_card(payload, width=ARENA_W, content_w=ARENA_CONTENT_W,
                             cols=ARENA_COLS, title=ARENA_TITLE, palette=ARENA_PALETTE)
aimg.save(f'{OUT}/arena_pillow.jpg', 'JPEG', quality=95)
print("[Pillow] 竞技日榜 %dx%d  %.0fms" % (aimg.size[0], aimg.size[1], (time.perf_counter() - t0) * 1000))

lb_data = {
    "board": {"group": "起床战争", "displayName": "总击杀", "unit": "次"},
    "type": "ALLTIME",
    "entries": [{"rank": i + 1, "owner": n, "value": 1000 - i * 77, "headImageUrl": ""}
                for i, n in enumerate(["dongtians", "Trusler", "啊这", "小明明", "路人甲"])],
}
t0 = time.perf_counter()
limg = render_leaderboard_card(build_payload(lb_data, "幻梦"))
limg.save(f'{OUT}/leaderboard_pillow.png', 'PNG')
print("[Pillow] 排行榜 %dx%d  %.0fms" % (limg.size[0], limg.size[1], (time.perf_counter() - t0) * 1000))

# ══════════════════════════════════════════════════════════
#  2. HTML：每张卡「构建 → 立刻渲染」
# ══════════════════════════════════════════════════════════
from modules.changelog import _ensure_browser  # noqa: E402
from services import wdsj_api as api  # noqa: E402
import modules.wdsj as wdsj_mod  # noqa: E402


def builders():
    """生成 (名字, 宽度, 构建函数) —— 构建函数在截图前一刻才调用"""
    yield ("leaderboard", 440, lambda: api.build_leaderboard_html(lb_data, "幻梦"))
    yield ("help_md", 520, lambda: api.build_help_card_html("/root/bot/data/wdsj_help.md", "幻梦"))
    yield ("dual", 2400, lambda: api.build_dual_card_html(
        {"player": {"name": "dongtians", "uid": "1", "uuid": ""},
         "labels": {"kills": "击杀"}, "values": {"kills": 128},
         "displayName": "起床战争", "headerCards": []},
        {"player": {"name": "dongtians", "uid": "1", "uuid": ""},
         "labels": {"kills": "击杀"}, "values": {"kills": 96},
         "displayName": "竞技场", "headerCards": []}))
    yield ("single", 680, lambda: wdsj_mod.build_card_html(
        {"player": {"name": "dongtians", "uid": "1"},
         "values": {"kills": 128, "deaths": 30, "wins": 9},
         "labels": {"kills": "击杀", "deaths": "死亡", "wins": "胜场"},
         "headerCards": [{"key": "rank", "label": "段位", "value": "钻石"}],
         "displayName": "起床战争"}))


async def main():
    browser = await _ensure_browser()

    # 群内排名卡（内部自带 构建→渲染，ms 真实）
    from services.wdsj_tracker import build_group_rank
    _m, g_img = await build_group_rank(123456, "bw_kills")
    print("[HTML] %-12s -> %s" % ("group_rank", g_img))

    for name, width, build in builders():
        try:
            html = build()                     # ★ 构建与渲染紧挨着
            page = await browser.new_page(viewport={"width": width, "height": 900})
            try:
                await page.set_content(html)
                await page.wait_for_timeout(600)
                got = await page.evaluate(
                    "()=>{var e=document.querySelector('.foot-l')||document.querySelector('.footer')"
                    "||document.querySelector('.foot');return e?e.textContent.replace(/\\s+/g,' ').trim():'(无页脚)';}")
                el = await page.query_selector(".card")
                out = f'{OUT}/{name}.jpg'
                if el:
                    await el.screenshot(path=out, type="jpeg", quality=92)
                else:
                    await page.screenshot(path=out, full_page=True, type="jpeg", quality=92)
                print("[HTML] %-12s 页脚=%r" % (name, got[-80:]))
            finally:
                await page.close()
        except Exception as e:
            print("[HTML] %-12s 失败: %s: %s" % (name, type(e).__name__, e))


asyncio.get_event_loop().run_until_complete(main())
print("产物目录:", OUT)

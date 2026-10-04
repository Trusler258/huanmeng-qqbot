# -*- coding: utf-8 -*-
"""渲染新版 /~wdsj help 卡片（用完即删）"""
import asyncio
import os
import sys

sys.path.insert(0, "/root/bot")
os.chdir("/root/bot")
from services import wdsj_api as api  # noqa: E402
from modules.changelog import _ensure_browser  # noqa: E402


async def main():
    html = api.build_wdsj_help_card_html("幻梦bot")
    browser = await _ensure_browser()
    page = await browser.new_page(viewport={"width": 560, "height": 100})
    await page.set_content(html)
    await page.wait_for_timeout(500)
    await page.set_viewport_size({"width": 560, "height": 100})
    out = "/root/bot/data/img_temp/wdsj_help_new.png"
    await page.screenshot(path=out, full_page=True)
    await page.close()
    print("rendered:", out, os.path.getsize(out), "bytes")


asyncio.run(main())

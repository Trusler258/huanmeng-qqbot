#!/usr/bin/env python3
"""把 Steam 资料卡 HTML 渲染成 PNG（在服务器上跑）

用法： python3 scripts/_steam_card_shot.py [输入html] [输出png]
验收： 尺寸 / 坏图数 / JS 错误数 / 内联字体是否加载 / 行数
"""
import asyncio
import sys
from pathlib import Path

CHROME = "/usr/bin/chromium-browser"


async def main():
    from playwright.async_api import async_playwright

    src = Path(sys.argv[1] if len(sys.argv) > 1 else "data/_steam_card.html")
    out = Path(sys.argv[2] if len(sys.argv) > 2 else "/tmp/_steam_card.png")
    if not src.exists():
        print("找不到输入:", src)
        return 1
    html = src.read_text(encoding="utf-8")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            executable_path=CHROME,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--font-render-hinting=none"],
        )
        page = await browser.new_page(
            viewport={"width": 1800, "height": 1400}, device_scale_factor=2
        )
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        await page.set_content(html, wait_until="load")
        try:
            await page.evaluate("document.fonts.ready")
        except Exception:
            pass
        await page.wait_for_timeout(1500)

        info = await page.evaluate(
            """() => ({
                w: document.body.scrollWidth,
                h: document.body.scrollHeight,
                imgs: document.querySelectorAll('img').length,
                bad: [...document.querySelectorAll('img')].filter(i=>!i.complete||i.naturalWidth===0).length,
                fontOK: document.fonts.check('16px HMSans'),
                rows: document.querySelectorAll('.row').length,
                pname: (document.getElementById('pname')||{}).textContent,
                hours: (document.getElementById('s-hours')||{}).textContent,
                ach: (document.getElementById('s-ach')||{}).textContent,
                state: (document.getElementById('state')||{}).className,
            })"""
        )

        el = await page.query_selector("body")
        await el.screenshot(path=str(out))
        await browser.close()

    print("尺寸        = %sx%s  (CSS px)" % (info["w"], info["h"]))
    print("玩家名      = %s" % info["pname"])
    print("累计/成就   = %s / %s" % (info["hours"], info["ach"]))
    print("状态 class  = %s" % info["state"])
    print("图片        = %s 张，坏图 %s" % (info["imgs"], info["bad"]))
    print("HMSans 内联 = %s" % info["fontOK"])
    print("游戏行数    = %s" % info["rows"])
    print("JS 错误     = %s %s" % (len(errs), errs[:2]))
    print("输出        = %s (%s KB)" % (out, out.stat().st_size // 1024 if out.exists() else 0))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

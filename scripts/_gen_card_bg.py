"""
深色卡片 body 背景生成器（一次性）

为什么用 Chromium 预渲染：深色 body 背景由 4 处椭圆光晕 + conic 扇区 +
噪点纹理层（body::before）+ 线性渐变叠成，程序化复刻难以像素级一致
（实测程序化版在卡外区域 R 通道低 6，偏差 3.7%）。
背景是**静态**的（不随数据变化），预渲染一次即可长期复用。

实测依据：背景按元素尺寸**按比例缩放**（同一相对位置在不同 body 高度下
颜色差 ≤2），所以基准图可以 resize 到任意目标尺寸。

用法：
    python3 scripts/_gen_card_bg.py            # 生成/更新基准图
生成物：data/web_assets/card_bg/dark_440x1200.png
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "data" / "web_assets" / "card_bg"
BASE_W, BASE_H = 440, 1200


def build_blank_html() -> str:
    """只有 body 背景（无卡片内容）的空白页 —— 直接摘模板的 :root + body 规则"""
    tpl = (ROOT / "data" / "templates" / "leaderboard_card.html").read_text(encoding="utf-8")
    import re
    css = re.search(r"<style>(.*?)</style>", tpl, re.S).group(1)
    # 去掉 @font-face（背景不需要字体）+ 去 base64
    css = re.sub(r"@font-face\s*\{[^}]*\}", "", css, flags=re.S)
    css = re.sub(r"data:[a-z/+;]+base64,[A-Za-z0-9+/=]+", "", css)
    return (f"<!DOCTYPE html><html><head><meta charset='UTF-8'><style>{css}</style>"
            f"</head><body><div style='height:{BASE_H}px'></div></body></html>")


async def main():
    from modules.changelog import _ensure_browser
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"dark_{BASE_W}x{BASE_H}.png"

    b = await _ensure_browser()
    page = await b.new_page()
    await page.set_viewport_size({"width": BASE_W, "height": 10})
    await page.set_content(build_blank_html())
    await page.wait_for_timeout(600)
    el = await page.query_selector("body")
    box = await el.bounding_box()
    await el.screenshot(path=str(out))
    await page.close()

    from PIL import Image
    im = Image.open(out)
    print(f"已生成 {out.name}  {im.size}  body_box={box['width']:.0f}x{box['height']:.0f}")
    if im.height != BASE_H:
        print(f"⚠️ 期望高 {BASE_H}，实际 {im.height}（差 {im.height - BASE_H}）")


asyncio.run(main())

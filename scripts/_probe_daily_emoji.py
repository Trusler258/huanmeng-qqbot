#!/usr/bin/env python3
"""探针：日报卡里的 emoji 到底能被渲染成什么样？（决定 Pillow 侧怎么还原）

需要回答三个问题：
  A. 服务器上 Chromium 渲染日报模板时，📊🥇🥈🥉🗣️🤿😴🌙☀️ 是**彩色**、**单色**还是**豆腐块**？
     （fc-list 里查不到 emoji 字体，但 NapCat 与 snap 目录里存在字体文件 → 必须实测）
  B. Pillow 能否直接用 NotoColorEmoji / AppleColorEmoji 画彩色 emoji？尺寸限制是什么？
  C. 若都不行 → 就得把 emoji 做成 PNG 资源贴图（card_base.paste_icon 已有这套）

判定"是否彩色"：把单元格裁出来，统计「通道极差 > 24」的像素占比。
  - 彩色 emoji：占比显著（>3%）
  - 单色字形/豆腐块：几乎为 0
用法（服务器）: python3 scripts/_probe_daily_emoji.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, "/root/bot")

EMOJIS = ["📊", "🥇", "🥈", "🥉", "🗣️", "🤿", "😴", "🌙", "☀️"]
EMOJI_FONTS = [
    "/snap/gnome-42-2204/247/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    "/root/NapCat/opt/QQ/resources/app/resource/fonts/AppleColorEmoji.ttf",
    "/root/NapCat/opt/QQ/resources/app/resource/fonts/AppleColorEmoji-fix.ttf",
]


def colored_ratio(im, box=None):
    """返回 (彩色像素占比, 非背景像素占比)"""
    im = im.convert("RGB")
    if box:
        im = im.crop(box)
    px = list(im.getdata())
    if not px:
        return 0.0, 0.0
    colored = 0
    inked = 0
    for r, g, b in px:
        mx, mn = max(r, g, b), min(r, g, b)
        if mx > 40:                      # 不是纯背景
            inked += 1
        if mx - mn > 24 and mx > 60:     # 明确有色
            colored += 1
    n = len(px)
    return colored / n, inked / n


def build_html() -> str:
    """每个 emoji 单独一格，黑底白字，方便定位与判定"""
    cells = "".join(
        f'<div class="c"><span>{e}</span></div>' for e in EMOJIS
    )
    n = len(EMOJIS)
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#000;width:{n*64}px}}
.row{{display:flex}}
.c{{width:64px;height:72px;display:flex;align-items:center;justify-content:center;
    font-size:32px;font-family:'PingFang SC','Microsoft YaHei UI','Noto Sans CJK SC',sans-serif;
    color:#fff;outline:1px solid #333}}
</style></head><body><div class="row">{cells}</div></body></html>"""


async def chromium_check():
    from modules.changelog import render_card_to_image
    out = Path("/tmp/_probe_emoji_chromium.png")
    p = await render_card_to_image(build_html(), output_filename=out.name, width=len(EMOJIS) * 64)
    print("Chromium 输出:", p)
    return Path(p) if p else None


def pillow_check():
    from PIL import Image, ImageDraw, ImageFont
    print("\n[B] Pillow 直用 emoji 字体：")
    for fp in EMOJI_FONTS:
        f = Path(fp)
        if not f.exists():
            print(f"   {fp}  → 不存在")
            continue
        for size in (109, 32, 64, 128):
            try:
                font = ImageFont.truetype(fp, size)
            except Exception as e:
                print(f"   {f.name}@{size} → truetype 失败: {type(e).__name__}: {e}")
                continue
            im = Image.new("RGBA", (200, 160), (0, 0, 0, 255))
            d = ImageDraw.Draw(im)
            try:
                d.text((10, 10), "🥇", font=font, embedded_color=True)
                ratio, ink = colored_ratio(im)
                print(f"   {f.name}@{size} → OK  彩色占比 {ratio:.3f}  墨迹占比 {ink:.3f}")
            except Exception as e:
                print(f"   {f.name}@{size} → draw 失败: {type(e).__name__}: {e}")


async def main():
    print("[A] Chromium 渲染 emoji：")
    p = await chromium_check()
    if p and p.exists():
        from PIL import Image
        im = Image.open(p)
        print("   图片尺寸:", im.size, "（每格 64×72）")
        for i, e in enumerate(EMOJIS):
            ratio, ink = colored_ratio(im, (i * 64, 0, (i + 1) * 64, 72))
            tag = "彩色" if ratio > 0.03 else ("有字形但单色" if ink > 0.02 else "空/豆腐")
            print(f"   {i} {e!r:8} 彩色 {ratio:.3f}  墨迹 {ink:.3f}  → {tag}")
    else:
        print("   Chromium 渲染失败")

    pillow_check()

    print("\n[结论参考] 若 A 里是「彩色」而 B 不可用 → 用 Chromium 把 emoji 渲染成 PNG 存进")
    print("data/web_assets/，再走 card_base.paste_icon 贴图（一次性生成，运行时零依赖）。")


asyncio.run(main())

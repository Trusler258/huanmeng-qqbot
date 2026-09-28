#!/usr/bin/env python3
"""一次性生成日报卡需要的 emoji PNG 资源（供 Pillow 侧 paste_icon 贴图）。

为什么走 Pillow 抽而不是 Chromium 抽：
  `modules.changelog._screenshot_html` 存的是 **JPEG（无 alpha 通道）**，抽出来必然是白底，
  没法做透明贴图（试过：ink bbox 等于整张画布）。改用 Pillow 直接读彩色字体，
  alpha 干净、PNG 只有 1~3KB，也不必把 11MB 字体塞进仓库。

为什么能用 Pillow 但必须 109px：
  NotoColorEmoji 是 CBDT 位图字体，Pillow 只接受它**原生 ppem=109**，
  其它尺寸抛 `OSError: invalid pixel size`；AppleColorEmoji.ttf 这版 Pillow 完全打不开。
  所以：109 渲染 → 按包围盒裁 → LANCZOS 缩到目标字号。

输出：data/web_assets/daily_icons/{key}.png（透明底，尺寸=目标字号）
用法（服务器）: python3 scripts/_gen_daily_emoji_assets.py
"""
import sys
from pathlib import Path

sys.path.insert(0, "/root/bot")

OUT_DIR = Path("/root/bot/data/web_assets/daily_icons")

# 候选彩色 emoji 字体（按优先级；前两个是 snap content snap 提供的 Noto 彩色字体）
FONT_CANDIDATES = [
    "/snap/gnome-42-2204/247/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    "/root/NapCat/opt/QQ/resources/app/resource/fonts/AppleColorEmoji-fix.ttf",
    "/root/NapCat/opt/QQ/resources/app/resource/fonts/AppleColorEmoji.ttf",
]
NATIVE_PPEM = 109

# key -> (emoji, 目标字号)   字号取自 daily_report.html：
#   .logo font-size:22px / .rnum font-size:13px / .fn-it font-size:12.5px(取 13)
SPECS = {
    "logo_chart":   ("📊", 22),
    "medal_gold":   ("🥇", 13),
    "medal_silver": ("🥈", 13),
    "medal_bronze": ("🥉", 13),
    "fact_mic":     ("🗣️", 13),
    "fact_diver":   ("🤿", 13),
    "fact_sleep":   ("😴", 13),
    "fact_moon":    ("🌙", 13),
    "fact_sun":     ("☀️", 13),
}


def pick_font():
    from PIL import ImageDraw, ImageFont, Image
    for path in FONT_CANDIDATES:
        if not Path(path).exists():
            continue
        try:
            font = ImageFont.truetype(path, NATIVE_PPEM)
        except Exception as e:
            print(f"  跳过 {Path(path).name}: {type(e).__name__}: {e}")
            continue
        # 实测确认是彩色（通道极差大的像素占比要够高）
        im = Image.new("RGBA", (160, 160), (0, 0, 0, 0))
        ImageDraw.Draw(im).text((4, 4), "🥇", font=font, embedded_color=True)
        px = list(im.convert("RGB").getdata())
        colored = sum(1 for r, g, b in px if max(r, g, b) - min(r, g, b) > 24)
        ratio = colored / max(1, len(px))
        if ratio > 0.02:
            print(f"  采用 {path}（彩色占比 {ratio:.3f}）")
            return path, font
        print(f"  跳过 {Path(path).name}: 彩色占比仅 {ratio:.4f}（可能是单色/豆腐）")
    raise RuntimeError("找不到可用的彩色 emoji 字体")


def main():
    from PIL import Image, ImageDraw

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"输出目录: {OUT_DIR}")
    path, font = pick_font()
    print()

    for key, (ch, size) in SPECS.items():
        canvas = Image.new("RGBA", (NATIVE_PPEM * 2, NATIVE_PPEM * 2), (0, 0, 0, 0))
        ImageDraw.Draw(canvas).text((8, 8), ch, font=font, embedded_color=True)
        bbox = canvas.getbbox()
        if not bbox:
            print(f"❌ {key}: 渲染为空")
            continue
        glyph = canvas.crop(bbox)
        # 等比缩放到目标字号（以高度对齐 em 框），不拉伸变形
        gw, gh = glyph.size
        scale = size / gh
        nw, nh = max(1, round(gw * scale)), max(1, round(gh * scale))
        resized = glyph.resize((nw, nh), Image.LANCZOS)
        out = OUT_DIR / f"{key}.png"
        resized.save(out)
        print(f"✅ {key:13} {ch!r:7} 原生bbox={bbox} → 缩放 {nw}×{nh} "
              f"({out.stat().st_size}B)")

    print("\n生成完毕。data/web_assets/daily_icons/ 纳入版本管理；"
          "以后要改字号，重跑本脚本即可。")


if __name__ == "__main__":
    main()

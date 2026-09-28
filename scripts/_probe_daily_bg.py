#!/usr/bin/env python3
"""标定背景：渲染一张"只有 body 背景、没有卡片"的金标，用来逐像素校准 _background()。

为什么单独做这一步：
  卡片是**半透明**的，底色偏差会弥散到整张图 —— 表现为每个条带都有 7~12 的误差底座，
  但用肉眼/条带剖面都看不出根因。把背景单独拎出来比，误差立刻定位到具体通道。
  （第一版就是靠这一步发现 radial-gradient 的 `ellipse 80% 55%` 是**半径**不是直径。）

用法（服务器）:
  python3 scripts/_probe_daily_bg.py            # 生成金标并分析
  python3 scripts/_probe_daily_bg.py --compare  # 与 Pillow 的 _background() 对比
"""
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, "/root/bot")
TPL = Path("/root/bot/data/templates/daily_report.html")
OUT = Path("/tmp/daily_ab")
H = 962


def bg_only_html() -> str:
    """取模板的 <style>，body 里不放任何内容（高度固定 962），只留背景"""
    tpl = TPL.read_text(encoding="utf-8")
    style = re.search(r"<style>(.*?)</style>", tpl, re.S).group(1)
    return (f'<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">'
            f'<style>{style}\nhtml,body{{height:{H}px;min-height:{H}px}}</style>'
            f'</head><body></body></html>')


async def main():
    import numpy as np
    from PIL import Image
    OUT.mkdir(parents=True, exist_ok=True)

    from modules.changelog import render_card_to_image
    p = await render_card_to_image(bg_only_html(), output_filename="_ab_bg.png", width=720)
    g = Image.open(p).convert("RGB")
    g.save(OUT / "bg_golden.png")
    ga = np.asarray(g).astype(np.float32)
    print(f"背景金标: {g.size}  → {OUT / 'bg_golden.png'}")

    pts = {"左上": (60, 120), "上中": (360, 40), "右上": (660, 60),
           "中左": (40, 480), "正中": (360, 480), "中右": (680, 480),
           "左下": (60, 880), "下中": (360, 930), "右下": (660, 880)}
    print("\n背景金标采样:")
    for k, (x, y) in pts.items():
        print(f"  {k:4} ({x:>3},{y:>3}) = {tuple(int(v) for v in ga[y, x])}")

    if "--compare" in sys.argv:
        from services.daily_report_pillow import _background
        pa = np.asarray(_background(720, H)).astype(np.float32)
        d = np.abs(ga - pa)
        print(f"\n══ 与 Pillow _background() 对比 ══")
        print(f"  平均差 {d.mean():.2f}  最大 {d.max():.0f}  >32 占比 {(d.max(axis=2) > 32).mean() * 100:.2f}%")
        for k, (x, y) in pts.items():
            print(f"  {k:4} golden={tuple(int(v) for v in ga[y, x])}  "
                  f"pillow={tuple(int(v) for v in pa[y, x])}")
        # 沿 y 的通道均值曲线（每 60 行），看光晕的纵向衰减对不对
        print("\n  纵向通道均值（每 60 行）: y  golden(R,G,B)  pillow(R,G,B)")
        for y in range(0, H, 60):
            gm = ga[y:y + 60].mean(axis=(0, 1))
            pm = pa[y:y + 60].mean(axis=(0, 1))
            print(f"   {y:>3}  ({gm[0]:5.1f},{gm[1]:5.1f},{gm[2]:5.1f})  "
                  f"({pm[0]:5.1f},{pm[1]:5.1f},{pm[2]:5.1f})")


asyncio.run(main())

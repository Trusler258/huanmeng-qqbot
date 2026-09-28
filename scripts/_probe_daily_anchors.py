#!/usr/bin/env python3
"""按**颜色**定位日报金标图里的各元素，产出渲染器要对齐的锚点坐标。

行剖面只能给出"有变化"的行，无法区分是哪个元素。这里直接找特征色：
  · 区块标题左侧的小竖条（3px 宽，linear-gradient 180deg #ec4899→#8b5cf6，x≈48..50）
  · 排行条的粉/金/银/铜渐变（x 200..610 一带）
  · 24h 热力格子（rgba(236,72,153,.22~1) 叠在卡片底上）
  · 页脚那颗绿点（#34d399）
把这些 y 范围量出来，Pillow 侧就有硬指标对齐，而不是靠"看着差不多"。

用法（服务器）: python3 scripts/_probe_daily_anchors.py /tmp/daily_ab/golden.png
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image


def near(px, rgb, tol):
    return np.all(np.abs(px.astype(np.int16) - np.array(rgb, dtype=np.int16)) <= tol, axis=-1)


def runs(mask_1d, min_len=1):
    """把一维布尔数组压成 [(start, end)] 连续段"""
    out, s = [], None
    for i, v in enumerate(mask_1d):
        if v and s is None:
            s = i
        elif not v and s is not None:
            if i - s >= min_len:
                out.append((s, i - 1))
            s = None
    if s is not None and len(mask_1d) - s >= min_len:
        out.append((s, len(mask_1d) - 1))
    return out


def main():
    p = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/daily_ab/golden.png")
    a = np.asarray(Image.open(p).convert("RGB")).astype(np.int16)
    H, W = a.shape[:2]
    print(f"图: {p.name}  {W}×{H}\n")

    # ── 1. 区块标题的小竖条：取 x=48..50 一列，找"偏紫红"的像素 ──
    strip = a[:, 48:51, :]
    # 竖条顶部 #ec4899 底部 #8b5cf6：取"红>150 且 蓝>120 且 绿<红"的像素
    m = (strip[..., 0] > 140) & (strip[..., 2] > 110) & (strip[..., 1] < strip[..., 0] - 30)
    ys = m.any(axis=1)
    print("① 区块竖条 (x48-50) y 段:", runs(ys, 5))

    # ── 2. 摘要区的 4 个格子分隔：在摘要带里找 x 方向的 1px 暗缝 ──
    if H > 190:
        band = a[140:190, :, :].mean(axis=2)
        cold = np.abs(np.diff(band, axis=1)).mean(axis=0)
        idx = [i + 1 for i, v in enumerate(cold) if v > 3.0]
        print("② 摘要带列缝 x:", idx[:24])

    # ── 3. 排行条：x 210 一列，找粉色系（bar 1 是 #ec4899→#f472b6）──
    col = a[:, 210, :]
    ispink = (col[:, 0] > 170) & (col[:, 1] < 150) & (col[:, 2] > 110) & (col[:, 2] < 210)
    print("③ 排行粉条 (x=210) y 段:", runs(ispink, 3))
    # 金色条（rb1 #fbbf24→#fde68a）
    isgold = (col[:, 0] > 200) & (col[:, 1] > 140) & (col[:, 2] < 150)
    print("   排行金条 (x=210) y 段:", runs(isgold, 3))

    # ── 4. 24h 热力：横向找"粉色格子"的行带（整行里有大量粉色像素）──
    pink = (a[..., 0] > 120) & (a[..., 1] < 140) & (a[..., 2] > 100)
    per_row = pink[:, 40:680].sum(axis=1)
    hot = per_row > 25
    segs = [s for s in runs(hot, 8)]
    print("④ 粉色密集行带（含排行条/热力/竖条）:", segs[:12])

    # ── 5. 页脚绿点 #34d399 ──
    green = near(a, (52, 211, 153), 40)
    gy, gx = np.where(green)
    if len(gy):
        print(f"⑤ 页脚绿点: y {gy.min()}..{gy.max()}  x {gx.min()}..{gx.max()}")
    else:
        print("⑤ 页脚绿点: 未找到")

    # ── 6. 卡片左右边界：取 y=300 一行，看两侧相对背景的过渡 ──
    if H > 320:
        row = a[300, :, :].mean(axis=1)
        d = np.abs(np.diff(row))
        cand = [i + 1 for i, v in enumerate(d) if v > 2.5]
        print("⑥ y=300 列过渡 x:", cand[:16])

    # ── 7. 卡片底边（最后一行有明显亮度的位置）──
    lum = a.mean(axis=(1, 2))
    tail = [y for y in range(H - 1, 0, -1) if lum[y] > 26]
    print("⑦ 底部最后有明显内容的 y:", tail[:5], " 总高:", H)


if __name__ == "__main__":
    main()

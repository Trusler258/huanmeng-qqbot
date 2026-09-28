#!/usr/bin/env python3
"""查"中文字体没了"到底发生在哪：对比文字区的墨迹量 + 判定是否有"豆腐块"。

判据：
  · 真实汉字 → 墨迹多、笔画分布不均（行/列的内部都有墨）
  · 豆腐块（缺字体的方框）→ 墨迹呈"空心矩形"：外框有墨、**内部几乎为空**
  · 完全没渲染 → 墨迹≈0

用法（服务器）:
  python3 scripts/_probe_cjk_ink.py            # 用 A/B 的两张图对比
  python3 scripts/_probe_cjk_ink.py <a.png> <b.png>
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image

DEFAULT_A = "/tmp/daily_ab/golden.png"      # Chromium
DEFAULT_B = "/tmp/daily_ab/pillow.png"      # Pillow


def ink(im, box, thr=110):
    """返回 (墨迹占比, 外框墨迹占比, 内部墨迹占比)"""
    x0, y0, x1, y1 = box
    a = np.asarray(im.convert("L")).astype(np.int16)[y0:y1, x0:x1]
    m = a > thr
    h, w = m.shape
    if h < 6 or w < 6:
        return 0.0, 0.0, 0.0
    outer = np.zeros_like(m)
    outer[0:2, :] = True
    outer[-2:, :] = True
    outer[:, 0:2] = True
    outer[:, -2:] = True
    inner = m[3:-3, 3:-3]
    return m.mean(), m[outer].mean(), (inner.mean() if inner.size else 0.0)


def report(path, label):
    im = Image.open(path)
    print(f"\n── {label}: {Path(path).name}  {im.size} ──")
    # 排行条的名字区（x 94..190）逐行；每行 y 中心 = 254+40*i+8
    for i in range(8):
        y = 254 + 40 * i
        r, o, inn = ink(im, (94, y - 10, 190, y + 24))
        flag = ""
        if r < 0.01:
            flag = "  ← 几乎无墨迹（可能没渲染）"
        elif o > 0.35 and inn < 0.06:
            flag = "  ← 疑似豆腐块（空心矩形）"
        print(f"   排行{i+1} 名字区 墨迹={r:.3f} 外框={o:.3f} 内部={inn:.3f}{flag}")
    # 头部标题「幻梦」
    r, o, inn = ink(im, (110, 50, 190, 82))
    print(f"   头部标题「幻梦」 墨迹={r:.3f} 外框={o:.3f} 内部={inn:.3f}")
    # 区块标题「发言排行」
    r, o, inn = ink(im, (59, 210, 140, 234))
    print(f"   区块标题「发言排行」 墨迹={r:.3f} 外框={o:.3f} 内部={inn:.3f}")
    # 页脚「幻梦 Bot」
    r, o, inn = ink(im, (48, 895, 110, 915))
    print(f"   页脚「幻梦 Bot」 墨迹={r:.3f} 外框={o:.3f} 内部={inn:.3f}")


def main():
    a = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_A
    b = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_B
    print("说明：外框高+内部≈0 ⇒ 空心方框（豆腐块）；墨迹≈0 ⇒ 完全没渲染")
    for p, lab in ((a, "A"), (b, "B")):
        if Path(p).exists():
            report(p, lab)
        else:
            print(f"\n{lab}: {p} 不存在")


if __name__ == "__main__":
    main()

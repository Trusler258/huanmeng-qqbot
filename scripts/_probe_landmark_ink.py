#!/usr/bin/env python3
"""逐地标比对"该有文字的地方"的墨迹量，找出 Pillow 版漏画/画淡的文字。

用法（服务器）: python3 scripts/_probe_landmark_ink.py <golden.png> <pillow.png>
"""
import sys
from pathlib import Path

sys.path.insert(0, "/root/bot/scripts")
from _probe_cjk_ink import ink           # noqa: E402
from PIL import Image                    # noqa: E402

MARKS = [
    ("head-name",       (110, 50, 190, 82)),
    ("head-dailyrep",   (232, 50, 360, 78)),
    ("head-sub",        (110, 78, 260, 100)),
    ("head-tag",        (560, 55, 670, 88)),
    ("sum-num-386",     (40, 128, 170, 168)),
    ("sum-lbl-TOTAL",   (40, 168, 170, 190)),
    ("sec1-label",      (59, 210, 140, 234)),
    ("rank1-name",      (94, 244, 190, 278)),
    ("rank8-name",      (94, 524, 190, 558)),
    ("sec2-label",      (59, 578, 180, 606)),
    ("hour-labels",     (44, 648, 120, 666)),
    ("sec3-label",      (59, 688, 150, 716)),
    ("fact1-text",      (34, 728, 660, 762)),
    ("fact2-text",      (34, 765, 660, 799)),
    ("fact3-text",      (34, 802, 660, 836)),
    ("fact4-text",      (34, 839, 660, 873)),
    ("foot-name",       (48, 895, 130, 915)),
    ("foot-huanmeng",   (600, 895, 672, 915)),
]


def main():
    gs = sys.argv[1] if len(sys.argv) > 1 else "/tmp/daily_ab/golden.png"
    ps = sys.argv[2] if len(sys.argv) > 2 else "/tmp/daily_ab/pillow.png"
    G, P = Image.open(gs), Image.open(ps)
    print("地标                   金标墨迹   我的墨迹   判定")
    problems = []
    for name, box in MARKS:
        rg, _og, _ig = ink(G, box)
        rp, _op, _ip = ink(P, box)
        if rp < 0.02 and rg > 0.05:
            v = "!! 我这边没墨迹（文字缺失）"
            problems.append((name, "缺失", rg, rp))
        elif rg > 0.05 and rp < rg * 0.55:
            v = f"!! 明显偏淡（{rp / rg * 100:.0f}%）"
            problems.append((name, "偏淡", rg, rp))
        elif rg > 0.05 and rp < rg * 0.8:
            v = f"!  偏淡（{rp / rg * 100:.0f}%）"
        else:
            v = "ok"
        print(f"{name:20} {rg:10.3f} {rp:10.3f}   {v}")
    print()
    if problems:
        print("需要处理的:", "、".join(f"{n}({k})" for n, k, _, _ in problems))
    else:
        print("没有发现「该有文字却缺失/明显偏淡」的地标。")


if __name__ == "__main__":
    main()

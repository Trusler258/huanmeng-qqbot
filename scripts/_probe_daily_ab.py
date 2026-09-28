#!/usr/bin/env python3
"""日报卡 A/B：同一份固定载荷，分别用 Chromium 与 Pillow 渲染并数值对比。

用途：
  - 建金标：Pillow 版还没写好时，本脚本先把 Chromium 输出落盘 + 打印几何分析，
    供渲染器按"实测坐标"对齐（我不能肉眼看图，只能靠数值）。
  - 回归对比：Pillow 版就绪后，输出整图平均像素差 + 分带差值 + 每个元素带的坐标偏差。

产物都在 /tmp/daily_ab/ 下：
  golden.png（Chromium）  pillow.png（Pillow，若可用）
  report.txt
用法（服务器）: python3 scripts/_probe_daily_ab.py [--analyze-only]
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, "/root/bot")

OUT = Path("/tmp/daily_ab")
FIXTURE = Path("/root/bot/tests/fixtures/daily_html_golden.json")
TPL = Path("/root/bot/data/templates/daily_report.html")


def load_case(key="night"):
    d = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return d[key]


# ══════════════════════════════════════════════════════════
#  几何分析：用行/列剖面把"元素带"量出来
# ══════════════════════════════════════════════════════════
def analyze(im, label):
    import numpy as np
    a = np.asarray(im.convert("RGB")).astype(np.int16)
    H, W = a.shape[:2]
    print(f"\n── {label} 尺寸 {W}×{H} ──")

    # 行剖面：与上一行的平均绝对差 → 突变行就是分隔线/区块边界
    rowd = np.abs(np.diff(a, axis=0)).mean(axis=(1, 2))
    thr = max(1.2, rowd.mean() + rowd.std() * 1.5)
    rows = [i + 1 for i, v in enumerate(rowd) if v > thr]
    merged = []
    for r in rows:
        if merged and r - merged[-1][-1] <= 3:
            merged[-1].append(r)
        else:
            merged.append([r])
    print("  行突变带（y）:", [m[0] for m in merged][:40])

    # 列剖面：找卡片左右边界（背景渐变 vs 卡片半透明底的过渡）
    cold = np.abs(np.diff(a, axis=1)).mean(axis=(0, 2))
    ct = max(1.0, cold.mean() + cold.std() * 2.0)
    cols = [i + 1 for i, v in enumerate(cold) if v > ct]
    print("  列突变（x）:", cols[:20])

    # 逐行平均亮度，用来粗判区域（深底 → 亮块）
    lum = a.mean(axis=2).mean(axis=1)
    print(f"  亮度 min/mean/max = {lum.min():.1f}/{lum.mean():.1f}/{lum.max():.1f}")
    # 每 20 行打一个亮度采样，便于对齐区块
    step = max(1, H // 38)
    samples = ", ".join(f"{y}:{lum[y]:.0f}" for y in range(0, H, step))
    print("  亮度采样(y:值):", samples)
    return {"H": H, "W": W, "row_bands": [m[0] for m in merged], "cols": cols}


# ══════════════════════════════════════════════════════════
#  对比
# ══════════════════════════════════════════════════════════
def compare(g_im, p_im, label):
    import numpy as np
    g = np.asarray(g_im.convert("RGB")).astype(np.float32)
    p = np.asarray(p_im.convert("RGB")).astype(np.float32)
    print(f"\n══ 对比 {label} ══")
    print(f"  尺寸: golden={g.shape[1]}×{g.shape[0]}  pillow={p.shape[1]}×{p.shape[0]}"
          + ("  ⚠️ 不一致！" if g.shape != p.shape else "  ✓"))
    if g.shape != p.shape:
        h = min(g.shape[0], p.shape[0]); w = min(g.shape[1], p.shape[1])
        g, p = g[:h, :w], p[:h, :w]
    d = np.abs(g - p)
    print(f"  整图平均像素差: {d.mean():.2f}   最大 {d.max():.0f}   "
          f">32 的像素占比 {(d.max(axis=2) > 32).mean() * 100:.2f}%")
    # 分带（按高度切成 10 段）
    H = d.shape[0]
    for i in range(10):
        y0, y1 = i * H // 10, (i + 1) * H // 10
        print(f"    y {y0:>4}-{y1:>4}: {d[y0:y1].mean():6.2f}")


async def main():
    only_analyze = "--analyze-only" in sys.argv
    OUT.mkdir(parents=True, exist_ok=True)

    case = load_case("night")
    from core.config import get_config  # noqa: F401
    from services.daily_report_data import build_payload, payload_to_html
    import re
    t = re.search(r"每日 (\d\d:\d\d)", case["html"]).group(1)
    payload = build_payload(case["stats"], case["group_id"], case["date_str"],
                           case["group_name"], report_time=t)
    html = payload_to_html(payload, TPL.read_text(encoding="utf-8"))
    print(f"载荷 HTML {len(html)} 字符（与基准一致: {html == case['html']}）")

    from modules.changelog import render_card_to_image
    gp = await render_card_to_image(html, output_filename="_ab_golden.png", width=720)
    from PIL import Image
    g_im = Image.open(gp).convert("RGB")
    g_im.save(OUT / "golden.png")
    print(f"金标: {gp} → {OUT / 'golden.png'}")
    analyze(g_im, "golden(Chromium)")

    if only_analyze:
        return

    try:
        from services.daily_report_pillow import render_daily_report
    except Exception as e:
        print(f"\n[Pillow] 渲染器不可用（还没写或导入失败）: {type(e).__name__}: {e}")
        return
    p_im = render_daily_report(payload).convert("RGB")
    p_im.save(OUT / "pillow.png")
    analyze(p_im, "pillow")
    compare(g_im, p_im, "night")


asyncio.run(main())

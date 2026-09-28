"""群聊日报卡 Pillow 渲染器 —— 一比一复刻 data/templates/daily_report.html

为什么不用 Chromium：日报是唯一的高频 Chromium 用户（30 天 118 次截图里占 91 次），
单张约 900ms 且浏览器常驻数百 MB；Pillow 约 10~20ms。

⚠️ 三条必须遵守的写法（第一版就是在这三处错的）
  1. **卡片半透明底要逐像素合成**：CSS 是 `rgba(20,18,32,.55)` 叠在（模糊过的）背景上，
     背景有粉色/紫色光晕 → 卡片底色是**渐变**的。取一个平均色会让左上角整体偏暗。
  2. **文字/形状的半透明要按局部底色预混合**：`ImageDraw` 在 RGB 图上画 4 元组会**忽略 alpha**，
     必须用 `_blend_rect/_text` 这类走 numpy 的助手（见 card_base.blend_over 的注释）。
  3. **纵向锚点用实测值，不要按 CSS 盒模型推**：实测行距 40 ≠ CSS 推的 37.8 —— 无头 Chromium
     里行盒比 `font-size×line-height` 高。改动后务必跑 `scripts/_probe_daily_ab.py` 看数值差。

emoji（📊🥇🥈🥉🗣️🤿😴🌙☀️）走贴图：Pillow 对 CBDT 位图字体只接受 109px，
资源由 `scripts/_gen_daily_emoji_assets.py` 从 NotoColorEmoji 抽出（与 Chromium 同源字体），
存在 `data/web_assets/daily_icons/`。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from services.card_base import (
    cjk, mono, linear_grad, radial_glow, conic_hint, to_img,
    rounded_mask, ring_mask, soft_shadow, truncate, grad_text,
)

W = 720
WRAP_L, WRAP_T, WRAP_B = 20, 24, 30
CARD_X0 = WRAP_L
CARD_W = W - 2 * WRAP_L
CARD_R = 16
PAD = 28                     # 卡片内左右内边距

# ── 实测锚点（Chromium 金标 720×962，固定载荷 9 人 / 4 条锐评）──
A_HEAD_BOTTOM = 121.0        # 头部分隔线 y（绝对坐标）
A_SUM_BOTTOM = 195.0         # 摘要区分隔线 y
A_SEC1_CT = 212.0            # 区块1 内容顶（= 摘要底 + 1 + 16）
A_RANK_TOP = 243.0           # 第一条 rrow 的顶
RANK_H, RANK_GAP = 37.0, 3.0
A_SEC2_CT = 584.0            # 区块2 内容顶（随排行条数变化，此值对应 8 条）
A_HOURS_TOP = 615.0          # 24 格顶（对应 8 条）
SEC_LINE_H = 21.0            # 区块标题行盒高（实测 21，不是 13×1.6=20.8）
SEC_PAD_TOP, SEC_PAD_BOT = 16.0, 6.0
FACT_H, FACT_GAP, FACT_PAD_BOT = 34.0, 3.0, 10.0
FOOT_PAD_T, FOOT_PAD_B, FOOT_LINE_H = 14.0, 18.0, 17.0

# ── 配色（逐项取自 CSS）──
_PRIMARY = (236, 72, 153)
_PRIMARY_LIGHT = (244, 114, 182)
_ACCENT = (139, 92, 246)
_ACCENT_LIGHT = (167, 139, 250)
_DEEPSEEK = (86, 134, 254)
_CYAN = (34, 211, 238)
_SUCCESS = (52, 211, 153)
_WARNING = (251, 191, 36)
_GOLD, _SILVER, _BRONZE = (251, 191, 36), (203, 213, 225), (217, 119, 6)

_ICON_DIR = Path(__file__).resolve().parent.parent / "data" / "web_assets" / "daily_icons"
_icon_cache: dict[str, Image.Image] = {}


# ══════════════════════════════════════════════════════════
#  绘制助手（全部走 numpy，保证半透明正确）
# ══════════════════════════════════════════════════════════
def _xyxy(box):
    return (int(round(box[0])), int(round(box[1])),
            int(round(box[2])), int(round(box[3])))


def _clip(dst, box):
    x0, y0, x1, y1 = _xyxy(box)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(dst.width, x1), min(dst.height, y1)
    return x0, y0, x1, y1


def _blend_rect(dst, box, color, alpha, radius: int = 0):
    """把 color 以 alpha 混合到 box 区域（可圆角）。alpha=1 时等价于实色填充。"""
    x0, y0, x1, y1 = _clip(dst, box)
    if x1 <= x0 or y1 <= y0:
        return
    reg = np.asarray(dst.crop((x0, y0, x1, y1)), dtype=np.float32)
    col = np.array(color, dtype=np.float32)[None, None, :]
    if radius > 0:
        m = np.asarray(rounded_mask((x1 - x0, y1 - y0), radius),
                       dtype=np.float32)[..., None] / 255.0
        a = m * alpha
    else:
        a = alpha
    out = reg * (1 - a) + col * a
    dst.paste(Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB"), (x0, y0))


def _blend_grad(dst, box, stops, angle, alpha=1.0):
    """把 CSS linear-gradient 以 alpha 混合到 box（无圆角；要圆角就用 _blend_rect 叠遮罩）"""
    x0, y0, x1, y1 = _clip(dst, box)
    if x1 <= x0 or y1 <= y0:
        return
    g = linear_grad((x1 - x0, y1 - y0), stops, angle)
    reg = np.asarray(dst.crop((x0, y0, x1, y1)), dtype=np.float32)
    a = alpha
    out = reg * (1 - a) + g * a
    dst.paste(Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB"), (x0, y0))


def _blend_grad_rounded(dst, box, stops, angle, alpha, radius):
    x0, y0, x1, y1 = _clip(dst, box)
    if x1 <= x0 or y1 <= y0:
        return
    g = linear_grad((x1 - x0, y1 - y0), stops, angle)
    m = np.asarray(rounded_mask((x1 - x0, y1 - y0), radius),
                   dtype=np.float32)[..., None] / 255.0
    reg = np.asarray(dst.crop((x0, y0, x1, y1)), dtype=np.float32)
    a = m * alpha
    out = reg * (1 - a) + g * a
    dst.paste(Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB"), (x0, y0))


def _text(dst, xy, s, font, color, alpha=1.0, anchor="lm"):
    """画文字。alpha<1 时按**局部底色**预混合（ImageDraw 会忽略 fill 的 alpha）。"""
    if alpha < 1.0:
        base = dst.getpixel((int(np.clip(xy[0], 0, dst.width - 1)),
                             int(np.clip(xy[1], 0, dst.height - 1))))
        color = tuple(int(round(color[i] * alpha + base[i] * (1 - alpha))) for i in range(3))
    ImageDraw.Draw(dst).text(xy, s, font=font, fill=tuple(int(v) for v in color), anchor=anchor)


def _tw(draw, s, font):
    return draw.textlength(s, font=font)


def _icon(key: str) -> Image.Image | None:
    if key not in _icon_cache:
        p = _ICON_DIR / f"{key}.png"
        _icon_cache[key] = Image.open(p).convert("RGBA") if p.exists() else None
    return _icon_cache[key]


def _paste_icon(dst, key, center):
    ic = _icon(key)
    if ic is None:
        return False
    dst.paste(ic, (int(round(center[0] - ic.width / 2)),
                   int(round(center[1] - ic.height / 2))), ic)
    return True


# ══════════════════════════════════════════════════════════
#  等宽字体：必须是 DejaVu Sans Mono
# ══════════════════════════════════════════════════════════
# ⚠️ 模板的 --mono 是 `Consolas,'JetBrains Mono','Courier New',monospace`。
#   服务器上这三个都不装，fontconfig 会把 monospace 解析成 **DejaVu Sans Mono**
#   （实测 `fc-match monospace` → DejaVuSansMono.ttf）。
#   第一版错用了 card_base.mono()（Monocraft，一款像素风字体），
#   于是 TOTAL/SPEAKERS/名次/时长/计数/页脚 全部字形不同 —— 那是像素差最大的一块。
_DEJAVU_MONO = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
]
_dejavu_cache: dict[tuple, ImageFont.FreeTypeFont] = {}


def _mono(size: float, bold: bool = False) -> "ImageFont.FreeTypeFont":
    """DejaVu Sans Mono（= Chromium 的 monospace 实际回退），找不到再用 Monocraft/cjk"""
    from PIL import ImageFont
    key = (round(size, 1), bold)
    if key in _dejavu_cache:
        return _dejavu_cache[key]
    order = _DEJAVU_MONO if bold else list(reversed(_DEJAVU_MONO))
    for path in order:
        if Path(path).exists():
            try:
                f = ImageFont.truetype(path, size)
                _dejavu_cache[key] = f
                return f
            except Exception:
                continue
    from services.card_base import mono as _fallback_mono
    f = _fallback_mono(int(round(size)))
    _dejavu_cache[key] = f
    return f


# ══════════════════════════════════════════════════════════
#  布局解算（自上而下，锚点用实测值）
# ══════════════════════════════════════════════════════════
def _layout(n_rank: int, n_fact: int) -> dict:
    sec1_ct = A_SEC1_CT
    rank_top = A_RANK_TOP
    rank_end = rank_top + max(1, n_rank) * (RANK_H + RANK_GAP) - RANK_GAP
    # 排行列表底(padding-bottom 8) → 区块2 内容顶：用实测差反推，避免 CSS 盒模型误差累积
    sec2_ct = rank_end + 8 + SEC_PAD_TOP + 1.0
    hours_top = sec2_ct + SEC_LINE_H + SEC_PAD_BOT + 4        # sec 行盒 + pad + hours pad-top
    labels_top = hours_top + 24 + 12
    sec3_ct = labels_top + 14.4 + 12 + SEC_PAD_TOP
    facts_top = sec3_ct + SEC_LINE_H + SEC_PAD_BOT + 4
    facts_end = facts_top + max(1, n_fact) * (FACT_H + FACT_GAP) - FACT_GAP
    foot_top = facts_end + FACT_PAD_BOT + 2.6                  # 2.6 = 实测修正
    card_bottom = foot_top + FOOT_PAD_T + FOOT_LINE_H + FOOT_PAD_B
    return {
        "total_h": int(round(card_bottom + WRAP_B)),
        "card_h": int(round(card_bottom - WRAP_T)),
        "sec1_ct": sec1_ct, "rank_top": rank_top, "rank_end": rank_end,
        "sec2_ct": sec2_ct, "hours_top": hours_top, "labels_top": labels_top,
        "sec3_ct": sec3_ct, "facts_top": facts_top, "foot_top": foot_top,
    }


# ══════════════════════════════════════════════════════════
#  背景
# ══════════════════════════════════════════════════════════
def _background(w: int, h: int) -> Image.Image:
    arr = linear_grad((w, h), [(0.0, (7, 6, 15)), (0.45, (13, 11, 26)), (1.0, (8, 10, 22))], 165)
    arr = radial_glow(arr, (w * 0.15, h * -0.10), (w * 0.80, h * 0.55),
                      _PRIMARY, 0.42, fade=0.50)
    arr = radial_glow(arr, (w * 0.88, h * 1.10), (w * 0.70, h * 0.50),
                      _ACCENT, 0.38, fade=0.50)
    arr = radial_glow(arr, (w * 0.50, h * 0.50), (w * 0.65, h * 0.60),
                      _DEEPSEEK, 0.12, fade=0.60)
    arr = radial_glow(arr, (w * 0.05, h * 0.80), (w * 0.50, h * 0.40),
                      _CYAN, 0.14, fade=0.55)
    # conic-gradient(from 200deg at 30% 20%, transparent 0deg, rgba(236,72,153,.08) 60deg,
    #                 transparent 120deg, rgba(139,92,246,.06) 200deg, transparent 280deg)
    #   → 两层不同颜色的低透明度扇形。
    #   ⚠️ 角度目前是**实测试出来的**（conic_hint 用 atan2，0deg=正右；CSS 0deg=正上），
    #      "CSS角度-90" 的推导与实测不一致（推导值让背景差从 2.77 涨到 3.46），
    #      所以照实测值写死。要精调就跑 scripts/_probe_daily_bg.py --compare 看收敛。
    arr = conic_hint(arr, (w * 0.30, h * 0.20), [150], sweep_deg=60,
                     color=_PRIMARY, alpha=0.08)
    arr = conic_hint(arr, (w * 0.30, h * 0.20), [290], sweep_deg=60,
                     color=_ACCENT, alpha=0.06)
    img = to_img(arr)
    # body::before 的细点噪声：3/5/7px 间距、白色、整体 opacity .6
    noise = Image.new("L", (w, h), 0)
    nd = ImageDraw.Draw(noise)
    for step, val in ((3, 6), (5, 5), (7, 4)):
        for yy in range(0, h, step):
            for xx in range(0, w, step):
                nd.point((xx, yy), fill=val)
    white = Image.new("RGB", (w, h), (255, 255, 255))
    return Image.composite(white, img, noise.point(lambda v: int(min(255, v * 1.4))))


# ══════════════════════════════════════════════════════════
#  主渲染
# ══════════════════════════════════════════════════════════
def render_daily_report(payload: dict) -> Image.Image:
    lay = _layout(len(payload["ranking"]), len(payload["facts"]))
    tot_h, card_h = lay["total_h"], lay["card_h"]
    card_box = (CARD_X0, WRAP_T, CARD_X0 + CARD_W, WRAP_T + card_h)

    bg = _background(W, tot_h)
    # backdrop-filter: blur(24px)：卡片下面的背景先糊一遍
    blurred = bg.crop(card_box).filter(ImageFilter.GaussianBlur(24))
    bg.paste(blurred, (card_box[0], card_box[1]))

    # 投影（box-shadow: 0 12px 48px rgba(0,0,0,.65) + 两圈彩色辉光）
    sh, shc = soft_shadow((W, tot_h), card_box, CARD_R, blur=24, alpha=0.62, offset=(0, 12))
    bg.paste(Image.new("RGB", (W, tot_h), shc), (0, 0), sh)
    for col, blurp, alp in ((_PRIMARY, 40, 0.10), (_ACCENT, 22, 0.08)):
        s2, c2 = soft_shadow((W, tot_h), card_box, CARD_R, blur=blurp, alpha=alp, offset=(0, 0))
        bg.paste(Image.new("RGB", (W, tot_h), col), (0, 0), s2)

    # ★ 卡片半透明底：逐像素叠在模糊背景上（不是取平均色）
    _blend_rect(bg, card_box, (20, 18, 32), 0.55, radius=CARD_R)

    _draw_content(bg, payload, lay, card_box)

    # 1px 渐变边框环
    ring = to_img(linear_grad((CARD_W, card_h),
                              [(0.0, _PRIMARY), (0.35, _ACCENT),
                               (0.65, _DEEPSEEK), (1.0, _CYAN)], 135))
    ring.putalpha(ring_mask((CARD_W, card_h), CARD_R, 1).point(lambda v: int(v * 0.55)))
    bg.paste(ring, (card_box[0], card_box[1]), ring)

    # 顶部高光（top:0 left:15% right:15% height:2px）
    hx = card_box[0] + int(CARD_W * 0.15)
    hw = int(CARD_W * 0.70)
    _blend_grad(bg, (hx, card_box[1], hx + hw, card_box[1] + 2),
                [(0.0, (0, 0, 0)), (0.5, (255, 255, 255)), (1.0, (0, 0, 0))], 90, alpha=0.70)
    return bg


def _draw_content(im: Image.Image, p: dict, lay: dict, card_box) -> None:
    """内容坐标：y 用**绝对**坐标（与实测锚点同系），x 用卡片内相对 +1 偏移"""
    ox, oy = card_box[0], card_box[1]
    d = ImageDraw.Draw(im)

    def X(v):
        """卡片内相对 x → 绝对整数像素（PIL 的 paste/box 只吃 int，别返回 float）"""
        return int(round(ox + v))

    def Y(v):
        # ⚠️ 锚点表里存的是**绝对 y**（从金标图顶部量出），不要再加 oy ——
        #    第一版加了，结果头部边界从 121 跑到 145，整卡下移 24px。
        return int(round(v))

    inner_l, inner_r = PAD, CARD_W - PAD

    # ══ 头部 ══
    d.line([(X(0), Y(A_HEAD_BOTTOM)), (X(CARD_W), Y(A_HEAD_BOTTOM))], fill=(60, 57, 74))
    _blend_grad(im, (X(PAD), Y(A_HEAD_BOTTOM), X(CARD_W - PAD), Y(A_HEAD_BOTTOM) + 1),
                [(0.0, (0, 0, 0)), (0.30, _PRIMARY), (0.70, _ACCENT), (1.0, (0, 0, 0))], 90, 0.45)

    logo = (X(inner_l), Y(WRAP_T + 24), X(inner_l + 46), Y(WRAP_T + 24 + 46))
    _blend_grad_rounded(im, logo, [(0.0, _PRIMARY), (1.0, _ACCENT)], 135, 0.35, 12)
    _blend_rect(im, logo, (255, 255, 255), 0.05, radius=12)      # radial 高光的等效提亮
    _paste_icon(im, "logo_chart", ((logo[0] + logo[2]) / 2, (logo[1] + logo[3]) / 2))
    # 1px 边框（用环遮罩叠色，避免 outlined_rectangle 的半透明陷阱）
    lm = ring_mask((46, 46), 12, 1)
    lay_border = Image.new("RGB", (46, 46), _PRIMARY)
    im.paste(lay_border, (logo[0], logo[1]), lm.point(lambda v: int(v * 0.45)))

    tx = logo[2] + 16
    title_cy = Y(WRAP_T + 24 + 16)
    grad_text(im, (tx, title_cy), "幻梦", cjk(20, True), _PRIMARY_LIGHT, _ACCENT, 135, "lm")
    wn = _tw(d, "幻梦", cjk(20, True))
    _text(im, (tx + wn + 10, title_cy), "·", cjk(20, True), (255, 255, 255), 0.40, "lm")
    wd = _tw(d, "·", cjk(20, True))
    _text(im, (tx + wn + 10 + wd + 10, title_cy), "DAILY REPORT", _mono(12),
          (255, 255, 255), 0.72, "lm")
    _text(im, (tx, title_cy + 24), f"{p['group_id']} · {p['date_str']}", _mono(11),
          (255, 255, 255), 0.40, "lm")

    # 右上角群名药丸
    tagf = _mono(11)
    tagw = _tw(d, p["group_name"], tagf) + 24
    tagh = 17.6 + 10
    tx1 = X(inner_r)
    tag = (int(round(tx1 - tagw)), int(round(Y(WRAP_T + 24 + (46 - tagh) / 2))),
           int(round(tx1)), int(round(Y(WRAP_T + 24 + (46 + tagh) / 2))))
    _blend_grad_rounded(im, tag, [(0.0, _PRIMARY), (1.0, _ACCENT)], 135, 0.25, int(tagh / 2))
    tm = ring_mask((tag[2] - tag[0], tag[3] - tag[1]), int(tagh / 2), 1)
    im.paste(Image.new("RGB", (tag[2] - tag[0], tag[3] - tag[1]), _PRIMARY),
             (tag[0], tag[1]), tm.point(lambda v: int(v * 0.45)))
    _text(im, ((tag[0] + tag[2]) / 2, (tag[1] + tag[3]) / 2), p["group_name"], tagf,
          (255, 255, 255), 1.0, "mm")

    # ══ 摘要 ══
    sum_top, sum_bot = Y(A_HEAD_BOTTOM + 1), Y(A_SUM_BOTTOM)
    _blend_rect(im, (X(0), sum_top, X(CARD_W), sum_bot), (20, 18, 32), 0.40)
    d.line([(X(0), sum_bot), (X(CARD_W), sum_bot)], fill=(60, 57, 74))
    cw = (CARD_W - 3) / 4
    items = [(str(p["total"]), "TOTAL", _PRIMARY_LIGHT),
             (str(p["participants"]), "SPEAKERS", _ACCENT_LIGHT),
             (p["peak_label"], "PEAK", _WARNING),
             (p["avg"], "AVG", _SUCCESS)]
    for i, (num, lbl, col) in enumerate(items):
        cx = X(i * (cw + 1) + cw / 2)
        if i:
            d.line([(X(i * (cw + 1) - 1), sum_top), (X(i * (cw + 1) - 1), sum_bot)],
                   fill=(60, 57, 74))
        _text(im, (cx, sum_top + 16 + 13), num, _mono(24, True), col, 1.0, "mm")
        _text(im, (cx, sum_top + 16 + 26 + 4 + 7), lbl, _mono(9.5),
              (255, 255, 255), 0.40, "mm")

    # ══ 区块标题 ══
    def section(ct_abs: float, label: str):
        y = Y(ct_abs)
        _blend_grad_rounded(im, (X(inner_l), y + 4, X(inner_l) + 3, y + 17),
                            [(0.0, _PRIMARY), (1.0, _ACCENT)], 90, 1.0, 2)
        _text(im, (X(inner_l) + 3 + 8, y + SEC_LINE_H / 2), label, cjk(13, True),
              (255, 255, 255), 1.0, "lm")

    section(lay["sec1_ct"], "发言排行")

    # ══ 排行 ══
    rowf = cjk(13)
    rnum_x = inner_l + 12 + 12
    rname_x, rname_w = inner_l + 12 + 24 + 10, 96
    bar_x0 = rname_x + rname_w + 10
    bar_x1 = inner_r - 12 - 40 - 10
    st_gold = [(0.0, _GOLD), (1.0, (253, 230, 138))]
    st_silver = [(0.0, _SILVER), (1.0, (226, 232, 240))]
    st_bronze = [(0.0, _BRONZE), (1.0, _GOLD)]
    st_pink = [(0.0, _PRIMARY), (1.0, _PRIMARY_LIGHT)]
    medals = ("medal_gold", "medal_silver", "medal_bronze")
    for i, r in enumerate(p["ranking"]):
        ry = Y(lay["rank_top"] + i * (RANK_H + RANK_GAP))
        box = (X(inner_l), ry, X(inner_r), ry + RANK_H)
        _blend_rect(im, box, (255, 255, 255), 0.025, radius=9)
        m = ring_mask((int(CARD_W - 2 * PAD), int(RANK_H)), 9, 1)
        im.paste(Image.new("RGB", (int(CARD_W - 2 * PAD), int(RANK_H)), (255, 255, 255)),
                 (box[0], box[1]), m.point(lambda v: int(v * 0.04)))
        cy = ry + RANK_H / 2
        if i < 3:
            _paste_icon(im, medals[i], (X(rnum_x), cy))
        else:
            _text(im, (X(rnum_x), cy), str(i + 1), _mono(13, True), (255, 255, 255), 0.40, "mm")
        _text(im, (X(rname_x), cy), truncate(d, r["name"], rowf, rname_w), rowf,
              (255, 255, 255), 1.0, "lm")
        trk = (X(bar_x0), cy - 8, X(bar_x1), cy + 8)
        _blend_rect(im, trk, (255, 255, 255), 0.05, radius=8)
        fw = (trk[2] - trk[0]) * r["pct"] / 100.0
        if fw >= 2:
            st = (st_gold, st_silver, st_bronze)[i] if i < 3 else st_pink
            _blend_grad_rounded(im, (trk[0], trk[1], trk[0] + fw, trk[3]), st, 90, 1.0, 8)
        _text(im, (X(inner_r - 12), cy), str(r["count"]), _mono(11, True),
              (255, 255, 255), 0.72, "rm")

    section(lay["sec2_ct"], "24h 活跃热力")

    # ══ 24h 热力 ══
    hy = Y(lay["hours_top"])
    cell_w = (CARD_W - 2 * PAD - 23 * 2) / 24
    lv_a = [0.04, 0.22, 0.40, 0.60, 0.80, 1.0]
    for i, h in enumerate(p["hours"]):
        a = lv_a[h["level"]]
        col = _PRIMARY if a >= 1.0 else (255, 255, 255) if a <= 0.05 else _PRIMARY
        x0 = X(PAD + i * (cell_w + 2))
        _blend_rect(im, (x0, hy, x0 + cell_w, hy + 24), col, a, radius=3)

    lab_y = Y(lay["labels_top"]) + 7
    labs = ["00", "03", "06", "09", "12", "15", "18", "21", "23"]
    for i, t in enumerate(labs):
        if i == 0:
            _text(im, (X(PAD), lab_y), t, _mono(9), (255, 255, 255), 0.40, "lm")
        elif i == len(labs) - 1:
            _text(im, (X(CARD_W - PAD), lab_y), t, _mono(9), (255, 255, 255), 0.40, "rm")
        else:
            cx = X(PAD) + (CARD_W - 2 * PAD) * (i + 0.5) / 9
            _text(im, (cx, lab_y), t, _mono(9), (255, 255, 255), 0.40, "mm")

    section(lay["sec3_ct"], "幻梦锐评")

    # ══ 锐评 ══
    for i, f in enumerate(p["facts"]):
        y = Y(lay["facts_top"] + i * (FACT_H + FACT_GAP))
        _blend_rect(im, (X(inner_l - PAD), y, X(inner_r), y + FACT_H), (255, 255, 255), 0.02)
        _blend_rect(im, (X(inner_l - PAD), y, X(inner_l - PAD + 2), y + FACT_H),
                    (255, 255, 255), 0.10)
        cy = y + FACT_H / 2
        ic = _icon(f"fact_{f['icon']}")
        icw = ic.width if ic else 13
        cur = X(inner_l - PAD + 14) + icw / 2
        _paste_icon(im, f"fact_{f['icon']}", (cur, cy))
        cur = X(inner_l - PAD + 14) + icw + 4
        for text, bold in f["runs"]:
            font = cjk(12.5, True) if bold else cjk(12.5)   # CSS .fn-it font-size:12.5px
            if bold:
                _text(im, (cur, cy), text, font, _PRIMARY_LIGHT, 1.0, "lm")
            else:
                _text(im, (cur, cy), text, font, (255, 255, 255), 0.72, "lm")
            cur += _tw(d, text, font)

    # ══ 页脚 ══
    fy = Y(lay["foot_top"])
    d.line([(X(0), fy), (X(CARD_W), fy)], fill=(60, 57, 74))
    foot_h = lay["card_h"] - lay["foot_top"]
    # linear-gradient(180deg, rgba(0,0,0,.15), rgba(0,0,0,.30)) → 逐行递增 alpha
    for i in range(int(foot_h)):
        a = 0.15 + 0.15 * (i / max(1, foot_h - 1))
        _blend_rect(im, (X(0), fy + i, X(CARD_W), fy + i + 1), (0, 0, 0), a)

    cyd = fy + FOOT_PAD_T + FOOT_LINE_H / 2
    _blend_rect(im, (X(inner_l), cyd - 3.5, X(inner_l) + 7, cyd + 3.5), _SUCCESS, 1.0,
                radius=4)
    fx = X(inner_l) + 7 + 8
    for txt, alpha, colr in (("幻梦 Bot", 1.0, _PRIMARY_LIGHT),
                             ("·", 0.40, (255, 255, 255)),
                             (f"每日 {p['report_time']}", 0.40, (255, 255, 255)),
                             ("·", 0.40, (255, 255, 255)),
                             (p["brand"], 0.40, (255, 255, 255))):
        _text(im, (fx, cyd), txt, _mono(10), colr, alpha, "lm")
        fx += _tw(d, txt, _mono(10)) + 8
    grad_text(im, (X(inner_r), cyd), "HUANMENG", _mono(10, True), _PRIMARY, _ACCENT, 135, "rm")


def save_daily_report_card(payload: dict, out_path: str | Path) -> Path:
    """渲染日报卡并保存为 JPEG（bot 走这条）"""
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    render_daily_report(payload).save(p, "JPEG", quality=95)
    return p

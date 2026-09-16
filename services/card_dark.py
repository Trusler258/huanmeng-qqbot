"""
深色卡片基础层（赛博朋克主题）—— 供多张共用同一套 CSS 的卡片复用

适用模板（body 背景与 .card/.head/.foot 结构完全相同）：
    leaderboard_card / daily_report / weather_card / box_card / help_card / md_card
    （sys_card / changelog_card 主题同族但细节不同，可选择性复用）

几何常量全部由 Playwright 读 DOM **实测**得出（见 /tmp/_geo_deep.py），勿按 CSS 估算：
    leaderboard_card 实测：
      body 440 宽 / .wrap padding 20 16 24 16 / .card x=16 y=20 w=408 r=15
      .head h=71（padding 18 22 14 + logo 38 + border 1）
      .title-row h=44（padding 14 22 6 + line-height 24）
      .entry h=54（padding 9 12 + 内容 34 + border 2）r=9
      .foot h=42.2（border 1 + padding 12 22 14 + line-height 15.2）
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from services.card_base import (
    _ROOT, cjk, mono, rounded_mask, ring_mask, blend_over, paste_alpha_rounded,
    grad_text, to_img, linear_grad, radial_glow, radial_circle, truncate,
)

# ══════════════════════════════════════════════════════════
#  配色（逐项取自模板 :root）
# ══════════════════════════════════════════════════════════
C = {
    "primary":       (236, 72, 153),    # --primary       #ec4899
    "primary_light": (244, 114, 182),   # --primary-light #f472b6
    "accent":        (139, 92, 246),    # --accent        #8b5cf6
    "accent_light":  (167, 139, 250),   # --accent-light  #a78bfa
    "deepseek":      (86, 134, 254),    # #5686FE
    "cyan":          (34, 211, 238),    # #22d3ee
    "success":       (52, 211, 153),    # --success       #34d399
    "warning":       (251, 191, 36),    # --warning       #fbbf24
    "gold":          (251, 191, 36),    # --gold          #fbbf24
    "silver":        (203, 213, 225),   # --silver        #cbd5e1
    "bronze":        (217, 119, 6),     # --bronze        #d97706
    "text":          (255, 255, 255),   # --text  #fff
    "t2":            (184, 184, 184),   # rgba(255,255,255,.72) 叠在深底
    "t3":            (102, 102, 102),   # rgba(255,255,255,.40) 叠在深底
    "card_bg":       (20, 18, 32),      # --bg-card rgba(20,18,32,.55)
    "card_a":        0.55,
    "hairline":      (255, 255, 255),
    "hairline_a":    0.10,              # --border rgba(255,255,255,.10)
    "entry_bg":      (255, 255, 255), "entry_bg_a": 0.025,   # rgba(255,255,255,.025)
    "entry_bd":      (255, 255, 255), "entry_bd_a": 0.04,    # rgba(255,255,255,.04)
}

# body 背景：4 处 ellipse 光晕 + conic 高光 + 线性渐变
# linear-gradient(165deg,#07060f 0%,#0d0b1a 45%,#080a16 100%)
BODY_LINEAR = [(0.00, (7, 6, 15)), (0.45, (13, 11, 26)), (1.00, (8, 10, 22))]
BODY_GLOWS = [
    # (位置x%, 位置y%, 半径x, 半径y, 颜色, alpha, fade)
    (0.15, -0.10, 0.80, 0.55, C["primary"], 0.42, 0.50),
    (0.88, 1.10, 0.70, 0.50, C["accent"], 0.38, 0.50),
    (0.50, 0.50, 0.65, 0.60, C["deepseek"], 0.12, 0.60),
    (0.05, 0.80, 0.50, 0.40, C["cyan"], 0.14, 0.55),
]
# conic-gradient(from 200deg at 30% 20%, transparent 0deg, rgba(236,72,153,.08) 60deg,
#                transparent 120deg, rgba(139,92,246,.06) 200deg, transparent 280deg)
BODY_CONIC = {"center": (0.30, 0.20), "from_deg": 200.0,
              "stops": [(0, 0.0, None), (60, 0.08, C["primary"]),
                        (120, 0.0, None), (200, 0.06, C["accent"]), (280, 0.0, None)]}

# ── 实测几何 ──
LH = 1.6


def lh(fs: float) -> float:
    return LH * fs


BODY_W = 440
WRAP_PT, WRAP_PX, WRAP_PB = 20, 16, 24
CARD_R = 15
BD = 1
CARD_X, CARD_Y = WRAP_PX, WRAP_PT
CARD_W = BODY_W - WRAP_PX * 2          # 408

HEAD_PT, HEAD_PX, HEAD_PB = 18, 22, 14
HEAD_H = HEAD_PT + 38 + HEAD_PB + BD   # 71（logo 38 为最高元素）
LOGO_SZ, LOGO_R = 38, 10
HEAD_TITLE_FS = 14
HEAD_SUB_FS = 11
HEAD_TAG_PT, HEAD_TAG_PX, HEAD_TAG_R = 4, 10, 20

TITLE_ROW_PT, TITLE_ROW_PX, TITLE_ROW_PB = 14, 22, 6
TITLE_ROW_FS = 15

LIST_PT, LIST_PX, LIST_PB = 6, 22, 10
ENTRY_PT, ENTRY_PX = 9, 12
ENTRY_R = 9
ENTRY_MB = 3
ENTRY_INNER_H = 34                    # .entry .head 34x34
RANK_W = 30
RANK_FS = 14
NAME_FS = 13
VALUE_FS = 12

FOOT_PT, FOOT_PX, FOOT_PB = 12, 22, 14
FOOT_L_FS, FOOT_R_FS = 9.5, 9


# ══════════════════════════════════════════════════════════
#  背景
# ══════════════════════════════════════════════════════════
def _conic_overlay(canvas: np.ndarray) -> np.ndarray:
    """CSS conic-gradient 近似（扇区亮度调制）"""
    H, W = canvas.shape[:2]
    cx, cy = W * BODY_CONIC["center"][0], H * BODY_CONIC["center"][1]
    ys, xs = np.mgrid[0:H, 0:W]
    # CSS 0deg 指向上，顺时针增大
    ang = (np.degrees(np.arctan2(xs - cx, -(ys - cy))) - BODY_CONIC["from_deg"]) % 360.0
    stops = BODY_CONIC["stops"]
    alphas = np.zeros((H, W), dtype=np.float32)
    colors = np.zeros((H, W, 3), dtype=np.float32)
    for i in range(len(stops) - 1):
        a0, v0, c0 = stops[i]
        a1, v1, c1 = stops[i + 1]
        m = (ang >= a0) & (ang < a1)
        if not m.any():
            continue
        t = ((ang - a0) / max(a1 - a0, 1e-6))[..., None]
        cols = np.array(c0 if c0 else (0, 0, 0), dtype=np.float32)[None, None, :]
        cols1 = np.array(c1 if c1 else (0, 0, 0), dtype=np.float32)[None, None, :]
        # 颜色按端点选（CSS 里两端的色一致或为 transparent）
        cc = np.where(t < 0.5, cols, cols1)
        av = (v0 + (v1 - v0) * t[..., 0]).astype(np.float32)
        colors[m] = cc[m]
        alphas[m] = av[m]
    a3 = alphas[..., None]
    return canvas * (1 - a3) + colors * a3


# 预渲染的深色背景基准图（由 scripts/_gen_card_bg.py 生成，一次性）
# 为什么用它：深色 body 背景 = 4 椭圆光晕 + conic 扇区 + body::before 噪点纹理
#   + 线性渐变，四层叠加；程序化复刻实测在卡外区域偏暗（R 通道低 6，整体差 3.7%）。
#   背景是静态的 → 预渲染一张，按目标尺寸缩放即可（实测按比例缩放成立：
#   同一相对位置在不同 body 高度下颜色差 ≤2）。
_BG_BASE = _ROOT / "data" / "web_assets" / "card_bg" / "dark_440x1200.png"


def body_bg(size, glows=None, linear=None, conic: bool = True) -> Image.Image:
    """深色 body 背景。

    默认走**预渲染基准图**（像素级一致）；只有显式覆盖 glows/linear/conic
    时才回退到程序化生成（供参数化实验/基准图缺失时兜底）。
    """
    Wd, Hd = size
    use_base = (glows is None and linear is None and conic)
    if use_base and _BG_BASE.exists():
        try:
            im = Image.open(_BG_BASE).convert("RGB")
            if im.size != (Wd, Hd):
                # 等比缩放到目标尺寸（CSS 的百分比语义 → 缩放即等价）
                im = im.resize((Wd, Hd), Image.BILINEAR)
            return im
        except Exception:
            pass
    # ── 回退：程序化生成 ──
    bg = linear_grad(size, linear or BODY_LINEAR, 165.0)
    for px, py, rw, rh, color, alpha, fade in (glows or BODY_GLOWS):
        bg = radial_glow(bg, (Wd * px, Hd * py), (Wd * rw, Hd * rh), color, alpha, fade)
    if conic:
        bg = _conic_overlay(bg)
    return to_img(bg)


# ══════════════════════════════════════════════════════════
#  玻璃卡外壳
# ══════════════════════════════════════════════════════════
def glass_card(img: Image.Image, x: int, y: int, w: int, h: int,
               radius: int = CARD_R) -> None:
    """玻璃卡：多重投影 + rgba(20,18,32,.55) 底 + 1px 渐变边框 + 顶部高光线

    ⚠️ 渐变边框用 ring_mask（composite 方向易写反，见 card_base.ring_mask 注释）
    """
    # box-shadow: 0 12px 48px rgba(0,0,0,.65), 0 0 100px rgba(236,72,153,.10),
    #             0 0 40px rgba(139,92,246,.08), inset 0 1px 0 rgba(255,255,255,.12)
    #
    # ⚠️ 外阴影**只画在元素外部**：CSS 的 box-shadow 会被元素自身裁掉。
    #   最初实现直接把模糊遮罩铺满整张图 → 粉/紫光晕糊进卡内，
    #   导致卡内均匀偏亮（entry 空白区 R 通道差 12）。这里减去元素区域。
    inner = Image.new("L", img.size, 0)
    inner.paste(rounded_mask((w, h), radius), (x, y))
    for blur, alpha, col, dy in ((24, 0.65, (0, 0, 0), 12),
                                 (50, 0.10, C["primary"], 0),
                                 (20, 0.08, C["accent"], 0)):
        sh = Image.new("L", img.size, 0)
        sh.paste(rounded_mask((w, h), radius), (x, y + dy))
        _a = alpha
        sh = sh.filter(ImageFilter.GaussianBlur(blur)).point(lambda v, a=_a: int(v * a))
        # 挖掉卡片区域（inner=255 → 取 0；inner=0 → 保留 sh）
        sh = Image.composite(Image.new("L", img.size, 0), sh, inner)
        img.paste(Image.new("RGB", img.size, col), (0, 0), sh)
    # inset 0 1px 0 rgba(255,255,255,.12)：顶部 1px 内高光（跳过圆角段）
    d0 = ImageDraw.Draw(img)
    d0.line([x + radius, y, x + w - radius, y],
            fill=blend_over(img, (x + w // 2, y), (255, 255, 255), 0.12), width=1)

    paste_alpha_rounded(img, Image.new("RGB", (w, h), C["card_bg"]), (x, y),
                        radius, C["card_a"])
    # 1px 渐变边框（135deg 粉→紫→蓝→青）
    border = to_img(linear_grad((w, h),
                                [(0.00, C["primary"]), (0.35, C["accent"]),
                                 (0.65, C["deepseek"]), (1.00, C["cyan"])], 135.0))
    img.paste(border, (x, y), ring_mask((w, h), radius, 1))
    # 卡顶高光线 .card::after（left/right 各 15%）
    grad_line(img, x + int(w * 0.15), y, int(w * 0.70),
              (255, 255, 255), (255, 255, 255), 0.70)


def grad_line(img: Image.Image, x0, y, w, c1, c2, alpha: float = 0.5):
    """两端渐隐的彩色细线（CSS linear-gradient 分隔线）"""
    w = int(w)
    if w <= 2:
        return
    t = np.linspace(0.0, 1.0, w, dtype=np.float32)
    a = np.clip(1.0 - np.abs(t - 0.5) * 2, 0, 1) * alpha
    seg = np.zeros((1, w, 3), dtype=np.float32)
    for k in range(3):
        seg[0, :, k] = c1[k] * (1 - t) + c2[k] * t
    img.paste(to_img(seg), (int(x0), int(y)),
              Image.fromarray((a * 255).astype(np.uint8).reshape(1, w), "L"))


def hairline(d, img: Image.Image, x0, x1, y, alpha=None):
    """1px 分隔线（半透明必须预混合，RGB 图 4 元组 fill 会被忽略 alpha）"""
    d.line([x0, y, x1, y],
           fill=blend_over(img, (x0 + 4, y), C["hairline"],
                           C["hairline_a"] if alpha is None else alpha), width=1)


# ══════════════════════════════════════════════════════════
#  头部（logo 或 icon + 标题 + 副标题 + 右侧 tag/badge）
# ══════════════════════════════════════════════════════════
def draw_head(img: Image.Image, x: int, y: int, w: int,
              logo_text: str = "HM", title_nm: str = "", title_tp: str = "",
              sub: str = "", tag: str = "", logo_icon: str | None = None,
              sep_sl: str = "/") -> int:
    """画 .head，返回其高度（含 1px border-bottom）

    · logo 与 head-ico 都是 38x38（icon 时用 emoji 或图标名）
    · title 是 "nm / tp" 结构：nm 为渐变文字，tp 是浅色大写
    · tag 靠右（.head-tag，圆角胶囊）
    """
    px, py = x + HEAD_PX, y + HEAD_PT
    inner_h = 38
    d = ImageDraw.Draw(img)

    # logo / icon 块
    if logo_icon:
        # head-ico：模板里是 emoji 或图片，这里用中文字体画 emoji
        d.text((px + LOGO_SZ / 2, py + inner_h / 2), logo_icon,
               font=cjk(24), fill=C["text"], anchor="mm")
    else:
        arr = linear_grad((LOGO_SZ, LOGO_SZ),
                          [(0.0, (255, 255, 255)), (1.0, C["primary"])], 135.0)
        base = np.array(C["card_bg"], dtype=np.float32)[None, None, :]
        paste_alpha_rounded(img, to_img(arr * 0.35 + base * 0.65),
                            (px, py), LOGO_R)
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([px, py, px + LOGO_SZ - 1, py + LOGO_SZ - 1], LOGO_R,
                            outline=C["primary"], width=1)
        d.text((px + LOGO_SZ / 2, py + inner_h / 2), logo_text, font=mono(12),
               fill=C["primary_light"], anchor="mm")

    # 标题行（nm 渐变 + sl 分隔 + tp 大写小字）
    tx = px + LOGO_SZ + 12
    lines = 2 if sub else 1
    if lines == 2:
        y_title_c = py + lh(HEAD_TITLE_FS) / 2
        y_sub_c = py + lh(HEAD_TITLE_FS) + lh(HEAD_SUB_FS) / 2
    else:
        y_title_c = py + inner_h / 2
        y_sub_c = 0
    if title_nm:
        grad_text(img, (tx, y_title_c), title_nm, mono(HEAD_TITLE_FS),
                  C["primary_light"], C["accent"], 135.0)
        d = ImageDraw.Draw(img)
        w_nm = d.textlength(title_nm, font=mono(HEAD_TITLE_FS))
        cx = tx + w_nm + 6
        if title_tp:
            d.text((cx, y_title_c), sep_sl, font=mono(HEAD_TITLE_FS),
                   fill=C["t3"], anchor="lm")
            d.text((cx + d.textlength(sep_sl, font=mono(HEAD_TITLE_FS)) + 6, y_title_c),
                   title_tp, font=mono(10), fill=C["t2"], anchor="lm")
    if sub:
        d.text((tx, y_sub_c), truncate(d, sub, cjk(HEAD_SUB_FS), w - (tx - x) - 90),
               font=cjk(HEAD_SUB_FS), fill=C["t3"], anchor="lm")

    # 右侧 tag / badge（圆角胶囊）
    if tag:
        f = mono(10)
        tw = d.textlength(tag, font=f) + HEAD_TAG_PX * 2
        th = 26
        bx = x + w - HEAD_PX - tw
        by = py + (inner_h - th) / 2
        # ⚠️ 数组混合必须在 to_img 之前做（to_img 返回 Image，不能再参与算术）
        cap_arr = linear_grad((int(tw), th),
                              [(0.0, C["primary"]), (1.0, C["accent"])], 135.0)
        base = np.array(C["card_bg"], dtype=np.float32)[None, None, :]
        # rgba(236,72,153,.25) → rgba(139,92,246,.20) 叠在卡底
        t2 = np.linspace(0.25, 0.20, th, dtype=np.float32)[:, None, None]
        cap = to_img(cap_arr * t2 + base * (1 - t2))
        paste_alpha_rounded(img, cap, (int(bx), int(by)), HEAD_TAG_R)
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([bx, by, bx + tw - 1, by + th - 1], HEAD_TAG_R,
                            outline=blend_over(img, (bx + tw / 2, by),
                                               C["primary"], 0.45), width=1)
        d.text((bx + tw / 2, by + th / 2), tag, font=f, fill=C["text"], anchor="mm")

    # border-bottom + 渐变覆盖线
    hb = y + HEAD_H - BD
    hairline(d, img, x, x + w, hb)
    grad_line(img, x + HEAD_PX, hb, w - HEAD_PX * 2, C["primary"], C["accent"], 0.5)
    return HEAD_H


# ══════════════════════════════════════════════════════════
#  条目行（.list > .entry）
# ══════════════════════════════════════════════════════════
def entry_h() -> int:
    """一行 .entry 的高度（含上下 1px border）"""
    return ENTRY_PT + ENTRY_INNER_H + ENTRY_PT + BD * 2


def draw_entry(img: Image.Image, x: int, y: int, w: int,
               rank: int | str, name: str, value: str,
               rank_color=None, name_color=None, value_color=None) -> int:
    """画一行 .entry（名次 + 圆形头像占位 + 名字 + 右侧数值），返回行高

    ⚠️ 模板里 .entry .head 的 <img> 是 display:none，只用 ::before 画一个
       粉紫渐变圆点当头像占位 —— 所以这里也画圆点，不要贴真实头像
    """
    h = entry_h()
    # 背景 rgba(255,255,255,.025) + 边框 rgba(255,255,255,.04)
    bg = blend_over(img, (x + w / 2, y + h / 2), C["entry_bg"], C["entry_bg_a"])
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([x, y, x + w - 1, y + h - 1], ENTRY_R, fill=bg)
    d.rounded_rectangle([x, y, x + w - 1, y + h - 1], ENTRY_R,
                        outline=blend_over(img, (x + w / 2, y), C["entry_bd"],
                                           C["entry_bd_a"]), width=1)
    cy = y + h / 2
    cx = x + ENTRY_PX

    # 名次（前 3 名有金银铜色 + 光晕）
    d.text((cx + RANK_W / 2, cy), str(rank), font=mono(RANK_FS),
           fill=rank_color or C["t3"], anchor="mm")
    cx += RANK_W + 10

    # 头像块 34x34 r9（渐变底 + 粉边 + 中间渐变圆点）
    av = ENTRY_INNER_H
    ab_arr = linear_grad((av, av),
                         [(0.0, C["primary"]), (1.0, C["accent"])], 135.0)
    base = np.array(C["card_bg"], dtype=np.float32)[None, None, :]
    ab = to_img(ab_arr * 0.18 + base * 0.82)
    paste_alpha_rounded(img, ab, (cx, cy - av / 2), 9)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([cx, cy - av / 2, cx + av - 1, cy + av / 2 - 1], 9,
                        outline=blend_over(img, (cx + av / 2, cy - av / 2),
                                           C["primary"], 0.25), width=1)
    dot = to_img(linear_grad((18, 18), [(0.0, C["primary"]), (1.0, C["accent"])], 135.0))
    img.paste(dot, (int(cx + 8), int(cy - 9)),
              rounded_mask((18, 18), 9))
    d = ImageDraw.Draw(img)
    cx += av + 10

    # 数值（右对齐，先量宽）
    fv = mono(VALUE_FS)
    vw = d.textlength(value, font=fv)
    vx = x + w - ENTRY_PX - vw
    # 名字（占剩余空间）
    fname = cjk(NAME_FS)
    avail = vx - 10 - cx
    d.text((cx, cy), truncate(d, name, fname, avail), font=fname,
           fill=name_color or C["text"], anchor="lm")
    d.text((vx, cy), value, font=fv, fill=value_color or C["primary_light"], anchor="lm")
    return h


# ══════════════════════════════════════════════════════════
#  页脚（.foot）
# ══════════════════════════════════════════════════════════
FOOT_H = BD + FOOT_PT + lh(FOOT_L_FS) + FOOT_PB      # ≈42.2


def draw_foot(img: Image.Image, x: int, y: int, w: int,
              left: str = "", left_brand: str = "", right: str = "") -> int:
    """画 .foot：顶部渐隐线 + 半透明黑底 + 左（绿点 + 品牌 + 文本）/ 右（渐变文字）"""
    h = int(round(FOOT_H))
    d = ImageDraw.Draw(img)
    # linear-gradient(180deg, rgba(0,0,0,.15), rgba(0,0,0,.30))
    fa = np.linspace(0.15, 0.30, h, dtype=np.float32)[:, None]
    fmask = Image.fromarray((np.repeat(fa, w, axis=1) * 255).astype(np.uint8), "L")
    img.paste(Image.new("RGB", (w, h), (0, 0, 0)), (x, y), fmask)
    d = ImageDraw.Draw(img)
    hairline(d, img, x, x + w, y)
    grad_line(img, x + FOOT_PX, y, w - FOOT_PX * 2, C["primary"], C["accent"], 0.4)

    cy = y + BD + FOOT_PT + lh(FOOT_L_FS) / 2
    fx = x + FOOT_PX
    d.ellipse([fx, cy - 3, fx + 6, cy + 3], fill=C["success"])
    fx += 6 + 6
    f = mono(FOOT_L_FS)
    for s_, col in ((left_brand, C["primary_light"]), (" · ", C["t3"]),
                    (left, C["t3"])):
        if not s_:
            continue
        d.text((fx, cy), s_, font=f, fill=col, anchor="lm")
        fx += d.textlength(s_, font=f)
    if right:
        grad_text(img, (x + w - FOOT_PX, cy), right, mono(FOOT_R_FS),
                  C["primary"], C["accent"], 135.0, anchor="rm")
    return h


# ══════════════════════════════════════════════════════════
#  便捷：整卡尺寸计算
# ══════════════════════════════════════════════════════════
def card_height(head_h: int, blocks_h: list, foot_h: int) -> int:
    """wrap 内容总高 → 卡高（各块高度直接相加，块内已含 padding/border）"""
    return head_h + sum(blocks_h) + foot_h


def canvas_size(card_h: int) -> tuple:
    return BODY_W, WRAP_PT + card_h + WRAP_PB

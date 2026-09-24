# -*- coding: utf-8 -*-
"""Steam 资料卡 —— Pillow 直绘（1:1 复刻 data/templates/steam_profile_card.html）

为什么不用 Chromium：服务器 i3-2130 上 Chromium 单张卡约 900ms、常驻近 400MB；
Pillow 约 20~80ms、内存几十 MB。（wdsj 战绩卡同一结论，见 services/wdsj_card_pillow.py）

⚠️ 几何/配色全部照抄模板 CSS，勿凭感觉改。改样子：先改 HTML 模板，再同步这里。
   本文件顶部的常量表就是 CSS 的逐条翻译，改哪条在注释里写清来源选择器。

主题：body.ingame（游戏中）时 hero 与强调色转绿 —— Steam 官方 #90ba3c 系
   （抓自 store.steampowered.com/public/shared/css/shared_global.css，该文件里
     #90ba3c 出现 4 次，是频率最高的绿）
"""
from __future__ import annotations

import base64
import io
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from services.card_base import (
    cjk, linear_grad, paste_alpha_rounded, ring_mask, rounded_mask,
    save_image, to_img, truncate,
)

# ══════════════════════════════════════════════════════════
#  布局常量（逐条对应模板 CSS）
# ══════════════════════════════════════════════════════════
W = 1600                 # body{width:1600px}
PAD = 38                 # .topbar/.ident/.stats/.cols 的左右 padding

TOPBAR_H = 58            # .topbar{height:58px}
HERO_H = 118             # .hero{height:118px}
IDENT_PULL = 58          # .ident{margin-top:-58px}

AVATAR = 150             # .avatar{width/height:150px}
AVATAR_GAP = 26          # .ident{gap:26px}
AVATAR_MAIN_PT = 66      # .ident-main{padding-top:66px}
AVATAR_RADIUS = 6        # .avatar{border-radius:6px}
AVATAR_BORDER = 3        # .avatar{border:3px solid #2a475e}

STATS_MT = 24            # .stats{margin-top:24px}
STAT_PAD_X = 22          # .stat{padding:15px 22px}
STAT_PAD_Y = 15

COLS_MT = 24             # .cols{padding:24px 38px 0}
COLS_GAP = 22            # .cols{gap:22px}
SECT_MB = 11             # .sect{margin-bottom:11px}
SECT_BAR_W = 3           # .sect i{width:3px;height:15px}
SECT_BAR_H = 15

ROW_PAD_X = 15           # .row{padding:10px 15px}
ROW_PAD_Y = 10
ROW_GAP = 14             # .row{gap:14px}
ROW_IMG_W = 124          # .row img{width:124px;height:58px}
ROW_IMG_H = 58
RANK_W = 24              # .row .rk{flex:0 0 24px}
BAR_H = 4                # .bar{height:4px;margin-top:7px}
BAR_MT = 7

SUB_MT = 11              # .subtotal{margin-top:11px}
SUB_PAD_X = 16
SUB_PAD_Y = 10
LVL_MT = 11              # .lvlcard{margin-top:11px}
LVL_PAD_X = 16
LVL_PAD_Y = 13
LVL_TRACK_H = 6          # .lvlcard .track{height:6px}
LIB_MT = 11              # 左栏底部「游戏库概览」的上边距（自绘块，非模板元素）

ACH_MT = 22              # .achwrap{margin:22px 38px 0}
ACH_GAP = 14             # .achlist{gap:14px}
ACH_PAD_X = 14           # .ach{padding:11px 14px}
ACH_PAD_Y = 11
ACH_ICO = 38             # .ach .ico{width/height:38px}

FOOT_MT = 22             # .foot{margin:22px 38px 0}
FOOT_PT = 14             # .foot{padding:14px 0 22px}
FOOT_PB = 22

# ══════════════════════════════════════════════════════════
#  配色（模板 :root 的翻译）
# ══════════════════════════════════════════════════════════
BG_1 = (27, 40, 56)      # #1b2838
BG_2 = (10, 20, 29)      # #0a141d
TOPBAR = (23, 26, 33)    # #171a21
BLUE = (103, 193, 245)   # #67c1f5
BLUE_D = (65, 122, 155)  # #417a9b
TXT = (198, 212, 223)    # #c6d4df
TXT_2 = (199, 213, 224)  # #c7d5e0
WHITE = (255, 255, 255)
DIM = (143, 152, 160)    # #8f98a0
LINE_A = 0.07            # --line: rgba(255,255,255,.07)
ROW_BG = (0, 0, 0)
ROW_BG_A = 0.20          # --rowbg
ROW_BG2_A = 0.10         # --rowbg2
IMG_BG = (14, 20, 27)    # #0e141b

# 「游戏中」绿（Steam 官方 #90ba3c 系）
GREEN = (144, 186, 60)   # #90ba3c

# hero 渐变（.hero{background:linear-gradient(-60deg,...)}）
HERO_BLUE = [(0.0, (103, 193, 245)), (0.55, (65, 122, 155)), (1.0, (42, 71, 94))]
HERO_GREEN = [(0.0, (163, 207, 6)), (0.55, (92, 126, 16)), (1.0, (37, 49, 15))]
HERO_ANGLE = -60.0       # CSS 角度语义与 linear_grad 一致（0=上）

# ── 字体 ──────────────────────────────────────────────────
_ASSETS = Path(__file__).resolve().parent.parent / "data" / "steam_assets" / "font"
_NUM_TTF = _ASSETS / "harmonyos_medium_latin.ttf"
_font_cache: dict = {}


def _num(size: int, bold: bool = False):
    """数字/拉丁：HarmonyOS Sans（模板里的 --HMSans）。

    只有 Medium 一个字重，bold 请求时用 stroke 加粗模拟（Pillow 无 fake-bold）。
    """
    key = ("num", int(size), bold)
    if key in _font_cache:
        return _font_cache[key]
    from PIL import ImageFont
    if _NUM_TTF.exists():
        f = ImageFont.truetype(str(_NUM_TTF), int(size))
    else:
        f = cjk(size, bold)
    _font_cache[key] = f
    return f


def _txt(size: int, bold: bool = False):
    """正文/中文：思源黑体（模板 font-family 的 CJK 回退项）"""
    return cjk(size, bold)


def _lh(font) -> int:
    a, d = font.getmetrics()
    return a + d


# ══════════════════════════════════════════════════════════
#  绘制原语（RGB 图上半透明必须预混合 —— 直接画 4 元组会被忽略 alpha）
# ══════════════════════════════════════════════════════════
def _panel(img: Image.Image, x, y, w, h, radius: int, color, alpha: float):
    """半透明圆角填充"""
    w, h = int(round(w)), int(round(h))
    if w <= 0 or h <= 0:
        return
    ov = Image.new("RGB", (w, h), color)
    mask = rounded_mask((w, h), radius)
    if alpha < 1.0:
        mask = mask.point(lambda v: int(v * alpha))
    img.paste(ov, (int(round(x)), int(round(y))), mask)


def _ring(img: Image.Image, x, y, w, h, radius: int, color, alpha: float,
          width: int = 1):
    """半透明圆角描边"""
    w, h = int(round(w)), int(round(h))
    if w <= 0 or h <= 0:
        return
    ov = Image.new("RGB", (w, h), color)
    mask = ring_mask((w, h), radius, width)
    if alpha < 1.0:
        mask = mask.point(lambda v: int(v * alpha))
    img.paste(ov, (int(round(x)), int(round(y))), mask)


def _hgrad(img: Image.Image, x, y, w, h, c1, c2):
    """水平渐变条（CSS linear-gradient(90deg, c1, c2)）"""
    w, h = int(round(w)), int(round(h))
    if w <= 0 or h <= 0:
        return
    bar = to_img(linear_grad((w, h), [(0.0, c1), (1.0, c2)], 90.0))
    img.paste(bar, (int(round(x)), int(round(y))),
              rounded_mask((w, h), max(0, h // 2)))


def _vgrad(img, x, y, w, h, stops):
    """垂直渐变块（CSS linear-gradient(180deg, ...)）"""
    w, h = int(round(w)), int(round(h))
    if w <= 0 or h <= 0:
        return
    img.paste(to_img(linear_grad((w, h), stops, 180.0)), (int(round(x)), int(round(y))))


_uri_cache: dict = {}


def _img_from_uri(uri: str):
    """data:image/...;base64,xxx → PIL.Image（带缓存）

    同一份 payload 里图片只解码一次；bot 进程内多次渲染同一账号时（图没换）
    也能直接命中 —— base64 解码 + LANCZOS 缩放在 16 张图上原本占 100ms+。
    """
    if not uri or not uri.startswith("data:"):
        return None
    key = (len(uri), uri[:64])
    hit = _uri_cache.get(key)
    if hit is not None:
        return hit
    try:
        b64 = uri.split(",", 1)[1]
        im = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
    except Exception:
        return None
    if len(_uri_cache) > 96:
        _uri_cache.clear()
    _uri_cache[key] = im
    return im


def _fill_cover(im: Image.Image, w: int, h: int) -> Image.Image:
    """object-fit:cover —— 等比缩放后居中裁切"""
    if im is None:
        return None
    sw, sh = im.size
    if sw <= 0 or sh <= 0:
        return None
    scale = max(w / sw, h / sh)
    nw, nh = max(1, int(sw * scale + 0.5)), max(1, int(sh * scale + 0.5))
    im = im.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - w) // 2, (nh - h) // 2
    return im.crop((left, top, left + w, top + h))


_thumb_cache: dict = {}


def _thumb(uri: str, w: int, h: int):
    """data URI → 已按 cover 裁到目标尺寸的小图（带缓存，省掉重复解码与缩放）"""
    if not uri:
        return None
    key = (len(uri), uri[:64], w, h)
    hit = _thumb_cache.get(key)
    if hit is not None:
        return hit
    im = _img_from_uri(uri)
    if im is None:
        return None
    out = _fill_cover(im, w, h)
    if len(_thumb_cache) > 96:
        _thumb_cache.clear()
    _thumb_cache[key] = out
    return out


def _fmt_int(n) -> str:
    try:
        return "{:,}".format(int(n))
    except Exception:
        return "—"


def _fmt_h(n) -> str:
    try:
        return "{:,.1f}".format(float(n))
    except Exception:
        return "—"


def _hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(rgb)


# ══════════════════════════════════════════════════════════
#  主渲染
# ══════════════════════════════════════════════════════════
def render_steam_card(payload: dict, root=None) -> Image.Image:
    """渲染整张资料卡（返回 RGB Image）"""
    t0 = time.perf_counter()
    P = payload.get("profile") or {}
    S = payload.get("stats") or {}
    recent = payload.get("recent") or []
    top = payload.get("top") or []
    achs = payload.get("achievements") or []

    game_now = (P.get("game_now") or "").strip()
    ingame = bool(game_now) or P.get("state_kind") == "ingame"
    online = bool(P.get("online"))

    acc = GREEN if ingame else BLUE                 # 强调色
    acc_d = (92, 126, 16) if ingame else BLUE_D     # 深强调色
    hero_stops = HERO_GREEN if ingame else HERO_BLUE

    # ── 先算高度（布局是线性的，逐段累加）──
    col_w = (W - PAD * 2 - COLS_GAP) // 2

    row_h = ROW_PAD_Y * 2 + ROW_IMG_H                       # 78
    sect_h = max(SECT_BAR_H, _lh(_txt(16.5, True))) + SECT_MB

    l_rows_h = len(recent) * row_h + 2                      # 2 = 上下边框
    sub_h = SUB_PAD_Y * 2 + _lh(_txt(14)) + 2
    lvl_h = LVL_PAD_Y * 2 + _lh(_txt(13.5)) + 9 + LVL_TRACK_H + 7 + _lh(_txt(12.5)) + 2
    l_fixed = sect_h + l_rows_h + SUB_MT + sub_h + LVL_MT + lvl_h

    r_rows_h = len(top) * row_h + 2
    r_col_h = sect_h + r_rows_h

    # 左栏底部「游戏库概览」：高度自适应，把左栏撑到与右栏齐平（3 行均分）
    lib_h = max(96.0, r_col_h - l_fixed - LIB_MT)
    l_col_h = l_fixed + LIB_MT + lib_h

    cols_h = max(l_col_h, r_col_h)

    ach_h = ACH_PAD_Y * 2 + max(ACH_ICO, _lh(_txt(14.5)) + 3 + _lh(_txt(12))) + 2
    foot_h = FOOT_PT + _lh(_txt(12.5)) + FOOT_PB + 1

    y_topbar = 0
    y_hero = y_topbar + TOPBAR_H
    y_ident = y_hero + HERO_H - IDENT_PULL
    y_stats = y_ident + AVATAR + STATS_MT
    stats_h = STAT_PAD_Y * 2 + _lh(_txt(13.5)) + 5 + _lh(_num(27, True)) + 2
    y_cols = y_stats + stats_h + COLS_MT
    y_ach = y_cols + cols_h + ACH_MT
    y_foot = y_ach + sect_h + ach_h + FOOT_MT
    H = int(y_foot + foot_h)

    # ── 画布 ──
    # 背景是 180deg 纯垂直渐变 → 只算 1 像素宽再横向拉伸（省掉 230 万像素的插值）
    img = to_img(linear_grad((1, H), [(0.0, BG_1), (1.0, BG_2)], 180.0)).resize(
        (W, H), Image.BILINEAR)
    d = ImageDraw.Draw(img)

    # ────────── 顶栏 ──────────
    _panel(img, 0, 0, W, TOPBAR_H, 0, TOPBAR, 1.0)
    d = ImageDraw.Draw(img)
    d.line([(0, TOPBAR_H - 1), (W, TOPBAR_H - 1)], fill=(0, 0, 0), width=1)

    fb = _num(23, True)
    d.text((PAD, TOPBAR_H / 2), "STEAM", font=fb, fill=WHITE, anchor="lm")
    bx = PAD + d.textlength("STEAM", font=fb)
    d.text((bx, TOPBAR_H / 2), ".", font=fb, fill=acc, anchor="lm")

    # 导航
    nav_x = bx + d.textlength(".", font=fb) + 22
    fn = _txt(14.5)
    for i, (_t, _hl) in enumerate((("个人资料", True), ("游戏库", False), ("成就", False))):
        d.text((nav_x, TOPBAR_H / 2), _t, font=fn, fill=(acc if _hl else DIM), anchor="lm")
        nav_x += d.textlength(_t, font=fn) + 20

    # 右上状态（游戏中时 hero 里已有"正在玩 XXX"，这里只写"游戏中"，避免同一个信息出现三次）
    st_txt = P.get("state_text") or ("在线" if online else "离线")
    top_txt = "游戏中" if ingame else st_txt
    badge_txt = "游戏中" if ingame else st_txt
    fs = _txt(13.5)
    sw = d.textlength(top_txt, font=fs)
    dot_c = acc if (online or ingame) else DIM
    dx = W - PAD - sw - 9 - 8
    d.ellipse([dx, TOPBAR_H / 2 - 4, dx + 8, TOPBAR_H / 2 + 4], fill=dot_c)
    d.text((W - PAD, TOPBAR_H / 2), top_txt, font=fs, fill=DIM, anchor="rm")

    # ────────── Hero ──────────
    # 底色：.hero{background:linear-gradient(-60deg, ...)}
    img.paste(to_img(linear_grad((W, HERO_H), hero_stops, HERO_ANGLE)), (0, y_hero))
    # ::after 斜纹（repeating-linear-gradient(-60deg, rgba(255,255,255,.07) 0 2px, transparent 2px 16px)）
    hatch = Image.new("RGB", (W, HERO_H), (0, 0, 0))
    hd = ImageDraw.Draw(hatch)
    span = HERO_H * 2
    for i in range(-span, W + span, 16):
        hd.line([(i, 0), (i + span, HERO_H)], fill=(255, 255, 255), width=2)
    img.paste(hatch, (0, y_hero),
              hatch.convert("L").point(lambda v: int(v * 0.07)))
    # ::before 压暗（linear-gradient(180deg, rgba(10,20,29,.15) → .65)）
    dark = Image.new("RGB", (W, HERO_H), (10, 20, 29))
    dmask = Image.new("L", (W, HERO_H))
    dd = ImageDraw.Draw(dmask)
    for row in range(HERO_H):
        t = row / max(1, HERO_H - 1)
        dd.line([(0, row), (W, row)], fill=int(255 * (0.15 + 0.50 * t)))
    img.paste(dark, (0, y_hero), dmask)
    d = ImageDraw.Draw(img)

    # 游戏中：hero 右侧「正在玩 XXX」
    if game_now:
        ft = _txt(15)
        label = "正在玩 " + game_now
        lw = d.textlength(label, font=ft)
        bw, bh = lw + 26 + 17, 34
        bx = W - PAD - bw
        by = y_hero + 22
        _panel(img, bx, by, bw, bh, 3, (0, 0, 0), 0.28)
        _ring(img, bx, by, bw, bh, 3, WHITE, 0.16)
        d = ImageDraw.Draw(img)
        d.ellipse([bx + 13, by + bh / 2 - 3.5, bx + 20, by + bh / 2 + 3.5],
                  fill=(214, 245, 106))
        d.text((bx + 26, by + bh / 2), label, font=ft, fill=(235, 245, 240), anchor="lm")

    # ────────── 资料区（压在 hero 上）──────────
    av = AVATAR
    ax, ay = PAD, y_ident
    avimg = None
    ap = P.get("avatar_path")
    if ap and Path(ap).exists():
        try:
            avimg = Image.open(ap).convert("RGB")
        except Exception:
            avimg = None
    if avimg is None:
        avimg = _img_from_uri(P.get("avatar") or "")
    # 头像投影 —— 只在头像周边的小区域做（全图尺寸做高斯模糊是纯浪费，
    # 1600x1426 上实测它能占掉渲染时间的四分之一）
    sp = 40
    sx0, sy0 = ax - sp, ay - sp
    sw2, sh2 = av + sp * 2, av + sp * 2 + 8
    sh = Image.new("L", (sw2, sh2), 0)
    sh.paste(rounded_mask((av, av), AVATAR_RADIUS), (sp, sp + 6))
    sh = sh.filter(ImageFilter.GaussianBlur(11)).point(lambda v: int(v * 0.6))
    img.paste(Image.new("RGB", (sw2, sh2), (0, 0, 0)), (sx0, sy0), sh)
    # 头像本体（cover 裁切 + 圆角）
    if avimg is not None:
        cov = _fill_cover(avimg, av, av) or avimg.resize((av, av), Image.LANCZOS)
        paste_alpha_rounded(img, cov, (ax, ay), AVATAR_RADIUS)
    else:
        _panel(img, ax, ay, av, av, AVATAR_RADIUS, IMG_BG, 1.0)
    # 3px 边框（.avatar{border:3px solid #2a475e}）
    _ring(img, ax, ay, av, av, AVATAR_RADIUS,
          (92, 126, 16) if ingame else (42, 71, 94), 1.0, AVATAR_BORDER)
    d = ImageDraw.Draw(img)

    mx = ax + av + AVATAR_GAP
    cy = ay + AVATAR_MAIN_PT

    # 昵称
    fnm = _txt(36, True)
    name = truncate(d, P.get("name") or "—", fnm, 720)
    d.text((mx, cy + _lh(fnm) / 2), name, font=fnm, fill=WHITE, anchor="lm")
    nw = d.textlength(name, font=fnm)

    # 状态徽章（.state）
    fst = _txt(15)
    bw = d.textlength(badge_txt, font=fst) + 12 * 2 + 8 + 8
    bh = 26
    sx, sy = mx + nw + 16, cy + _lh(fnm) / 2 - bh / 2 + 4
    if ingame:
        _panel(img, sx, sy, bw, bh, 3, GREEN, 0.10)
        _ring(img, sx, sy, bw, bh, 3, GREEN, 0.35)
        sc = GREEN
    elif online:
        _panel(img, sx, sy, bw, bh, 3, BLUE, 0.10)
        _ring(img, sx, sy, bw, bh, 3, BLUE, 0.32)
        sc = BLUE
    else:
        _panel(img, sx, sy, bw, bh, 3, DIM, 0.10)
        _ring(img, sx, sy, bw, bh, 3, DIM, 0.30)
        sc = DIM
    d = ImageDraw.Draw(img)
    d.ellipse([sx + 12, sy + bh / 2 - 4, sx + 20, sy + bh / 2 + 4], fill=sc)
    d.text((sx + 12 + 8 + 8, sy + bh / 2), badge_txt, font=fst, fill=sc, anchor="lm")

    # meta 行（Steam 等级 / 注册于 / SteamID）
    my = cy + _lh(fnm) + 12
    fm = _txt(15)
    parts = [("Steam 等级 ", str(P.get("level")) if P.get("level") is not None else "—"),
             ("注册于 ", P.get("created") or "—"),
             ("", P.get("steamid") or "—")]
    px = mx
    for i, (pre, val) in enumerate(parts):
        if i:
            d.text((px, my + _lh(fm) / 2), "|", font=fm, fill=(90, 98, 105), anchor="lm")
            px += d.textlength("|", font=fm) + 12
        if pre:
            d.text((px, my + _lh(fm) / 2), pre, font=fm, fill=DIM, anchor="lm")
            px += d.textlength(pre, font=fm)
        fv = _num(15) if (i == 2 or (i == 0 and P.get("level") is not None)) else fm
        d.text((px, my + _lh(fm) / 2), val, font=fv, fill=TXT_2, anchor="lm")
        px += d.textlength(val, font=fv) + 12

    # ────────── 统计条（6 格）──────────
    st_x, st_y = PAD, y_stats
    st_w = W - PAD * 2
    _panel(img, st_x, st_y, st_w, stats_h, 4, ROW_BG, ROW_BG_A)
    _ring(img, st_x, st_y, st_w, stats_h, 4, WHITE, LINE_A)
    d = ImageDraw.Draw(img)

    cells = [
        ("游戏数量", _fmt_int(S.get("games")), "", False),
        ("累计时长", _fmt_int(S.get("hours")), "小时", False),
        ("成就", ("%s / %s" % (S.get("ach_got"), S.get("ach_total")))
         if S.get("ach_total") else "—", "", True),
        ("徽章", _fmt_int(S["badges"]) if S.get("badges") is not None else "—", "", False),
        ("好友", _fmt_int(S["friends"]) if S.get("friends") is not None else "—", "", False),
        ("在线状态", badge_txt, "", False),
    ]
    cw = st_w / len(cells)
    for i, (k, v, unit, hl) in enumerate(cells):
        cx = st_x + cw * i
        if i:
            # .stat+.stat::before 竖分隔线
            seg = int(stats_h * 0.68)
            img.paste(Image.new("RGB", (1, seg), (255, 255, 255)),
                      (int(cx), int(st_y + stats_h * 0.16)),
                      Image.new("L", (1, seg), int(255 * LINE_A)))
        fk = _txt(13.5)
        d.text((cx + STAT_PAD_X, st_y + STAT_PAD_Y), k, font=fk, fill=DIM, anchor="lt")
        big = (k == "在线状态")
        fv = _txt(22, True) if big else _num(27, True)
        vy = st_y + STAT_PAD_Y + _lh(fk) + 5
        col = acc if hl else WHITE
        if big:
            d.text((cx + STAT_PAD_X, vy + _lh(fv) / 2), v, font=fv, fill=col, anchor="lm")
        else:
            d.text((cx + STAT_PAD_X, vy), v, font=fv, fill=col, anchor="lt")
            if unit:
                ux = cx + STAT_PAD_X + d.textlength(v, font=fv) + 5
                fu = _txt(14)
                d.text((ux, vy + _lh(fv) - _lh(fu)), unit, font=fu, fill=DIM, anchor="lt")

    # ────────── 两栏 ──────────
    def _sect(x, y, w, title, right_note=""):
        _panel(img, x, y + (max(SECT_BAR_H, _lh(_txt(16.5, True))) - SECT_BAR_H) / 2,
               SECT_BAR_W, SECT_BAR_H, 2, acc, 1.0)
        dd = ImageDraw.Draw(img)
        fh = _txt(16.5, True)
        dd.text((x + SECT_BAR_W + 10, y + max(SECT_BAR_H, _lh(fh)) / 2), title,
                font=fh, fill=WHITE, anchor="lm")
        if right_note:
            fe = _txt(13)
            dd.text((x + w, y + max(SECT_BAR_H, _lh(fh)) / 2), right_note,
                    font=fe, fill=DIM, anchor="rm")

    def _rows(x, y, w, items, rank: bool, note: str):
        """一组行（.rows）。

        整块先画在 RGBA 图层上、最后按圆角合成 —— 否则交替行背景是矩形，
        会盖掉容器的 4px 圆角（图片同理）。
        """
        n = len(items)
        h = int((n * row_h + 2) if n else (26 * 2 + _lh(_txt(14.5)) + 2))
        blk = Image.new("RGBA", (int(w), h), (0, 0, 0, 0))
        bd = ImageDraw.Draw(blk)

        # 底色 --rowbg
        bd.rectangle([0, 0, int(w) - 1, h - 1], fill=ROW_BG + (int(255 * ROW_BG_A),))

        if not items:
            bd.text((w / 2, h / 2), note, font=_txt(14.5), fill=DIM + (255,), anchor="mm")
        else:
            mx_h = max([float(g.get("hours") or 0) for g in items] + [1.0])
            for i, g in enumerate(items):
                ry = i * row_h
                # .row:nth-child(even){background:rgba(0,0,0,.10)} —— 是替换不是叠加
                if i % 2 == 1:
                    bd.rectangle([0, ry, int(w) - 1, ry + row_h - 1],
                                 fill=ROW_BG + (int(255 * ROW_BG2_A),))
                if i:
                    bd.line([(1, ry), (int(w) - 2, ry)], fill=(255, 255, 255, 12), width=1)

                px = ROW_PAD_X
                if rank:
                    bd.text((px + RANK_W, ry + row_h / 2), str(i + 1),
                            font=_num(14, True), fill=acc + (255,), anchor="rm")
                    px += RANK_W + ROW_GAP
                # 横幅图（object-fit:cover）
                cover = _thumb(g.get("img") or "", ROW_IMG_W, ROW_IMG_H)
                if cover is not None:
                    paste_alpha_rounded(blk, cover, (px, ry + ROW_PAD_Y), 3)
                else:
                    bd.rounded_rectangle(
                        [px, ry + ROW_PAD_Y, px + ROW_IMG_W - 1, ry + ROW_PAD_Y + ROW_IMG_H - 1],
                        3, fill=IMG_BG + (255,))
                px += ROW_IMG_W + ROW_GAP

                # 右侧时数（.row-h）
                hrs = float(g.get("hours") or 0)
                fh = _num(19, True)
                hs = _fmt_h(hrs)
                hw = max(bd.textlength(hs, font=fh), bd.textlength("小时", font=_txt(12)))
                rgt = int(w) - ROW_PAD_X
                bd.text((rgt, ry + ROW_PAD_Y + 2), hs, font=fh, fill=acc + (255,), anchor="rt")
                bd.text((rgt, ry + ROW_PAD_Y + 2 + _lh(fh) + 3), "小时",
                        font=_txt(12), fill=DIM + (255,), anchor="rt")

                # 名称 + 比例条（.row-main）
                main_w = rgt - hw - ROW_GAP - px
                if main_w < 40:
                    main_w = 40
                fg = _txt(15.5)
                nm = truncate(bd, g.get("name") or "?", fg, main_w)
                bd.text((px, ry + ROW_PAD_Y + 2), nm, font=fg, fill=WHITE + (255,), anchor="lt")
                bar_y = ry + ROW_PAD_Y + 2 + _lh(fg) + BAR_MT
                bd.rounded_rectangle([px, bar_y, px + main_w - 1, bar_y + BAR_H - 1], 2,
                                     fill=(255, 255, 255, 20))
                pct = max(0.0, min(1.0, hrs / mx_h)) if mx_h > 0 else 0.0
                if pct > 0:
                    bw = max(2.0, main_w * pct)
                    bar = to_img(linear_grad((int(bw), BAR_H), [(0.0, acc_d), (1.0, acc)], 90.0))
                    blk.paste(bar, (int(px), int(bar_y)),
                              rounded_mask((int(bw), BAR_H), 2))

        base = img.crop((int(x), int(y), int(x + w), int(y + h))).convert("RGBA")
        img.paste(Image.alpha_composite(base, blk).convert("RGB"),
                  (int(x), int(y)), rounded_mask((int(w), h), 4))
        _ring(img, x, y, w, h, 4, WHITE, LINE_A)
        return h

    lx, rx2 = PAD, PAD + col_w + COLS_GAP
    _sect(lx, y_cols, col_w, "最近两周",
          ("%d 款" % len(recent)) if recent else "")
    _rows(lx, y_cols + sect_h, col_w, recent, False, "最近两周没有游玩记录")

    # 小计（.subtotal）
    sub_y = y_cols + sect_h + l_rows_h + SUB_MT
    if recent:
        _panel(img, lx, sub_y, col_w, sub_h, 4, acc, 0.07)
        _ring(img, lx, sub_y, col_w, sub_h, 4, acc, 0.18)
        d = ImageDraw.Draw(img)
        f1 = _txt(14)
        d.text((lx + SUB_PAD_X, sub_y + sub_h / 2), "两周合计", font=f1,
               fill=TXT_2, anchor="lm")
        f2 = _num(18, True)
        tail = _fmt_h(payload.get("recent_sum")) + " 小时"
        d.text((lx + col_w - SUB_PAD_X, sub_y + sub_h / 2), tail, font=f2,
               fill=acc, anchor="rm")

    # 等级进度卡
    lv_y = sub_y + (sub_h + LVL_MT if recent else 0)
    if not recent:
        lv_y = y_cols + sect_h + l_rows_h + LVL_MT
    _panel(img, lx, lv_y, col_w, lvl_h, 4, ROW_BG, ROW_BG_A)
    _ring(img, lx, lv_y, col_w, lvl_h, 4, WHITE, LINE_A)
    d = ImageDraw.Draw(img)
    fk2 = _txt(13.5)
    xp = S.get("xp")
    if xp is not None:
        d.text((lx + LVL_PAD_X, lv_y + LVL_PAD_Y), "Steam 等级进度", font=fk2,
               fill=DIM, anchor="lt")
        d.text((lx + col_w - LVL_PAD_X, lv_y + LVL_PAD_Y), _fmt_int(xp) + " XP",
               font=_num(15, True), fill=TXT_2, anchor="rt")
        ty = lv_y + LVL_PAD_Y + _lh(fk2) + 9
        _panel(img, lx + LVL_PAD_X, ty, col_w - LVL_PAD_X * 2, LVL_TRACK_H, 3,
               WHITE, 0.08)
        cur, need = S.get("xp_cur"), S.get("xp_need")
        if isinstance(cur, int) and isinstance(need, int) and need > 0:
            pct = max(0.01, min(1.0, cur / need))
            _hgrad(img, lx + LVL_PAD_X, ty, (col_w - LVL_PAD_X * 2) * pct,
                   LVL_TRACK_H, acc_d, acc)
            note = "距下一级还需 %s XP" % _fmt_int(max(0, need - cur))
        else:
            pct = 1.0
            _hgrad(img, lx + LVL_PAD_X, ty, col_w - LVL_PAD_X * 2, LVL_TRACK_H,
                   acc_d, acc)
            note = "已达最高等级"
    else:
        note = "等级数据不可用"
    d.text((lx + LVL_PAD_X, lv_y + LVL_PAD_Y + _lh(fk2) + 9 + LVL_TRACK_H + 7),
           note, font=_txt(12.5), fill=DIM, anchor="lt")

    # ── 游戏库概览（左栏底部，撑满剩餘高度）──
    lib_y = lv_y + lvl_h + LIB_MT
    _panel(img, lx, lib_y, col_w, lib_h, 4, ROW_BG, ROW_BG_A)
    _ring(img, lx, lib_y, col_w, lib_h, 4, WHITE, LINE_A)
    d = ImageDraw.Draw(img)
    lib = payload.get("library") or {}
    sitems = [
        ("玩过的游戏", "%s / %s 款" % (_fmt_int(lib.get("played")), _fmt_int(S.get("games")))),
        ("平均每款时长", _fmt_h(lib.get("avg")) + " 小时"),
        ("最常玩占比", "%.1f%%" % float(lib.get("top_share") or 0)),
    ]
    srh = lib_h / len(sitems)
    for i, (k, v) in enumerate(sitems):
        cy2 = lib_y + srh * i + srh / 2
        if i:
            seg_w = col_w - SUB_PAD_X * 2
            img.paste(Image.new("RGB", (int(seg_w), 1), (255, 255, 255)),
                      (lx + SUB_PAD_X, int(lib_y + srh * i)),
                      Image.new("L", (int(seg_w), 1), int(255 * 0.06)))
        d.text((lx + SUB_PAD_X, cy2), k, font=_txt(13.5), fill=DIM, anchor="lm")
        d.text((lx + col_w - SUB_PAD_X, cy2), v, font=_num(15, True),
               fill=TXT_2, anchor="rm")

    _sect(rx2, y_cols, col_w, "游戏时长榜",
          ("共 %s 款" % _fmt_int(S.get("games"))) if top else "")
    _rows(rx2, y_cols + sect_h, col_w, top, True, "没有数据")

    # ────────── 最近解锁成就 ──────────
    _sect(PAD, y_ach, W - PAD * 2, "最近解锁的成就",
          ("%d 项" % len(achs)) if achs else "")
    ay2 = y_ach + sect_h
    if achs:
        n = len(achs)
        cw2 = (W - PAD * 2 - ACH_GAP * (n - 1)) / n
        for i, a in enumerate(achs):
            cx = PAD + i * (cw2 + ACH_GAP)
            _panel(img, cx, ay2, cw2, ach_h, 4, ROW_BG, ROW_BG_A)
            _ring(img, cx, ay2, cw2, ach_h, 4, WHITE, LINE_A)
            iy = ay2 + (ach_h - ACH_ICO) / 2
            ico = _thumb(a.get("icon") or "", ACH_ICO, ACH_ICO)
            if ico is not None:
                paste_alpha_rounded(img, ico, (cx + ACH_PAD_X, iy), 4)
                _ring(img, cx + ACH_PAD_X, iy, ACH_ICO, ACH_ICO, 4, WHITE, LINE_A)
            else:
                _panel(img, cx + ACH_PAD_X, iy, ACH_ICO, ACH_ICO, 4, (26, 36, 46), 1.0)
                _ring(img, cx + ACH_PAD_X, iy, ACH_ICO, ACH_ICO, 4, WHITE, LINE_A)
                d = ImageDraw.Draw(img)
                d.text((cx + ACH_PAD_X + ACH_ICO / 2, ay2 + ach_h / 2), "ACH",
                       font=_num(13, True), fill=acc, anchor="mm")
            d = ImageDraw.Draw(img)
            ico_y = ay2 + ach_h / 2
            tx = cx + ACH_PAD_X + ACH_ICO + 11
            tw = cx + cw2 - ACH_PAD_X - tx
            f1 = _txt(14.5)
            d.text((tx, ico_y - _lh(f1) / 2 - 1),
                   truncate(d, a.get("name") or "?", f1, tw), font=f1,
                   fill=WHITE, anchor="lt")
            sub = "%s · %s" % (payload.get("ach_game") or "", a.get("date") or "")
            d.text((tx, ico_y + 3), truncate(d, sub, _txt(12), tw), font=_txt(12),
                   fill=DIM, anchor="lt")
    else:
        _panel(img, PAD, ay2, W - PAD * 2, ach_h, 4, ROW_BG, ROW_BG2_A)
        _ring(img, PAD, ay2, W - PAD * 2, ach_h, 4, WHITE, LINE_A)
        d = ImageDraw.Draw(img)
        d.text((W / 2, ay2 + ach_h / 2), "近期没有解锁记录", font=_txt(14.5),
               fill=DIM, anchor="mm")

    # ────────── 页脚 ──────────
    d = ImageDraw.Draw(img)
    d.line([(PAD, y_foot), (W - PAD, y_foot)], fill=(255, 255, 255, int(255 * LINE_A)),
           width=1)
    d.text((PAD, y_foot + FOOT_PT), "数据来源 Steam Web API · 仅公开展示",
           font=_txt(12.5), fill=DIM, anchor="lt")
    cost = int((time.perf_counter() - t0) * 1000)
    tail = "%s · 渲染 %dms" % (payload.get("updated") or "", cost)
    if payload.get("stale"):
        # Steam 不通时用的是落盘快照，必须让看图的人知道（别把旧数据当实时）
        tail = "缓存数据 %.1f 小时前 · %s" % (float(payload.get("stale_age_h") or 0), tail)
    d.text((W - PAD, y_foot + FOOT_PT), tail, font=_txt(12.5),
           fill=(200, 170, 90) if payload.get("stale") else DIM, anchor="rt")
    return img


def save_steam_card(img: Image.Image, out_path) -> Path:
    return save_image(img, out_path, 95)

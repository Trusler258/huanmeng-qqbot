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
W = 2560                 # 横屏（16:9 构图，高度按内容自适应）
PAD = 44                 # 左右留白

TOPBAR_H = 60            # 顶栏
HERO_H = 160             # hero 横幅
IDENT_PULL = 60          # 资料区上提量（压在 hero 上）

AVATAR = 170             # 头像
AVATAR_GAP = 28
AVATAR_MAIN_PT = 74      # 头像右侧文字区相对 ident 顶部的下移
AVATAR_RADIUS = 8
AVATAR_BORDER = 4

STATS_MT = 24
STAT_PAD_X = 26
STAT_PAD_Y = 16

COLS_MT = 24
COLS_GAP = 22
SECT_MB = 12
SECT_BAR_W = 4           # 分区标题左侧竖条
SECT_BAR_H = 17

ROW_PAD_X = 16
ROW_PAD_Y = 11
ROW_GAP = 16
ROW_IMG_W = 150          # 行内游戏横幅（横屏可以更大）
ROW_IMG_H = 70
RANK_W = 28
BAR_H = 5
BAR_MT = 8

SUB_MT = 12
SUB_PAD_X = 18
SUB_PAD_Y = 12
LVL_MT = 12
LVL_PAD_X = 18
LVL_PAD_Y = 15
LVL_TRACK_H = 7
LIB_MT = 12              # 左栏「游戏库概览」
VAL_MT = 12              # 右栏「库存价值」
ACH_MT = 12              # 右栏「最近解锁成就」

FOOT_MT = 22
FOOT_PT = 16
FOOT_PB = 24

TOP_ROWS = 9             # 时长榜展示条数（中栏高度基准）
ACH_ICO = 52             # 成就图标尺寸

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


def _rgba_from_uri(uri: str):
    """data URI -> RGBA 图（logo 这类需要保留透明通道，不能走 _img_from_uri）"""
    if not uri or not uri.startswith("data:"):
        return None
    key = ("rgba", len(uri), uri[:48])
    hit = _uri_cache.get(key)
    if hit is not None:
        return hit
    try:
        im = Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1]))).convert("RGBA")
    except Exception:
        return None
    _uri_cache[key] = im
    return im


def _brand_img(uri: str, height: int):
    """官方 logo 等比缩放到指定高度（保留透明）"""
    im = _rgba_from_uri(uri)
    if im is None:
        return None
    w = max(1, int(im.size[0] * height / max(1, im.size[1])))
    key = ("brand", len(uri), uri[:48], height)
    hit = _uri_cache.get(key)
    if hit is not None:
        return hit
    out = im.resize((w, height), Image.LANCZOS)
    _uri_cache[key] = out
    return out


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
    """渲染整张资料卡（横屏 2560 宽 / 三栏）

    布局（自上而下）：顶栏 → hero → 资料区 → 7 格统计条 → 三栏 → 页脚
      左栏：最近两周 + 小计 + 游戏库概览（自适应撑满）
      中栏：游戏时长榜（TOP_ROWS 条，作为整卡高度基准）
      右栏：Steam 等级进度 + 库存价值 + 最近解锁成就（自适应撑满）
    """
    t0 = time.perf_counter()
    P = payload.get("profile") or {}
    S = payload.get("stats") or {}
    V = payload.get("value") or {}
    B = payload.get("brand") or {}
    recent = payload.get("recent") or []
    top = payload.get("top") or []
    achs = payload.get("achievements") or []

    game_now = (P.get("game_now") or "").strip()
    ingame = bool(game_now) or P.get("state_kind") == "ingame"
    online = bool(P.get("online"))
    st_txt = P.get("state_text") or ("在线" if online else "离线")
    top_txt = "游戏中" if ingame else st_txt
    badge_txt = "游戏中" if ingame else st_txt

    acc = GREEN if ingame else BLUE
    acc_d = (92, 126, 16) if ingame else BLUE_D
    hero_stops = HERO_GREEN if ingame else HERO_BLUE

    col_w = (W - PAD * 2 - COLS_GAP * 2) / 3.0
    lc = int(col_w)
    row_h = ROW_PAD_Y * 2 + ROW_IMG_H
    sect_h = max(SECT_BAR_H, _lh(_txt(17, True))) + SECT_MB

    top_show = top[:TOP_ROWS]
    mid_h = sect_h + (len(top_show) * row_h + 2 if top_show
                      else (26 * 2 + _lh(_txt(15)) + 2))

    y_hero = TOPBAR_H
    y_ident = y_hero + HERO_H - IDENT_PULL
    y_stats = y_ident + AVATAR + STATS_MT
    stats_h = STAT_PAD_Y * 2 + _lh(_txt(14)) + 6 + _lh(_num(30, True)) + 2
    y_cols = y_stats + stats_h + COLS_MT
    cols_h = mid_h
    y_foot = y_cols + cols_h + FOOT_MT
    foot_h = FOOT_PT + _lh(_txt(13)) + FOOT_PB + 1
    H = int(y_foot + foot_h)

    # 背景 180deg 是纯垂直渐变 → 只算 1 像素宽再横向拉伸
    img = to_img(linear_grad((1, H), [(0.0, BG_1), (1.0, BG_2)], 180.0)).resize(
        (W, H), Image.BILINEAR)
    d = ImageDraw.Draw(img)

    # ────────── 顶栏 ──────────
    _panel(img, 0, 0, W, TOPBAR_H, 0, TOPBAR, 1.0)
    d = ImageDraw.Draw(img)
    d.line([(0, TOPBAR_H - 1), (W, TOPBAR_H - 1)], fill=(0, 0, 0), width=1)

    # 官方 Steam logo（浅色透明 PNG，等比缩放到高度 28）
    logo = _brand_img(B.get("steam_logo") or "", 32)
    if logo is not None:
        img.paste(logo, (int(PAD), int((TOPBAR_H - 32) / 2)), logo)
        nav_x = PAD + logo.size[0] + 28
    else:
        fb = _num(24, True)
        d.text((PAD, TOPBAR_H / 2), "STEAM", font=fb, fill=WHITE, anchor="lm")
        nav_x = PAD + d.textlength("STEAM", font=fb) + 28
    fn = _txt(15)
    for _tt, _hl in (("个人资料", True), ("游戏库", False), ("成就", False)):
        d.text((nav_x, TOPBAR_H / 2), _tt, font=fn, fill=(acc if _hl else DIM), anchor="lm")
        nav_x += d.textlength(_tt, font=fn) + 22

    fs = _txt(14)
    sw = d.textlength(top_txt, font=fs)
    dot_c = acc if (online or ingame) else DIM
    dx = W - PAD - sw - 10 - 9
    d.ellipse([dx, TOPBAR_H / 2 - 4.5, dx + 9, TOPBAR_H / 2 + 4.5], fill=dot_c)
    d.text((W - PAD, TOPBAR_H / 2), top_txt, font=fs, fill=DIM, anchor="rm")

    # ────────── Hero ──────────
    img.paste(to_img(linear_grad((W, HERO_H), hero_stops, HERO_ANGLE)), (0, y_hero))
    hatch = Image.new("RGB", (W, HERO_H), (0, 0, 0))
    hd = ImageDraw.Draw(hatch)
    span = HERO_H * 2
    for i in range(-span, W + span, 16):
        hd.line([(i, 0), (i + span, HERO_H)], fill=(255, 255, 255), width=2)
    img.paste(hatch, (0, y_hero), hatch.convert("L").point(lambda v: int(v * 0.07)))
    dark = Image.new("RGB", (W, HERO_H), (10, 20, 29))
    dmask = Image.new("L", (W, HERO_H))
    dd = ImageDraw.Draw(dmask)
    for row in range(HERO_H):
        tt = row / max(1, HERO_H - 1)
        dd.line([(0, row), (W, row)], fill=int(255 * (0.15 + 0.50 * tt)))
    img.paste(dark, (0, y_hero), dmask)
    d = ImageDraw.Draw(img)

    # hero 右下：官方 Steam logo 水印（半透明压暗，不抢主体）
    wm = _brand_img(B.get("steam_logo") or "", 52)
    if wm is not None:
        wm2 = wm.copy()
        alpha = wm2.getchannel("A").point(lambda v: int(v * 0.22))
        wm2.putalpha(alpha)
        img.paste(wm2, (int(W - PAD - wm2.size[0]),
                        int(y_hero + HERO_H - wm2.size[1] - 18)), wm2)

    if game_now:
        ft = _txt(16)
        label = "正在玩 " + game_now
        lw = d.textlength(label, font=ft)
        bw, bh = lw + 30 + 18, 38
        bx, by = W - PAD - bw, y_hero + 24
        _panel(img, bx, by, bw, bh, 4, (0, 0, 0), 0.30)
        _ring(img, bx, by, bw, bh, 4, WHITE, 0.18)
        d = ImageDraw.Draw(img)
        d.ellipse([bx + 14, by + bh / 2 - 4, bx + 22, by + bh / 2 + 4],
                  fill=(214, 245, 106))
        d.text((bx + 30, by + bh / 2), label, font=ft, fill=(236, 246, 242), anchor="lm")

    # ────────── 资料区 ──────────
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
    sp = 46
    sw2, sh2 = av + sp * 2, av + sp * 2 + 10
    sh = Image.new("L", (sw2, sh2), 0)
    sh.paste(rounded_mask((av, av), AVATAR_RADIUS), (sp, sp + 8))
    sh = sh.filter(ImageFilter.GaussianBlur(13)).point(lambda v: int(v * 0.6))
    img.paste(Image.new("RGB", (sw2, sh2), (0, 0, 0)), (ax - sp, ay - sp), sh)

    if avimg is not None:
        cov = _fill_cover(avimg, av, av) or avimg.resize((av, av), Image.LANCZOS)
        paste_alpha_rounded(img, cov, (ax, ay), AVATAR_RADIUS)
    else:
        _panel(img, ax, ay, av, av, AVATAR_RADIUS, IMG_BG, 1.0)
    _ring(img, ax, ay, av, av, AVATAR_RADIUS,
          (92, 126, 16) if ingame else (42, 71, 94), 1.0, AVATAR_BORDER)
    d = ImageDraw.Draw(img)

    mx = ax + av + AVATAR_GAP
    cy = ay + AVATAR_MAIN_PT
    fnm = _txt(42, True)
    name = truncate(d, P.get("name") or "—", fnm, 900)
    d.text((mx, cy + _lh(fnm) / 2), name, font=fnm, fill=WHITE, anchor="lm")
    nw = d.textlength(name, font=fnm)

    fst = _txt(16)
    bw = d.textlength(badge_txt, font=fst) + 14 * 2 + 9 + 9
    bh = 30
    sx, sy = mx + nw + 18, cy + _lh(fnm) / 2 - bh / 2 + 5
    if ingame:
        _panel(img, sx, sy, bw, bh, 4, GREEN, 0.10)
        _ring(img, sx, sy, bw, bh, 4, GREEN, 0.35)
        sc = GREEN
    elif online:
        _panel(img, sx, sy, bw, bh, 4, BLUE, 0.10)
        _ring(img, sx, sy, bw, bh, 4, BLUE, 0.32)
        sc = BLUE
    else:
        _panel(img, sx, sy, bw, bh, 4, DIM, 0.10)
        _ring(img, sx, sy, bw, bh, 4, DIM, 0.30)
        sc = DIM
    d = ImageDraw.Draw(img)
    d.ellipse([sx + 14, sy + bh / 2 - 4.5, sx + 23, sy + bh / 2 + 4.5], fill=sc)
    d.text((sx + 14 + 9 + 9, sy + bh / 2), badge_txt, font=fst, fill=sc, anchor="lm")

    my = cy + _lh(fnm) + 14
    fm = _txt(16)
    parts = [("Steam 等级 ", str(P.get("level")) if P.get("level") is not None else "—"),
             ("注册于 ", P.get("created") or "—"),
             ("", P.get("steamid") or "—")]
    px = mx
    for i, (pre, val) in enumerate(parts):
        if i:
            d.text((px, my + _lh(fm) / 2), "|", font=fm, fill=(92, 100, 108), anchor="lm")
            px += d.textlength("|", font=fm) + 14
        if pre:
            d.text((px, my + _lh(fm) / 2), pre, font=fm, fill=DIM, anchor="lm")
            px += d.textlength(pre, font=fm)
        fv = _num(16) if (i == 2 or (i == 0 and P.get("level") is not None)) else fm
        d.text((px, my + _lh(fm) / 2), val, font=fv, fill=TXT_2, anchor="lm")
        px += d.textlength(val, font=fv) + 14

    # ────────── 统计条（7 格，含库存价值）──────────
    money = ("¥" + _fmt_int(round((V.get("final") or 0) / 100.0))) if V.get("priced") else ""
    st_x, st_y = PAD, y_stats
    st_w = W - PAD * 2
    _panel(img, st_x, st_y, st_w, stats_h, 5, ROW_BG, ROW_BG_A)
    _ring(img, st_x, st_y, st_w, stats_h, 5, WHITE, LINE_A)
    d = ImageDraw.Draw(img)
    cells = [
        ("游戏数量", _fmt_int(S.get("games")), "", False),
        ("累计时长", _fmt_int(S.get("hours")), "小时", False),
        ("成就", ("%s / %s" % (S.get("ach_got"), S.get("ach_total"))) if S.get("ach_total") else "—", "", True),
        ("徽章", _fmt_int(S["badges"]) if S.get("badges") is not None else "—", "", False),
        ("好友", _fmt_int(S["friends"]) if S.get("friends") is not None else "—", "", False),
        ("库存价值", money or "—", "", True),
        ("在线状态", badge_txt, "", False),
    ]
    cw = st_w / len(cells)
    for i, (k, v, unit, hl) in enumerate(cells):
        cx = st_x + cw * i
        if i:
            seg = int(stats_h * 0.68)
            img.paste(Image.new("RGB", (1, seg), (255, 255, 255)),
                      (int(cx), int(st_y + stats_h * 0.16)),
                      Image.new("L", (1, seg), int(255 * LINE_A)))
        fk = _txt(14)
        d.text((cx + STAT_PAD_X, st_y + STAT_PAD_Y), k, font=fk, fill=DIM, anchor="lt")
        big = k == "在线状态"
        fv = _txt(23, True) if big else _num(30, True)
        vy = st_y + STAT_PAD_Y + _lh(fk) + 6
        col = acc if hl else WHITE
        if big:
            d.text((cx + STAT_PAD_X, vy + _lh(fv) / 2), v, font=fv, fill=col, anchor="lm")
        else:
            d.text((cx + STAT_PAD_X, vy), v, font=fv, fill=col, anchor="lt")
            if unit:
                ux = cx + STAT_PAD_X + d.textlength(v, font=fv) + 6
                d.text((ux, vy + _lh(fv) - _lh(_txt(15))), unit, font=_txt(15),
                       fill=DIM, anchor="lt")

    # ────────── 三栏 ──────────
    def _sect(x, y, w, title, right_note=""):
        _panel(img, x, y + (max(SECT_BAR_H, _lh(_txt(17, True))) - SECT_BAR_H) / 2,
               SECT_BAR_W, SECT_BAR_H, 2, acc, 1.0)
        dd = ImageDraw.Draw(img)
        fh = _txt(17, True)
        dd.text((x + SECT_BAR_W + 11, y + max(SECT_BAR_H, _lh(fh)) / 2), title,
                font=fh, fill=WHITE, anchor="lm")
        if right_note:
            dd.text((x + w, y + max(SECT_BAR_H, _lh(fh)) / 2), right_note,
                    font=_txt(13.5), fill=DIM, anchor="rm")

    def _rows(x, y, w, items, rank, note):
        """一组行（交替背景 + 圆角容器：整块画在 RGBA 图层上最后裁圆角）"""
        n = len(items)
        hh = int((n * row_h + 2) if n else (26 * 2 + _lh(_txt(15)) + 2))
        blk = Image.new("RGBA", (int(w), hh), (0, 0, 0, 0))
        bd = ImageDraw.Draw(blk)
        bd.rectangle([0, 0, int(w) - 1, hh - 1], fill=ROW_BG + (int(255 * ROW_BG_A),))
        if not items:
            bd.text((w / 2, hh / 2), note, font=_txt(15), fill=DIM + (255,), anchor="mm")
        else:
            mx_h = max([float(g.get("hours") or 0) for g in items] + [1.0])
            for i, g in enumerate(items):
                ry = i * row_h
                if i % 2 == 1:
                    bd.rectangle([0, ry, int(w) - 1, ry + row_h - 1],
                                 fill=ROW_BG + (int(255 * ROW_BG2_A),))
                if i:
                    bd.line([(1, ry), (int(w) - 2, ry)], fill=(255, 255, 255, 12), width=1)
                px = ROW_PAD_X
                if rank:
                    bd.text((px + RANK_W, ry + row_h / 2), str(i + 1), font=_num(15, True),
                            fill=acc + (255,), anchor="rm")
                    px += RANK_W + ROW_GAP
                cover = _thumb(g.get("img") or "", ROW_IMG_W, ROW_IMG_H)
                if cover is not None:
                    paste_alpha_rounded(blk, cover, (px, ry + ROW_PAD_Y), 4)
                else:
                    bd.rounded_rectangle(
                        [px, ry + ROW_PAD_Y, px + ROW_IMG_W - 1,
                         ry + ROW_PAD_Y + ROW_IMG_H - 1], 4, fill=IMG_BG + (255,))
                px += ROW_IMG_W + ROW_GAP
                hrs = float(g.get("hours") or 0)
                fh2 = _num(20, True)
                hs = _fmt_h(hrs)
                hw = max(bd.textlength(hs, font=fh2), bd.textlength("小时", font=_txt(13)))
                rgt = int(w) - ROW_PAD_X
                bd.text((rgt, ry + ROW_PAD_Y + 2), hs, font=fh2, fill=acc + (255,), anchor="rt")
                bd.text((rgt, ry + ROW_PAD_Y + 2 + _lh(fh2) + 3), "小时", font=_txt(13),
                        fill=DIM + (255,), anchor="rt")
                main_w = rgt - hw - ROW_GAP - px
                if main_w < 60:
                    main_w = 60
                fg = _txt(16.5)
                nm = truncate(bd, g.get("name") or "?", fg, main_w)
                bd.text((px, ry + ROW_PAD_Y + 3), nm, font=fg, fill=WHITE + (255,), anchor="lt")
                bar_y = ry + ROW_PAD_Y + 3 + _lh(fg) + BAR_MT
                bd.rounded_rectangle([px, bar_y, px + main_w - 1, bar_y + BAR_H - 1], 2,
                                     fill=(255, 255, 255, 20))
                pct = max(0.0, min(1.0, hrs / mx_h)) if mx_h > 0 else 0.0
                if pct > 0:
                    bwd = max(3.0, main_w * pct)
                    blk.paste(to_img(linear_grad((int(bwd), BAR_H),
                                                 [(0.0, acc_d), (1.0, acc)], 90.0)),
                              (int(px), int(bar_y)), rounded_mask((int(bwd), BAR_H), 2))
        base = img.crop((int(x), int(y), int(x + w), int(y + hh))).convert("RGBA")
        img.paste(Image.alpha_composite(base, blk).convert("RGB"), (int(x), int(y)),
                  rounded_mask((int(w), hh), 5))
        _ring(img, x, y, w, hh, 5, WHITE, LINE_A)
        return hh

    lx = PAD
    mx2 = PAD + lc + COLS_GAP
    rx2 = mx2 + lc + COLS_GAP
    cols_bottom = y_cols + cols_h

    # ── 左栏 ──
    _sect(lx, y_cols, lc, "最近两周", ("%d 款" % len(recent)) if recent else "")
    l_rows_y = y_cols + sect_h
    l_rows_h = _rows(lx, l_rows_y, lc, recent, False, "最近两周没有游玩记录")

    sub_h = SUB_PAD_Y * 2 + _lh(_txt(15)) + 2
    sub_y = l_rows_y + l_rows_h + SUB_MT
    if recent:
        _panel(img, lx, sub_y, lc, sub_h, 5, acc, 0.07)
        _ring(img, lx, sub_y, lc, sub_h, 5, acc, 0.18)
        d = ImageDraw.Draw(img)
        d.text((lx + SUB_PAD_X, sub_y + sub_h / 2), "两周合计", font=_txt(15),
               fill=TXT_2, anchor="lm")
        d.text((lx + lc - SUB_PAD_X, sub_y + sub_h / 2),
               _fmt_h(payload.get("recent_sum")) + " 小时", font=_num(19, True),
               fill=acc, anchor="rm")
        lib_top = sub_y + sub_h + LIB_MT
    else:
        lib_top = sub_y

    lib_h = max(120.0, cols_bottom - lib_top)
    _panel(img, lx, lib_top, lc, lib_h, 5, ROW_BG, ROW_BG_A)
    _ring(img, lx, lib_top, lc, lib_h, 5, WHITE, LINE_A)
    d = ImageDraw.Draw(img)
    lib = payload.get("library") or {}
    sitems = [
        ("玩过的游戏", "%s / %s 款" % (_fmt_int(lib.get("played")), _fmt_int(S.get("games")))),
        ("平均每款时长", _fmt_h(lib.get("avg")) + " 小时"),
        ("最常玩占比", "%.1f%%" % float(lib.get("top_share") or 0)),
    ]
    if V.get("priced"):
        sitems.append(("已计价游戏",
                       "%s 款（%s 款无价格）" % (_fmt_int(V.get("priced")),
                                                _fmt_int(V.get("unpriced")))))
    srh = lib_h / len(sitems)
    for i, (k, v) in enumerate(sitems):
        cy2 = lib_top + srh * i + srh / 2
        if i:
            seg_w = lc - SUB_PAD_X * 2
            img.paste(Image.new("RGB", (int(seg_w), 1), (255, 255, 255)),
                      (lx + SUB_PAD_X, int(lib_top + srh * i)),
                      Image.new("L", (int(seg_w), 1), int(255 * 0.06)))
        d.text((lx + SUB_PAD_X, cy2), k, font=_txt(14), fill=DIM, anchor="lm")
        d.text((lx + lc - SUB_PAD_X, cy2), v, font=_num(16, True), fill=TXT_2, anchor="rm")

    # ── 中栏 ──
    _sect(mx2, y_cols, lc, "游戏时长榜",
          ("共 %s 款" % _fmt_int(S.get("games"))) if top else "")
    _rows(mx2, y_cols + sect_h, lc, top_show, True, "没有数据")

    # ── 右栏 ──
    ry2 = y_cols
    _sect(rx2, ry2, lc, "账户概览")
    ry2 += sect_h

    fk2 = _txt(14)
    lvl_h = LVL_PAD_Y * 2 + _lh(fk2) + 10 + LVL_TRACK_H + 9 + _lh(_txt(13)) + 2
    _panel(img, rx2, ry2, lc, lvl_h, 5, ROW_BG, ROW_BG_A)
    _ring(img, rx2, ry2, lc, lvl_h, 5, WHITE, LINE_A)
    d = ImageDraw.Draw(img)
    xp = S.get("xp")
    if xp is not None:
        d.text((rx2 + LVL_PAD_X, ry2 + LVL_PAD_Y), "Steam 等级进度", font=fk2,
               fill=DIM, anchor="lt")
        d.text((rx2 + lc - LVL_PAD_X, ry2 + LVL_PAD_Y), _fmt_int(xp) + " XP",
               font=_num(16, True), fill=TXT_2, anchor="rt")
        ty = ry2 + LVL_PAD_Y + _lh(fk2) + 10
        _panel(img, rx2 + LVL_PAD_X, ty, lc - LVL_PAD_X * 2, LVL_TRACK_H, 3, WHITE, 0.08)
        cur, need = S.get("xp_cur"), S.get("xp_need")
        if isinstance(cur, int) and isinstance(need, int) and need > 0:
            pct = max(0.01, min(1.0, cur / need))
            _hgrad(img, rx2 + LVL_PAD_X, ty, (lc - LVL_PAD_X * 2) * pct,
                   LVL_TRACK_H, acc_d, acc)
            note = "距下一级还需 %s XP" % _fmt_int(max(0, need - cur))
        else:
            _hgrad(img, rx2 + LVL_PAD_X, ty, lc - LVL_PAD_X * 2, LVL_TRACK_H, acc_d, acc)
            note = "已达最高等级"
    else:
        note = "等级数据不可用"
    d.text((rx2 + LVL_PAD_X, ry2 + LVL_PAD_Y + _lh(fk2) + 10 + LVL_TRACK_H + 9),
           note, font=_txt(13), fill=DIM, anchor="lt")
    ry2 += lvl_h + VAL_MT

    # 库存价值卡
    val_h = LVL_PAD_Y * 2 + 20 + _lh(_num(34, True)) + 14 + _lh(_txt(14)) + 2
    _panel(img, rx2, ry2, lc, val_h, 5, acc, 0.07)
    _ring(img, rx2, ry2, lc, val_h, 5, acc, 0.20)
    d = ImageDraw.Draw(img)
    if V.get("priced"):
        d.text((rx2 + LVL_PAD_X, ry2 + LVL_PAD_Y), "库存价值（现价合计）", font=_txt(14),
               fill=DIM, anchor="lt")
        d.text((rx2 + LVL_PAD_X, ry2 + LVL_PAD_Y + 20), "¥" + "{:,.2f}".format(
            (V.get("final") or 0) / 100.0), font=_num(34, True), fill=acc, anchor="lt")
        vy2 = ry2 + LVL_PAD_Y + 20 + _lh(_num(34, True)) + 14
        d.text((rx2 + LVL_PAD_X, vy2), "原价合计 ¥" + "{:,.2f}".format(
            (V.get("original") or 0) / 100.0), font=_txt(14), fill=TXT_2, anchor="lt")
        if (V.get("saved") or 0) > 0:
            pct = 100.0 * V["saved"] / max(1, V["original"])
            d.text((rx2 + lc - LVL_PAD_X, vy2),
                   "省 ¥%s（%.0f%%）" % ("{:,.2f}".format(V["saved"] / 100.0), pct),
                   font=_num(14, True), fill=(126, 200, 96), anchor="rt")
    else:
        d.text((rx2 + LVL_PAD_X, ry2 + LVL_PAD_Y), "库存价值", font=_txt(14), fill=DIM, anchor="lt")
        d.text((rx2 + LVL_PAD_X, ry2 + LVL_PAD_Y + 22), "价格数据不可用",
               font=_num(22, True), fill=DIM, anchor="lt")
    ry2 += val_h + ACH_MT

    # 最近解锁成就（自适应撑满右栏底部）
    _sect(rx2, ry2, lc, "最近解锁的成就", ("%d 项" % len(achs)) if achs else "")
    ay2 = ry2 + sect_h
    avail = max(120.0, cols_bottom - ay2)
    if achs:
        gap = 10
        ah = max(52.0, (avail - (len(achs) - 1) * gap) / len(achs))
        for i, a in enumerate(achs):
            yy = ay2 + i * (ah + gap)
            _panel(img, rx2, yy, lc, ah, 5, ROW_BG, ROW_BG_A)
            _ring(img, rx2, yy, lc, ah, 5, WHITE, LINE_A)
            iy = yy + (ah - ACH_ICO) / 2
            ico = _thumb(a.get("icon") or "", ACH_ICO, ACH_ICO)
            if ico is not None:
                paste_alpha_rounded(img, ico, (rx2 + 16, iy), 5)
                _ring(img, rx2 + 16, iy, ACH_ICO, ACH_ICO, 5, WHITE, LINE_A)
            else:
                _panel(img, rx2 + 16, iy, ACH_ICO, ACH_ICO, 5, (26, 36, 46), 1.0)
                _ring(img, rx2 + 16, iy, ACH_ICO, ACH_ICO, 5, WHITE, LINE_A)
                d = ImageDraw.Draw(img)
                d.text((rx2 + 16 + ACH_ICO / 2, yy + ah / 2), "ACH",
                       font=_num(14, True), fill=acc, anchor="mm")
            d = ImageDraw.Draw(img)
            tx = rx2 + 16 + ACH_ICO + 13
            tw = rx2 + lc - 16 - tx
            f1 = _txt(16)
            d.text((tx, yy + ah / 2 - _lh(f1) / 2 - 2),
                   truncate(d, a.get("name") or "?", f1, tw), font=f1,
                   fill=WHITE, anchor="lt")
            sub = "%s · %s" % (payload.get("ach_game") or "", a.get("date") or "")
            d.text((tx, yy + ah / 2 + 2), truncate(d, sub, _txt(13), tw),
                   font=_txt(13), fill=DIM, anchor="lt")
    else:
        _panel(img, rx2, ay2, lc, avail, 5, ROW_BG, ROW_BG2_A)
        _ring(img, rx2, ay2, lc, avail, 5, WHITE, LINE_A)
        d = ImageDraw.Draw(img)
        d.text((rx2 + lc / 2, ay2 + avail / 2), "近期没有解锁记录", font=_txt(15),
               fill=DIM, anchor="mm")

    # ────────── 页脚 ──────────
    d = ImageDraw.Draw(img)
    d.line([(PAD, y_foot), (W - PAD, y_foot)], fill=(255, 255, 255, int(255 * LINE_A)),
           width=1)
    fy = y_foot + FOOT_PT
    src = "数据来源 Steam Web API · 仅公开展示"
    d.text((PAD, fy), src, font=_txt(13), fill=DIM, anchor="lt")
    vlogo = _brand_img(B.get("valve_logo") or "", 20)
    if vlogo is not None:
        lw2 = d.textlength(src, font=_txt(13))
        img.paste(vlogo, (int(PAD + lw2 + 18),
                          int(fy + (_lh(_txt(13)) - 20) / 2)), vlogo)
    cost = int((time.perf_counter() - t0) * 1000)
    tail_s = "%s · 渲染 %dms" % (payload.get("updated") or "", cost)
    if payload.get("stale"):
        tail_s = "缓存数据 %.1f 小时前 · %s" % (float(payload.get("stale_age_h") or 0), tail_s)
    d.text((W - PAD, fy), tail_s, font=_txt(13),
           fill=(200, 170, 90) if payload.get("stale") else DIM, anchor="rt")
    return img


def save_steam_card(img: Image.Image, out_path) -> Path:
    return save_image(img, out_path, 95)

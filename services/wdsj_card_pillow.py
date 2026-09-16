"""
日榜卡 Pillow 渲染器 —— 一比一复刻 data/templates/daily_rank_card.html

为什么不用 Chromium：服务器（i3-2130）上 Chromium 单张卡约 990ms、常驻 394MB；
Pillow 约 10~20ms、56MB。详见 docs/渲染方案实测.md

⚠️ 配色/尺寸全部照抄模板 CSS，勿凭感觉改。
   第一版曾把浅色主题画成深紫底、并用 DejaVu（不含中文）当字体，是错的。
"""
from __future__ import annotations

import base64
import html as _html
import io
import math
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# 按渲染宽度截断文本（card_base 里已有实现，不重复造）
from services.card_base import truncate, render_stamp

# ══════════════════════════════════════════════════════════
#  字体
# ══════════════════════════════════════════════════════════
_ROOT = Path(__file__).resolve().parent.parent
_MONOCRAFT = _ROOT / "data" / "web_assets" / "monocraft.ttf"

_CJK_REGULAR = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
]
_CJK_BOLD = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
]

_font_cache: dict[tuple, ImageFont.FreeTypeFont] = {}
_resolved: dict[bool, tuple[str, int]] = {}
_cjk_choice: tuple[str, int] | None = None      # 兼容旧引用（= Regular）


def _is_real_cjk(font) -> bool:
    """真字形 vs 豆腐块：渲染两个不同汉字，位图应不同且有墨迹。"""
    def ink(ch: str) -> list:
        im = Image.new("L", (48, 48), 0)
        ImageDraw.Draw(im).text((4, 4), ch, font=font, fill=255)
        return list(im.getdata())
    a, b = ink("测"), ink("试")
    return a != b and max(a) > 0


def _resolve_font(bold: bool) -> tuple[str, int]:
    """找可用中文字体 (路径, ttc 索引)。

    ★ 必须区分 Regular/Bold：Noto CJK 的 Bold 与 Regular 是不同文件。
      第一版只找"第一个能用的"，结果 Regular 请求也返回 Bold 文件 ——
      全部文字被加粗，整卡偏重、区域均值偏差近 20。
    """
    global _cjk_choice
    if bold in _resolved:
        return _resolved[bold]
    for path in (_CJK_BOLD if bold else _CJK_REGULAR):
        if not Path(path).exists():
            continue
        for idx in range(8):
            try:
                f = ImageFont.truetype(path, 24, index=idx)
            except Exception:
                continue
            if _is_real_cjk(f):
                _resolved[bold] = (path, idx)
                if _cjk_choice is None:
                    _cjk_choice = (path, idx)
                return _resolved[bold]
    raise RuntimeError("找不到可用中文字体（需要 fonts-noto-cjk 或 fonts-wqy-*）")


def _cjk_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    key = ("cjk", size, bold)
    if key in _font_cache:
        return _font_cache[key]
    path, idx = _resolve_font(bold)
    f = ImageFont.truetype(path, size, index=idx)
    _font_cache[key] = f
    return f


def _mono_font(size: int) -> ImageFont.FreeTypeFont:
    key = ("mono", size)
    if key in _font_cache:
        return _font_cache[key]
    f = ImageFont.truetype(str(_MONOCRAFT), size) if _MONOCRAFT.exists() else _cjk_font(size)
    _font_cache[key] = f
    return f


# ══════════════════════════════════════════════════════════
#  配色（逐项取自 daily_rank_card.html 的 CSS）
# ══════════════════════════════════════════════════════════
P = {
    "body_stops": [(0.00, (253, 244, 236)), (0.45, (249, 236, 226)), (1.00, (243, 239, 242))],
    "glow1": ((255, 172, 132), 0.60),   # radial at 8% -10%, size 640x340, transparent 60%
    "glow2": ((255, 206, 186), 0.45),   # radial at 98% -4%,  size 320x220, transparent 62%
    "wrap_bg": (255, 250, 245), "wrap_bg_a": 0.78,
    "wrap_border": (255, 255, 255), "wrap_border_a": 0.90,
    "shadow": (120, 90, 70), "shadow_a": 0.10,
    "hic_a": (255, 164, 130, 0.46), "hic_b": (238, 90, 58, 0.22),
    "title": (74, 47, 38),              # #4a2f26
    "sub": (154, 138, 128),             # #9a8a80
    "th": (143, 124, 108),              # #8f7c6c
    "th_hint": (174, 158, 144),         # th @ .72 opacity
    "th_border": (226, 178, 150, 0.55),
    "tr_border": (215, 190, 175, 0.30),
    "td": (74, 61, 53),                 # #4a3d35
    "td_num": (194, 67, 44),            # #c2432c
    "rank_n": (110, 98, 90),            # td @ .8 opacity
    "up": (192, 178, 169),              # #9a8a80 @ .62
    "newp_bg": (255, 214, 190), "newp_bg_a": 0.50,
    "newp_fg": (138, 90, 58),           # #8a5a3a
    "foot": (162, 149, 138),            # #a2958a
    "hash_bg": (150, 130, 110), "hash_bg_a": 0.09,
    "hash_fg": (194, 184, 173),         # #c2b8ad
}

# MC 颜色码（浅底适配版，取自模板 MC_LIGHT_ADAPT / MC_COLORS）
MC_COLORS = {"0": (0, 0, 0), "1": (0, 0, 170), "2": (0, 170, 0), "3": (0, 170, 170),
             "4": (170, 0, 0), "5": (170, 0, 170), "6": (255, 170, 0), "7": (170, 170, 170),
             "8": (85, 85, 85), "9": (85, 85, 255), "a": (85, 255, 85), "b": (85, 255, 255),
             "c": (255, 85, 85), "d": (255, 85, 255), "e": (255, 255, 85), "f": (255, 255, 255)}
MC_LIGHT_ADAPT = {"7": (107, 114, 128), "8": (75, 85, 99), "f": (107, 114, 128),
                  "b": (14, 116, 144), "a": (21, 128, 61), "e": (180, 83, 9),
                  "d": (162, 28, 175), "9": (29, 78, 216)}
MC_FMT = set("lonmkr")

# ══════════════════════════════════════════════════════════
#  布局（px）—— 全部由 Playwright 读 DOM 实测得出（_probe/_measure_card_geo.py）
#  勿凭感觉改：第一版按 CSS 字面估算，行高少算 8px、列宽全偏，高度差 68px
# ══════════════════════════════════════════════════════════
BODY_W, BODY_PAD = 620, 22
WRAP_BORDER = 1                       # 1px 白边会把内容整体推 1px
WRAP_PAD_T, WRAP_PAD_X, WRAP_PAD_B = 20, 22, 16
WRAP_R = 24
CONTENT_X = BODY_PAD + WRAP_BORDER + WRAP_PAD_X     # 45（实测）
CONTENT_W = 530                                     # 实测
HEAD_H, HEAD_MB = 52, 16            # 实测 head 高 52（不是 46）
HIC_SZ, HIC_R, HIC_IMG = 46, 13, 32
THEAD_H = 35.5                      # 实测
ROW_H = 44                          # 实测：奖牌 21px 撑高行盒，不是 padding+字号
FOOT_MT, FOOT_H = 12, 21
NEWP_MT, NEWP_PAD_X, NEWP_LH = 12, 12, 20
NEWP_H = 9 + NEWP_LH + 9            # padding 9px 上下 + 行高 20

# 名次列（实测 td.rank: x=45 w=68, padding-left 8）
COL_RANK_L, COL_RANK_W = CONTENT_X, 68
MEDAL_X = COL_RANK_L + 8            # 53
RANK_NUM_X = MEDAL_X + 21 + 5       # 79（奖牌槽位固定，数字对齐同一竖线）

# 列几何：(key, label, hint, icon, is_num, x_left, x_right) —— 实测 thead th 边界
COLS = [
    ("name", "玩家", "", None, False, 113.0, 246.7),
    ("kills", "击杀", "(终杀)", "i_kills", True, 246.7, 361.0),
    ("wins", "胜场", "", "i_wins", True, 361.0, 435.7),
    ("deaths", "死亡", "", "i_deaths", True, 435.7, 510.4),
    ("kd", "KD", "", "i_kd", True, 510.4, 575.0),
]

# 图标目录（由模板导出，见 _extract_icons）
ICON_DIR = _ROOT / "data" / "web_assets" / "wdsj_icons"
_icon_cache: dict[str, Image.Image] = {}


# 两个日榜模板的内嵌图标名单（顺序 = 模板里 base64 出现的顺序）
# 竞技场复用了起床日榜的公共图标（金/铁/铜/蛋/击杀/胜场/死亡/KD），
# 所以同名直接覆盖，不产生重复文件；竞技场独有的是 head_arena / i_arena_div / i_arena_losses
_TPL_ICONS = {
    "daily_rank_card.html": ["head_bed", "gold", "iron", "copper", "egg",
                              "i_kills", "i_wins", "i_deaths", "i_kd"],
    "daily_arena_card.html": ["head_arena", "gold", "iron", "copper", "egg",
                               "i_arena_div", "i_kills", "i_wins", "i_arena_losses",
                               "i_deaths", "i_kd"],
}


def extract_icons(force: bool = False) -> Path:
    """把两个日榜模板内嵌的 base64 图标导出为 PNG（一次性，结果缓存在磁盘）

    ★ v2.3.25 改为遍历两个模板：竞技场日榜复用同一批公共图标 +
      自己独有的 3 个（头像/段位/败场）。
    """
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    need = {n for names in _TPL_ICONS.values() for n in names}
    if not force and all((ICON_DIR / f"{n}.png").exists() for n in need):
        return ICON_DIR
    import re as _re
    for tpl_name, names in _TPL_ICONS.items():
        tpl = _ROOT / "data" / "templates" / tpl_name
        if not tpl.exists():
            continue
        found = _re.findall(r"data:image/png;base64,([A-Za-z0-9+/=]+)",
                            tpl.read_text(encoding="utf-8"))
        for i, b64 in enumerate(found):
            if i >= len(names):
                break
            (ICON_DIR / f"{names[i]}.png").write_bytes(base64.b64decode(b64))
    return ICON_DIR


def _icon(name: str, size: int) -> Image.Image | None:
    key = f"{name}@{size}"
    if key in _icon_cache:
        return _icon_cache[key]
    p = ICON_DIR / f"{name}.png"
    if not p.exists():
        return None
    im = Image.open(p).convert("RGBA")
    if im.size != (size, size):
        # image-rendering:pixelated → 最近邻
        # 兼容 Pillow 9.0（无 Image.Resampling 枚举）与 9.1+
        nearest = getattr(getattr(Image, "Resampling", Image), "NEAREST", Image.NEAREST)
        im = im.resize((size, size), nearest)
    _icon_cache[key] = im
    return im


# ══════════════════════════════════════════════════════════
#  绘制原语
# ══════════════════════════════════════════════════════════
def _linear_grad(size, stops, angle_deg=160.0) -> np.ndarray:
    """CSS linear-gradient(160deg, ...) → float32 RGB 数组"""
    W, H = size
    a = math.radians(angle_deg)          # 0deg=to top, 90deg=to right
    dx, dy = math.sin(a), -math.cos(a)
    L = abs(W * dx) + abs(H * dy)
    xs = np.arange(W, dtype=np.float32)[None, :]
    ys = np.arange(H, dtype=np.float32)[:, None]
    proj = ((xs - W / 2) * dx + (ys - H / 2) * dy) / L + 0.5
    ps = np.array([p for p, _ in stops], dtype=np.float32)
    out = np.zeros((H, W, 3), dtype=np.float32)
    for ch in range(3):
        cs = np.array([c[ch] for _, c in stops], dtype=np.float32)
        out[..., ch] = np.interp(proj, ps, cs)
    return out


def _radial_glow(canvas: np.ndarray, center, radii, color, alpha, fade=0.60) -> np.ndarray:
    """CSS radial-gradient 近似：中心 alpha → fade 处透明"""
    H, W = canvas.shape[:2]
    cx, cy = center
    rx, ry = radii
    xs = np.arange(W, dtype=np.float32)[None, :]
    ys = np.arange(H, dtype=np.float32)[:, None]
    d = np.sqrt(((xs - cx) / rx) ** 2 + ((ys - cy) / ry) ** 2)
    a = np.clip(1.0 - d / fade, 0.0, 1.0) * alpha
    a3 = a[..., None]
    c = np.array(color, dtype=np.float32)[None, None, :]
    return canvas * (1 - a3) + c * a3


def _rounded_mask(size, radius: int) -> Image.Image:
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius, fill=255)
    return m


def _rgba(color, alpha: float):
    return tuple(int(v) for v in color) + (int(round(alpha * 255)),)


def _blend_at(img: Image.Image, xy, color, alpha: float):
    """取 img 上某点底色，把 color 按 alpha 混合后返回实色。

    ⚠️ 必须这样做：`ImageDraw` 在 **RGB 图**上画 4 元组 fill 会**忽略 alpha**，
    半透明直接变实心。第一版药丸/表格线全用 _rgba() → 页脚药丸变成实心深棕，
    该区域像素差 61；表头线与行线也偏重。
    底色在目标处取样（这些元素底下都是近似纯色面板，取样足够准）。
    """
    base = img.getpixel((int(xy[0]), int(xy[1])))
    base = base[:3] if isinstance(base, tuple) else (base, base, base)
    return tuple(int(round(color[i] * alpha + base[i] * (1 - alpha))) for i in range(3))


def _blend(fg, bg, alpha: float):
    """把 fg 以 alpha 叠在 bg 上（预计算用）"""
    return tuple(int(round(fg[i] * alpha + bg[i] * (1 - alpha))) for i in range(3))


def _text_runs(text: str, base_color):
    """把 MC 颜色码文本拆成 [(子串, 颜色)]，无颜色码则单段"""
    s = str(text)
    if "\u00a7" not in s:
        return [(s, base_color)]
    parts = s.split("\u00a7")
    runs = [(parts[0] or "", base_color)]
    cur = base_color
    for seg in parts[1:]:
        if not seg:
            continue
        code = seg[0].lower()
        rest = seg[1:]
        if code in MC_FMT:
            if rest:
                runs.append((rest, cur))
            continue
        cur = MC_LIGHT_ADAPT.get(code, MC_COLORS.get(code, base_color))
        if rest:
            runs.append((rest, cur))
    return [(t, c) for t, c in runs if t]


# ══════════════════════════════════════════════════════════
#  主渲染
# ══════════════════════════════════════════════════════════
def render_daily_rank_card(payload: dict, *, width: int = BODY_W,
                           content_w: int = CONTENT_W, cols: list | None = None,
                           title: str = "今日增量 · 起床战争",
                           palette: dict | None = None) -> Image.Image:
    """渲染日榜卡（与 HTML 模板同数据、同外观）

    ★ v2.3.25 参数化：起床战争与竞技场共用同一套渲染，差异只在
      画布宽（620 / 660）、内容宽（530 / 570）、列定义、标题。
      默认值 = 起床战争，调用方不传则行为与之前完全一致。

    payload 与 _build_daily_rank_html 注入的 JSON 同结构：
      {date, range, when, rows:[{rank,name,cells{name,kills,final,wins,deaths,kd}}],
       newPlayers:[], nextTime, brand}
    """
    extract_icons()
    _t0 = time.perf_counter()   # 页脚「渲染时间」的耗时基准
    # ★ v2.3.25: 每张卡有自己的配色（起床=暖橙系，竞技场=冷蓝系），
    #   这里按卡覆盖 P 的部分键（未覆盖的沿用起床日榜值）
    PU = {**P, **(palette or {})}
    W_CANVAS = int(width)
    CW = int(content_w)
    _cols = cols if cols is not None else COLS
    rows = payload.get("rows") or []
    newp = [_html.unescape(str(n)) for n in (payload.get("newPlayers") or [])]
    date = str(payload.get("date") or "")
    rng = str(payload.get("range") or "")
    when = str(payload.get("when") or "")
    brand = str(payload.get("brand") or "幻梦Bot")
    next_time = str(payload.get("nextTime") or "")

    n = len(rows)
    newp_total = (NEWP_MT + NEWP_H) if newp else 0
    wrap_h = (WRAP_BORDER + WRAP_PAD_T + HEAD_H + HEAD_MB + THEAD_H + n * ROW_H
              + newp_total + FOOT_MT + FOOT_H + WRAP_PAD_B + WRAP_BORDER)
    W, H = W_CANVAS, int(round(wrap_h)) + BODY_PAD * 2
    wrap_x, wrap_y = BODY_PAD, BODY_PAD
    wrap_w = W_CANVAS - BODY_PAD * 2
    wrap_h = int(round(wrap_h))

    # ── 背景：线性渐变 + 两处暖色径向光晕 ──
    # ⚠️ CSS 的 `radial-gradient(<size> at X% Y%, …)`：X%/Y% 是**渐变色块内**的
    #    中心位置，色块本身锚定在 background-origin（默认 padding-box）。
    #    所以中心 = padding 原点 + size × 百分比，不是整张画布的比例。
    #    第一版按画布比例算，光晕跑到卡片中间，左上角该有的橙色完全没有。
    bg = _linear_grad((W, H), PU["body_stops"], 160.0)
    g1 = (BODY_PAD + 640 * 0.08, BODY_PAD + 340 * (-0.10))   # glow1: (73.2, -12)
    g2 = (BODY_PAD + 320 * 0.98, BODY_PAD + 220 * (-0.04))   # glow2: (335.6, 13.2)
    bg = _radial_glow(bg, g1, (320, 170), *PU["glow1"])
    bg = _radial_glow(bg, g2, (160, 110), *PU["glow2"])
    img = Image.fromarray(np.clip(bg, 0, 255).astype(np.uint8), "RGB")

    # ── wrap 投影：0 14px 34px rgba(120,90,70,.10) ──
    sh = Image.new("L", (W, H), 0)
    sh.paste(_rounded_mask((wrap_w, wrap_h), WRAP_R), (wrap_x, wrap_y + 14))
    sh = sh.filter(ImageFilter.GaussianBlur(17))
    if PU["shadow_a"] > 0:
        sh = sh.point(lambda v: int(v * PU["shadow_a"]))
    # ⚠️ 外阴影只应画在元素外部：不挖掉卡片区会让阴影糊进卡内
    _im2 = Image.new("L", (W, H), 0)
    _im2.paste(_rounded_mask((wrap_w, wrap_h), WRAP_R), (wrap_x, wrap_y))
    sh = Image.composite(Image.new("L", (W, H), 0), sh, _im2)
    img = Image.composite(Image.new("RGB", (W, H), PU["shadow"]), img, sh)

    # ── wrap 底板：rgba(255,250,245,.78) + 1px 白边 ──
    panel = Image.new("RGB", (wrap_w, wrap_h), PU["wrap_bg"])
    mask = _rounded_mask((wrap_w, wrap_h), WRAP_R).point(lambda v: int(v * PU["wrap_bg_a"]))
    img.paste(panel, (wrap_x, wrap_y), mask)

    d = ImageDraw.Draw(img)
    d.rounded_rectangle(
        [wrap_x, wrap_y, wrap_x + wrap_w - 1, wrap_y + wrap_h - 1], WRAP_R,
        outline=_blend_at(img, (wrap_x + wrap_w // 2, wrap_y),
                          PU["wrap_border"], PU["wrap_border_a"]), width=1,
    )

    # ── 头部：图标块 + 标题/副标题 + 右侧来源 ──
    head_y = wrap_y + WRAP_BORDER + WRAP_PAD_T          # 实测 43
    hic_x, hic_y = CONTENT_X, head_y + (HEAD_H - HIC_SZ) // 2   # 实测 y=46
    # 图标块：linear-gradient(135deg, rgba(255,164,130,.46), rgba(238,90,58,.22))
    hg = _linear_grad((HIC_SZ, HIC_SZ), [(0.0, PU["hic_a"][:3]), (1.0, PU["hic_b"][:3])], 135.0)
    # 该渐变是半透明叠在 wrap 上的：还原为在 wrap 底色上按 alpha 混合（对角方向）
    tt = np.linspace(0.0, 1.0, HIC_SZ, dtype=np.float32)
    aa2 = (tt[:, None] + tt[None, :]) / 2.0             # 135deg 对角权重
    aa2 = (PU["hic_a"][3] * (1 - aa2) + PU["hic_b"][3] * aa2)[..., None]
    base = np.array(PU["wrap_bg"], dtype=np.float32)[None, None, :]
    hg = hg * aa2 + base * (1 - aa2)
    hic = Image.fromarray(np.clip(hg, 0, 255).astype(np.uint8), "RGB")
    img.paste(hic, (hic_x, hic_y), _rounded_mask((HIC_SZ, HIC_SZ), HIC_R))
    ic = _icon("head_bed", HIC_IMG)
    if ic:
        img.paste(ic, (hic_x + (HIC_SZ - HIC_IMG) // 2, hic_y + (HIC_SZ - HIC_IMG) // 2), ic)

    tx = hic_x + HIC_SZ + 13                            # 实测 title x=104
    d.text((tx, head_y + 15), title, font=_cjk_font(21, bold=True),
           fill=PU["title"], anchor="lm")               # title 盒 43..73，中线 58
    sub = date + ((" · " + rng) if rng else "")
    d.text((tx, head_y + 33 + 9), sub, font=_cjk_font(13), fill=PU["sub"], anchor="lm")
    if when:
        d.text((CONTENT_X + CW, head_y + HEAD_H // 2), when, font=_cjk_font(12),
               fill=PU["sub"], anchor="rm")

    # ── 表头 ──
    ty = head_y + HEAD_H + HEAD_MB                      # 实测 111
    th_cy = ty + THEAD_H / 2
    for key, label, hint, icon, is_num, x0, x1 in _cols:
        pieces = []
        if icon:
            ic = _icon(icon, 17)
            if ic:
                pieces.append(("img", ic, 17))
        pieces.append(("txt", label, _cjk_font(13, bold=True), PU["th"]))
        if hint:
            pieces.append(("txt", hint, _cjk_font(11), PU["th_hint"]))
        total = 0.0
        for kind, obj, extra, *_c in pieces:
            total += (obj.size[0] + 3) if kind == "img" else d.textlength(obj, font=extra)
        # th/td 都是 padding 5px；居中列由居中抵消，左对齐列要补 5px
        # is_num: True/"num"=数字列(居中)｜"text"=文本列(左对齐)｜False=name 列(左对齐)
        cx = ((x0 + x1) / 2 - total / 2) if is_num is True or is_num == "num" else (x0 + 5)
        for piece in pieces:
            kind, obj, extra = piece[0], piece[1], piece[2]
            color = piece[3] if len(piece) > 3 else PU["th"]
            if kind == "img":
                img.paste(obj, (int(cx), int(th_cy - obj.size[1] / 2)), obj)
                cx += obj.size[0] + 3
            else:
                d.text((cx, th_cy), obj, font=extra, fill=color, anchor="lm")
                cx += d.textlength(obj, font=extra)
    _thly = int(ty + THEAD_H) - 1
    d.line([CONTENT_X, _thly, CONTENT_X + CW, _thly],
           fill=_blend_at(img, (CONTENT_X + 20, _thly),
                          PU["th_border"][:3], PU["th_border"][3]), width=2)

    # ── 数据行 ──
    ry = ty + THEAD_H
    medals = {1: "gold", 2: "iron", 3: "copper"}
    for i, r in enumerate(rows):
        rank = r.get("rank") or (i + 1)
        cells = r.get("cells") or {}
        row_cy = ry + ROW_H / 2
        if i > 0:
            _ryi = int(ry)
            d.line([CONTENT_X, _ryi, CONTENT_X + CW, _ryi],
                   fill=_blend_at(img, (CONTENT_X + 20, _ryi),
                                  PU["tr_border"][:3], PU["tr_border"][3]), width=1)
        # 名次：奖牌槽位固定（无名次图标也留空）→ 数字对齐同一竖线（实测 x=53/79）
        med = _icon(medals.get(rank, ""), 21) if rank in medals else None
        if med:
            img.paste(med, (MEDAL_X, int(row_cy - med.size[1] / 2)), med)
        d.text((RANK_NUM_X, row_cy), str(rank), font=_mono_font(13),
               fill=PU["rank_n"], anchor="lm")
        # 数据列
        for key, label, hint, icon, is_num, x0, x1 in _cols:
            v = cells.get(key)
            v = _html.unescape(str(v)) if v is not None else "—"
            if key == "name":
                # CSS 是 font-weight:600，但 Noto CJK 只有 400/700 两档；
                # 实测用 Regular 比 Bold 更贴近 Chromium（平均像素差 6.28 vs 6.81）
                d.text((x0 + 5, row_cy), v, font=_cjk_font(14, bold=False),
                       fill=PU["td"], anchor="lm")       # td padding-left 5
            elif is_num == "text":
                # 文本列（如竞技场「段位」）：中文字体 + 左对齐 + 常规字色
                # 段位含 MC 颜色码（§c大师），复用 _text_runs 着色
                cx = x0 + 5
                runs = _text_runs(v, PU["td"])
                total_w = sum(d.textlength(t, font=_cjk_font(14)) for t, _ in runs)
                avail = (x1 - x0) - 10
                scale_font = _cjk_font(14)
                if total_w > avail:      # 太长就截断（CSS 里是 nowrap + 溢出裁切）
                    runs = [(truncate(d, v, scale_font, avail), PU["td"])]
                for t, c in runs:
                    d.text((cx, row_cy), t, font=scale_font, fill=c, anchor="lm")
                    cx += d.textlength(t, font=scale_font)
            else:
                f_num = _mono_font(14)
                runs = _text_runs(v, PU["td_num"])
                total = sum(d.textlength(t, font=f_num) for t, _ in runs)
                sub = ""
                if key == "kills" and cells.get("final"):
                    sub = f"({_html.unescape(str(cells['final']))})"
                    total += d.textlength(sub, font=_cjk_font(12))
                cx = (x0 + x1) / 2 - total / 2
                for t, c in runs:
                    d.text((cx, row_cy), t, font=f_num, fill=c, anchor="lm")
                    cx += d.textlength(t, font=f_num)
                if sub:
                    d.text((cx, row_cy), sub, font=_cjk_font(12), fill=PU["up"], anchor="lm")
        ry += ROW_H

    # ── 新玩家条 ──
    if newp:
        ny = ry + NEWP_MT
        box = Image.new("RGB", (CW, NEWP_H), PU["newp_bg"])
        # ⚠️ ry = ty + THEAD_H，而 THEAD_H = 35.5 → ny 是 float。
        #   Pillow 的 paste 只接受整数坐标，传 float 直接 TypeError。
        #   之前被上层的 try 吞掉、静默回退 Chromium，所以有新人入榜时才暴露。
        img.paste(box, (CONTENT_X, int(ny)),
                  _rounded_mask((CW, NEWP_H), 12).point(lambda v: int(v * PU["newp_bg_a"])))
        nx = CONTENT_X + NEWP_PAD_X
        ic = _icon("egg", 15)
        if ic:
            img.paste(ic, (int(nx), int(ny + NEWP_H / 2 - ic.size[1] / 2)), ic)
            nx += 15 + 3
        names = "、".join(newp[:8]) + (f" 等 {len(newp)} 人" if len(newp) > 8 else "")
        d.text((nx, ny + NEWP_H / 2), f"新玩家（下次入榜）：{names}",
               font=_cjk_font(13), fill=PU["newp_fg"], anchor="lm")
        ry = ny + NEWP_H

    # ── 页脚（实测 foot y=466 h=21）──
    fy = ry + FOOT_MT + FOOT_H / 2
    _brand_txt = f"由 {brand} 生成"
    _brand_w = d.textlength(_brand_txt, font=_cjk_font(12))
    d.text((CONTENT_X, fy), _brand_txt, font=_cjk_font(12), fill=PU["foot"], anchor="lm")
    # v2.3.32: 渲染时间 —— 淡色小字跟在生成来源右侧，
    #   格式与双模式卡 #f-time 一致（YYYY-MM-DD HH:MM · NNNms），
    #   配色沿用页脚色 PU["foot"]（本就在背景上很淡），不另造新色
    _stamp = render_stamp(int((time.perf_counter() - _t0) * 1000))
    _stamp_x = CONTENT_X + _brand_w + 7
    d.text((_stamp_x, fy), _stamp, font=_cjk_font(11), fill=PU["foot"], anchor="lm")
    # 右侧两个药丸 + 中间渐变分隔线
    py = fy - 9
    ph = 18
    hash_txt = "#" + _hash8(date + "|" + str(payload.get("_seed") or ""))
    hw = d.textlength(hash_txt, font=_mono_font(12)) + 14
    hx = CONTENT_X + CW - hw
    d.rounded_rectangle([hx, py, hx + hw, py + ph], 6,
                        fill=_blend_at(img, (hx + 4, py + 2), PU["hash_bg"], PU["hash_bg_a"]))
    d.text((hx + hw / 2, fy), hash_txt, font=_mono_font(12), fill=PU["hash_fg"], anchor="mm")
    nx2 = hx
    if next_time:
        nt = f"下一轮 {next_time}"
        nw = d.textlength(nt, font=_cjk_font(12)) + 14
        nx2 = hx - 8 - nw
        d.rounded_rectangle([nx2, py, nx2 + nw, py + ph], 6,
                            fill=_blend_at(img, (nx2 + 4, py + 2), PU["newp_bg"], PU["newp_bg_a"]))
        d.text((nx2 + nw / 2, fy), nt, font=_cjk_font(12), fill=PU["newp_fg"], anchor="mm")
    # 渐变分隔线（中间实、两端渐隐，rgba(150,130,110,.25)）
    # ★ 起点要跟到时间后面，否则分隔线会压在时间上
    sx0 = int(_stamp_x + d.textlength(_stamp, font=_cjk_font(11)) + 8)
    sx1 = int(nx2 - 8)
    if sx1 > sx0 + 4:
        w = sx1 - sx0
        t = np.linspace(-1.0, 1.0, w, dtype=np.float32)
        a = np.clip(1.0 - np.abs(t), 0.0, 1.0) * 0.25
        # 明确 reshape 成 (1, w)：PIL 对一维数组的尺寸推断在不同版本不一致
        sep_mask = Image.fromarray((a * 255).astype(np.uint8).reshape(1, w), "L")
        img.paste(Image.new("RGB", (w, 1), (150, 130, 110)), (sx0, int(fy)), sep_mask)

    return img


def _hash8(s: str) -> str:
    """与模板 JS 一致的 FNV-1a 8 位十六进制"""
    h = 2166136261
    for ch in str(s):
        h ^= ord(ch)
        h = (h * 16777619) & 0xFFFFFFFF
    return f"{h:08x}"[:8]


def save_daily_rank_card(payload: dict, out_path: str | Path) -> Path:
    img = render_daily_rank_card(payload)
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    img.save(p, "JPEG", quality=95)
    return p

# ══════════════════════════════════════════════════════════
#  竞技场日榜（daily_arena_card.html）
#  与起床日榜同一套 CSS，差异：画布宽 660（内容宽 570）、8 列、标题「今日战绩 · 竞技场」
#  ⚠️ 以下几何全部由 Playwright 读 DOM 实测（/tmp/_geo_arena.py），勿按 CSS 估算：
#     body 660 宽 / wrap 616 / 内容 x=45 w=570 / thead 35.5 / 行高 44 / foot y=378
# ══════════════════════════════════════════════════════════
ARENA_W, ARENA_CONTENT_W = 660, 570
ARENA_TITLE = "今日战绩 · 竞技场"

# (key, label, hint, icon, kind, x0, x1)
#   kind: False=玩家名（左对齐粗体）｜"text"=文本列（左对齐）｜True=数字列（居中）
ARENA_COLS = [
    ("name",   "玩家", "", None,             False,  113.0, 246.7),
    ("div",    "段位", "", "i_arena_div",    "text", 246.7, 324.1),
    ("kills",  "击杀", "", "i_kills",        True,   324.1, 383.9),
    ("wins",   "胜场", "", "i_wins",         True,   383.9, 443.7),
    ("losses", "败场", "", "i_arena_losses", True,   443.7, 503.5),
    ("deaths", "死亡", "", "i_deaths",       True,   503.5, 563.3),
    ("kd",     "KD",   "", "i_kd",           True,   563.3, 615.0),
]


# 竞技场配色（逐项取自 daily_arena_card.html —— 是**冷蓝系**，与起床日榜的暖橙完全不同）
# 半透明值按「在 wrap 底色上预混合」估算，最终以像素对比校正
ARENA_PALETTE = {
    "body_stops": [(0.00, (243, 248, 255)), (0.45, (234, 241, 252)), (1.00, (239, 239, 248))],
    "glow1": ((148, 184, 255), 0.58),      # rgba(148,184,255,.58)
    "glow2": ((190, 214, 255), 0.48),      # rgba(190,214,255,.48)
    "wrap_bg": (246, 250, 255),            # rgba(246,250,255,.78)
    "hic_a": (128, 168, 255, 0.46),        # linear-gradient(135deg, rgba(128,168,255,.46), ...)
    "hic_b": (79, 110, 224, 0.22),         #                          ... rgba(79,110,224,.22))
    "title": (40, 51, 79),                 # #28334f
    "sub": (135, 146, 168),                # #8792a8
    "th": (125, 136, 160),                 # #7d88a0
    "th_hint": (159, 168, 187),            # th @ .72 on wrap_bg
    "th_border": (168, 190, 228, 0.58),    # rgba(168,190,228,.58)
    "tr_border": (180, 198, 230, 0.32),    # rgba(180,198,230,.32)
    "td": (58, 68, 89),                    # #3a4459
    "td_num": (61, 99, 224),               # #3d63e0
    "rank_n": (96, 104, 122),              # td @ .8 on wrap_bg
    "up": (177, 186, 201),                 # #8792a8 @ .62 on wrap_bg
    "newp_bg": (200, 215, 255), "newp_bg_a": 0.55,   # rgba(200,215,255,.55)
    "newp_fg": (74, 95, 146),              # #4a5f92
    "foot": (152, 162, 182),               # #98a2b6
    # .wrap 的 box-shadow 竞技场没有覆盖 → 沿用起床日榜的暖色阴影
}


def render_arena_daily_card(payload: dict) -> Image.Image:
    """渲染竞技场日榜卡（与 HTML 模板同数据、同外观）"""
    return render_daily_rank_card(payload, width=ARENA_W, content_w=ARENA_CONTENT_W,
                                  cols=ARENA_COLS, title=ARENA_TITLE,
                                  palette=ARENA_PALETTE)


def save_arena_daily_card(payload: dict, out_path: str | Path) -> Path:
    """渲染竞技场日榜并保存为 JPEG（bot 走这条）"""
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    render_arena_daily_card(payload).save(p, "JPEG", quality=95)
    return p

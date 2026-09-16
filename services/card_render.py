"""
统一卡片渲染（Pillow 直绘）

主题源：`data/templates/wdsj_dual_card.html`（双模式卡）——
暖色浅底 + 白色玻璃面板 + 暖橙/冷蓝/青三色强调 + Monocraft 数字。

为什么用 Pillow：服务器 i3-2130，Chromium 单卡约 870ms、常驻 394MB；
Pillow 约 80ms、56MB。

⚠️ 四条铁律（都是踩过的坑）
  1. 颜色/圆角/阴影一律取自模板 CSS，**不凭印象**（曾把暖色底画成深紫底）
  2. `ImageDraw` 在 **RGB 图**上画 4 元组 fill 会**忽略 alpha** → 用 blend_over() 预混合
  3. 中文字体必须区分 Regular/Bold（只找"第一个能用的"会全卡变粗）
  4. 尺寸按浏览器实测或等比重算，不按 CSS 字面估算
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

_ROOT = Path(__file__).resolve().parent.parent

# ══════════════════════════════════════════════════════════
#  字体
# ══════════════════════════════════════════════════════════
MONOCRAFT = _ROOT / "data" / "web_assets" / "monocraft.ttf"
ICON_DIR = _ROOT / "data" / "web_assets" / "icons"

CJK_REGULAR = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
]
CJK_BOLD = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
]

_font_cache: dict[tuple, ImageFont.FreeTypeFont] = {}
_resolved: dict[bool, tuple[str, int]] = {}


def _is_real_cjk(font) -> bool:
    """真字形 vs 豆腐块：两个不同汉字的位图应不同且有墨迹"""
    def ink(ch: str):
        im = Image.new("L", (48, 48), 0)
        ImageDraw.Draw(im).text((4, 4), ch, font=font, fill=255)
        return list(im.getdata())
    a, b = ink("测"), ink("试")
    return a != b and max(a) > 0


def resolve_cjk(bold: bool = False) -> tuple[str, int]:
    """找可用中文字体 (路径, ttc 索引)，Regular 与 Bold 分开解析"""
    if bold in _resolved:
        return _resolved[bold]
    for path in (CJK_BOLD if bold else CJK_REGULAR):
        if not Path(path).exists():
            continue
        for idx in range(8):
            try:
                f = ImageFont.truetype(path, 24, index=idx)
            except Exception:
                continue
            if _is_real_cjk(f):
                _resolved[bold] = (path, idx)
                return _resolved[bold]
    raise RuntimeError("找不到可用中文字体（需 fonts-noto-cjk 或 fonts-wqy-*）")


def cjk(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    key = ("cjk", size, bold)
    if key not in _font_cache:
        path, idx = resolve_cjk(bold)
        _font_cache[key] = ImageFont.truetype(path, size, index=idx)
    return _font_cache[key]


def mono(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Monocraft（模板里的 --mono / 数字字体）"""
    key = ("mono", size, bold)
    if key not in _font_cache:
        if MONOCRAFT.exists():
            f = ImageFont.truetype(str(MONOCRAFT), size)
            if bold:
                try:
                    f.set_variation_by_name("Bold")
                except Exception:
                    pass
            _font_cache[key] = f
        else:
            _font_cache[key] = cjk(size, bold)
    return _font_cache[key]


def line_h(font) -> int:
    a, d = font.getmetrics()
    return a + d


# ══════════════════════════════════════════════════════════
#  主题（逐项取自 wdsj_dual_card.html）
# ══════════════════════════════════════════════════════════
T = {
    # .card 背景：linear-gradient(160deg,#fbf6f0,#f4f0ec 42%,#eef1f6) + 两处大半径光晕
    "bg_stops": [(0.00, (251, 246, 240)), (0.42, (244, 240, 236)), (1.00, (238, 241, 246))],
    "bg_angle": 160.0,
    "bg_glows": [
        # radial-gradient(1100px 520px at 12% -8%, rgba(255,183,154,.34), transparent 60%)
        ((0.12, -0.08), (1100.0, 520.0), (255, 183, 154), 0.34, 0.60),
        ((0.88, -0.08), (1100.0, 520.0), (158, 188, 255), 0.36, 0.60),
    ],
    # 文字
    "text":       (44, 38, 34),      # #2c2622  .t-name
    "text_sub":   (138, 127, 118),   # #8a7f76  .t-sub
    "text_dim":   (167, 154, 143),   # #a79a8f  .t-time
    "label":      (152, 140, 128),   # #988c80  .cell .cl
    "value":      (50, 43, 38),      # #322b26  .cell .cv
    "value_warm": (74, 47, 38),      # #4a2f26  .panel.bw .cv
    "value_cool": (40, 51, 79),      # #28334f  .panel.ar .cv
    "foot":       (162, 146, 135),   # #a29287  .foot
    "hash_fg":    (194, 184, 173),   # #c2b8ad  .foot .hash
    # 玻璃
    "white":      (255, 255, 255),
    "shadow":     (120, 80, 60),     # rgba(120,80,60,…) 系
    # 强调（panel 三色系：暖红 / 冷蓝 / 青）
    "warm":  ((255, 139, 112), (240, 90, 60)),   # #ff8b70 → #f05a3c
    "cool":  ((111, 150, 255), (61, 99, 224)),   # #6f96ff → #3d63e0
    "teal":  ((67, 196, 176), (43, 147, 162)),   # #43c4b0 → #2b93a2
    "brand": ((255, 158, 122), (238, 90, 58)),   # #ff9e7a → #ee5a3a（按钮/主色）
}

# 卡片几何（680 宽竖版；按双模式卡的材质比例重算，非字面照搬）
W = 680
PAD = 20                      # 画布外边距
CARD_W = W - PAD * 2          # 640
R_CARD = 22                   # 外层玻璃卡圆角（双卡 34px@2200 → 竖版取 22）
PAD_IN = 20                   # 卡内边距
CONTENT_W = CARD_W - PAD_IN * 2   # 600
R_BLOCK = 16                  # 内部块圆角（topbar / section）
R_CELL = 11
GAP = 10
FOOT_H = 34
MEDAL_W = 30          # 表格首列的奖牌槽位宽（与日榜卡一致：槽位固定，名次对齐）


# ══════════════════════════════════════════════════════════
#  渐变 / 遮罩 / 混合
# ══════════════════════════════════════════════════════════
def linear_grad(size, stops, angle_deg: float = 160.0) -> np.ndarray:
    """CSS linear-gradient(<angle>deg,…) → float32 RGB（0deg 朝上，90deg 朝右）"""
    Wd, Hd = size
    a = math.radians(angle_deg)
    dx, dy = math.sin(a), -math.cos(a)
    L = abs(Wd * dx) + abs(Hd * dy) or 1.0
    xs = np.arange(Wd, dtype=np.float32)[None, :]
    ys = np.arange(Hd, dtype=np.float32)[:, None]
    proj = ((xs - Wd / 2) * dx + (ys - Hd / 2) * dy) / L + 0.5
    ps = np.array([p for p, _ in stops], dtype=np.float32)
    out = np.zeros((Hd, Wd, 3), dtype=np.float32)
    for ch in range(3):
        cs = np.array([c[ch] for _, c in stops], dtype=np.float32)
        out[..., ch] = np.interp(proj, ps, cs)
    return out


def radial_glow(canvas: np.ndarray, center, radii, color, alpha: float,
                fade: float = 0.60) -> np.ndarray:
    """CSS radial-gradient 近似。`at X% Y%` 是**色块内**位置，调用方传画布坐标。"""
    Hd, Wd = canvas.shape[:2]
    cx, cy = center
    rx, ry = radii
    xs = np.arange(Wd, dtype=np.float32)[None, :]
    ys = np.arange(Hd, dtype=np.float32)[:, None]
    d = np.sqrt(((xs - cx) / max(rx, 1e-6)) ** 2 + ((ys - cy) / max(ry, 1e-6)) ** 2)
    a = np.clip(1.0 - d / fade, 0.0, 1.0) * alpha
    a3 = a[..., None]
    c = np.array(color, dtype=np.float32)[None, None, :]
    return canvas * (1 - a3) + c * a3


def make_bg(size) -> Image.Image:
    """画布背景：暖色线性渐变 + 两处光晕（源：.card 的 background）"""
    Wd, Hd = size
    bg = linear_grad(size, T["bg_stops"], T["bg_angle"])
    for (px, py), (rx, ry), color, alpha, fade in T["bg_glows"]:
        bg = radial_glow(bg, (Wd * px, Hd * py), (rx * Wd / 2200.0, ry * Wd / 2200.0),
                         color, alpha, fade)
    return Image.fromarray(np.clip(bg, 0, 255).astype(np.uint8), "RGB")


def rounded_mask(size, radius: int) -> Image.Image:
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius, fill=255)
    return m


def blend_over(base: Image.Image, xy, color, alpha: float):
    """取底图某点颜色做预混合后返回实色。

    ⚠️ 必须预混合：RGB 图上 ImageDraw 画 4 元组 fill 会忽略 alpha（半透明变实心）
    """
    px = base.getpixel((int(xy[0]), int(xy[1])))
    px = px[:3] if isinstance(px, tuple) else (px, px, px)
    return tuple(int(round(color[i] * alpha + px[i] * (1 - alpha))) for i in range(3))


def paste_glass(img: Image.Image, box, radius: int, alpha: float,
                border_alpha: float = 0.85, shadow: bool = True) -> None:
    """贴一块白色玻璃面板：投影 + 半透明白底 + 1px 白边

    ⚠️ 白底必须用遮罩 paste（不能用 round_rectangle fill 传 alpha）
    """
    x, y, w, h = [int(v) for v in box]
    if shadow:
        sh = Image.new("L", img.size, 0)
        sh.paste(rounded_mask((w, h), radius), (x, y + 8))
        sh = sh.filter(ImageFilter.GaussianBlur(12))
        sh = sh.point(lambda v: int(v * 0.10))
        img.paste(Image.new("RGB", img.size, T["shadow"]), (0, 0), sh)
    panel = Image.new("RGB", (w, h), T["white"])
    paste_mask = rounded_mask((w, h), radius).point(lambda v: int(v * alpha))
    img.paste(panel, (x, y), paste_mask)
    # 1px 白边
    ed = ImageDraw.Draw(img)
    ed.rounded_rectangle([x, y, x + w - 1, y + h - 1], radius,
                         outline=blend_over(img, (x + w // 2, y), T["white"], border_alpha),
                         width=1)


def accent_img(size, accent) -> Image.Image:
    """强调色渐变块（linear-gradient(135deg, c1, c2)）"""
    c1, c2 = T.get(accent, T["brand"])
    g = linear_grad(size, [(0.0, c1), (1.0, c2)], 135.0)
    return Image.fromarray(np.clip(g, 0, 255).astype(np.uint8), "RGB")


_icon_cache: dict[str, Image.Image] = {}


def load_icon(name: str, size: int) -> Image.Image | None:
    key = f"{name}@{size}"
    if key in _icon_cache:
        return _icon_cache[key]
    for cand in (ICON_DIR / f"{name}.png", ICON_DIR / name):
        if cand.exists():
            im = Image.open(cand).convert("RGBA")
            if im.size != (size, size):
                nearest = getattr(getattr(Image, "Resampling", Image), "NEAREST",
                                  Image.NEAREST)
                im = im.resize((size, size), nearest)
            _icon_cache[key] = im
            return im
    return None


def paste_icon(dst: Image.Image, name: str, xy, size: int) -> bool:
    ic = load_icon(name, size)
    if ic is None:
        return False
    dst.paste(ic, (int(xy[0]), int(xy[1])), ic)
    return True


def truncate(draw: ImageDraw.ImageDraw, s: str, font, max_w: float,
             ellipsis: str = "…") -> str:
    """按实际渲染宽度截断（中日韩+拉丁混排比按字符数准）"""
    if draw.textlength(s, font=font) <= max_w:
        return s
    ew = draw.textlength(ellipsis, font=font)
    out = ""
    for ch in s:
        if draw.textlength(out + ch, font=font) + ew > max_w:
            break
        out += ch
    return out + ellipsis


_scratch = Image.new("RGB", (8, 8))
_sd = ImageDraw.Draw(_scratch)


# ══════════════════════════════════════════════════════════
#  卡片构建器（ops 列表 → 先算高度，再一次性绘制）
# ══════════════════════════════════════════════════════════
def _color(key: str | None, default=(50, 43, 38)):
    """安全取单个颜色。

    ⚠️ 主题里 "warm"/"cool"/"teal"/"brand" 是**渐变色对**（两个三元组），
      误当文字色传给 ImageDraw 会报 "color must be int, or tuple of 1/3/4 elements"。
      这里校验形状，不是纯颜色就回退默认值。
    """
    v = T.get(key) if key else None
    if isinstance(v, tuple) and len(v) == 3 and all(isinstance(x, int) for x in v):
        return v
    return default


class Card:
    """统一风格卡片。顺序 add_* 追加区块，finish() 出图。

    所有卡片共用同一外观：暖色浅底 + 白色玻璃区块 + 三色强调。
    """

    def __init__(self, width: int = W):
        self.W = width
        self.cw = width - PAD * 2
        self.cw_inner = self.cw - PAD_IN * 2
        self._ops: list[tuple] = []

    # ── 区块：顶部玩家/主题条 ──
    def add_header(self, title: str, subtitle: str = "", badge: str = "",
                   badge_accent: str = "brand", avatar_icon: str | None = None,
                   avatar_text: str = "", accent: str = "warm") -> None:
        self._ops.append(("header", dict(title=title, subtitle=subtitle, badge=badge,
                                         badge_accent=badge_accent,
                                         avatar_icon=avatar_icon, avatar_text=avatar_text,
                                         accent=accent)))

    # ── 区块：带彩色标题条的面板 ──
    def add_section(self, title: str, items: list | None = None, cols: int = 3,
                    accent: str = "warm", icon: str | None = None,
                    note: str = "", val_color: str | None = None) -> None:
        """items: [(icon名或None, label, value)]；value 为空则跳过该格"""
        self._ops.append(("section", dict(title=title, items=items or [], cols=cols,
                                          accent=accent, icon=icon, note=note,
                                          val_color=val_color)))

    # ── 区块：纯文本行 ──
    def add_lines(self, lines: list[str], size: int = 13,
                  color: str = "text_sub", accent: str = "") -> None:
        self._ops.append(("lines", dict(lines=lines, size=size, color=color, accent=accent)))

    # ── 区块：表格（排行榜 / 搜索结果 / 撤回记录…） ──
    def add_table(self, columns: list[dict], rows: list[dict], title: str = "",
                  icon: str | None = None, accent: str = "warm", note: str = "",
                  medal: bool = False) -> None:
        """columns: [{"key","label","w":相对宽度,"align":"l"/"c"/"r","num":bool,"icon":图标名}]
           rows:    [{"rank":1, "cells":{"key":值}}]，值可为 (主文本, 小字后缀) 元组
           medal:   前三名显示金银铜图标（模板里的积分奖牌）
        """
        self._ops.append(("table", dict(columns=columns, rows=rows, title=title,
                                        icon=icon, accent=accent, note=note,
                                        medal=medal)))

    # ── 区块：自定义绘制（自由发挥，h 由调用方给） ──
    def add_custom(self, h: int, fn) -> None:
        """fn(img, x, y, w) —— 在内容区画任意内容"""
        self._ops.append(("custom", dict(h=h, fn=fn)))

    # ── 区块：页脚 ──
    def add_foot(self, left: str = "", right: str = "", hash_text: str = "") -> None:
        self._ops.append(("foot", dict(left=left, right=right, hash_text=hash_text)))

    # ── 高度计算 ──
    def _block_h(self, kind: str, p: dict) -> int:
        if kind == "header":
            av = 52
            return 14 + av + 14
        if kind == "section":
            h = 14                                   # 面板上内边距
            h += 10 + 30 + 10                        # 标题条（icon 30 + 上下 10）
            h += 14                                  # 标题条下间距
            items = [it for it in p["items"] if it[2] not in (None, "")]
            if items:
                cols = max(1, p["cols"])
                rows = (len(items) + cols - 1) // cols
                h += rows * self._cell_h() + (rows - 1) * GAP
            if p.get("note"):
                h += 12 + line_h(cjk(12))
            h += 14                                  # 面板下内边距
            return h
        if kind == "lines":
            return sum(line_h(cjk(p["size"])) + 4 for _ in p["lines"]) + 6
        if kind == "table":
            h = 14 + 50 + 14                     # 面板内边距 + 标题条 + 间距
            h += 26                              # 表头行
            h += len(p["rows"]) * self._row_h()
            if p.get("note"):
                h += 12 + line_h(cjk(12))
            h += 14
            return h
        if kind == "custom":
            return p["h"]
        if kind == "foot":
            has = [x for x in (p["left"], p["right"], p["hash_text"]) if x]
            return FOOT_H if has else 0
        return 0

    def _cell_h(self) -> int:
        return 7 + line_h(cjk(11)) + line_h(mono(16)) + 7

    def _row_h(self) -> int:
        """表格数据行行高（容纳 15px 数字 + 上下内边距）"""
        return 10 + line_h(mono(15)) + 10

    def finish(self) -> Image.Image:
        # 1) 算总高
        blocks = []
        total = PAD + 14 + PAD_IN          # 顶部：外边距 + 卡上内边距
        for kind, p in self._ops:
            h = self._block_h(kind, p)
            if h <= 0:
                continue
            blocks.append((kind, p, h))
            total += h + GAP
        if blocks:
            total -= GAP                    # 最后一个不加间距
        total += PAD_IN + 14 + PAD

        H = int(total)
        img = make_bg((self.W, H))

        # 2) 外层玻璃卡（整张卡）
        card_box = (PAD, PAD, self.cw, H - PAD * 2)
        paste_glass(img, card_box, R_CARD, 0.42, border_alpha=0.9, shadow=False)

        # 3) 逐块绘制
        y = PAD + 14 + PAD_IN
        x = PAD + PAD_IN
        for kind, p, h in blocks:
            getattr(self, f"_draw_{kind}")(img, x, y, self.cw_inner, p, h)
            y += h + GAP

        return img

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        self.finish().save(p, "JPEG", quality=95)
        return p

    # ── 各区块绘制 ──
    def _draw_header(self, img, x, y, w, p, h) -> None:
        paste_glass(img, (x, y, w, h), R_BLOCK, 0.55, border_alpha=0.85)
        d = ImageDraw.Draw(img)
        av_sz = 52
        ax, ay = x + 14, y + 14
        # 头像块
        paste_icon_acc = accent_img((av_sz, av_sz), p.get("accent", "warm"))
        img.paste(paste_icon_acc, (ax, ay), rounded_mask((av_sz, av_sz), 13))
        if p.get("avatar_icon") and paste_icon(img, p["avatar_icon"], (ax + 10, ay + 10), 32):
            pass
        else:
            t = (p.get("avatar_text") or (p["title"] or "?")[:2]).upper()
            d.text((ax + av_sz / 2, ay + av_sz / 2), t, font=mono(20),
                   fill=T["white"], anchor="mm")
        # 标题 + 副标题
        tx = ax + av_sz + 14
        d.text((tx, y + 14 + 16), truncate(d, p["title"], mono(24), w - (tx - x) - 150),
               font=mono(24), fill=T["text"], anchor="lm")
        if p.get("subtitle"):
            d.text((tx, y + 14 + 42), truncate(d, p["subtitle"], cjk(12), w - (tx - x) - 150),
                   font=cjk(12), fill=T["text_sub"], anchor="lm")
        # 右侧徽章
        if p.get("badge"):
            bf = cjk(13, bold=True)
            bw = d.textlength(p["badge"], font=bf) + 26
            bh = 26
            bx, by = x + w - 14 - bw, y + (h - bh) / 2
            badge_img = accent_img((int(bw), bh), p.get("badge_accent", "brand"))
            img.paste(badge_img, (int(bx), int(by)), rounded_mask((int(bw), bh), 13))
            d = ImageDraw.Draw(img)
            d.text((bx + bw / 2, by + bh / 2), p["badge"], font=bf,
                   fill=T["white"], anchor="mm")

    def _draw_section(self, img, x, y, w, p, h) -> None:
        paste_glass(img, (x, y, w, h), R_BLOCK, 0.52, border_alpha=0.85)
        d = ImageDraw.Draw(img)
        # 标题条
        bh = 50
        bar = accent_img((w - 28, bh), p.get("accent", "warm"))
        img.paste(bar, (x + 14, y + 14), rounded_mask((w - 28, bh), 12))
        d = ImageDraw.Draw(img)
        ix = x + 14 + 14
        if p.get("icon"):
            paste_icon(img, p["icon"], (ix, y + 14 + 11), 28)
            d = ImageDraw.Draw(img)
        else:
            d.ellipse([ix + 6, y + 14 + 18, ix + 22, y + 14 + 34], fill=(255, 255, 255))
        d.text((ix + 34, y + 14 + bh / 2), p["title"], font=cjk(17, bold=True),
               fill=T["white"], anchor="lm")
        cnt = len([it for it in p["items"] if it[2] not in (None, "")])
        if cnt:
            d.text((x + w - 28, y + 14 + bh / 2), f"{cnt} 项", font=mono(12),
                   fill=(255, 255, 255), anchor="rm")

        # 字段格
        cy = y + 14 + bh + 14
        items = [it for it in p["items"] if it[2] not in (None, "")]
        if items:
            cols = max(1, p["cols"])
            cw = (w - 28 - (cols - 1) * GAP) / cols
            ch = self._cell_h()
            for i, (ic, label, value) in enumerate(items):
                r, c = divmod(i, cols)
                cx = x + 14 + c * (cw + GAP)
                cyy = cy + r * (ch + GAP)
                paste_glass(img, (cx, cyy, int(cw), ch), R_CELL, 0.62,
                            border_alpha=0.88, shadow=False)
                dd = ImageDraw.Draw(img)
                vx = cx + 8
                if ic and paste_icon(img, ic, (cx + 8, cyy + (ch - 26) / 2), 26):
                    dd = ImageDraw.Draw(img)
                    vx = cx + 8 + 26 + 8
                dd.text((vx, cyy + 7 + line_h(cjk(11)) / 2),
                        truncate(dd, str(label), cjk(11), int(cw) - (vx - cx) - 6),
                        font=cjk(11), fill=T["label"], anchor="lm")
                vcol = _color(p.get("val_color"), T["value"])
                dd.text((vx, cyy + ch - 7 - line_h(mono(16)) / 2),
                        truncate(dd, str(value), mono(16), int(cw) - (vx - cx) - 6),
                        font=mono(16), fill=vcol, anchor="lm")
            cy += ((len(items) + cols - 1) // cols) * (ch + GAP)

        # 备注
        if p.get("note"):
            d.text((x + 14, cy + 2), truncate(d, p["note"], cjk(12), w - 28),
                   font=cjk(12), fill=T["text_dim"], anchor="lt")

    def _draw_lines(self, img, x, y, w, p, h) -> None:
        d = ImageDraw.Draw(img)
        col = _color(p.get("color"), T["text_sub"])
        cy = y + 3
        for ln in p["lines"]:
            f = cjk(p["size"])
            d.text((x, cy), truncate(d, ln, f, w), font=f, fill=col, anchor="lt")
            cy += line_h(f) + 4

    def _draw_custom(self, img, x, y, w, p, h) -> None:
        p["fn"](img, x, y, w)

    def _draw_table(self, img, x, y, w, p, h) -> None:
        paste_glass(img, (x, y, w, h), R_BLOCK, 0.52, border_alpha=0.85)
        d = ImageDraw.Draw(img)
        # 标题条
        bh = 50
        bar = accent_img((w - 28, bh), p.get("accent", "warm"))
        img.paste(bar, (x + 14, y + 14), rounded_mask((w - 28, bh), 12))
        d = ImageDraw.Draw(img)
        ix = x + 28
        if p.get("icon"):
            paste_icon(img, p["icon"], (ix, y + 14 + 11), 28)
            d = ImageDraw.Draw(img)
        else:
            d.ellipse([ix + 6, y + 14 + 18, ix + 22, y + 14 + 34], fill=(255, 255, 255))
        d.text((ix + 34, y + 14 + bh / 2), p["title"], font=cjk(17, bold=True),
               fill=T["white"], anchor="lm")
        if p["rows"]:
            d.text((x + w - 28, y + 14 + bh / 2), f"{len(p['rows'])} 行", font=mono(12),
                   fill=(255, 255, 255), anchor="rm")

        # 列几何：按 w 权重分配
        cols = p["columns"]
        totw = sum(max(0.01, c.get("w", 1)) for c in cols) or 1.0
        avail = w - 28 - MEDAL_W if p.get("medal") else w - 28
        xs, acc = [], x + 14
        for c in cols:
            cwid = avail * (max(0.01, c.get("w", 1)) / totw)
            xs.append((acc, acc + cwid))
            acc += cwid

        # 表头行
        hy = y + 14 + bh + 14
        hyc = hy + 13
        for (c, (cx0, cx1)) in zip(cols, xs):
            lx = cx0 + (MEDAL_W if p.get("medal") else 0) if c is cols[0] else cx0
            t = c["label"]
            f = cjk(11, bold=True)
            tw = d.textlength(t, font=f)
            align = c.get("align", "l")
            if align == "c":
                px = (lx + cx1) / 2 - tw / 2
            elif align == "r":
                px = cx1 - tw
            else:
                px = lx + 5
            if c.get("icon"):
                ic = load_icon(c["icon"], 15)
                if ic:
                    img.paste(ic, (int(px), int(hyc - 7)), ic)
                    d = ImageDraw.Draw(img)
                    px += 18
            d.text((px, hyc), t, font=f, fill=T["label"], anchor="lm")
        # 表头下细线
        ly = hy + 26 - 1
        d.line([x + 14, ly, x + w - 14, ly],
               fill=blend_over(img, (x + 20, ly), (215, 190, 175), 0.55), width=1)

        # 数据行
        rh = self._row_h()
        medals = {1: "gold", 2: "iron", 3: "copper"}
        for i, r in enumerate(p["rows"]):
            ry = hy + 26 + i * rh
            cyc = ry + rh / 2
            if i > 0:
                d.line([x + 14, ry, x + w - 14, ry],
                       fill=blend_over(img, (x + 20, ry), (215, 190, 175), 0.30), width=1)
            cells = r.get("cells") or {}
            for ci, (c, (cx0, cx1)) in enumerate(zip(cols, xs)):
                k = c["key"]
                v = cells.get(k)
                if v is None:
                    v = ""
                sub = ""
                if isinstance(v, (tuple, list)):
                    v, sub = (v + ("",))[:2]
                v = str(v)
                lx = cx0
                if ci == 0 and p.get("medal"):
                    rk = r.get("rank") or (i + 1)
                    med = medals.get(rk)
                    if med:
                        ic = load_icon(med, 20)
                        if ic:
                            img.paste(ic, (int(cx0), int(cyc - 10)), ic)
                            d = ImageDraw.Draw(img)
                    else:
                        d.text((cx0 + 2, cyc), str(rk), font=mono(12),
                               fill=T["text_dim"], anchor="lm")
                    lx = cx0 + MEDAL_W
                f = mono(15) if c.get("num") else cjk(14, bold=(ci == 0 and not p.get("medal")))
                col = _color(c.get("color"), T["value"]) if c.get("num") else T["text"]
                tw = d.textlength(v, font=f)
                sw = d.textlength(sub, font=cjk(11)) if sub else 0
                align = c.get("align", "l")
                if align == "c":
                    px = (lx + cx1) / 2 - (tw + sw) / 2
                elif align == "r":
                    px = cx1 - tw - sw
                else:
                    px = lx + 5
                maxw = (cx1 - lx) - 10
                v = truncate(d, v, f, maxw)
                tw = d.textlength(v, font=f)
                d.text((px, cyc), v, font=f, fill=col, anchor="lm")
                if sub:
                    d.text((px + tw, cyc), sub, font=cjk(11),
                           fill=T["text_dim"], anchor="lm")

        # 备注
        if p.get("note"):
            ny = hy + 26 + len(p["rows"]) * rh + 2
            d.text((x + 14, ny), truncate(d, p["note"], cjk(12), w - 28),
                   font=cjk(12), fill=T["text_dim"], anchor="lt")

    def _draw_foot(self, img, x, y, w, p, h) -> None:
        d = ImageDraw.Draw(img)
        cy = y + h / 2
        left = p.get("left") or ""
        if left:
            d.text((x, cy), left, font=cjk(11), fill=T["foot"], anchor="lm")
        right = p.get("right") or ""
        hx = x + w
        if p.get("hash_text"):
            f = mono(11)
            hw = d.textlength(p["hash_text"], font=f) + 16
            bx = x + w - hw
            d.rounded_rectangle([bx, cy - 9, bx + hw, cy + 9], 7,
                                fill=blend_over(img, (bx + 3, cy - 7),
                                                (150, 130, 110), 0.09))
            d.text((bx + hw / 2, cy), p["hash_text"], font=f,
                   fill=T["hash_fg"], anchor="mm")
            hx = bx - 8
        if right:
            f = cjk(11)
            tw = d.textlength(right, font=f)
            d.text((hx - tw, cy), right, font=f, fill=T["foot"], anchor="lm")


def hash8(s: str) -> str:
    """FNV-1a 8 位十六进制（与模板 JS 一致）"""
    h = 0x811C9DC5
    for ch in str(s):
        h ^= ord(ch)
        h = (h * 0x01000193) & 0xFFFFFFFF
    return f"{h:08x}"[:8]

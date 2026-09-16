"""
Pillow 绘图原语库（供各卡片复刻使用）

用途：把 Chromium 渲染换成 Pillow 直绘，**提速的同时保住原画质**。
服务器是 i3-2130（2011 年双核），Chromium 单卡约 870ms、常驻 394MB；
Pillow 约 80ms、56MB。

⚠️ 本模块**只提供原语**，不含主题、不含布局框架。
   每张卡片的外观必须照它自己的 HTML 模板 1:1 复刻 ——
   主题/配色/圆角都写在各自的卡片模块里，不要在这里统一。

四条铁律（都是踩过的坑）：
  1. 颜色/尺寸一律照抄模板 CSS，**不凭印象**（曾把暖色底画成深紫底，全部返工）
  2. `ImageDraw` 在 **RGB 图**上画 4 元组 fill 会**忽略 alpha** → 用 blend_over() 预混合
  3. 中文字体必须区分 Regular/Bold（只找"第一个能用的"会拿到 Bold，全卡变粗）
  4. 尺寸按浏览器实测或等比重算，不按 CSS 字面估算
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

_ROOT = Path(__file__).resolve().parent.parent

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


# ══════════════════════════════════════════════════════════
#  字体
# ══════════════════════════════════════════════════════════
def _is_real_cjk(font) -> bool:
    """真字形 vs 豆腐块：两个不同汉字的位图应不同且有墨迹"""
    def ink(ch: str):
        im = Image.new("L", (48, 48), 0)
        ImageDraw.Draw(im).text((4, 4), ch, font=font, fill=255)
        return list(im.getdata())
    a, b = ink("测"), ink("试")
    return a != b and max(a) > 0


def resolve_cjk(bold: bool = False) -> tuple[str, int]:
    """找可用中文字体 (路径, ttc 索引)。Regular 与 Bold 分开解析。"""
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
    """中文字体（思源黑体）。size 会取整 —— CSS 里的 10.5px 之类 PIL 不收。"""
    size = int(round(size))
    key = ("cjk", size, bold)
    if key not in _font_cache:
        path, idx = resolve_cjk(bold)
        _font_cache[key] = ImageFont.truetype(path, size, index=idx)
    return _font_cache[key]


def mono(size: int) -> ImageFont.FreeTypeFont:
    """Monocraft（模板里的 --mono / 数字字体）。size 会取整。"""
    size = int(round(size))
    key = ("mono", size)
    if key not in _font_cache:
        if MONOCRAFT.exists():
            _font_cache[key] = ImageFont.truetype(str(MONOCRAFT), size)
        else:
            _font_cache[key] = cjk(size)
    return _font_cache[key]


def line_h(font) -> int:
    a, d = font.getmetrics()
    return a + d


# ══════════════════════════════════════════════════════════
#  渐变
# ══════════════════════════════════════════════════════════
def linear_grad(size, stops, angle_deg: float = 160.0) -> np.ndarray:
    """CSS linear-gradient(<angle>deg, ...) → float32 RGB 数组

    angle 语义与 CSS 一致：0deg 指向**上**，90deg 指向**右**。
    stops: [(位置0~1, (r,g,b)), ...]
    """
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
    """CSS radial-gradient 近似：中心 alpha → fade 处全透明。

    ⚠️ CSS 里 `radial-gradient(640px 340px at 8% -10%, …)` 的 `8% -10%` 是
      **渐变色块内**的位置，色块锚定 background-origin（默认 padding-box）。
      调用方要传**画布坐标**：中心 = padding原点 + size × 百分比。
      （按画布比例算会让光晕跑偏，日榜卡复刻时踩过，左上角差 23.9）
    """
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


def conic_hint(canvas: np.ndarray, center, angles: list, sweep_deg: float = 60.0,
               color=(255, 255, 255), alpha: float = 0.08) -> np.ndarray:
    """CSS conic-gradient 的简化：在指定角度附近加扇形高光（点缀用）"""
    Hd, Wd = canvas.shape[:2]
    cx, cy = center
    ys, xs = np.mgrid[0:Hd, 0:Wd]
    ang = np.degrees(np.arctan2(ys - cy, xs - cx)) % 360.0
    mask = np.zeros((Hd, Wd), dtype=np.float32)
    for a0 in angles:
        d = np.abs((ang - a0 + 180) % 360 - 180)
        mask += np.clip(1.0 - d / sweep_deg, 0.0, 1.0)
    mask = np.clip(mask, 0, 1) * alpha
    c = np.array(color, dtype=np.float32)[None, None, :]
    return canvas * (1 - mask[..., None]) + c * mask[..., None]


def to_img(arr: np.ndarray) -> Image.Image:
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


def radial_circle(size, inner, outer, cx_pct: float = 50.0, cy_pct: float = 50.0) -> Image.Image:
    """CSS radial-gradient(circle at X% Y%, inner, outer) → RGB 贴图

    用于棋子/头像等球面质感（模板里黑子是 `circle at 35% 35%,#666,#0a0a0a`）。
    """
    Wd, Hd = int(size[0]), int(size[1])
    cx, cy = Wd * cx_pct / 100.0, Hd * cy_pct / 100.0
    # 用聚焦点到四角的最大距离作渐变半径，保证边缘不会被提前截断
    r_max = max(math.hypot(cx, cy), math.hypot(Wd - cx, cy),
                math.hypot(cx, Hd - cy), math.hypot(Wd - cx, Hd - cy)) or 1.0
    xs = np.arange(Wd, dtype=np.float32)[None, :]
    ys = np.arange(Hd, dtype=np.float32)[:, None]
    t = np.clip(np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2) / r_max, 0.0, 1.0)
    out = np.zeros((Hd, Wd, 3), dtype=np.float32)
    for i in range(3):
        out[..., i] = inner[i] + (outer[i] - inner[i]) * t
    return to_img(out)


def ellipse_glow2(canvas: np.ndarray, center, radii, color, alpha: float,
                  fade: float = 0.50) -> np.ndarray:
    """与 radial_glow 相同，语义别名 —— 用于 CSS 里写 `ellipse` 的场合"""
    return radial_glow(canvas, center, radii, color, alpha, fade)


# ══════════════════════════════════════════════════════════
#  圆角 / 遮罩 / 混合
# ══════════════════════════════════════════════════════════
def rounded_mask(size, radius: int) -> Image.Image:
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius, fill=255)
    return m


def ring_mask(size, radius: int, width: int = 1) -> Image.Image:
    """圆角描边环遮罩（外圆角 - 内圆角），用于 1px 渐变边框

    ⚠️ 合成方向极易写反：`Image.composite(A, B, mask)` 是
      **mask 处取 A、非 mask 处取 B**。
      想要"环"= 外圆角内 ∩ 内圆角外 → composite(0, outer, inner)：
        inner=255（卡内）→ 0（透明）；inner=0（边缘）→ outer（显示）
      若写成 composite(255, outer, inner) 会得到**整块 255**，
      于是渐变边框被当作实色铺满整张卡片（五子棋卡首版整图偏亮偏紫的根因）。
    """
    w, h = size
    outer = rounded_mask((w, h), radius)
    inner = Image.new("L", (w, h), 0)
    ImageDraw.Draw(inner).rounded_rectangle(
        [width, width, w - 1 - width, h - 1 - width],
        max(0, radius - width), fill=255)
    return Image.composite(Image.new("L", (w, h), 0), outer, inner)


def blend_over(base: Image.Image, xy, color, alpha: float):
    """取底图某点颜色，把 color 按 alpha 预混合后返回不透明实色。

    ⚠️ 必须预混合：`ImageDraw` 在 **RGB 图**上画 4 元组 fill 会**忽略 alpha**，
      半透明直接画成实心（日榜卡页脚药丸因此变实心深棕，该区域像素差 61）。
    """
    px = base.getpixel((int(xy[0]), int(xy[1])))
    px = px[:3] if isinstance(px, tuple) else (px, px, px)
    return tuple(int(round(color[i] * alpha + px[i] * (1 - alpha))) for i in range(3))


def paste_alpha_rounded(dst: Image.Image, src: Image.Image, xy, radius: int,
                        alpha: float = 1.0):
    """把 src 以圆角遮罩贴到 dst（可整体半透明）

    ⚠️ 白底/面板类一定要走这里，别用 rounded_rectangle(fill=(r,g,b,a))
    """
    mask = rounded_mask(src.size, radius)
    if alpha < 1.0:
        mask = mask.point(lambda v: int(v * alpha))
    dst.paste(src, (int(xy[0]), int(xy[1])), mask)


def soft_shadow(canvas_size, box, radius: int, blur: float = 16.0,
                alpha: float = 0.12, offset=(0, 10), color=(0, 0, 0)) -> tuple:
    """圆角矩形投影 → (L遮罩, 颜色)，配合 Image.composite 使用"""
    Wd, Hd = canvas_size
    sh = Image.new("L", (Wd, Hd), 0)
    w, h = int(box[2] - box[0]), int(box[3] - box[1])
    sh.paste(rounded_mask((w, h), radius),
             (int(box[0] + offset[0]), int(box[1] + offset[1])))
    sh = sh.filter(ImageFilter.GaussianBlur(blur))
    if alpha < 1.0:
        sh = sh.point(lambda v: int(v * alpha))
    return sh, color


def drop_shadow(dst: Image.Image, box, radius: int, blur: float = 16.0,
                alpha: float = 0.12, offset=(0, 10), color=(0, 0, 0)) -> None:
    """在 dst 上直接落一层圆角投影"""
    mask, col = soft_shadow(dst.size, box, radius, blur, alpha, offset, color)
    dst.paste(Image.new("RGB", dst.size, col), (0, 0), mask)


# ══════════════════════════════════════════════════════════
#  文本
# ══════════════════════════════════════════════════════════
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


def fit_font(draw: ImageDraw.ImageDraw, s: str, font_fn, max_w: float,
             max_size: int, min_size: int = 9):
    """从 max_size 递减找能放进 max_w 的字号，返回 (font, size)"""
    size = max_size
    while size > min_size:
        f = font_fn(size)
        if draw.textlength(s, font=f) <= max_w:
            return f, size
        size -= 1
    return font_fn(min_size), min_size


def grad_text(img: Image.Image, xy, s: str, font, c1, c2, angle: float = 135.0,
              anchor: str = "lm") -> None:
    """渐变文字（CSS background-clip:text 的效果）"""
    d0 = ImageDraw.Draw(img)
    bbox = d0.textbbox((0, 0), s, font=font, anchor=anchor)
    w = max(1, bbox[2] - bbox[0])
    h = max(1, bbox[3] - bbox[1])
    layer = to_img(linear_grad((w, h), [(0.0, c1), (1.0, c2)], angle))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).text((-bbox[0], -bbox[1]), s, font=font, fill=255)
    img.paste(layer, (int(xy[0] + bbox[0]), int(xy[1] + bbox[1])), mask)


# ══════════════════════════════════════════════════════════
#  图标
# ══════════════════════════════════════════════════════════
_icon_cache: dict[str, Image.Image] = {}


def load_icon(name: str, size: int) -> Image.Image | None:
    """加载 PNG 图标并缩放（NEAREST，对应 CSS image-rendering:pixelated）"""
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


def save_jpeg(img: Image.Image, path, quality: int = 95) -> Path:
    """存 JPEG（不看扩展名，强制 JPEG）"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    img.save(p, "JPEG", quality=quality)
    return p


def save_image(img: Image.Image, path, quality: int = 95) -> Path:
    """按**扩展名**决定格式保存。

    ⚠️ 必须这样：调用方沿用原模板的文件名（.png / .jpg），
      若统一写 JPEG 内容但用 .png 扩展名，文件与后缀不符，
      QQ/NapCat 可能按后缀判断而拒收。
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fmt = "PNG" if p.suffix.lower() == ".png" else "JPEG"
    if fmt == "PNG":
        img.save(p, "PNG")
    else:
        img.save(p, "JPEG", quality=quality)
    return p


def hash8(s: str) -> str:
    """FNV-1a 8 位十六进制（与模板 JS 一致）"""
    h = 0x811C9DC5
    for ch in str(s):
        h ^= ord(ch)
        h = (h * 0x01000193) & 0xFFFFFFFF
    return f"{h:08x}"[:8]


def render_stamp(ms: int | None = None) -> str:
    """卡片页脚「渲染时间」文案（Pillow 路径用）。

    格式与双模式卡（wdsj_dual_card.html 的 #f-time）保持一致：
    日期用 `YYYY-MM-DD HH:MM`，传 ms 时追加本次渲染耗时。
    """
    import datetime as _dt
    text = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    if ms is not None:
        text += " · %dms" % int(ms)
    return text


def render_stamp_script(t0_epoch_ms: float) -> str:
    """卡片页脚「渲染时间」注入脚本（HTML / Chromium 路径用）。

    为什么用注入而不是改模板：wdsj 有 6 个模板、5 个构建函数，
    逐模板加占位符要改 6 处且容易漏；这里统一在 `</body>` 前塞一段脚本，
    自己找位置挂上去，以后新增卡片自动就有。

    耗时口径与 Pillow 路径一致：都以「开始渲染」为起点
    （`t0_epoch_ms` 由构建函数用 `time.time()*1000` 记下），
    到脚本执行完为止 —— 因此包含排队等待 + 浏览器渲染的真实总耗时。

    挂载规则（三张卡的页脚结构不一样，踩过的坑都写在注释里）：
      1. 已有 `#f-time` 槽位（双模式卡）→ **填进去**，不再追加。
         第一版直接 append，导致双模式卡一左一右显示两个时间。
      2. `.foot-l` 是 `display:flex; gap:6px` → inline span 直接追加，
         **不能再加 margin-left**，否则间距翻倍（gap 已经给了 6px）。
      3. `.footer` 是 block + 居中 → span 设 `display:block` 另起一行，
         否则会跟「Powered by …」挤成很长的一行。
      4. 用 DOMContentLoaded 而不是立即执行：双模式卡的 `init()` 也挂在
         DOMContentLoaded 上，它会把 `#f-time` 覆盖成不带耗时的版本。
         我们的监听器后注册 → 后触发 → 最终留下的是带耗时的版本。
    """
    return (
        "<script>(function(){"
        "var t0=%d;"
        "function put(){"
        "var d=new Date(),p=function(n){return String(n).padStart(2,'0')};"
        "var txt=d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate())+' '+"
        "p(d.getHours())+':'+p(d.getMinutes())+' \\u00b7 '+Math.round(Date.now()-t0)+'ms';"
        "var el=document.getElementById('f-time');"
        "if(!el){"
        "var h=document.querySelector('.foot-l')||document.querySelector('.footer')"
        "||document.querySelector('.foot');"
        "if(!h)return;"
        "el=document.createElement('span');"
        "var flex=/(flex)/.test(getComputedStyle(h).display);"
        "el.setAttribute('style','opacity:.62'+(flex?'':'display:block;margin-top:2px'));"
        "h.appendChild(el);"
        "}"
        "el.textContent=txt;"
        "}"
        "if(document.readyState==='loading'){document.addEventListener('DOMContentLoaded',put);}"
        "else{put();}"
        "})();</script>"
    ) % int(t0_epoch_ms)


def inject_stamp(html: str, t0_epoch_ms: float) -> str:
    """把「渲染时间」脚本插到 `</body>` 前（没有 body 就追加到末尾）"""
    script = render_stamp_script(t0_epoch_ms)
    idx = html.rfind("</body>")
    if idx < 0:
        return html + script
    return html[:idx] + script + html[idx:]

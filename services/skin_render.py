"""MC 皮肤 3D 旋转 GIF 渲染。

方案来源：`Trae-CN/temp_20261024_01/mc-skin-preview`（skinview3d + three.js 的 Web 预览）。
这里把同一套渲染搬成**离屏逐帧**：Chromium 打开本地页 → 一次性在页面内旋转 360° 逐帧
抠 canvas → 回传 PNG dataURL 数组 → Pillow 合成 30fps GIF。

为什么不自己用 Pillow 撸 3D：
  皮肤第二层 overlay、六面 UV 布局、手臂 4px / 头 8px 的尺寸关系、透视与光照，
  自己写很容易在细节上歪，而且每改一版都要肉眼比对。skinview3d 已经把这些做对了。

⚠️ **不能用卡片渲染那套 Chromium 参数**：`modules/changelog.py` 的启动参数里有
   `--disable-gpu` + `--disable-software-rasterizer`，那会**关掉 WebGL**，skinview3d
   直接白屏。这里单独 launch，用 ANGLE + SwiftShader 走软件 WebGL。
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import time
from pathlib import Path

import httpx

from core.logger import get_logger

logger = get_logger("skin")

_ROOT = Path(__file__).resolve().parent.parent
VIEWER_DIR = _ROOT / "data" / "skin_viewer"
OUT_DIR = _ROOT / "data" / "skin_output"
OUT_DIR.mkdir(parents=True, exist_ok=True)

_UA = "Mozilla/5.0 (compatible; HuanmengSkinRender/1.0)"

# 软件 WebGL：服务器没有 GPU，必须显式启用 SwiftShader
_CHROME_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--use-gl=angle",
    "--use-angle=swiftshader",
    "--enable-unsafe-swiftshader",
    "--disable-extensions",
    "--hide-scrollbars",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-breakpad",
    "--disable-component-update",
    "--mute-audio",
]

# 皮肤来源（按顺序尝试；都是 64x64 原始皮肤，不是渲染图）
_SKIN_SOURCES = (
    ("minotar", "https://minotar.net/skin/{name}"),
    ("mc-heads", "https://mc-heads.net/skin/{name}"),
    ("crafatar", "https://crafatar.com/skins/{uuid}"),
)


# ── 皮肤获取 ──────────────────────────────────────────────

def _is_valid_skin(raw: bytes) -> bool:
    """是不是一张合法的 MC 皮肤（64x64 或旧版 64x32）。"""
    try:
        from PIL import Image
        with Image.open(io.BytesIO(raw)) as im:
            return im.size in ((64, 64), (64, 32), (128, 128))
    except Exception:
        return False


async def _mojang_uuid(name: str) -> str:
    """正版名 → 无横线 UUID；查不到返回空串。"""
    try:
        async with httpx.AsyncClient(timeout=10, headers={"User-Agent": _UA}) as c:
            r = await c.get(f"https://api.mojang.com/users/profiles/minecraft/{name}")
            if r.status_code == 200:
                return (r.json() or {}).get("id") or ""
            if r.status_code == 404:
                return ""
            logger.warning("Mojang 查名返回 %s", r.status_code)
    except Exception as e:
        logger.warning("Mojang 查名失败: %s", e)
    return ""


async def fetch_skin(name: str, dest: Path | None = None) -> tuple[Path | None, str]:
    """按正版 ID 拉皮肤 PNG。返回 (本地路径, 错误信息)。

    优先 minotar / mc-heads（一步到位，不需要 UUID），最后才走 Mojang + crafatar。
    """
    name = (name or "").strip()
    if not name:
        return None, "没给玩家 ID"
    dest = dest or (OUT_DIR / f"skin_{name}_{int(time.time())}.png")

    uuid = ""
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": _UA},
                                 follow_redirects=True) as c:
        for src, tpl in _SKIN_SOURCES:
            if "{uuid}" in tpl:
                if not uuid:
                    uuid = await _mojang_uuid(name)
                if not uuid:
                    continue
            url = tpl.format(name=name, uuid=uuid)
            try:
                r = await c.get(url)
                if r.status_code != 200 or not _is_valid_skin(r.content):
                    logger.debug("皮肤源 %s 不可用（%s / %d bytes）",
                                 src, r.status_code, len(r.content or b""))
                    continue
                dest.write_bytes(r.content)
                logger.info("皮肤下载成功: %s via %s (%d bytes)",
                            name, src, len(r.content))
                return dest, ""
            except Exception as e:
                logger.debug("皮肤源 %s 异常: %s", src, e)
    return None, f"找不到玩家「{name}」的皮肤（可能不是正版 ID，或穿了默认皮肤）"


async def download_skin(url: str, dest: Path | None = None) -> tuple[Path | None, str]:
    """直接下载一张皮肤图（QQ 图片 URL）。"""
    dest = dest or (OUT_DIR / f"skin_url_{int(time.time())}.png")
    try:
        async with httpx.AsyncClient(timeout=25, headers={"User-Agent": _UA},
                                     follow_redirects=True) as c:
            r = await c.get(url)
        if r.status_code != 200:
            return None, f"皮肤图下载失败（HTTP {r.status_code}）"
        if not _is_valid_skin(r.content):
            return None, "这张图不是 MC 皮肤（要是 64x64 的皮肤原图，不是渲染好的截图）"
        dest.write_bytes(r.content)
        return dest, ""
    except Exception as e:
        return None, f"皮肤图下载失败: {e}"


# ── 渲染 ──────────────────────────────────────────────────

def _build_page(width: int, height: int, zoom: float) -> str:
    html = (VIEWER_DIR / "index.html").read_text(encoding="utf-8")
    return (html.replace("__W__", str(int(width)))
                .replace("__H__", str(int(height)))
                .replace("__ZOOM__", f"{zoom:.3f}"))


async def _shoot_frames(skin_path: Path, frames: int, width: int, height: int,
                        zoom: float) -> list[str]:
    """在 Chromium 里逐帧旋转并取 canvas，返回 dataURL 列表。"""
    from playwright.async_api import async_playwright
    try:
        from modules.changelog import _chromium_path
        exe = _chromium_path()
    except Exception:
        exe = None

    page_file = VIEWER_DIR / f"_render_{int(time.time() * 1000)}.html"
    page_file.write_text(_build_page(width, height, zoom), encoding="utf-8")
    data_url = "data:image/png;base64," + base64.b64encode(
        skin_path.read_bytes()).decode()

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, executable_path=exe,
                                               args=_CHROME_ARGS)
            try:
                page = await browser.new_page(viewport={"width": width, "height": height})
                page.on("console", lambda m: logger.debug("[skin-web] %s", m.text))
                await page.goto(page_file.as_uri())
                await page.wait_for_function("window.__ready === true", timeout=20000)
                # WebGL 是否真的起来了（SwiftShader 没生效时这里会 false）
                ok = await page.evaluate(
                    "() => { const c = document.createElement('canvas');"
                    "return !!(c.getContext('webgl2') || c.getContext('webgl')); }")
                if not ok:
                    raise RuntimeError("浏览器没有可用的 WebGL（SwiftShader 未生效）")
                out = await page.evaluate(
                    "([u, n]) => window.loadAndShoot(u, n)", [data_url, frames])
                return list(out or [])
            finally:
                await browser.close()
    finally:
        try:
            page_file.unlink()
        except OSError:
            pass


def _background(w: int, h: int):
    """深色径向渐变背景（比纯色更有层次，GIF 里也不会脏）。"""
    from PIL import Image, ImageDraw
    bg = Image.new("RGB", (w, h), (18, 20, 26))
    d = ImageDraw.Draw(bg)
    # 从中心偏上向外一圈圈叠暗，模拟布光
    cx, cy = w * 0.5, h * 0.42
    steps = 60
    maxr = int((w ** 2 + h ** 2) ** 0.5 * 0.62)
    for i in range(steps, 0, -1):
        r = int(maxr * i / steps)
        t = i / steps
        col = (int(42 - 18 * t), int(46 - 20 * t), int(58 - 26 * t))
        d.ellipse([cx - r, cy - r * 0.92, cx + r, cy + r * 0.92], fill=col)
    return bg


def _compose_gif(data_urls: list[str], out_path: Path, fps: int,
                 target_w: int) -> tuple[Path | None, str]:
    """dataURL 帧 → 合成 GIF。返回 (路径, 错误)。"""
    from PIL import Image

    if not data_urls:
        return None, "没拿到任何帧"

    raws: list[Image.Image] = []
    for u in data_urls:
        try:
            b = base64.b64decode(u.split(",", 1)[1])
            raws.append(Image.open(io.BytesIO(b)).convert("RGBA"))
        except Exception as e:
            logger.warning("解码帧失败: %s", e)
    if not raws:
        return None, "帧解码全部失败"

    # 统一裁掉四周空白（所有帧取同一个 bbox，避免抖动）
    union = None
    for im in raws:
        bb = im.getchannel("A").getbbox()
        if not bb:
            continue
        union = bb if union is None else (
            min(union[0], bb[0]), min(union[1], bb[1]),
            max(union[2], bb[2]), max(union[3], bb[3]))
    if union:
        pad = 6
        union = (max(0, union[0] - pad), max(0, union[1] - pad),
                 min(raws[0].width, union[2] + pad), min(raws[0].height, union[3] + pad))
        raws = [im.crop(union) for im in raws]

    # 统一缩放到目标宽度（保持比例）
    src_w = raws[0].width
    scale = target_w / float(src_w) if src_w else 1.0
    tw = max(64, int(src_w * scale))
    th = max(64, int(raws[0].height * scale))
    raws = [im.resize((tw, th), Image.LANCZOS) for im in raws]

    # 合成背景
    bg = _background(tw, th).convert("RGBA")
    flat = []
    for im in raws:
        canvas = bg.copy()
        canvas.alpha_composite(im)
        flat.append(canvas.convert("RGB"))

    # 全局调色板：所有帧共用，避免播放时颜色跳
    try:
        sheet = Image.new("RGB", (tw, th * len(flat)))
        for i, f in enumerate(flat):
            sheet.paste(f, (0, i * th))
        pal = sheet.quantize(colors=256, method=Image.MEDIANCUT)
        frames_q = [f.quantize(palette=pal, dither=Image.NONE) for f in flat]
    except Exception as e:
        logger.warning("全局调色板失败，退回逐帧量化: %s", e)
        frames_q = [f.quantize(colors=256, method=Image.MEDIANCUT) for f in flat]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        frames_q[0].save(
            out_path,
            save_all=True,
            append_images=frames_q[1:],
            duration=max(20, int(round(1000.0 / fps))),
            loop=0,
            optimize=True,
            disposal=1,
        )
    except Exception as e:
        return None, f"GIF 保存失败: {e}"
    return out_path, ""


async def render_skin_gif(skin_path: Path, *, fps: int = 30, seconds: float = 4.0,
                          target_w: int = 300, ss: float = 1.5) -> tuple[Path | None, str]:
    """皮肤 PNG → 旋转一整圈的 30fps GIF。返回 (路径, 错误)。

    fps      帧率（用户要求 30）
    seconds  转一圈用几秒（4 秒 = 每帧 3°）
    target_w 输出宽度
    ss       超采样倍率（1.5 = 渲染 1.5 倍再缩，边缘更干净）
    """
    frames = max(12, int(round(fps * seconds)))
    vw = int(target_w * ss)
    vh = int(vw * 1.3)          # 玩家高瘦，画布略高于宽
    zoom = 0.92

    t0 = time.monotonic()
    try:
        urls = await _shoot_frames(skin_path, frames, vw, vh, zoom)
    except Exception as e:
        logger.error("皮肤渲染失败: %s", e, exc_info=True)
        return None, f"渲染失败: {e}"
    if not urls:
        return None, "渲染没产出帧（页面可能没加载成功）"

    out = OUT_DIR / f"skin_{int(time.time())}.gif"
    path, err = await asyncio.to_thread(_compose_gif, urls, out, fps, target_w)
    if err:
        return None, err
    dt = time.monotonic() - t0
    logger.info("皮肤 GIF 完成: %s | %d 帧 %.1fs | %d KB",
                path.name, len(urls), dt, path.stat().st_size // 1024)
    return path, ""

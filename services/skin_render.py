"""MC 皮肤 3D 渲染：旋转 GIF / 动作 GIF / 三视图。

方案来源：`Trae-CN/temp_20261024_01/mc-skin-preview`（skinview3d + three.js 的 Web 预览）。
这里把同一套渲染搬成**离屏逐帧**：Chromium 打开本地页 → 在页面内一次循环转角度/播动作、
逐帧把 WebGL 画面合成到渐变背景上 → 回传帧 → Pillow 合成 GIF / 拼三视图。

v2.3.107 提速要点（原来 120 帧要 16~26 秒，用户嫌慢）：
  1. 帧用 **JPEG** 而不是 PNG：PNG 编码是主要瓶颈，JPEG 快 3~5 倍，体积也小
  2. 合成放到页面内的 2D canvas 上做（先填渐变再 drawImage），同时解决了
     "JPEG 不支持透明 → 背景会变黑"的问题
  3. 不再做自动裁边（JPEG 没有 alpha 通道），改成固定取景 + 调 zoom，
     顺带让动画播放时画面不抖
  4. 转一圈从 4 秒 120 帧收到 3 秒 90 帧（30fps 不变，每帧 4°，观感差别很小）

⚠️ **不能用卡片渲染那套 Chromium 参数**：`modules/changelog.py` 的启动参数里有
   `--disable-gpu` + `--disable-software-rasterizer`，那会**关掉 WebGL**，skinview3d
   直接白屏。这里单独 launch，用 ANGLE + SwiftShader 走软件 WebGL。
"""
from __future__ import annotations

import asyncio
import base64
import io
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

# 支持的动作 → 页面内 ANIMS 的键
ACTIONS = {
    "walk": "走路",
    "run": "跑步",
    "fly": "飞行",
    "idle": "站立",
    "wave": "招手",
    "swim": "游泳",
    "crouch": "潜行",
    "hit": "挥拳",
}
_ACTION_ALIASES = {
    "走路": "walk", "走": "walk", "walking": "walk", "行走": "walk",
    "跑步": "run", "跑": "run", "running": "run", "奔跑": "run",
    "飞行": "fly", "飞": "fly", "flying": "fly", "飞翔": "fly",
    "站立": "idle", "站着": "idle", "待机": "idle", "idle": "idle",
    "招手": "wave", "挥手": "wave", "wave": "wave",
    "游泳": "swim", "潜行": "crouch", "蹲": "crouch",
    "挥拳": "hit", "打": "hit",
}

# 渲染画布与取景。不同动作要不同构图：
#   fly 会把身体放平（横向），竖构图必然出画 → 单独给宽画布 + 拉远
_LAYOUTS = {
    "default": {"w": 300, "h": 390, "zoom": 0.90, "yaw": -0.62},
    "fly":     {"w": 300, "h": 390, "zoom": 0.80, "yaw": -0.62},
}


def _layout(action: str | None) -> dict:
    return _LAYOUTS.get((action or "").lower()) or _LAYOUTS["default"]


_CANVAS_W = _LAYOUTS["default"]["w"]
_ZOOM = _LAYOUTS["default"]["zoom"]

# 每个动作在 GIF 里放几个完整周期。
# 目的是**速度接近真人**，同时保证帧数够（48 帧里分几周期，每周期至少 12 帧）。
#   walk 周期约 0.8s（真人步频），run 约 0.53s
_ACTION_CYCLES = {"walk": 2, "run": 3, "swim": 2, "wave": 1, "hit": 2,
                  "idle": 1, "fly": 1, "crouch": 1}

# 一个完整动作周期对应的 progress 增量（bundle 里 t = k*progress → 周期 2π/k）。
# 与 index.html 里的 PERIODS 必须保持一致。
# 动画推进速率：内置动画按真实时间走，每帧渲染约 0.09~0.10s，
# 而 GIF 按 30fps（33ms/帧）播放 → 不降速会快 3 倍左右。
# 取 0.0333/0.095 ≈ 0.35。
_ANIM_SPEED = 0.35

_PERIODS = {"idle": 3.14159265, "still": 3.14159265, "fly": 3.14159265,
            "crouch": 3.14159265,
            "walk": 2 * 3.14159265 / 8, "run": 2 * 3.14159265 / 15,
            "hit": 2 * 3.14159265 / 18,
            "wave": 2.0, "swim": 1.3}


def action_key(raw: str) -> str | None:
    """把用户输入的动作词解析成标准 key；不认识返回 None。"""
    t = (raw or "").strip().lower()
    if not t:
        return None
    if t in ACTIONS:
        return t
    return _ACTION_ALIASES.get(t)


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


async def fetch_cape(name: str, dest: Path | None = None) -> tuple[Path | None, str]:
    """按正版 ID 取披风纹理（Mojang 账号资料里的 CAPE）。没有披风返回 (None, 原因)。

    披风不在皮肤 PNG 里，必须走 session server 的 textures 属性。
    """
    import json as _json
    uuid = await _mojang_uuid(name)
    if not uuid:
        return None, "查不到这个 ID（披风要正版账号）"
    try:
        async with httpx.AsyncClient(timeout=15, headers={"User-Agent": _UA}) as c:
            r = await c.get(
                f"https://sessionserver.mojang.com/session/minecraft/profile/{uuid}")
            if r.status_code != 200:
                return None, f"账号资料查询失败（HTTP {r.status_code}）"
            props = ((r.json() or {}).get("properties") or [])
            url = ""
            for prop in props:
                if prop.get("name") != "textures":
                    continue
                try:
                    data = _json.loads(base64.b64decode(prop.get("value") or ""))
                except Exception:
                    continue
                url = (((data.get("textures") or {}).get("CAPE") or {}).get("url")) or ""
                if url:
                    break
            if not url:
                return None, "这个账号没挂披风"
            ir = await c.get(url)
            if ir.status_code != 200 or not ir.content:
                return None, "披风图下载失败"
            dest = dest or (OUT_DIR / f"cape_{name}_{int(time.time())}.png")
            dest.write_bytes(ir.content)
            logger.info("披风下载成功: %s (%d bytes)", name, len(ir.content))
            return dest, ""
    except Exception as e:
        logger.warning("取披风失败: %s", e)
        return None, f"取披风失败: {e}"


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
            return None, "这张图不是 MC 皮肤（要 64x64 的皮肤原图，不是渲染好的截图）"
        dest.write_bytes(r.content)
        return dest, ""
    except Exception as e:
        return None, f"皮肤图下载失败: {e}"


# ── 页面渲染 ──────────────────────────────────────────────

def _build_page(width: int, height: int, zoom: float) -> str:
    html = (VIEWER_DIR / "index.html").read_text(encoding="utf-8")
    return (html.replace("__W__", str(int(width)))
                .replace("__H__", str(int(height)))
                .replace("__ZOOM__", f"{zoom:.3f}"))


async def _shoot(skin_path: Path, script: str, args: list, *,
                 width: int, height: int, zoom: float,
                 cape_path: Path | None = None,
                 speed: float | None = None) -> list[str]:
    """打开渲染页 → prepare(皮肤, 动作) → 执行 script(args) → 返回帧 dataURL 列表。"""
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
    cape_url = ""
    if cape_path:
        cape_url = "data:image/png;base64," + base64.b64encode(
            cape_path.read_bytes()).decode()

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, executable_path=exe,
                                               args=_CHROME_ARGS)
            try:
                page = await browser.new_page(viewport={"width": width, "height": height})
                page.on("console", lambda m: logger.debug("[skin-web] %s", m.text))
                await page.goto(page_file.as_uri())
                await page.wait_for_function("window.__ready === true", timeout=20000)
                ok = await page.evaluate(
                    "() => { const c = document.createElement('canvas');"
                    "return !!(c.getContext('webgl2') || c.getContext('webgl')); }")
                if not ok:
                    raise RuntimeError("浏览器没有可用的 WebGL（SwiftShader 未生效）")
                await page.evaluate("([u, a, c, s]) => window.prepare(u, a, c, s)",
                                    [data_url, str(args[0] or "idle"), cape_url, speed])
                out = await page.evaluate(f"([a]) => window.{script}(...a)", [args[1:]])
                return list(out or [])
            finally:
                await browser.close()
    finally:
        try:
            page_file.unlink()
        except OSError:
            pass


async def _shoot_anim(skin_path: Path, anim: str, frames: int, cycles: int,
                      yaw: float, *, width: int, height: int, zoom: float,
                      cape_path: Path | None = None) -> list[str]:
    """逐帧 seek 取动作帧：每帧单独 evaluate（设进度 → 立即重绘 → 取图）。

    ⚠️ 为什么不用一次 evaluate 循环：skinview3d 的动画进度会被它内部的 rAF
    update 覆盖，且 `speed=0` 会让 animate 不执行、`FunctionAnimation` 回调实测
    也不生效（2026-10-25 连续踩了一上午）。逐帧「设进度 → renderer.render()
    → toDataURL」把时序完全握住，不依赖任何内部循环。
    """
    import math
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
    cape_url = ""
    if cape_path:
        cape_url = "data:image/png;base64," + base64.b64encode(
            cape_path.read_bytes()).decode()

    period = _PERIODS.get(anim, math.pi)
    total = period * max(1, cycles)
    is_fly = anim == "fly"

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, executable_path=exe,
                                               args=_CHROME_ARGS)
            try:
                page = await browser.new_page(viewport={"width": width, "height": height})
                page.on("console", lambda m: logger.debug("[skin-web] %s", m.text))
                await page.goto(page_file.as_uri())
                await page.wait_for_function("window.__ready === true", timeout=20000)
                await page.evaluate("([u, a, c]) => window.prepare(u, a, c)",
                                    [data_url, anim, cape_url])
                out = []
                for i in range(frames):
                    f = i / frames
                    progress = f * total
                    if is_fly:
                        ph = f * math.pi * 2 * max(1, cycles)
                        fly_y = math.sin(ph) * 3.2
                        fly_rx = math.sin(ph * 2) * 0.05
                    else:
                        fly_y, fly_rx = None, None
                    out.append(await page.evaluate(
                        "([a]) => window.seek(a)",
                        [[progress, yaw, fly_y, fly_rx, "image/jpeg", 0.9]]))
                return out
            finally:
                await browser.close()
    finally:
        try:
            page_file.unlink()
        except OSError:
            pass


# ── 合成 ──────────────────────────────────────────────────

def _decode(data_urls: list[str]):
    from PIL import Image
    frames = []
    for u in data_urls:
        try:
            b = base64.b64decode(u.split(",", 1)[1])
            frames.append(Image.open(io.BytesIO(b)).convert("RGB"))
        except Exception as e:
            logger.warning("解码帧失败: %s", e)
    return frames


def _compose_gif(data_urls: list[str], out_path: Path, fps: int,
                 target_w: int) -> tuple[Path | None, str]:
    """帧 → GIF。背景已由页面烤进图里，这里只做缩放 + 全局调色板量化。"""
    from PIL import Image

    flat = _decode(data_urls)
    if not flat:
        return None, "帧解码全部失败"

    w, h = flat[0].size
    scale = target_w / float(w) if w else 1.0
    tw, th = max(64, int(w * scale)), max(64, int(h * scale))
    flat = [f.resize((tw, th), Image.LANCZOS) for f in flat]

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
            optimize=False,      # 120 帧下 optimize 很慢，收益不大
            disposal=1,
        )
    except Exception as e:
        return None, f"GIF 保存失败: {e}"
    return out_path, ""


# ── 三个公开入口 ──────────────────────────────────────────

async def render_spin_gif(skin_path: Path, *, fps: int = 30, seconds: float = 2.0,
                          target_w: int = 300,
                          cape_path: Path | None = None) -> tuple[Path | None, str]:
    """绕一圈的旋转 GIF（30fps，默认 3 秒 / 90 帧 / 每帧 4°）。"""
    frames = max(12, int(round(fps * seconds)))
    t0 = time.monotonic()
    try:
        urls = await _shoot(skin_path, "shootSpin", ["still", frames, "image/jpeg", 0.9],
                            width=_CANVAS_W, height=int(_CANVAS_W * 1.3), zoom=_ZOOM,
                            cape_path=cape_path)
    except Exception as e:
        logger.error("旋转渲染失败: %s", e, exc_info=True)
        return None, f"渲染失败: {e}"
    if not urls:
        return None, "渲染没产出帧（页面可能没加载成功）"

    out = OUT_DIR / f"skin_spin_{int(time.time())}.gif"
    path, err = await asyncio.to_thread(_compose_gif, urls, out, fps, target_w)
    if err:
        return None, err
    logger.info("旋转 GIF: %s | %d 帧 %.1fs | %d KB",
                path.name, len(urls), time.monotonic() - t0, path.stat().st_size // 1024)
    return path, ""


async def render_action_gif(skin_path: Path, action: str = "walk", *,
                            fps: int = 30, seconds: float = 1.6,
                            target_w: int = 300,
                            cape_path: Path | None = None) -> tuple[Path | None, str]:
    """固定 3/4 视角播放一个动作的 GIF（走/跑/飞…）。"""
    key = action_key(action) or "walk"
    frames = max(12, int(round(fps * seconds)))
    lay = _layout(key)   # 3/4 侧视，看得清腿的前后摆动；fly 另给宽画布
    t0 = time.monotonic()
    try:
        cycles = _ACTION_CYCLES.get(key, 1)
        urls = await _shoot_anim(skin_path, key, frames, cycles, lay["yaw"],
                                 width=lay["w"], height=lay["h"], zoom=lay["zoom"],
                                 cape_path=cape_path)
    except Exception as e:
        logger.error("动作渲染失败(%s): %s", key, e, exc_info=True)
        return None, f"渲染失败: {e}"
    if not urls:
        return None, "渲染没产出帧（页面可能没加载成功）"

    out = OUT_DIR / f"skin_{key}_{int(time.time())}.gif"
    path, err = await asyncio.to_thread(_compose_gif, urls, out, fps, target_w)
    if err:
        return None, err
    logger.info("动作 GIF(%s): %s | %d 帧 %.1fs | %d KB",
                key, path.name, len(urls), time.monotonic() - t0,
                path.stat().st_size // 1024)
    return path, ""


async def render_three_views(skin_path: Path, *, target_h: int = 420,
                             cape_path: Path | None = None
                             ) -> tuple[Path | None, str]:
    """三视图：正面 / 右侧 / 背面 横拼成一张 PNG。"""
    t0 = time.monotonic()
    angles = [0.0, 1.5707963, 3.1415927]
    try:
        urls = await _shoot(skin_path, "shootAngles", ["still", angles, "image/png", 1],
                            width=_CANVAS_W, height=int(_CANVAS_W * 1.3), zoom=_ZOOM,
                            cape_path=cape_path)
    except Exception as e:
        logger.error("三视图渲染失败: %s", e, exc_info=True)
        return None, f"渲染失败: {e}"
    frames = _decode(urls)
    if len(frames) < 3:
        return None, "三视图渲染不完整"

    from PIL import Image
    w, h = frames[0].size
    scale = target_h / float(h) if h else 1.0
    tw, th = int(w * scale), int(h * scale)
    frames = [f.resize((tw, th), Image.LANCZOS) for f in frames]

    sheet = Image.new("RGB", (tw * len(frames), th))
    for i, f in enumerate(frames):
        sheet.paste(f, (i * tw, 0))
    out = OUT_DIR / f"skin_views_{int(time.time())}.png"
    sheet.save(out, optimize=True)
    logger.info("三视图: %s | %.1fs | %d KB",
                out.name, time.monotonic() - t0, out.stat().st_size // 1024)
    return out, ""


# 兼容旧调用名
render_skin_gif = render_spin_gif

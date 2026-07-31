"""
PC 状态接收端点 — 运行在 bot 服务器上，接收 PC 端上报的状态数据
"""
from __future__ import annotations

import asyncio, json, time
from aiohttp import web

_PC_DATA: dict = {}        # 最新状态
_LAST_UPDATE: float = 0    # 最后更新时间
_AUTH_KEY = "huanmeng_pc_2026"


async def _handle_pc_status(request: web.Request):
    """POST /pc_status — 接收 PC 端状态"""
    global _PC_DATA, _LAST_UPDATE
    auth = request.headers.get("X-Auth", "")
    if auth != _AUTH_KEY:
        return web.Response(status=403, text="bad auth")

    try:
        data = await request.json()
    except Exception:
        return web.Response(status=400, text="bad json")

    _PC_DATA = data
    _LAST_UPDATE = time.time()
    return web.Response(text="ok")


def get_pc_status() -> dict | None:
    """获取缓存的 PC 状态，超过 30s 未更新返回 None"""
    if not _PC_DATA:
        return None
    if time.time() - _LAST_UPDATE > 30:
        return None
    return dict(_PC_DATA)


def format_pc_status() -> str:
    """格式化为自然语言文本"""
    data = get_pc_status()
    if not data:
        return "暂无 PC 状态数据（可能未开机或未运行采集脚本）"

    lines = []
    hostname = data.get("hostname", "未知")
    window = data.get("window", "")
    music = data.get("music", {})

    if window:
        lines.append(f"🖥️ {hostname} — 当前窗口: {window}")
    else:
        lines.append(f"🖥️ {hostname} — 在线（无窗口信息）")

    if music:
        song = music.get("song", "")
        if song:
            lines.append(f"🎵 正在播放: {song}")
        lyric = music.get("lyric", "")
        if lyric:
            lines.append(f"📝 歌词: {lyric[:200]}")

    return "\n".join(lines) if lines else f"🖥️ {hostname} — 在线"


async def start_pc_server(port: int = 58890):
    """启动 HTTP 接收服务器（后台任务）"""
    app = web.Application()
    app.router.add_post("/pc_status", _handle_pc_status)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    from core.logger import get_logger
    get_logger("pc_status").info("PC 状态接收端启动: 0.0.0.0:%d", port)
    return runner

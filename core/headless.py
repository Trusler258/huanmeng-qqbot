"""无头模式（BOT_HEADLESS=1）：跑采集/生成，不发消息、不连 NapCat。

## 为什么需要

2026-10-31 QQ 账号被风控封 7 天。停机等解封会让 `wdsj_history.json` 断 7 天采样，
趋势图中间空一段。但采集和日榜生成都只依赖 `wdsj.net` 的 HTTP API，不需要 QQ。

所以把 bot 拆成两种跑法：

    BOT_HEADLESS=1  →  不连 NapCat，所有对外发送被拦掉，后台任务照跑
    （缺省）        →  正常在线

指令天然不可用（没有 WS 就没有消息进来），不需要额外开关。

## 拦在哪

统一拦 `services.sender`：把各 `send_*` 换成空实现、`get_ws_manager()` 换成
返回一个永远成功的假客户端。这样无论调用方是 `send_group_msg` 还是直接
`get_ws_manager().call_api(...)`（qzone 那种），都不会真的发出去，也不会
因为连不上 WS 反复报错。
"""
from __future__ import annotations

from core.logger import get_logger

logger = get_logger("headless")

_SEND_FUNCS = (
    "send_group_msg", "send_private_msg", "send_by_chat_type",
    "send_by_chat_type_ex", "send_raw_group", "send_raw_user",
    "send_sentences", "send_file",
)

_INSTALLED = False


class _DummyWS:
    """假 WS 管理器：所有 call_api 直接回成功，不碰网络。"""

    ws = None

    async def call_api(self, action, params=None, timeout=8.0):
        logger.info("[headless] 拦截 API 调用: %s", action)
        return {"status": "ok", "retcode": 0, "data": {}, "echo": "headless"}

    async def send(self, *a, **k):
        return True

    async def ensure_connected(self):
        return False


def install() -> None:
    """把 services.sender 的对外通道全部换成空实现（幂等）。"""
    global _INSTALLED
    if _INSTALLED:
        return

    import services.sender as S

    async def _noop(*args, **kwargs):
        head = str(args[0])[:60] if args else ""
        logger.info("[headless] 已拦截发送: %s", head or "<空>")
        return True

    for name in _SEND_FUNCS:
        if hasattr(S, name):
            setattr(S, name, _noop)

    S.get_ws_manager = lambda: _DummyWS()      # type: ignore[assignment]
    _INSTALLED = True
    logger.warning("★ 无头模式已启用：不连 NapCat，所有发送被拦截，采集/生成照常")

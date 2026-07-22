"""
好友请求管理模块
- 接收 NapCat 好友请求事件
- 通知管理员审批
- 支持 /#添加 /#拒绝 /#好友列表 指令
"""

from __future__ import annotations

from core.logger import get_logger

logger = get_logger("friend_request")

# 挂起的请求: {flag: {"user_id": int, "nickname": str, "comment": str}}
_pending_requests: dict[str, dict] = {}


def add_pending(flag: str, user_id: int, nickname: str, comment: str):
    """记录一个待处理的好友请求"""
    _pending_requests[flag] = {
        "user_id": user_id,
        "nickname": nickname,
        "comment": comment,
    }
    logger.info("好友请求待审批: flag=%s user=%d nickname=%s comment=%s",
                flag, user_id, nickname, comment)


def get_pending(flag: str) -> dict | None:
    """根据 flag 获取待处理请求"""
    return _pending_requests.get(flag)


def get_latest_pending() -> dict | None:
    """获取最新的（最后一条）待处理请求"""
    if not _pending_requests:
        return None
    last_flag = list(_pending_requests.keys())[-1]
    req = _pending_requests[last_flag]
    return {"flag": last_flag, **req}


def remove_pending(flag: str):
    """移除已处理的请求"""
    _pending_requests.pop(flag, None)


async def approve_request(flag: str, add_whitelist: bool = False) -> str:
    """批准好友请求，可选同时加入私聊白名单"""
    req = get_pending(flag)
    if not req:
        return f"未找到请求: {flag}"

    try:
        from services.sender import get_ws_manager
        mgr = get_ws_manager()
        result = await mgr.call_api("set_friend_add_request", {
            "flag": flag,
            "approve": True,
        })
        remove_pending(flag)
        logger.info("好友请求已通过: flag=%s user=%d", flag, req["user_id"])

        msg = f"已通过 {req['nickname']}({req['user_id']}) 的好友请求"
        if add_whitelist:
            from core.config import get_config
            cfg = get_config()
            wl = list(cfg.private_whitelist or [])
            if req["user_id"] not in wl:
                wl.append(req["user_id"])
                cfg.private_whitelist = wl  # 更新内存中的白名单
                msg += " 并加入私聊白名单（需要 /~reload 后持久化）"
        return msg
    except Exception as e:
        logger.error("批准好友请求失败: %s", e)
        return f"操作失败: {e}"


async def reject_request(flag: str) -> str:
    """拒绝好友请求"""
    req = get_pending(flag)
    if not req:
        return f"未找到请求: {flag}"

    try:
        from services.sender import get_ws_manager
        mgr = get_ws_manager()
        result = await mgr.call_api("set_friend_add_request", {
            "flag": flag,
            "approve": False,
        })
        remove_pending(flag)
        logger.info("好友请求已拒绝: flag=%s user=%d", flag, req["user_id"])
        return f"已拒绝 {req['nickname']}({req['user_id']}) 的好友请求"
    except Exception as e:
        logger.error("拒绝好友请求失败: %s", e)
        return f"操作失败: {e}"


def list_pending() -> str:
    """列出所有挂起的好友请求"""
    if not _pending_requests:
        return "暂无待处理的好友请求喵~"
    lines = ["【待处理好友请求】"]
    for flag, req in _pending_requests.items():
        lines.append(
            f"  {req['nickname']}({req['user_id']})\n"
            f"    理由: {req['comment'] or '(无)'}\n"
            f"    flag: {flag}"
        )
    return "\n".join(lines)

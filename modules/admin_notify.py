"""
管理员私聊通知（v2.3.43）
- bot 被拉进陌生群 / 加群邀请 / 好友请求等"管理员应该知道"的事件
- 收件人取 cfg.admin_qq（不硬编码）；admin_qq 未配置时静默跳过
- 通知失败只告警不影响主流程（这类通知是锦上添花，不能把事件处理搞挂）
"""

from __future__ import annotations

from core.logger import get_logger

logger = get_logger("admin_notify")


async def notify_admin(text: str) -> bool:
    """发一条私聊给管理员。返回是否成功。"""
    from core.config import get_config
    cfg = get_config()
    admin = getattr(cfg, "admin_qq", 0) or 0
    if not admin:
        logger.debug("admin_notify: 未配置 admin_qq，跳过通知: %s", text[:40])
        return False
    try:
        from services.sender import send_private_msg
        await send_private_msg(text, admin)
        logger.info("admin_notify -> %s: %s", admin, text[:60].replace("\n", " "))
        return True
    except Exception as e:
        logger.warning("admin_notify 发送失败: %s", e)
        return False

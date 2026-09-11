"""
管理员代发消息（/~say）

用途：主人在私聊里让 bot 把一条消息发到指定群 / 指定私聊。
      （群聊里也能用，但需要该群管理员权限）

用法：
  /~say g<群号> <内容>      发到群
  /~say u<QQ号> <内容>      发到私聊
  /~say <群号> <内容>       纯数字默认按群号处理
  /~say 群 <群号> <内容>    等价写法；前缀支持 g/group/群/群聊 与 u/user/p/private/私/私聊

内容里的 @123456 会自动转成真正的 @ 提醒；也支持直接写 CQ 码。

权限：主人 / 全局 OP / 该群管理员。
"""

from __future__ import annotations

import re

from core.logger import get_logger

logger = get_logger("relay")

_HELP = (
    "用法：\n"
    "/~say g<群号> <内容>    发到群\n"
    "/~say u<QQ号> <内容>    发到私聊\n"
    "（纯数字默认当群号）例：/~say g1058782600 大家晚上好呀\n"
    "内容里的 @123456 会变成真的 @ 提醒"
)

# 前缀表：长的放前面，避免 "p" 抢了 "private"
_PREFIX_GROUP = ("group", "群聊", "群", "g")
_PREFIX_USER = ("private", "user", "私聊", "私", "p", "u")


def _parse_target(token: str) -> tuple[bool, int] | None:
    """解析目标 token → (是否群聊, 号码)；无法识别返回 None"""
    t = (token or "").strip()
    if not t:
        return None
    low = t.lower()
    for prefixes, is_group in ((_PREFIX_GROUP, True), (_PREFIX_USER, False)):
        for p in prefixes:
            if not low.startswith(p):
                continue
            rest = low[len(p):].lstrip("_")
            if rest.isdigit():
                return is_group, int(rest)
    if low.isdigit():
        return True, int(low)      # 纯数字默认群号
    return None


def _render(text: str) -> str:
    """@123456 → [CQ:at,qq=123456]；其余原样保留（支持直接写 CQ 码）"""
    return re.sub(r"@(\d{5,12})(?!\d)", r"[CQ:at,qq=\1]", text)


async def cmd_say(args, user_id, group_id, sender_name, is_group, bot_qq):
    """/~say — 管理员代发消息到指定群/私聊"""
    from core.config import get_config
    cfg = get_config()

    a = list(args or [])
    if len(a) < 2:
        return _HELP

    # 权限：主人 / 全局 OP / 该群管理员（私聊场景下 is_admin 仅对主人成立）
    if not (cfg.is_admin(user_id, group_id) or cfg.is_op(user_id)):
        logger.warning("非管理员尝试代发消息 user=%s chat=%s", user_id, group_id)
        return "代发消息需要管理员权限喵~"

    target = _parse_target(a[0])
    if not target:
        return f"认不出要发到哪儿喵~\n{_HELP}"
    to_group, tid = target

    content = " ".join(a[1:]).strip()
    if not content:
        return _HELP

    msg = _render(content)
    from services.sender import send_group_msg, send_private_msg
    try:
        ok = await (send_group_msg(msg, tid) if to_group else send_private_msg(msg, tid))
    except Exception as e:
        logger.exception("代发失败 → %s", tid)
        return f"代发失败：{str(e)[:150]}"

    where = f"群 {tid}" if to_group else f"私聊 {tid}"
    logger.info("代发消息 → %s (by=%s id=%s): %s", where, sender_name, user_id, content[:60])
    if ok is False:
        return f"{where} 发送失败（可能不在该群 / 不是好友）"
    return f"已发往{where}喵~\n{content[:80]}"

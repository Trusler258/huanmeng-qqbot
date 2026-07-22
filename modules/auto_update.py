"""
自动更新模块 v2 — Git Patch 行级增量合并

/~update       手动触发增量更新
/~update check 只检查不下载
/~update force 强制全量对比（跳过 SHA 缓存）
"""

from __future__ import annotations

from modules._auto_update.engine import check_and_update


async def cmd_update(args, user_id, group_id, sender_name, is_group, bot_qq):
    """/~update [check|force]"""
    try:
        from core.config import load_roles_config
        roles = load_roles_config()
        admin_qq = roles.get("admin_qq", 0)
        if admin_qq and user_id != admin_qq:
            return "权限不足喵~"
    except Exception:
        pass

    check_only = args and args[0].lower() == "check"
    force = args and args[0].lower() == "force"
    result = await check_and_update(check_only=check_only, force=force)

    if check_only:
        return result
    if result and "已更新" in result and "个文件" in result:
        result += "\n\n建议重启 bot 使更新生效"
    return result

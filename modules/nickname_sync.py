"""
每天 23:59 自动补全 QQ 昵称映射
支持 /~nickname update 手动触发
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

from core.logger import get_logger

logger = get_logger("nickname_sync")

_ROLES_PATH = Path(__file__).resolve().parent.parent / "config" / "roles.toml"


async def nickname_sync_loop():
    """每天 23:59 自动同步"""
    while True:
        now = datetime.now()
        target = now.replace(hour=23, minute=59, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        wait = (target - now).total_seconds()
        logger.info("昵称同步将在 %s 后执行", _fmt(wait))
        await asyncio.sleep(wait)
        await _do_sync()


async def _do_sync():
    """执行一次自动同步（静默，只写文件）"""
    from services.sender import get_ws_manager
    from core.config import get_config
    import toml

    cfg = get_config()
    BLOCKED_NAMES = {"主人", "admin", "管理员", "群主", "bot", "机器人", cfg.bot_name}

    new_map = {}
    try:
        result = await get_ws_manager().call_api("get_friend_list", timeout=15)
        if result:
            friends = result if isinstance(result, list) else result.get("data", result)
            if isinstance(friends, list):
                for f in friends:
                    qq = str(f.get("user_id", ""))
                    nick = f.get("nickname", "")
                    if qq and nick:
                        new_map[qq] = nick
    except Exception as e:
        logger.warning("好友列表失败: %s", e)

    try:
        glist = await get_ws_manager().call_api("get_group_list", timeout=10)
        groups = glist if isinstance(glist, list) else (glist.get("data", glist) if glist else [])
        if isinstance(groups, list):
            for g in groups:
                try:
                    members = await get_ws_manager().call_api(
                        "get_group_member_list", {"group_id": g.get("group_id", 0), "no_cache": True}, timeout=20)
                    if isinstance(members, list):
                        for m in members:
                            qq = str(m.get("user_id", ""))
                            nick = m.get("card", "") or m.get("nickname", "")
                            if qq and nick:
                                new_map[qq] = nick
                except Exception:
                    pass
    except Exception as e:
        logger.warning("群列表失败: %s", e)

    if new_map:
        # ★ 过滤敏感昵称
        for qq, nick in list(new_map.items()):
            if nick in BLOCKED_NAMES:
                new_map.pop(qq, None)
                logger.info("自动同步过滤: QQ=%s nick='%s'", qq, nick)
        _merge_and_save(new_map)
        logger.info("自动昵称同步: %d 条", len(new_map))


def _merge_and_save(new_map: dict[str, str]) -> int:
    import toml
    try:
        roles = toml.loads(_ROLES_PATH.read_text(encoding="utf-8")) if _ROLES_PATH.exists() else {}
    except Exception:
        roles = {}
    old = roles.get("qq_name_map", {})
    added = 0
    for qq, nick in new_map.items():
        if qq not in old:
            old[qq] = nick
            added += 1
        elif old.get(qq) != nick:
            old[qq] = nick
            added += 1
    roles["qq_name_map"] = old
    _ROLES_PATH.write_text(toml.dumps(roles), encoding="utf-8")
    # ★ 刷新内存中的配置，让 favlist/游戏等模块立即用到新名字
    try:
        from core.config import reload_config
        reload_config()
    except Exception:
        pass
    return added


async def sync_and_report(chat_id: int = None, is_group: bool = False) -> str:
    """手动触发同步，返回可读结果列表"""
    from services.sender import get_ws_manager
    from core.config import get_config
    import toml

    cfg = get_config()
    new_map = {}
    source = ""

    # ★ 过滤敏感/迷惑性昵称
    BLOCKED_NAMES = {"主人", "admin", "管理员", "群主", "bot", "机器人", cfg.bot_name}

    if is_group and chat_id:
        try:
            members = await get_ws_manager().call_api(
                "get_group_member_list", {"group_id": int(chat_id), "no_cache": True}, timeout=20)
            if isinstance(members, list):
                for m in members:
                    qq = str(m.get("user_id", ""))
                    nick = m.get("card", "") or m.get("nickname", "")
                    if qq and nick:
                        new_map[qq] = nick
            source = f"群 {chat_id}"
        except Exception as e:
            return "获取群成员失败: " + str(e)
    else:
        try:
            result = await get_ws_manager().call_api("get_friend_list", timeout=15)
            if result:
                friends = result if isinstance(result, list) else result.get("data", result)
                if isinstance(friends, list):
                    for f in friends:
                        qq = str(f.get("user_id", ""))
                        nick = f.get("nickname", "")
                        if qq and nick:
                            new_map[qq] = nick
        except Exception as e:
            return "获取好友列表失败: " + str(e)
        try:
            glist = await get_ws_manager().call_api("get_group_list", timeout=10)
            groups = glist if isinstance(glist, list) else (glist.get("data", glist) if glist else [])
            if isinstance(groups, list):
                for g in groups:
                    try:
                        members = await get_ws_manager().call_api(
                            "get_group_member_list", {"group_id": g.get("group_id", 0), "no_cache": True}, timeout=20)
                        if isinstance(members, list):
                            for m in members:
                                qq = str(m.get("user_id", ""))
                                nick = m.get("card", "") or m.get("nickname", "")
                                if qq and nick:
                                    new_map[qq] = nick
                    except Exception:
                        pass
        except Exception:
            pass
        source = "全部好友+群成员"

    if not new_map:
        return "未获取到任何昵称数据"

    # ★ 过滤敏感昵称（主人/admin等，所有人都不允许作为昵称同步）
    blocked_count = 0
    for qq, nick in list(new_map.items()):
        if nick in BLOCKED_NAMES:
            new_map.pop(qq, None)
            blocked_count += 1
            logger.info("昵称同步过滤: QQ=%s nick='%s'", qq, nick)
    if blocked_count:
        logger.info("昵称同步共过滤 %d 条敏感昵称", blocked_count)

    # 读取旧 map 用于 diff
    try:
        roles = toml.loads(_ROLES_PATH.read_text(encoding="utf-8")) if _ROLES_PATH.exists() else {}
    except Exception:
        roles = {}
    old = roles.get("qq_name_map", {})

    added = _merge_and_save(new_map)

    lines = [f"昵称同步完成 ({source}) - 更新 {added} 条", ""]
    for qq, nick in sorted(new_map.items(), key=lambda x: x[0]):
        if qq not in old:
            tag = " [新]"
        elif old.get(qq) != nick:
            tag = f" [原:{old[qq]}→{nick}]"
        else:
            tag = ""
        lines.append(f"{qq} -> {nick}{tag}")
    return "\n".join(lines)


def _fmt(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"

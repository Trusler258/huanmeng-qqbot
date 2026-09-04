"""
每日昵称同步（分群版）
- 分群昵称: data/group_nicknames.json  {群号: {QQ: 昵称}}  （card 优先，无 card 用主页昵称）
- 全局兜底: roles.toml qq_name_map 只存好友/主页昵称（QQ 级，全局一致，不做分群覆盖）
- 支持 /~nickname update 手动触发
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from core.logger import get_logger

logger = get_logger("nickname_sync")

_ROLES_PATH = Path(__file__).resolve().parent.parent / "config" / "roles.toml"
_GROUP_NICK_PATH = Path(__file__).resolve().parent.parent / "data" / "group_nicknames.json"


# ── 分群昵称文件读写 ──────────────────────────────────────
def _load_group_nicks() -> dict[str, dict[str, str]]:
    if not _GROUP_NICK_PATH.exists():
        return {}
    try:
        raw = json.loads(_GROUP_NICK_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
        return {str(k): {str(q): n for q, n in v.items() if isinstance(v, dict)}
                for k, v in raw.items()}
    except Exception as e:
        logger.warning("加载分群昵称文件失败: %s", e)
        return {}


def _save_group_nicks(data: dict[str, dict[str, str]]) -> None:
    _GROUP_NICK_PATH.parent.mkdir(parents=True, exist_ok=True)
    _GROUP_NICK_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── 敏感昵称过滤 ──────────────────────────────────────────
def _blocked_names(cfg) -> set:
    return {"主人", "admin", "管理员", "群主", "bot", "机器人", cfg.bot_name}


def _safe_nick(nick: str, blocked: set) -> str:
    """昵称安全化：去掉 @、换行、过长的字符串；敏感词直接返回空"""
    nick = (nick or "").strip().replace("\n", "").replace("\r", "")
    if len(nick) > 40:
        nick = nick[:40]
    if nick in blocked or nick == "":
        return ""
    return nick


# ── 自动同步（23:59 静默执行）──────────────────────────────
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

    cfg = get_config()
    blocked = _blocked_names(cfg)

    # 好友 → 全局兜底
    global_map: dict[str, str] = {}
    try:
        result = await get_ws_manager().call_api("get_friend_list", timeout=15)
        if result:
            friends = result if isinstance(result, list) else result.get("data", result)
            if isinstance(friends, list):
                for f in friends:
                    qq = str(f.get("user_id", ""))
                    nick = _safe_nick(f.get("nickname", ""), blocked)
                    if qq and nick:
                        global_map[qq] = nick
    except Exception as e:
        logger.warning("好友列表失败: %s", e)

    # 群成员 → 分群 map（card 优先）+ 全局兜底（主页昵称）
    group_map: dict[str, dict[str, str]] = {}
    try:
        glist = await get_ws_manager().call_api("get_group_list", timeout=10)
        groups = glist if isinstance(glist, list) else (glist.get("data", glist) if glist else [])
        if isinstance(groups, list):
            for g in groups:
                gid = str(g.get("group_id", 0))
                if not gid or gid == "0":
                    continue
                per_group: dict[str, str] = {}
                try:
                    members = await get_ws_manager().call_api(
                        "get_group_member_list", {"group_id": int(gid), "no_cache": True}, timeout=20)
                    if isinstance(members, list):
                        for m in members:
                            qq = str(m.get("user_id", ""))
                            if not qq:
                                continue
                            card = _safe_nick(m.get("card", ""), blocked)
                            home = _safe_nick(m.get("nickname", ""), blocked)
                            per_group[qq] = card or home  # 分群: card 优先
                            if home and qq not in global_map:
                                global_map[qq] = home    # 全局兜底: 主页昵称
                except Exception:
                    pass
                if per_group:
                    group_map[gid] = per_group
    except Exception as e:
        logger.warning("群列表失败: %s", e)

    if not group_map and not global_map:
        logger.warning("昵称同步: 未获取到任何数据")
        return

    _save_group_nicks(group_map)
    _merge_global_map(global_map)
    logger.info("自动昵称同步完成: 分群 %d 个群 | 全局 %d 条", len(group_map), len(global_map))


def _merge_global_map(new_map: dict[str, str]) -> int:
    """增量合并到 roles.toml 的 qq_name_map（保留注释，只更新值）"""
    import toml
    try:
        roles = toml.loads(_ROLES_PATH.read_text(encoding="utf-8")) if _ROLES_PATH.exists() else {}
    except Exception:
        roles = {}
    old = roles.get("qq_name_map", {})
    added = 0
    for qq, nick in new_map.items():
        if old.get(qq) != nick:
            old[qq] = nick
            added += 1
    roles["qq_name_map"] = old
    _ROLES_PATH.write_text(toml.dumps(roles), encoding="utf-8")
    try:
        from core.config import reload_config
        reload_config()
    except Exception:
        pass
    return added


# ── 手动触发（/~nickname update）──────────────────────────
async def sync_and_report(chat_id: int = None, is_group: bool = False) -> str:
    """手动触发同步，返回可读结果列表"""
    from services.sender import get_ws_manager
    from core.config import get_config

    cfg = get_config()
    blocked = _blocked_names(cfg)
    group_map: dict[str, dict[str, str]] = _load_group_nicks()
    global_map: dict[str, str] = {}
    source = ""

    if is_group and chat_id:
        # 只同步指定群
        try:
            members = await get_ws_manager().call_api(
                "get_group_member_list", {"group_id": int(chat_id), "no_cache": True}, timeout=20)
            per_group: dict[str, str] = {}
            if isinstance(members, list):
                for m in members:
                    qq = str(m.get("user_id", ""))
                    if not qq:
                        continue
                    card = _safe_nick(m.get("card", ""), blocked)
                    home = _safe_nick(m.get("nickname", ""), blocked)
                    per_group[qq] = card or home
                    if home:
                        global_map[qq] = home
            group_map[str(chat_id)] = per_group
            source = f"群 {chat_id}"
        except Exception as e:
            return "获取群成员失败: " + str(e)
    else:
        # 好友列表 → 全局
        try:
            result = await get_ws_manager().call_api("get_friend_list", timeout=15)
            if result:
                friends = result if isinstance(result, list) else result.get("data", result)
                if isinstance(friends, list):
                    for f in friends:
                        qq = str(f.get("user_id", ""))
                        nick = _safe_nick(f.get("nickname", ""), blocked)
                        if qq and nick:
                            global_map[qq] = nick
        except Exception as e:
            return "获取好友列表失败: " + str(e)
        # 全群成员
        try:
            glist = await get_ws_manager().call_api("get_group_list", timeout=10)
            groups = glist if isinstance(glist, list) else (glist.get("data", glist) if glist else [])
            if isinstance(groups, list):
                for g in groups:
                    gid = str(g.get("group_id", 0))
                    if not gid or gid == "0":
                        continue
                    per_group = group_map.setdefault(gid, {})
                    try:
                        members = await get_ws_manager().call_api(
                            "get_group_member_list", {"group_id": int(gid), "no_cache": True}, timeout=20)
                        if isinstance(members, list):
                            for m in members:
                                qq = str(m.get("user_id", ""))
                                if not qq:
                                    continue
                                card = _safe_nick(m.get("card", ""), blocked)
                                home = _safe_nick(m.get("nickname", ""), blocked)
                                per_group[qq] = card or home
                                if home:
                                    global_map.setdefault(qq, home)
                    except Exception:
                        pass
        except Exception:
            pass
        source = "全部好友+群成员"

    if not group_map and not global_map:
        return "未获取到任何昵称数据"

    _save_group_nicks(group_map)
    g_added = _merge_global_map(global_map)

    if is_group:
        # 群聊触发：只报当前群，不出现"全局/分群"跨群字样
        per = group_map.get(str(chat_id), {})
        lines = ["当前群昵称同步完成", ""]
        lines.append(f"更新 {g_added} 条昵称，共 {len(per)} 名成员")
        return "\n".join(lines)

    lines = [f"昵称同步完成 ({source})", ""]
    lines.append(f"全局兜底更新 {g_added} 条，分群覆盖 {len(group_map)} 个群")

    # 群维度明细（默认隐藏，避免刷屏）
    if os.environ.get("NICKNAME_VERBOSE") == "1":
        for gid in sorted(group_map.keys()):
            per = group_map[gid]
            if not per:
                continue
            lines.append(f"── 群 {gid} ({len(per)} 人) ──")
            for qq, nick in sorted(per.items()):
                lines.append(f"  {qq} -> {nick}")
            lines.append("")
    return "\n".join(lines)


def _fmt(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"
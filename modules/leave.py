"""
退群模块：收集群数据（GH/统计/好感度）→ 发送 → 清理 → 退群
调用入口: cmd_leave() ← commands.py COMMAND_MAP
"""

from __future__ import annotations

import json
from pathlib import Path

from core.logger import get_logger

logger = get_logger("leave")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


# ── 数据收集（复用已有指令的格式化输出） ────────────────────

async def collect_group_data(group_id: int) -> list[tuple[str, str]]:
    """返回 [(标题, 内容), ...]，每条分别发送"""
    parts: list[tuple[str, str]] = []

    gh = _fmt_gh(group_id)
    if gh:
        parts.append(("🏰 GH 公会", gh))

    stats = _fmt_stats(group_id)
    if stats:
        parts.append(("📊 群统计", stats))

    fav = _fmt_fav(group_id)
    if fav:
        parts.append(("💖 好感度", fav))

    return parts


def _fmt_gh(group_id: int) -> str | None:
    """复用 ~gh list 的格式化输出"""
    from modules.gh import _load, _gkey
    path = DATA_DIR / "gh.json"
    data = _load(path)
    gid = _gkey(group_id)
    group_data = data.get(gid, {})
    members = group_data.get("members", {})
    if not members:
        return "本群还没有人登记公会喵~"
    lines = [f"【本群已登记公会 {len(members)}】"]
    for qq, info in members.items():
        if isinstance(info, str):
            lines.append(f"  {qq}: {info}")
        else:
            lines.append(f"  {info.get('nick', qq)}({qq}): {info.get('name', '?')}")
    return "\n".join(lines)


def _fmt_stats(group_id: int) -> str | None:
    """复用 format_stats_report 的格式化输出"""
    from core.config import get_config
    from modules.stats import get_today_stats, format_stats_report
    cfg = get_config()
    stats = get_today_stats(group_id)
    if not stats:
        return "今天还没有统计数据喵~"
    return format_stats_report(stats, cfg, group_id, title="今日群聊统计")


def _fmt_fav(group_id: int) -> str | None:
    """复用 ~favlist 的格式化输出"""
    from core.config import get_config
    from modules.fav import get_all_fav
    cfg = get_config()
    fav_data = get_all_fav(chat_id=group_id, is_group=True)
    if not fav_data:
        return None
    lines = ["【本群好感度排行】"]
    for key, val in sorted(fav_data.items(), key=lambda x: x[1], reverse=True):
        uid = key.split(":")[-1] if ":" in key else key
        name = cfg.qq_name_map.get(uid, uid)
        lines.append(f"  {name}: {val}")
    return "\n".join(lines)

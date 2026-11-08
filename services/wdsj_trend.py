#!/usr/bin/env python3
"""/~wdsj trend（趋势折线图）的实现。

★ 2026-11-06 从 modules/commands.py 抽出。原因：
  commands.py 已经 5000+ 行，trend 那段参数解析 + 三种失败分支 + 出图约 70 行，
  塞在 cmd_wdsj（456 行、9 个 action）里既难读也难改。

为什么放 services 而不是 plugins/wdsj：
  plugins/ 不是 Python 包（loader 按路径 exec），跨模块 import 得走
  load_plugin_core() 绕一层；而这段是纯业务逻辑，天然属于 services 层。
  plugins/wdsj 继续负责「指令所有权」，实现留在这里供两边调用。

依赖约定：
  bound_player / suppress_tip 由调用方传入，本模块不 import modules.commands，
  避免 commands ↔ services 循环依赖。
"""
from __future__ import annotations

import logging

from services.sender import send_group_msg, send_private_msg
from services.wdsj_tracker import (
    _TREND_LABELS,
    _load_history,
    generate_trend_card,
    get_player_trend,
    resolve_metric,
)
from utils.format_lang import format_lang

logger = logging.getLogger(__name__)


async def handle_trend(args, user_id: int, group_id: int, is_group: bool,
                       bound_player: str | None = None,
                       suppress_tip: bool = False):
    """画趋势图。

    返回 str  → 调用方回给用户的文本
    返回 None → 图表已经自己发过了，调用方不用再回

    args[0] 是指令名（trend/趋势/...），args[1:] 是「玩家名 + 指标」，
    两者顺序不限，靠 resolve_metric 识别哪些是指标。
    """
    player = None
    metrics: list[str] = []
    for a in args[1:]:
        # ★ 先按别名归一化（jjc/竞技场/bw/胜场…），认不出来才当玩家名。
        #   原来只认五个英文 key，用户发 `trend jjc` 会被当成玩家名，
        #   回「记录里没有 jjc 这个人」——纯误导（2026-11-06 用户实测）。
        m = resolve_metric(a)
        if m:
            if m not in metrics:
                metrics.append(m)          # 多指标叠加，最多 3 条
        else:
            player = a

    if not metrics:
        metrics = ["bw_kills"]
    metrics = metrics[:3]
    if not player:
        player = bound_player
    if not player:
        return format_lang("cmd_wdsj.m47")

    # ★ 先分三种情况再动手，不要一失败就说「需要 2 天记录」：
    #   玩家名根本没采集过时那句话是错的。
    hist = _load_history()
    key = next((k for k in hist if k.lower() == str(player).lower()), None)
    if key is None:
        tip = format_lang("cmd_wdsj.m55", player=player)
        if not bound_player:
            tip += "\n" + format_lang("cmd_wdsj.m58")
        return tip

    # 逐个指标取序列：够 2 天的留下，其余提示一句
    ok: list[str] = []
    short: list[tuple[str, int]] = []
    for m in metrics:
        s = get_player_trend(key, m)
        if len(s) >= 2:
            ok.append(m)
        else:
            short.append((m, len(s)))

    if not ok:
        if all(n == 0 for _m, n in short):
            return format_lang("cmd_wdsj.m56", player=player,
                               label=_TREND_LABELS.get(metrics[0], metrics[0]))
        m, n = max(short, key=lambda x: x[1])
        return format_lang("cmd_wdsj.m57", n=n, label=_TREND_LABELS.get(m, m))

    label = "+".join(_TREND_LABELS.get(m, m) for m in ok)

    # 确认能画了再发「正在生成」，避免"提示完又报错"的割裂感
    if not suppress_tip:
        tip = format_lang("cmd_wdsj.m48", player=player, label=label)
        if short:
            tip += "（" + "、".join(_TREND_LABELS.get(m, m) for m, _ in short) + "还没攒够两天，这次先不画喵）"
        await (send_group_msg(tip, group_id) if is_group
               else send_private_msg(tip, user_id))

    img = await generate_trend_card(key, ok)
    if img:
        cq = f"[CQ:image,file=file:///{img.replace(chr(92), '/')}]"
        await (send_group_msg(cq, group_id) if is_group
               else send_private_msg(cq, user_id))
        return None
    return format_lang("cmd_wdsj.m49", player=player, label=label)

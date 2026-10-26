"""
指令系统（重构版）
- 所有指令处理函数集中管理
- 统一签名: async def cmd_xxx(args, user_id, group_id, sender_name, is_group, bot_qq) -> str | None
- 全量 i18n + 详细日志
- 指令注册表 COMMAND_MAP
- 分发器 handle_command
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import string
import time
import urllib.parse
from datetime import date
from pathlib import Path

import httpx

from core.logger import get_logger
from core.config import get_config, reload_config, load_roles_config, save_roles_config
from utils.format_lang import format_lang
from modules.fav import get_all_fav, FAV_FILE as FAV_FILE_REF, reset_all_fav
from services.sender import send_group_msg, send_private_msg, send_raw_group, send_raw_user
from modules.search import perform_search
from core.plugin.cores import load_plugin_core
_core_earthquake = load_plugin_core("earthquake")
cmd_eq = _core_earthquake.cmd_eq
from core.plugin.cores import load_plugin_core
_core_agnes = load_plugin_core("agnes")
cmd_draw = _core_agnes.cmd_draw
cmd_video = _core_agnes.cmd_video
cmd_img2video = _core_agnes.cmd_img2video
owner_quota_get = _core_agnes.owner_quota_get
owner_quota_set = _core_agnes.owner_quota_set
owner_quota_reset = _core_agnes.owner_quota_reset
try:
    from core.plugin.cores import load_plugin_core
    _core_steam = load_plugin_core("steam")
    cmd_steam = _core_steam.cmd_steam
    cmd_steam_doing = _core_steam.cmd_steam_doing
except ImportError:          # 缺依赖时不拖垮整条指令链路
    cmd_steam = cmd_steam_doing = None
try:
    from core.plugin.cores import load_plugin_core
    _core_op = load_plugin_core("op")
    cmd_op = _core_op.cmd_op
    cmd_persona = _core_op.cmd_persona
    cmd_master = _core_op.cmd_master
    cmd_sleep = _core_op.cmd_sleep
    cmd_hanxu = _core_op.cmd_hanxu
except ImportError:
    cmd_op = cmd_persona = cmd_master = cmd_sleep = cmd_hanxu = None

from modules.changelog import send_changelog_card, send_weather_card, send_box_card
from modules.whois_lookup import lookup_domain   # ★ WHOIS 域名查询

try:
    from modules.tuf_commands import cmd_tuflevel, cmd_tuf_search, cmd_tufd, cmd_tufpage
except ImportError:
    cmd_tuflevel = cmd_tuf_search = cmd_tufd = cmd_tufpage = None

from core.token_tracker import cmd_cost, cmd_tokens, cmd_cache
from core.bot_notes import cmd_note
from core.ctx_usage import cmd_ctx
from core.plugin.cores import load_plugin_core
_core_memory_link = load_plugin_core("memory_link")
cmd_mlink = _core_memory_link.cmd_mlink

# ★ 经济系统已迁移为插件（points/shop，v2.0.1），不再内置

logger = get_logger("commands")


async def _cmd_update(args, user_id, group_id, sender_name, is_group, bot_qq):
    from modules.auto_update import cmd_update
    return await cmd_update(args, user_id, group_id, sender_name, is_group, bot_qq)


# ════════════════════════════════════════════════════════════
#  指令处理函数
# ════════════════════════════════════════════════════════════

async def cmd_help(args, user_id, group_id, sender_name, is_group, bot_qq):
    """显示帮助列表 — /~help 发送完整指令卡片，/~help <指令> 查看详细用法"""
    cfg = get_config()
    name = cfg.bot_name
    logger.info("指令 /~help 触发 user=%d args=%s", user_id, args)

    # ── 详细帮助：/~help <关键词> ──
    if args:
        keyword = args[0].lower().lstrip("/~")
        # format_lang 无 default 参数，缺失会返回 [LANG:...] 裸键 → 先用 has_key 判断
        from utils.format_lang import has_key
        if has_key(f"help.detail.{keyword}"):
            return format_lang(f"help.detail.{keyword}")
        # 兜底：docstring / _CMD_DESC / capability 描述（覆盖所有 COMMAND_MAP + 插件指令）
        try:
            from modules.commands import COMMAND_MAP
            target_handler = COMMAND_MAP.get(keyword)
            desc = ""
            if target_handler is not None:
                doc = (getattr(target_handler, '__doc__', '') or '').strip()
                if doc:
                    desc = doc.split('\n')[0]
            if not desc:
                from services.llm import _CMD_DESC
                desc = _CMD_DESC.get(keyword, "")
                if not desc and target_handler is not None:
                    for k, h in COMMAND_MAP.items():
                        if h is target_handler:
                            desc = _CMD_DESC.get(k, "")
                            if desc:
                                break
            # 插件指令描述（help_card._PLUGIN_COMMANDS）
            if not desc:
                try:
                    from modules.help_card import _PLUGIN_COMMANDS
                    desc = _PLUGIN_COMMANDS.get(keyword, "")
                except Exception:
                    pass
            if desc:
                return format_lang("cmd_help.m1", keyword=keyword, desc=desc)
            # 插件指令描述
            try:
                from core.capability import get_capability_registry
                reg = get_capability_registry()
                for c in (list(reg._caps.values()) if hasattr(reg, "_caps") else []):
                    if getattr(c, "name", "") == keyword and getattr(c, "description", ""):
                        return format_lang("cmd_help.m2", keyword=keyword, description=c.description)
            except Exception:
                pass
        except Exception:
            pass
        return format_lang("help.not_found", keyword=keyword)

    # ── 完整帮助：发送预渲染指令卡片 ──
    card_path = Path(__file__).resolve().parent.parent / "data" / "help_card.png"
    if card_path.exists():
        try:
            from services.sender import send_by_chat_type
            cq = f"[CQ:image,file=file:///{card_path.as_posix()}]"
            chat_id = group_id if is_group else user_id
            await send_by_chat_type(cq, chat_id, is_group, user_id if not is_group else None)
            return None  # 已发送
        except Exception as e:
            logger.warning("帮助卡片发送失败，降级: %s", e)
    else:
        logger.warning("帮助卡片文件不存在，降级文本")

    # ── 降级：文本帮助 ──
    is_admin = cfg.is_admin(user_id, group_id)
    lines = [format_lang("help.header", name=name), ""]
    lines.append(format_lang("help.usage_hint"))
    lines.append(format_lang("help.usage_example"))
    lines.append("")

    from utils.format_lang import get_lang_data
    sections = get_lang_data().get("help", {}).get("sections", [])
    if isinstance(sections, str):
        sections = [
            [format_lang("cmd_help.m3"), format_lang("cmd_help.m4"), format_lang("cmd_help.m5")],
            [format_lang("cmd_help.m6"), format_lang("cmd_help.m7"), format_lang("cmd_help.m8"), format_lang("cmd_help.m9")],
            [format_lang("cmd_help.m10"), format_lang("cmd_help.m11"), format_lang("cmd_help.m12"), format_lang("cmd_help.m13")],
            [format_lang("cmd_help.m14"), format_lang("cmd_help.m15"), format_lang("cmd_help.m16"), format_lang("cmd_help.m17"), format_lang("cmd_help.m18")],
            [format_lang("cmd_help.m19"), format_lang("cmd_help.m20"), format_lang("cmd_help.m21"), format_lang("cmd_help.m22")],
        ]
    for section in sections:
        if not section:
            continue
        label = section[0]
        # ⚠️ 这里比对的是 section 的**分类标签**，故意用字面量：
        #   若改成 format_lang，用户改文案就会改掉权限逻辑（code review 指出）
        if "主人专用" in label and not is_admin:
            continue
        lines.append(label)
        for cmd in section[1:]:
            lines.append(f"   {cmd}")
        lines.append("")

    lines.append(format_lang("help.footer"))
    return "\n".join(lines)


# ════════════════════════════════════════════════════════════
#  详细帮助数据
# ════════════════════════════════════════════════════════════

_HELP_DETAIL = {
    "nasa": format_lang("mod.m1"),
    "oldphi": format_lang("mod.m2"),
    "wzq": format_lang("mod.m3"),

    "remind": format_lang("mod.m4"),

    "chou": format_lang("mod.m5"),

    "stats": format_lang("mod.m6"),

    "recall": format_lang("mod.m7"),

    "s": format_lang("mod.m8"),

    "favlist": format_lang("mod.m9"),

    "luck": format_lang("mod.m10"),

    "up": format_lang("mod.m11"),

    "updateinfo": format_lang("mod.m12"),

    "info": format_lang("mod.m13"),

    "ping": format_lang("mod.m14"),

    "owner": format_lang("mod.m15"),

    "preset": format_lang("mod.m16"),

    "reload": format_lang("mod.m17"),

    "tr": format_lang("mod.m18"),

    "translate": format_lang("mod.m19"),

    "countdown": format_lang("mod.m20"),

    "balance": format_lang("mod.m21"),

    "plugin": format_lang("mod.m22"),
    "apy": format_lang("mod.m23"),

    "voice": format_lang("mod.m24"),

    "cost": format_lang("mod.m25"),

    "tokens": format_lang("mod.m26"),

    "note": format_lang("mod.m27"),

    "cache": format_lang("mod.m28"),

    "resetfav": format_lang("mod.m29"),

    "memory": format_lang("mod.m30"),
}


async def cmd_favlist(args, user_id, group_id, sender_name, is_group, bot_qq):
    """查看当前聊天的好感度列表"""
    logger.info("指令 /~favlist 触发 user=%d chat=%d", user_id, group_id if is_group else user_id)
    cfg = get_config()
    chat_id = group_id if is_group else user_id

    fav_data = get_all_fav(chat_id=chat_id, is_group=is_group)
    if not fav_data:
        return format_lang("favlist.empty")

    lines = [format_lang("favlist.header")]
    for key, val in sorted(fav_data.items(), key=lambda x: x[1], reverse=True):
        # 从 key 中提取 user_id: "g123:456" -> "456", "p:456" -> "456"
        uid = key.split(":")[-1] if ":" in key else key
        name = cfg.get_display_name(uid, group_id=group_id if is_group else 0)
        lines.append(format_lang("favlist.item_format", name=name, value=val))
    return "\n".join(lines)


async def cmd_info(args, user_id, group_id, sender_name, is_group, bot_qq):
    """返回系统和运行状态"""
    import platform
    import time as _time
    
    cfg = get_config()
    name = cfg.bot_name
    logger.info("指令 /~info 触发 user=%d", user_id)

    lines: list[str] = []
    lines.append(format_lang("cmd_info.m1", name=name))
    lines.append("")

    # ── 系统 ──
    lines.append("[System]")
    lines.append(f"  OS     : {platform.system()} {platform.release()} ({platform.machine()})")
    lines.append(f"  Python : {platform.python_version()}")
    try:
        import psutil
        mem = psutil.virtual_memory()
        total_mb = mem.total // (1024**2)
        used_mb = mem.used // (1024**2)
        lines.append(f"  Memory : {used_mb}MB / {total_mb}MB ({mem.percent}%)")
        cpu_name = platform.processor() or "Unknown"
        cpu_count = psutil.cpu_count(logical=True)
        cpu_percent = psutil.cpu_percent(interval=0.3)
        lines.append(f"  CPU    : {cpu_name} ({cpu_count}c) [{cpu_percent}%]")
        disk = psutil.disk_usage('/')
        disk_gb = disk.used / (1024**3)
        disk_total = disk.total / (1024**3)
        lines.append(f"  Disk   : {disk_gb:.1f}GB / {disk_total:.1f}GB ({disk.percent}%)")
    except ImportError:
        lines.append(f"  Memory : unavailable (install psutil)")
        lines.append(f"  Disk   : unavailable")

    lines.append("")

    # ── 运行 ──
    lines.append("[Runtime]")
    try:
        import psutil
        proc = psutil.Process()
        create_time = proc.create_time()
        uptime_sec = int(_time.time() - create_time)
        hours = uptime_sec // 3600
        mins = (uptime_sec % 3600) // 60
        lines.append(f"  Uptime : {hours}h {mins}m")
    except Exception:
        lines.append(f"  Uptime : unknown")

    from core.dispatcher import get_current_dispatcher
    from core.context_manager import get_context_mgr
    disp = get_current_dispatcher()
    ctx = get_context_mgr()
    stats = ctx.get_stats()
    lines.append(f"  Msgs   : {disp.msg_count if disp else '?'} processed")
    lines.append(f"  Chats  : {stats.get('active_chats', 0)} active")
    lines.append(f"  Tasks  : {stats.get('active_tasks', 0)} pending")

    from modules.judge import get_cache_stats
    cache_s = get_cache_stats()
    lines.append(f"  S-Cache: {cache_s.get('entries', 0)} entries")

    lines.append("")

    # ── 连接 ──
    lines.append("[Connection]")
    from services.sender import get_ws_manager
    ws = get_ws_manager()
    lines.append(f"  WS     : {'connected' if ws.is_connected else 'disconnected'}")
    lines.append(f"  Reconn : {ws._connect_count} times")

    lines.append("")

    # ── 配置 ──
    lines.append("[Config]")
    lines.append(f"  Reply Threshold : {cfg.reply_interest}")
    lines.append(f"  Context Length  : {cfg.context_length}")
    lines.append(f"  Private Chat    : {'ON' if cfg.enable_private else 'OFF'}")
    lines.append(f"  Image Recog     : {'ON' if cfg.image_model.switch else 'OFF'}")
    lines.append(f"  Groups          : {len(cfg.group_list)} whitelisted")
    lines.append(f"  Debug           : {'ON' if cfg.debug_mode else 'OFF'}")

    lines.append("")

    # ── 模型 ──（从运行时配置读取，不写死）
    lines.append("[Models]")
    lines.append(f"  Reply  : {cfg.reply_model.name} @ {cfg.reply_model.provider}")
    lines.append(f"  Cheap  : {cfg.cheap_model.name} @ {cfg.cheap_model.provider}")
    lines.append(f"  Judge  : {cfg.judge_model.name} @ {cfg.judge_model.provider}")
    img_enabled = "ON" if cfg.image_model.switch else "DISABLED"
    lines.append(f"  Vision : {cfg.image_model.name} @ {cfg.image_model.provider} ({img_enabled})")

    lines.append("")

    # ── Token 消耗 ──
    lines.append("[Token]")
    try:
        from core.token_tracker import calc_cost
        cost = calc_cost(today_only=False)
        t = cost["today"]
        total = cost["total"]
        lines.append(f"  Today  : {t['calls']} calls, {t['prompt']+t['completion']:,} tokens = ¥{t['cost']:.4f}")
        lines.append(f"  Total  : {total['calls']} calls, {total['prompt']+total['completion']:,} tokens = ¥{total['cost']:.2f}")
    except Exception:
        lines.append("  (unavailable)")

    return "\n".join(lines)


async def cmd_balance(args, user_id, group_id, sender_name, is_group, bot_qq):
    """余额查询 /~balance"""
    from core.config import get_config
    cfg = get_config()
    key = cfg.reply_model.key
    if not key:
        return format_lang("cmd_balance.m1")

    try:
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                "https://api.deepseek.com/user/balance",
                headers={"Accept": "application/json", "Authorization": f"Bearer {key}"},
            )
            if resp.status_code != 200:
                return format_lang("cmd_balance.m2", status_code=resp.status_code)
            data = resp.json()
    except Exception as e:
        return format_lang("cmd_balance.m3", e=e)

    if not data.get("is_available"):
        return format_lang("cmd_balance.m4")

    infos = data.get("balance_infos", [])
    if not infos:
        return format_lang("cmd_balance.m5")

    lines = [format_lang("cmd_balance.m6")]
    for info in infos:
        currency = info.get("currency", "?")
        total = info.get("total_balance", "?")
        granted = info.get("granted_balance", "?")
        topped_up = info.get("topped_up_balance", "?")
        lines.append(format_lang("cmd_balance.m7", total=total, currency=currency))
        lines.append(format_lang("cmd_balance.m8", granted=granted, currency=currency))
        lines.append(format_lang("cmd_balance.m9", topped_up=topped_up, currency=currency))
    return "\n".join(lines)


async def cmd_box(args, user_id, group_id, sender_name, is_group, bot_qq):
    """查询快递物流信息（优先输出精美卡片图片）"""
    if not args:
        return format_lang("box.prompt_input")
    
    tracking_no = args[0]
    ckey = os.getenv("KUAIBAO_CKEY", "")
    logger.info("指令 /~box 触发 单号=%s user=%d (卡片模式优先)", tracking_no, user_id)
    
    if not ckey:
        logger.warning("快递API密钥未配置 (KUAIBAO_CKEY)")
        return format_lang("error.api_fail")

    api_url = f"https://openapi.dwo.cc/api/kuaiok?ckey={ckey}&trackingNo={tracking_no}"
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(api_url)
            if resp.status_code != 200:
                return format_lang("box.api_error")
            data = resp.json()
    except Exception as e:
        logger.warning("快递查询网络错误: %s", e)
        return format_lang("box.network_error", error=str(e))

    code = data.get("code", "")
    if code != "0000000000":
        logger.debug("快递API业务码异常: %s desc=%s", code, data.get("desc", ""))
        return format_lang("box.api_error",
                           message=data.get("desc", format_lang("common.unknown_error")))

    try:
        pkgs = data.get("data", {}).get("packageInfoList", [])
        if not pkgs:
            return format_lang("box.no_result")
        
        pkg = pkgs[0]
        cp_name = pkg.get("cpName", format_lang("common.unknown_courier"))
        state = pkg.get("state", format_lang("common.unknown"))
        
        # 状态映射（i18n）
        state_map = {
            "TRANSPORT": format_lang("box.state_transport", default=format_lang("cmd_box.m1")),
            "DELIVERING": format_lang("box.state_delivering", default=format_lang("cmd_box.m2")),
            "SIGNED": format_lang("box.state_signed", default=format_lang("cmd_box.m3")),
            "RETURN": format_lang("box.state_return", default=format_lang("cmd_box.m4")),
            "PENDING": format_lang("box.state_pending", default=format_lang("cmd_box.m5")),
            "FINISH": format_lang("box.state_finish", default=format_lang("cmd_box.m6")),
        }
        state_text = state_map.get(state, state)

        latest_msg = pkg.get("operateMessage", "")
        details = pkg.get("trackingDetails", [])

        logger.info("快递查询成功: 单号=%s 状态=%s (%d条轨迹)", tracking_no, state_text, len(details))

        # ── 异步卡片生成（不阻塞聊天线程）──
        async def _bg_send_box():
            try:
                card_result = await send_box_card(
                    cp_name=cp_name,
                    tracking_no=tracking_no,
                    state=state,
                    state_text=state_text,
                    latest_msg=latest_msg,
                    details=details,
                    group_id=group_id if is_group else None,
                    user_id=user_id if not is_group else None,
                    is_group=is_group,
                )
                if card_result is not None:
                    # 卡片生成失败，回退纯文本
                    fallback_lines = [
                        format_lang("box.header", cp_name=cp_name, tracking_no=tracking_no),
                        format_lang("box.state", state=state_text),
                    ]
                    if latest_msg:
                        clean_msg = re.sub(r'[（(【][^）)]*(如遇问题|物流问题)[^）)]*[）)]️]?', '', latest_msg).strip()
                        fallback_lines.append(format_lang("box.latest", msg=clean_msg))

                    fallback_lines.append(format_lang("box.trajectory_header"))
                    for d in details:
                        t = d.get("time", "")
                        ctx = d.get("context", "")
                        ctx = re.sub(r'[（(【][^）)]*(如遇问题|物流问题)[^）)]*[）)]️]?', '', ctx).strip()
                        time_str = f"{t[4:6]}-{t[6:8]} {t[8:10]}:{t[10:12]}" if len(t) >= 12 else t
                        fallback_lines.append(format_lang("box.trajectory_item", time=time_str, context=ctx))

                    if state == "DELIVERING":
                        fallback_lines.append(format_lang("box.delivering_tip"))
                    elif state == "SIGNED":
                        fallback_lines.append(format_lang("box.signed_tip"))

                    fallback_text = "\n".join(fallback_lines)
                    if is_group:
                        await send_group_msg(fallback_text, group_id)
                    else:
                        await send_private_msg(fallback_text, user_id)
            except Exception as e:
                logger.error("[BG] 快递卡片后台发送失败: %s", e, exc_info=True)

        asyncio.create_task(_bg_send_box())
        return None  # 后台异步处理，无需立即回复

    except Exception as e:
        logger.error("快递信息解析失败: %s", e)
        return format_lang("box.parse_error")


LUCK_FILE = Path(__file__).resolve().parent.parent / "data" / "luck.json"


def _get_today_luck(user_id: int) -> int | None:
    """获取今天该用户的幸运值，未抽签返回 None"""
    today = date.today().isoformat()
    if not LUCK_FILE.exists():
        return None
    try:
        with open(LUCK_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get(today, {}).get(str(user_id))
    except Exception as e:
        logger.warning("获取今日运气失败: %s", e)
        return None


def _set_today_luck(user_id: int, value: int):
    """保存今天该用户的幸运值"""
    LUCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    
    data = {}
    if LUCK_FILE.exists():
        try:
            with open(LUCK_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    
    if today not in data:
        data[today] = {}
    data[today][str(user_id)] = value
    
    # 只保留最近 7 天的数据
    sorted_days = sorted(data.keys(), reverse=True)[:7]
    data = {d: data[d] for d in sorted_days}
    
    with open(LUCK_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


async def cmd_luck(args, user_id, group_id, sender_name, is_group, bot_qq):
    """每日运势抽签 /~luck — 今天内同一人返回相同值"""
    logger.info("指令 /~luck 触发 user=%d", user_id)
    
    # 检查今天是否已经抽过
    cached = _get_today_luck(user_id)
    if cached is not None:
        return format_lang("cmd_luck.m1", user_id=user_id, cached=cached)
    
    # 首次抽签，生成幸运值
    if random.random() < 0.001:
        num = 1000
    else:
        num = random.randint(1, 100)
    
    _set_today_luck(user_id, num)
    return format_lang("cmd_luck.m2", user_id=user_id, num=num)


async def cmd_testsys(args, user_id, group_id, sender_name, is_group, bot_qq):
    """发送测试卡片消息"""
    logger.info("指令 /~testsys 触发 user=%d group=%d", user_id, group_id or 0)
    
    markdown_content = format_lang("testsys.card_markdown")
    button = {
        "id": "btn_test",
        "render_data": {
            "label": format_lang("testsys.button_label"),
            "visited_label": format_lang("testsys.button_visited"),
            "style": 1,
        },
        "action": {
            "type": 2,
            "permission": {"type": 2},
            "data": "/~testok",
            "enter": True,
            "unsupport_tips": format_lang("testsys.unsupport_tip", default=format_lang("cmd_testsys.m1")),
        },
    }
    keyboard = {"rows": [{"buttons": [button]}]}
    card = {"type": "markdown", "data": {"markdown": {"content": markdown_content}, "keyboard": keyboard}}
    
    # 返回特殊标记，由分发器负责实际发送
    return "__SYS_TEST_CARD__"


async def cmd_testok(args, user_id, group_id, sender_name, is_group, bot_qq):
    """测试卡片回调"""
    logger.info("指令 /~testok 触发（卡片按钮点击）user=%d", user_id)
    return "test ok"


async def cmd_jsonraw(args, user_id, group_id, sender_name, is_group, bot_qq):
    """/~jsonraw <对话内容> → 输出 LLM 原始 JSON"""
    text = " ".join(args) if args else "喵"
    logger.info("指令 /~jsonraw 触发 user=%d text='%s'", user_id, text)
    from services.llm import call_llm
    from core.config import get_config
    cfg = get_config()
    max_chars = "40" if is_group else "12"
    # ★ v2.2.2: 提醒模板统一放 data/skills/40_reminders.md（原硬编码已迁走）
    from services.llm import _build_reminder
    fmt_reminder = _build_reminder("jsonraw_reminder", max_chars=max_chars)

    system = cfg.system_prompt or format_lang("cmd_jsonraw.m1")
    raw = await call_llm(cfg.reply_model, [
        {"role": "system", "content": system},
        {"role": "user", "content": format_lang("cmd_jsonraw.m2", fmt_reminder=fmt_reminder, sender_name=sender_name, text=text)},
    ], max_tokens=3000, temperature=0.8, json_mode=True)
    return raw or format_lang("cmd_jsonraw.m3")


async def cmd_md(args, user_id, group_id, sender_name, is_group, bot_qq):
    """发送 Markdown 消息卡片 /~md <内容>"""
    if not args:
        return format_lang("cmd_md.m1")
    raw = " ".join(args)
    raw = raw.replace("\\n", "\n")

    from services.sender import get_ws_manager
    mgr = get_ws_manager()
    payload = {
        "action": "send_group_msg" if is_group else "send_private_msg",
        "params": {
            "message": [
                {
                    "type": "markdown",
                    "data": {"content": raw},
                }
            ],
        },
    }
    if is_group:
        payload["params"]["group_id"] = group_id
    else:
        payload["params"]["user_id"] = user_id

    await mgr.send(payload)
    return None


async def cmd_update_info(args, user_id, group_id, sender_name, is_group, bot_qq):
    """显示更新日志 /~up [版本号]，无参数默认最新版本"""
    logger.info("指令 /~up 触发 user=%d group=%d args=%s", user_id, group_id or 0, args)

    from modules.changelog import _read_update_log
    full_md = _read_update_log()

    custom_md = None
    if args and args[0].lower() != "all":
        # 指定版本
        target_version = args[0]
        import re
        clean = target_version.lstrip('vV')
        pattern = rf'(##\s*v?{re.escape(clean)}[\s\S]*?)(?=\n## |$)'
        match = re.search(pattern, full_md, re.IGNORECASE)
        if match:
            custom_md = match.group(1).strip()
        else:
            return format_lang("cmd_update_info.m1", target_version=target_version)
    elif not args:
        # 无参数：提取最新（第一个 ## v 段落）
        import re
        match = re.search(r'(##\s*v[\d.]+.*?)(?=\n## |$)', full_md, re.DOTALL)
        if match:
            custom_md = match.group(1).strip()

    # 异步生成并发送卡片
    async def _bg_send_changelog():
        try:
            result = await send_changelog_card(
                group_id=group_id if is_group else None,
                user_id=user_id if not is_group else None,
                is_group=is_group,
                custom_md=custom_md,
            )
            if result is not None:
                if is_group:
                    await send_group_msg(result, group_id)
                else:
                    await send_private_msg(result, user_id)
        except Exception as e:
            logger.error("[BG] 更新日志卡片后台发送失败: %s", e, exc_info=True)

    asyncio.create_task(_bg_send_changelog())
    return None


# ─── 搜索指令 ──────────────────────────────────────────────

async def cmd_search(args, user_id, group_id, sender_name, is_group, bot_qq):
    """搜索指令 /~search [条数] [数据源] <关键词>，数据源: baidu/baike/bing/all(默认)"""
    if not args:
        return format_lang("search.prompt_input")

    limit = 4
    source = "all"

    # 解析可选条数
    if args[0].isdigit():
        limit = min(int(args[0]), 20)
        args = args[1:]
        if not args:
            return format_lang("search.prompt_input")

    # 解析可选数据源
    if args and args[0].lower() in ("baidu", "baike", "bing", "all"):
        source = args[0].lower()
        args = args[1:]
        if not args:
            return format_lang("search.prompt_input")

    query = " ".join(args)
    chat_id = group_id if is_group else user_id
    logger.info("指令 /~search 触发 query='%s' limit=%d source=%s user=%d", query, limit, source, user_id)

    result = await perform_search(query, sender_name=sender_name, user_id=user_id,
                                   chat_id=chat_id, limit=limit, source=source, is_group=is_group)
    return result or format_lang("search.no_result")


async def cmd_read(args, user_id, group_id, sender_name, is_group, bot_qq):
    """/~read <URL> — 深度读取网页正文，返回纯文本摘要。用于搜索后精读具体链接"""
    if not args:
        return format_lang("cmd_read.m1")
    url = args[0]
    if not url.startswith("http"):
        return format_lang("cmd_read.m2")

    try:
        from modules.local_search import get_scraper
        scraper = get_scraper()
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, lambda: scraper.scrape(url))
        if result:
            return result
        return format_lang("cmd_read.m3", url=url)
    except Exception as e:
        logger.warning("页面读取失败: %s", e)
        return format_lang("cmd_read.m4", e=e)


# ─── WHOIS 指令 ──────────────────────────────────────────────

async def cmd_whois(args, user_id, group_id, sender_name, is_group, bot_qq):
    """/~whois <域名> — 查询域名注册信息（注册商/注册时间/到期时间/NS/状态）"""
    if not args:
        return format_lang("cmd_whois.m1")
    domain = " ".join(args)
    logger.info("指令 /~whois 触发 domain=%s user=%d", domain, user_id)
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, lookup_domain, domain)
    return result


async def cmd_write_code(args, user_id, group_id, sender_name, is_group, bot_qq):
    """/~write_code <描述> — 生成代码文件并发送"""
    if not args:
        return format_lang("cmd_write_code.m1")
    desc = " ".join(args)
    lang = "html" if any(kw in desc.lower() for kw in ("html", format_lang("cmd_write_code.m2"), "web", "css", "javascript", "js")) else "python"
    from core.tools import _write_code
    return await _write_code(
        language=lang, description=desc,
        user_id=user_id, group_id=group_id, sender_name=sender_name,
        is_group=is_group, bot_qq=bot_qq,
    )


# ─── 忽略/解除忽略指令 ──────────────────────────────────────

async def cmd_ignore(args, user_id, group_id, sender_name, is_group, bot_qq):
    """/~ignore <QQ> — 全群忽略该用户（仅 admin）"""
    cfg = get_config()
    if user_id != cfg.admin_qq:
        return format_lang("cmd_ignore.m1")
    if not args:
        return format_lang("cmd_ignore.m2")
    try:
        target = int(args[0])
    except ValueError:
        return format_lang("cmd_ignore.m3")
    from modules.ignore_users import add_ignored
    add_ignored(target)
    return format_lang("cmd_ignore.m4", target=target)


async def cmd_unignore(args, user_id, group_id, sender_name, is_group, bot_qq):
    """/~unignore <QQ> — 解除全群忽略（仅 admin）"""
    cfg = get_config()
    if user_id != cfg.admin_qq:
        return format_lang("cmd_unignore.m1")
    if not args:
        return format_lang("cmd_unignore.m2")
    arg = args[0].lower()
    from modules.ignore_users import remove_ignored, list_ignored
    if arg == "all":
        ids = list_ignored()
        count = 0
        for uid in ids:
            remove_ignored(uid)
            count += 1
        return format_lang("cmd_unignore.m3", count=count)
    try:
        target = int(args[0])
    except ValueError:
        return format_lang("cmd_unignore.m4")
    if remove_ignored(target):
        return format_lang("cmd_unignore.m5", target=target)
    return format_lang("cmd_unignore.m6", target=target)




# ─── 管理员指令 ────────────────────────────────────────────

async def cmd_reload(args, user_id, group_id, sender_name, is_group, bot_qq):
    """热重载配置（不重启进程）"""
    roles = load_roles_config()
    if user_id != roles.get("admin_qq"):
        logger.warning("非管理员尝试 reload user=%d", user_id)
        return format_lang("reload.permission_denied")

    logger.info("管理员 %s(%d) 触发热重载", sender_name, user_id)
    from modules.judge import reload_keywords
    reload_config()
    reload_keywords()
    # ★ v2.3.95: 技能文件（data/skills/*.md）也要重读。
    #   原来只重载 config + keywords —— 改 skills 后发 /~reload 完全不生效，只能重启进程
    #   （2026-10-19 实测三条路径全空：本函数、bg_tasks 的 control.txt 都只 reload_config；
    #    唯一会调 reload_skill_cache() 的 Bot.handle_reload() 所在循环 bot.py:147 已注释停用）。
    try:
        from services.llm import reload_skill_cache, _load_skill_sections
        reload_skill_cache()
        _n = len(_load_skill_sections())
        logger.info("技能文件已重载: %d 个章节", _n)
    except Exception as e:
        logger.error("技能文件重载失败: %s", e, exc_info=True)
    logger.info("热重载完成")
    return format_lang("reload.success")


async def cmd_add_relation(args, user_id, group_id, sender_name, is_group, bot_qq):
    """添加用户关系（仅管理员私聊）"""
    if is_group:
        return format_lang("error.permission_denied")
    
    roles = load_roles_config()
    admin_qq = roles["admin_qq"]
    if user_id != admin_qq:
        return format_lang("error.permission_denied")
    
    if len(args) < 3:
        return format_lang("relation.prompt_usage")
    
    qq_id = args[0]
    nick = args[1]
    relation = args[2].lower()
    
    if not qq_id.isdigit():
        return format_lang("relation.invalid_qq")
    if relation == "admin":
        return format_lang("relation.invalid_role")
    
    if "qq_name_map" not in roles:
        roles["qq_name_map"] = {}
    roles["qq_name_map"][qq_id] = nick
    
    if relation == "friend":
        if "friend_qqs" not in roles:
            roles["friend_qqs"] = []
        if int(qq_id) not in roles["friend_qqs"]:
            roles["friend_qqs"].append(int(qq_id))
    
    try:
        save_roles_config(roles)
        # ★ 立即刷新内存配置，否则 favlist/游戏等模块拿不到新名字
        try:
            from core.config import reload_config
            reload_config()
        except Exception:
            pass
        logger.info("关系更新: %s(%s) → %s 操作者=%s", qq_id, nick, relation, sender_name)
        return format_lang("relation.success", qq=qq_id, name=nick, role=relation)
    except Exception as e:
        logger.error("保存关系配置失败: %s", e)
        return format_lang("relation.save_fail", error=e)


async def cmd_reset_fav(args, user_id, group_id, sender_name, is_group, bot_qq):
    """重置好感度数据（仅管理员）"""
    logger.info("指令 /~resetfav 触发 user=%d", user_id)
    
    # 权限检查
    roles = load_roles_config()
    if user_id != roles.get("admin_qq"):
        return format_lang("reload.permission_denied")
    
    import modules.fav as fav_module
    fav_file = str(fav_module.FAV_FILE)
    if not os.path.exists(fav_file):
        return format_lang("resetfav.empty")
    
    success = reset_all_fav()
    if success:
        logger.info("管理员 %s(%d) 重置了全部好感度", sender_name, user_id)
        return format_lang("resetfav.success")
    else:
        return format_lang("error.command_error")


# ─── 图片指令 ──────────────────────────────────────────────

# Lolicon API（Pixiv 来源，免费无需 key）
# 坑1: API 端点带 Referer 直接 403，必须拆两套头
# 坑2: size 默认只返回 original，要 regular 必须手工拼接重复键 size=regular&size=original
# 坑3: API 的 r18 字段是"库分类"，r18=0 仍可能返回带 R-18 标签的图 → 需黑名单二次过滤
_LOLICON_API = "https://api.lolicon.app/setu/v2"
_LOLICON_API_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
_LOLICON_IMG_HEADERS = {"Referer": "https://www.pixiv.net/", "User-Agent": "Mozilla/5.0"}

_IMG_TEMP_DIR = Path(__file__).resolve().parent.parent / "data" / "img_temp"

# 最近发送过的 pid 缓存：Lolicon 带 tag 查询返回顺序固定，同 tag 会命中同一批结果，
# 用「多拉几张 + 随机选 + 跳过最近已发」保证重复 tag 也能拿到不同图
_IMG_SENT_PIDS: set[int] = set()
_IMG_SENT_PIDS_MAX = 200

# r18=0（全年龄）时的标签黑名单，与 lolicon_client.py 一致（大小写不敏感）
_R18_TAG_BLACKLIST = [
    "r-18", "r18", "r-18g", "r18g", format_lang("mod.m31"), format_lang("mod.m32"), format_lang("mod.m33"), format_lang("mod.m34"),
    "裸", "sex", "sexual", "hentai", "ero", "erotic", "porn", "nsfw",
    "bukkake", "paizuri", "anal", "bondage", "tentacle", "ahegao",
    "incest", "shotacon", "lolicon", "futanari", "furry", "beastiality",
    "guro", "ryona", "scat", "vore", "exhibitionism", "masturbation",
    "rape", "torture", "urine", "urethra", "prolapse", "fisting",
    "diaper", "omorashi", "peeing", "pissing", "enema", "necrophilia",
]


def _is_r18_tag(tag: str) -> bool:
    """判断单个标签是否命中 R18 黑名单（子串匹配，大小写不敏感）"""
    tl = tag.lower()
    return any(b in tl for b in _R18_TAG_BLACKLIST)


def _build_lolicon_url(r18: int, tags: list[str], num: int = 1) -> str:
    """构造 Lolicon 请求 URL。

    size/tag 均为数组参数，GET 需用重复键手工拼接（urlencode 对数组不可靠）。
    tag 数组之间是 AND 规则（最多 3 个）；单个 tag 内可用 | 分隔做 OR（最多 20 个），
    例：tag=萝莉|少女&tag=白丝|黑丝  →  (萝莉 OR 少女) AND (白丝 OR 黑丝)
    """
    qs = ["r18=%d" % r18, "num=%d" % num, "size=regular", "size=original"]
    for t in tags:
        if t:
            qs.append("tag=" + urllib.parse.quote(t))
    return _LOLICON_API + "?" + "&".join(qs)


async def _lolicon_fetch_and_send(
    r18: int,
    tags: list[str],
    group_id: int | None,
    user_id: int | None,
    is_group: bool,
    cmd_tag: str,
):
    """
    Lolicon API 拉图 → 下载到本地临时目录 → 通过 CQ 码发送。

    与天气/快递/更新日志卡片相同的发送方式：先下载到本地再用 `file:///` 路径，
    NapCat 会读取本地文件并上传到 QQ 服务器。下载失败则降级直发远程 URL。
    """
    _IMG_TEMP_DIR.mkdir(parents=True, exist_ok=True)
    image_url = ""  # 外层作用域保存，供 except 块 fallback 使用

    try:
        # Step 1: 请求 API（不带 Referer，否则 403）
        # 统一拉 5 张：一是黑名单过滤后有剩余，二是随机选图避免同 tag 永远同一张
        api_url = _build_lolicon_url(r18, tags, num=5)
        logger.info("[%s] 请求 Lolicon API: %s", cmd_tag, api_url)
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True,
                                     headers=_LOLICON_API_HEADERS) as client:
            resp = await client.get(api_url)
            resp.raise_for_status()
            items = (resp.json() or {}).get("data", [])
        if r18 == 0:
            # 双保险：库分类 r18 字段 + 标签黑名单（API r18 字段不等同作品本身标识）
            items = [it for it in items
                     if not it.get("r18") and not any(_is_r18_tag(t) for t in it.get("tags", []))]

        # 跳过最近已发过的 pid（去重），全被跳过则放宽不跳过
        fresh = [it for it in items if it.get("pid") not in _IMG_SENT_PIDS]
        if not fresh:
            fresh = items

        if not fresh:
            logger.warning("[%s] Lolicon 无结果 tags=%r", cmd_tag, tags)
            tip = format_lang("_lolicon_fetch_and_send.m1", tags=' '.join(tags)) if tags else format_lang("_lolicon_fetch_and_send.m2")
            if is_group and group_id:
                await send_group_msg(tip, group_id)
            elif not is_group and user_id:
                await send_private_msg(tip, user_id)
            return

        item = random.choice(fresh)  # 随机选一张，避免同 tag 固定第一张
        urls = item.get("urls", {})
        image_url = urls.get("regular") or urls.get("original") or ""
        if not image_url:
            logger.error("[%s] Lolicon 返回无 urls 字段: %s", cmd_tag, item)
            return

        # 记录已发 pid（下载前记录：即使下载失败也不再反复选中这张）
        _IMG_SENT_PIDS.add(item.get("pid"))
        if len(_IMG_SENT_PIDS) > _IMG_SENT_PIDS_MAX:
            _IMG_SENT_PIDS.clear()  # 超上限直接清空，简单可靠

        logger.info("[%s] 获取到图片: %s (pid=%s)", cmd_tag, image_url, item.get("pid"))

        # Step 2: 下载图片（必须带 Referer: https://www.pixiv.net/，走 i.pixiv.re 反代）
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True,
                                     headers=_LOLICON_IMG_HEADERS) as client:
            img_resp = await client.get(image_url)
            img_resp.raise_for_status()
            img_bytes = img_resp.content

        # 从 URL 或 Content-Type 推断扩展名
        ext = ".jpg"
        content_type = img_resp.headers.get("content-type", "")
        if "png" in content_type:
            ext = ".png"
        elif "gif" in content_type:
            ext = ".gif"
        elif "webp" in content_type:
            ext = ".webp"
        else:
            url_path = image_url.split("?")[0]
            if url_path.endswith(".png"):
                ext = ".png"
            elif url_path.endswith(".gif"):
                ext = ".gif"
            elif url_path.endswith(".webp"):
                ext = ".webp"

        import uuid
        local_filename = f"img_{cmd_tag}_{uuid.uuid4().hex[:8]}{ext}"
        local_path = _IMG_TEMP_DIR / local_filename
        local_path.write_bytes(img_bytes)
        logger.info("[%s] 图片已保存: %s (%d bytes)", cmd_tag, local_path.name, len(img_bytes))

        # Step 3: 构造 CQ 码并发送
        normalized = str(local_path).replace("\\", "/")
        cq_msg = f"[CQ:image,file=file:///{normalized}]"

        if is_group and group_id:
            await send_group_msg(cq_msg, group_id)
        elif not is_group and user_id:
            await send_private_msg(cq_msg, user_id)

        logger.info("[%s] 图片已发送", cmd_tag)

    except httpx.HTTPStatusError as e:
        logger.warning("[%s] Lolicon 请求失败 (HTTP %d): %s", cmd_tag, e.response.status_code, e)
        if image_url:
            await _send_image_url_fallback(image_url, group_id, user_id, is_group, cmd_tag)
    except Exception as e:
        logger.error("[%s] Lolicon 图片处理异常: %s", cmd_tag, e, exc_info=True)
        if image_url:
            await _send_image_url_fallback(image_url, group_id, user_id, is_group, cmd_tag)


async def _send_image_url_fallback(
    image_url: str,
    group_id: int | None,
    user_id: int | None,
    is_group: bool,
    tag: str = "img",
):
    """备用方案：直接用远程 URL 构造 CQ 码发送（部分 NapCat 版本支持远程 URL）"""
    try:
        logger.info("[%s] 尝试备用方案: 直接发送远程 URL", tag)
        cq_msg = f"[CQ:image,file={image_url}]"
        if is_group and group_id:
            await send_group_msg(cq_msg, group_id)
        elif not is_group and user_id:
            await send_private_msg(cq_msg, user_id)
        logger.info("[%s] 备用方案已发送", tag)
    except Exception as e2:
        logger.error("[%s] 备用方案也失败了: %s", tag, e2)
        # 最终兜底：纯文本提示
        fallback = format_lang("image.service_error")
        if is_group and group_id:
            await send_group_msg(fallback, group_id)
        elif not is_group and user_id:
            await send_private_msg(fallback, user_id)


async def cmd_img(args, user_id, group_id, sender_name, is_group, bot_qq):
    """随机二次元图片 /~img [标签...]，多标签为 AND 匹配，如 /~img 甘雨 原神"""
    tags = [a.strip() for a in args if a.strip()]
    logger.info("指令 /~img 触发 user=%d group=%d tags=%r", user_id, group_id or 0, tags)

    if tags and tags[0].lower() in ("help", "帮助", "?", "usage"):
        return (
            format_lang("cmd_img.m1")
        )

    async def _bg_send():
        await _lolicon_fetch_and_send(
            r18=0,
            tags=tags,
            group_id=group_id if is_group else None,
            user_id=user_id if not is_group else None,
            is_group=is_group,
            cmd_tag="img",
        )

    asyncio.create_task(_bg_send())
    return None  # 后台异步处理


async def cmd_img18(args, user_id, group_id, sender_name, is_group, bot_qq):
    """R18 图片 /~img18 [标签...]（仅私聊，群聊拒绝防封号）"""
    if is_group:
        return format_lang("cmd_img18.m1")

    tags = [a.strip() for a in args if a.strip()]
    logger.info("指令 /~img18 触发 user=%d tags=%r", user_id, tags)

    if tags and tags[0].lower() in ("help", "帮助", "?", "usage"):
        return (
            format_lang("cmd_img18.m2")
        )

    async def _bg_send():
        await _lolicon_fetch_and_send(
            r18=1,
            tags=tags,
            group_id=None,
            user_id=user_id,
            is_group=False,
            cmd_tag="img18",
        )

    asyncio.create_task(_bg_send())
    return None  # 后台异步处理

# ─── 撤回记录 ──────────────────────────────────────────────

async def cmd_recall(args, user_id, group_id, sender_name, is_group, bot_qq):
    """查看最近的群消息撤回记录 /~recall [数量]"""
    if not is_group:
        return format_lang("recall.group_only")

    count = 5
    if args and args[0].isdigit():
        count = min(int(args[0]), 20)

    logger.info("指令 /~recall 触发 user=%d group=%d count=%d", user_id, group_id, count)

    from modules.recall import get_recent_recalls

    records = get_recent_recalls(group_id, count)
    if not records:
        return format_lang("recall.empty")

    cfg = get_config()
    lines = [format_lang("cmd_recall.m1", records=len(records))]
    images_to_send = []

    for i, r in enumerate(records, 1):
        t = time.strftime("%H:%M:%S", time.localtime(r["time"]))
        mid = r.get("msg_id", "")  # 消息号：可按它在 msglog 中精确匹配（撤回前已录制时）
        uid = str(r["user_id"])
        name = cfg.get_display_name(uid, group_id=group_id)

        msg_type = r.get("type", "text")
        if msg_type == "图片":
            # 有本地缓存的图片：直接发送原图
            img_path = r.get("img_path", "")
            if img_path:
                images_to_send.append((i, img_path, name, r))
                content_display = format_lang("cmd_recall.m2")
            else:
                content_display = format_lang("cmd_recall.m3")
        elif msg_type == "文件":
            content_display = format_lang("cmd_recall.m4")
        else:
            content_display = r["content"][:60] + ("..." if len(r["content"]) > 60 else "")
            if not content_display:
                content_display = format_lang("cmd_recall.m5")

        if r.get("recalled_by") == r["user_id"]:
            if r["user_id"] == 0:
                op_id = str(r.get("recalled_by", ""))
                op_name = cfg.get_display_name(op_id, group_id=group_id)
                lines.append(format_lang("cmd_recall.m6", i=i, t=t, mid=mid, op_name=op_name, content_display=content_display))
            else:
                lines.append(format_lang("cmd_recall.m7", i=i, t=t, mid=mid, name=name, content_display=content_display))
        else:
            op_id = str(r.get("recalled_by", ""))
            op_name = cfg.get_display_name(op_id, group_id=group_id)
            target_name = name if r["user_id"] != 0 else format_lang("cmd_recall.m8")
            lines.append(format_lang("cmd_recall.m9", i=i, t=t, mid=mid, op_name=op_name, target_name=target_name, content_display=content_display))

    # 先发送图片
    from services.sender import send_group_msg
    for idx, img_path, name, r in images_to_send:
        try:
            await send_group_msg(f"[CQ:image,file=file://{img_path}]", group_id)
        except Exception as e:
            logger.warning("发送撤回图片失败: %s", e)
            # 图片发送失败，更新文字行
            fallback = f"[CQ:image,file=file://{img_path}]"
            # 无法修改已构建的行，记录日志即可

    return "\n".join(lines)


# ─── 提醒指令 ──────────────────────────────────────────────

async def cmd_remind(args, user_id, group_id, sender_name, is_group, bot_qq):
    """设置定时提醒 /~remind <时间> <内容>"""
    if not args or len(args) < 2:
        return format_lang("remind.usage")

    time_part = ""
    content_part = ""

    for i in range(len(args) - 1, 0, -1):
        candidate_time = " ".join(args[:i])
        from modules.remind import _parse_time
        ts, err = _parse_time(candidate_time)
        if err is None:
            time_part = candidate_time
            content_part = " ".join(args[i:])
            break

    if not time_part:
        return format_lang("remind.invalid_time")

    from modules.remind import _parse_time, add_reminder

    ts, err = _parse_time(time_part)
    if err:
        return err

    record = add_reminder(
        chat_id=group_id if is_group else user_id,
        user_id=user_id,
        target_time=ts,
        content=content_part,
        is_group=is_group,
    )

    from datetime import datetime
    target_dt = datetime.fromtimestamp(ts)
    time_display = target_dt.strftime("%m月%d日 %H:%M")
    logger.info("指令 /~remind 触发: user=%d time=%s content='%s'",
               user_id, time_display, content_part)
    return format_lang("cmd_remind.m1", time_display=time_display, content_part=content_part[:40])


# ─── 抽签指令 ──────────────────────────────────────────────

async def cmd_chou(args, user_id, group_id, sender_name, is_group, bot_qq):
    """随机抽取 /~抽 选项A 选项B 选项C 或 /~抽 选项A,选项B,选项C"""
    if not args:
        return format_lang("luck.prompt")

    full = " ".join(args)
    if "," in full:
        options = [o.strip() for o in full.split(",") if o.strip()]
    else:
        options = args

    if len(options) < 2:
        return format_lang("luck.min_options")

    pick = random.choice(options)
    logger.info("指令 /~抽 触发: user=%d options=%s → '%s'", user_id, options, pick)

    reactions = [
        format_lang("cmd_chou.m1", pick=pick),
        format_lang("cmd_chou.m2", pick=pick),
        format_lang("cmd_chou.m3", pick=pick),
        format_lang("cmd_chou.m4", pick=pick),
        format_lang("cmd_chou.m5", pick=pick),
    ]
    return random.choice(reactions)


# ─── 群统计指令 ──────────────────────────────────────────────

async def cmd_stats(args, user_id, group_id, sender_name, is_group, bot_qq):
    """查看群聊今日统计 /~stats [昨天|send]"""
    if not is_group:
        return format_lang("stats.group_only")

    # 手动发送日报卡片
    if args and args[0] == "send":
        from modules.stats import get_yesterday_stats, generate_daily_report_image
        from datetime import datetime, timedelta
        yesterday = datetime.now() - timedelta(days=1)
        stats = get_yesterday_stats(group_id)
        if not stats or stats.get("_meta", {}).get("total", 0) == 0:
            return format_lang("cmd_stats.m1")
        card = await generate_daily_report_image(stats, group_id, yesterday.strftime("%Y.%m.%d"), format_lang("cmd_stats.group_label", group_id=group_id))
        if card:
            cq = f"[CQ:image,file=file:///{card.replace(chr(92), '/')}]"
            await (send_group_msg(cq, group_id) if is_group else send_private_msg(cq, user_id))
            return None
        return format_lang("cmd_stats.m2")

    show_yesterday = args and args[0] == "昨天"

    cfg = get_config()

    if show_yesterday:
        from modules.stats import get_yesterday_stats, format_stats_report
        stats = get_yesterday_stats(group_id)
        report = format_stats_report(stats, cfg, group_id, title=format_lang("cmd_stats.m3")) if stats else format_lang("cmd_stats.m4")
    else:
        from modules.stats import get_today_stats, format_stats_report
        stats = get_today_stats(group_id)
        report = format_stats_report(stats, cfg, group_id, title=format_lang("cmd_stats.m5"))

    logger.info("指令 /~stats 触发: user=%d group=%d yesterday=%s", user_id, group_id, show_yesterday)
    return report


async def cmd_unstats(args, user_id, group_id, sender_name, is_group, bot_qq):
    """暂停群聊统计 /~unstats"""
    if not is_group:
        return format_lang("cmd_unstats.m1")
    
    from modules.stats import set_stats_state, is_stats_enabled
    
    if not is_stats_enabled(group_id):
        return format_lang("cmd_unstats.m2")
    
    set_stats_state(group_id, enabled=False)
    logger.info("指令 /~unstats: 群=%d 统计已暂停 user=%d", group_id, user_id)
    return format_lang("cmd_unstats.m3")


async def cmd_setstats(args, user_id, group_id, sender_name, is_group, bot_qq):
    """恢复群聊统计 /~setstats"""
    if not is_group:
        return format_lang("cmd_setstats.m1")
    
    from modules.stats import set_stats_state, is_stats_enabled
    
    if is_stats_enabled(group_id):
        return format_lang("cmd_setstats.m2")
    
    set_stats_state(group_id, enabled=True)
    logger.info("指令 /~setstats: 群=%d 统计已恢复 user=%d", group_id, user_id)
    return format_lang("cmd_setstats.m3")


async def cmd_leave(args, user_id, group_id, sender_name, is_group, bot_qq):
    """退群指令 /~leave [群号] — 收集数据 → 发送 → 重置好感度 → 退群（仅管理员）"""
    cfg = get_config()
    if not cfg.is_admin(user_id, group_id):
        return format_lang("cmd_leave.m1")
    
    # 解析目标群号：有参数用参数，没参数用当前群
    if args and args[0].isdigit():
        target_gid = int(args[0])
    elif is_group:
        target_gid = group_id
    else:
        return format_lang("cmd_leave.m2")
    
    from services.sender import send_group_msg, get_ws_manager
    from modules.leave import collect_group_data
    from core.logger import get_logger
    
    _log = get_logger("leave_cmd")
    
    # 1. 收集数据
    ok = await send_group_msg(format_lang("cmd_leave.m3"), target_gid)
    _log.info("发送进度提示 → 群%d: %s", target_gid, "成功" if ok else "失败")
    data_parts = await collect_group_data(target_gid)
    
    if data_parts:
        for title, content in data_parts:
            msg = f"【{title}】\n{content}"
            if len(msg) > 2000:
                msg = msg[:1990] + format_lang("cmd_leave.m4")
            ok = await send_group_msg(msg, target_gid)
            _log.info("发送 %s → 群%d: %s (%d字)", title, target_gid, "成功" if ok else "失败", len(msg))
    else:
        await send_group_msg(format_lang("cmd_leave.m5"), target_gid)
    
    # 2. 重置好感度
    import json
    from pathlib import Path
    fav_path = Path(__file__).resolve().parent.parent / "data" / "fav.json"
    fav_count = 0
    if fav_path.exists():
        try:
            data = json.loads(fav_path.read_text(encoding="utf-8"))
            prefix = f"g{target_gid}:"
            to_del = [k for k in data if k.startswith(prefix)]
            for k in to_del:
                del data[k]
            fav_count = len(to_del)
            if fav_count:
                fav_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
    
    # 3. 清理内存状态
    try:
        from core.context_manager import get_context_mgr
        get_context_mgr().clear_context(target_gid)
    except Exception:
        pass
    try:
        from modules.preset import clear_preset
        clear_preset(target_gid)
    except Exception:
        pass
    
    await send_group_msg(format_lang("cmd_leave.m6", fav_count=fav_count), target_gid)
    
    # 4. 退群
    try:
        mgr = get_ws_manager()
        await mgr.call_api("set_group_leave", {"group_id": target_gid})
        _log.info("退群成功: 群=%d user=%d", target_gid, user_id)
    except Exception as e:
        _log.error("退群API调用失败: %s", e)
        return format_lang("cmd_leave.m7", e=e)
    
    return None  # 不发返回值，_handle_command_route 会跳过


# ─── Minecraft 日志分析（零上下文）─────────────────────────────

async def cmd_analyze(args, user_id, group_id, sender_name, is_group, bot_qq):
    """
    零上下文分析 /~analyze <日志内容>
    直接发给 LLM，不注入任何聊天记录、记忆、架构上下文。
    """
    if not args:
        return format_lang("cmd_analyze.m1")
    
    content = " ".join(args)
    from services.llm import call_llm_raw
    prompt = (
        "你是一个 Minecraft 日志分析助手。请分析以下日志，找出问题、错误、警告，并给出修复建议。"
        "只基于当前提供的日志内容进行分析，不要引入任何外部知识或假设。\n\n"
        f"日志内容:\n{content}"
    )
    reply = await call_llm_raw(prompt)
    if not reply:
        return format_lang("analyze.fail")
    return reply


# ─── 提示词注入管理 ──────────────────────────────────────────

async def cmd_preset(args, user_id, group_id, sender_name, is_group, bot_qq):
    """查看或清除系统提示词注入 /~preset [show|clear]"""
    from modules.preset import get_preset, clear_preset

    cfg = get_config()
    if not cfg.is_admin(user_id, group_id):
        return format_lang("preset.permission")

    action = args[0].lower() if args else "show"
    chat_id = group_id if is_group else user_id

    if action == "clear":
        cleared = clear_preset(chat_id)
        logger.info("指令 /~preset clear 用户=%d cleared=%s", user_id, cleared)
        return format_lang("preset.cleared") if cleared else format_lang("preset.no_preset")

    preset = get_preset(chat_id)
    if preset:
        return format_lang("cmd_preset.m1", preset=len(preset), preset2=preset[:500])
    else:
        return format_lang("preset.empty")


# ─── 五子棋 ─────────────────────────────────────────────────

async def _wzq_render_and_send(chat_id: int, is_group: bool, group_id: int, user_id: int, cfg):
    """背景生成棋盘卡片并发送"""
    from modules import wzq as g
    img = await g.render_board(chat_id, cfg)
    if img:
        cq = f"[CQ:image,file=file:///{img.replace(chr(92), '/')}]"
        await (send_group_msg(cq, group_id) if is_group else send_private_msg(cq, user_id))


_GAME_CN = {"go": format_lang("mod.m35"), "wzq": format_lang("mod.m36"), "xq": format_lang("mod.m37")}


def _room_links(kind: str, chat_id: int, players) -> tuple:
    """取（或新建）房间号，并生成观战链接与各人对局链接。

    返回 (房间号, 观战链接, {uid: 对局链接})；服务不可用时返回空串/空字典。
    """
    try:
        from services.game_web import ensure_room, play_link, spectate_link
        code = ensure_room(kind, chat_id, is_group=chat_id in get_config().group_list)
        return (code, spectate_link(code),
                {int(u): play_link(code, int(u)) for u in players})
    except Exception as e:
        logger.warning("生成棋局链接失败: %s", e)
        return "", "", {}


async def _announce_room(kind: str, chat_id: int, players: list, is_group: bool,
                         group_id: int = 0, me_uid: int = 0, mode_text: str = "") -> str:
    """开赛分发。

    - 群里：返回的文本带**观战链接**（由调用方发到群里）
    - 双方：各自的对局链接**私聊**发过去
    players: [(uid, 昵称, 执子说明)]
    """
    from services.sender import send_private_msg
    code, spec, links = _room_links(kind, chat_id, [p[0] for p in players])
    if not code:
        return ""
    title = format_lang("_announce_room.m1", kind=_GAME_CN.get(kind, '棋局'), code=code)
    roster = " vs ".join(str(p[1]) for p in players)
    if roster:
        title += f"｜{roster}"
    if mode_text:
        title += f"｜{mode_text}"

    for uid, _name, role in players:
        link = links.get(int(uid))
        if not link:
            continue
        if not is_group and int(uid) == int(me_uid):
            continue                     # 私聊开局：发起人的链接直接放回复里
        try:
            await send_private_msg(
                format_lang("_announce_room.m2", title=title, role=role, link=link), int(uid))
        except Exception as e:
            logger.warning("对局链接私聊失败 uid=%s: %s", uid, e)

    if is_group:
        return format_lang("_announce_room.m3", title=title, spec=spec)
    own = links.get(int(me_uid), "")
    return format_lang("_announce_room.m4", title=title, own=own) if own else title


def _link_text(kind: str, chat_id: int, uid: int, has_game: bool) -> str:
    """`link` 子命令回复：房间号 + 自己的对局链接 + 观战链接"""
    if not has_game:
        return format_lang("_link_text.m1")
    try:
        from services.game_web import ensure_room, play_link, spectate_link
    except Exception as e:
        return format_lang("_link_text.m2", e=e)
    code = ensure_room(kind, chat_id, is_group=chat_id in get_config().group_list)
    return (format_lang("_link_text.m3", code=code, uid=play_link(code, uid), code2=spectate_link(code)))


def _parse_opponent(text: str, group_id: int, cfg) -> int | None:
    """从 @某人 / 昵称 / QQ 号 里解析对手的 QQ"""
    s = str(text or "").strip()
    if not s:
        return None
    m = re.search(r"(\d{4,12})", s)
    if m:
        return int(m.group(1))
    name = s.lstrip("@").strip()
    raw = name
    # 去掉可能附带的其它词（如"/~xq duel 某某 困难"）
    name = name.split()[0] if name else ""
    if not name:
        return None
    per = (cfg.group_nicknames or {}).get(str(group_id), {}) if group_id else {}
    for qq, nick in (per or {}).items():
        if nick == name or nick == raw:
            return int(qq)
    for qq, nick in (cfg.qq_name_map or {}).items():
        if nick == name or nick == raw:
            return int(qq)
    return None


async def cmd_watch(args, user_id, group_id, sender_name, is_group, bot_qq):
    """/~观战 <房间号> —— 取某个棋局房间的观战链接"""
    if not args:
        return (format_lang("cmd_watch.m1"))
    code = str(args[0]).strip().upper()
    try:
        from services.game_web import KIND_LABEL, room_of, spectate_link
    except Exception as e:
        return format_lang("cmd_watch.m2", e=e)
    room = room_of(code)
    if not room:
        return format_lang("cmd_watch.m3", code=code)
    kind_cn = KIND_LABEL.get(room.get("kind"), format_lang("common.chess_default"))
    return format_lang("cmd_watch.m4", kind_cn=kind_cn, code=code, code2=spectate_link(code))


async def cmd_wzq(args, user_id, group_id, sender_name, is_group, bot_qq):
    """
    五子棋 /~wzq <操作>
      duel @某人     发起挑战
      ai <难度>     人机对战
      accept         接受挑战
      cancel/unduel  取消挑战
      <坐标>         落子 (如 H8, 8,8)
      board          查看棋盘
      surrender      认输
      undo           申请悔棋
      status         查看状态
      admin clear    强制清除
    """
    chat_id = group_id if is_group else user_id
    from modules import wzq as g

    if not args:
        game = g.get_game(chat_id)
        if game and game.status == "playing":
            args = ["board"]
        else:
            return (
                format_lang("cmd_wzq.m1")
            )

    action = args[0].lower()
    cfg = get_config()

    # ── 人机对战 ──
    if action in ("ai", "人机"):
        if len(args) < 2:
            return format_lang("cmd_wzq.m2")
        diff = args[1]
        diff_map = {"新手": format_lang("cmd_wzq.m3"), "普通": format_lang("cmd_wzq.m4"), "困难": format_lang("cmd_wzq.m5"), "专家": format_lang("cmd_wzq.m6"), "easy": format_lang("cmd_wzq.m7"), "normal": format_lang("cmd_wzq.m8"), "hard": format_lang("cmd_wzq.m9"), "expert": format_lang("cmd_wzq.m10")}
        diff = diff_map.get(diff, diff)
        if diff not in ("新手", "普通", "困难", "专家"):
            return format_lang("cmd_wzq.m11", diff=diff)
        nofb = args[-1].lower() in ("nofb", "无禁手", "noforbidden")
        result = g.create_duel_ai(chat_id, user_id, diff, forbidden=not nofb)
        if result.startswith("started_ai"):
            diff_name = result.split(":")[1]
            async def _bg_ai_start():
                img = await g.render_board(chat_id, cfg)
                if img:
                    cq = f"[CQ:image,file=file:///{img.replace(chr(92), '/')}]"
                    await (send_group_msg(cq, group_id) if is_group else send_private_msg(cq, user_id))
            asyncio.create_task(_bg_ai_start())
            base = format_lang("wzq.start", diff=diff_name)
            uname = cfg.get_display_name(str(user_id), group_id=group_id if is_group else 0)
            ann = await _announce_room("wzq", chat_id, [(user_id, uname, "黑")], is_group,
                                       group_id, me_uid=user_id, mode_text=format_lang("cmd_wzq.m12", diff_name=diff_name))
            return f"{base}\n{ann}" if ann else base
        return result

    # ── 发起挑战 ──
    if action == "duel":
        if len(args) < 2:
            return format_lang("cmd_wzq.m13")

        # 从参数中提取 QQ 号（支持 @昵称 反向查映射）
        opponent = " ".join(args[1:])
        white_id = _parse_opponent(opponent, group_id if is_group else 0, cfg)
        if not white_id:
            return format_lang("wzq.user_not_found", name=opponent.lstrip("@"))

        result = g.create_duel(chat_id, user_id, white_id,
                                forbidden=args[-1].lower() not in ("nofb", "无禁手", "noforbidden"))
        if result.startswith("waiting"):
            async def _bg_duel():
                import asyncio as _a
                await _a.sleep(0.5)
                img = await g.render_board(chat_id, cfg)
                if img:
                    cq = f"[CQ:image,file=file:///{img.replace(chr(92), '/')}]"
                    await (send_group_msg(cq, group_id) if is_group else send_private_msg(cq, user_id))
            asyncio.create_task(_bg_duel())
            return (format_lang("cmd_wzq.m14", white_id=white_id))
        return result

    # ── 接受 ──
    if action == "accept":
        result = g.accept_duel(chat_id, user_id)
        if result == "started":
            game = g.get_game(chat_id)
            gid = group_id if is_group else 0
            name_b = cfg.get_display_name(str(game.black), group_id=gid)
            name_w = cfg.get_display_name(str(game.white), group_id=gid)
            ann = await _announce_room("wzq", chat_id,
                                       [(game.black, name_b, "黑"), (game.white, name_w, "白")],
                                       is_group, group_id, me_uid=user_id, mode_text=format_lang("cmd_wzq.m15"))

            async def _bg():
                img = await g.render_board(chat_id, cfg)
                if img:
                    cq = f"[CQ:image,file=file:///{img.replace(chr(92), '/')}]"
                    await (send_group_msg(cq, group_id) if is_group else send_private_msg(cq, user_id))
            asyncio.create_task(_bg())
            head = format_lang("cmd_wzq.m16")
            return f"{head}\n{ann}" if ann else head
        return result

    # ── 拒绝 ──
    if action == "decline":
        return g.decline_duel(chat_id, user_id)

    # ── 取消 ──
    if action in ("cancel", "unduel"):
        return g.cancel_duel(chat_id, user_id)

    # ★ admin 强制清除本群所有棋局
    if action == "admin":
        if not cfg.is_admin(user_id, group_id):
            return format_lang("cmd_wzq.m17")
        sub = args[1] if len(args) > 1 else ""
        if sub == "clear":
            game = g.get_game(chat_id)
            if not game:
                return format_lang("cmd_wzq.m18")
            g.force_end(chat_id)
            return format_lang("cmd_wzq.m19", status=game.status)
        return format_lang("cmd_wzq.m20")

    # ── 房间链接 ──
    if action in ("link", "链接", "网页", "房间"):
        game = g.get_game(chat_id)
        if not game:
            return format_lang("cmd_wzq.m21")
        side = ""
        if user_id == game.black:
            side = format_lang("cmd_wzq.m22")
        elif user_id == game.white:
            side = format_lang("cmd_wzq.m23")
        return _link_text("wzq", chat_id, user_id, True) + f"\n{side}" if side else \
            _link_text("wzq", chat_id, user_id, True)

    # ── 落子 ──
    coord = g.parse_coord(action if len(args) == 1 else " ".join(args))
    if coord:
        row, col = coord
        ok, msg = g.make_move(chat_id, user_id, row, col)
        if ok:
            # ★ 玩家落子：先渲染发送棋盘，再让 AI 走（避免图片顺序颠倒）
            img = await g.render_board(chat_id, cfg)
            if img:
                cq = f"[CQ:image,file=file:///{img.replace(chr(92), '/')}]"
                await (send_group_msg(cq, group_id) if is_group else send_private_msg(cq, user_id))

            if msg == "win":
                game = g.get_game(chat_id)
                winner_name = cfg.get_display_name(str(game.white if game.winner == 2 else game.black), group_id=group_id if is_group else 0)
                return format_lang("cmd_wzq.m24", winner_name=winner_name)
            if msg == "draw":
                return format_lang("cmd_wzq.m25")
            if msg.startswith("forbidden:"):
                reason = msg.split(":", 1)[1]
                game = g.get_game(chat_id)
                winner = cfg.get_display_name(str(game.white), group_id=group_id if is_group else 0)
                return format_lang("cmd_wzq.m26", reason=reason, winner=winner)

            # AI 自动走棋
            game = g.get_game(chat_id)
            if game and game.ai_difficulty and game.turn == 2 and game.status == "playing":
                async def _ai_turn():
                    ai_ok, ai_msg = await g.ai_move_async(chat_id)
                    if ai_ok:
                        img = await g.render_board(chat_id, cfg)
                        if img:
                            cq = f"[CQ:image,file=file:///{img.replace(chr(92), '/')}]"
                            await (send_group_msg(cq, group_id) if is_group else send_private_msg(cq, user_id))
                        if ai_msg == "win":
                            ai_game = g.get_game(chat_id)
                            if ai_game.winner == 2:
                                await (send_group_msg(format_lang("_ai_turn.m1"), group_id) if is_group else send_private_msg(format_lang("_ai_turn.m2"), user_id))
                            elif ai_game.winner == 1:
                                await (send_group_msg(format_lang("_ai_turn.m3"), group_id) if is_group else send_private_msg(format_lang("_ai_turn.m4"), user_id))
                        elif ai_msg.startswith("forbidden:"):
                            await (send_group_msg(format_lang("_ai_turn.m5"), group_id) if is_group else send_private_msg(format_lang("_ai_turn.m6"), user_id))
                    else:
                        logger.warning("AI turn failed: %s", ai_msg)
                asyncio.create_task(_ai_turn())
            return None
        return msg

    # ── 棋盘 ──
    if action == "board":
        async def _bg():
            img = await g.render_board(chat_id, cfg)
            if img:
                cq = f"[CQ:image,file=file:///{img.replace(chr(92), '/')}]"
                await (send_group_msg(cq, group_id) if is_group else send_private_msg(cq, user_id))
        asyncio.create_task(_bg())
        return None

    # ── 认输 ──
    if action == "surrender":
        ok, msg = g.surrender(chat_id, user_id)
        if ok:
            loser = cfg.get_display_name(str(user_id), group_id=group_id if is_group else 0)
            return format_lang("cmd_wzq.m27", loser=loser)
        return msg

    # ── 悔棋 ──
    if action == "undo":
        game = g.get_game(chat_id)
        # ★ 如果对方已有悔棋申请，当前用户发送 undo = 确认悔棋
        if game and game.undo_request is not None and game.undo_request != user_id:
            ok, msg_res = g.confirm_undo(chat_id, user_id)
            if ok:
                async def _bg_undo():
                    img = await g.render_board(chat_id, cfg)
                    if img:
                        cq = f"[CQ:image,file=file:///{img.replace(chr(92), '/')}]"
                        await (send_group_msg(cq, group_id) if is_group else send_private_msg(cq, user_id))
                asyncio.create_task(_bg_undo())
                return msg_res
            return msg_res

        result = g.request_undo(chat_id, user_id)
        if result == "undo_request":
            opponent = game.white if user_id == game.black else game.black
            return format_lang("cmd_wzq.m28", opponent=opponent)
        return result

    # ── 状态 ──
    if action == "status":
        game = g.get_game(chat_id)
        if not game:
            return format_lang("wzq.no_game")
        bn = cfg.get_display_name(str(game.black), group_id=group_id if is_group else 0)
        wn = cfg.get_display_name(str(game.white), group_id=group_id if is_group else 0)
        return format_lang("cmd_wzq.m29", bn=bn, wn=wn, move_count=game.move_count, status=game.status)

    # ── 历史记录 ──
    if action == "history":
        # 参数: [数量] [board] — board 渲染指定编号的棋盘
        count = 5; show_board_idx = 0
        for a in args[1:]:
            if a.lower() in ("board", "棋盘", "b"):
                if show_board_idx == 0:
                    show_board_idx = 1  # 默认第1局
            elif a.isdigit():
                n = min(int(a), 20)
                if show_board_idx == 0:
                    count = n
                else:
                    show_board_idx = n  # 指定第N局

        records = g.get_history(chat_id, max(count, show_board_idx))
        if not records:
            return format_lang("wzq.no_records")

        # 渲染特定对局的棋盘
        if show_board_idx > 0:
            idx = show_board_idx - 1
            if idx >= len(records):
                return format_lang("wzq.only_n_records", count=len(records))
            async def _bg_hist():
                img = await g.render_history_board(records[idx], cfg)
                if img:
                    cq = f"[CQ:image,file=file:///{img.replace(chr(92), '/')}]"
                    await (send_group_msg(cq, group_id) if is_group else send_private_msg(cq, user_id))
            asyncio.create_task(_bg_hist())
            return None

        # 文字列表
        lines = [format_lang("cmd_wzq.m30", records=min(count, len(records)))]
        lines.append(format_lang("cmd_wzq.m31"))
        for i, r in enumerate(records[:count], 1):
            bn = cfg.get_display_name(str(r["black"]), group_id=group_id if is_group else 0)
            wn = cfg.get_display_name(str(r["white"]), group_id=group_id if is_group else 0)
            if r["winner"] == 1:
                result = format_lang("cmd_wzq.m32", bn=bn)
            elif r["winner"] == 2:
                result = format_lang("cmd_wzq.m33", wn=wn)
            else:
                result = format_lang("cmd_wzq.m34")
            lines.append(format_lang("cmd_wzq.m35", i=i, time=r['time'], bn=bn, wn=wn, result=result, moves=r['moves']))
        return "\n".join(lines)

    # ── 测试渲染 ──
    if action == "test":
        async def _bg_test():
            img = await g.render_test_board(cfg)
            if img:
                cq = f"[CQ:image,file=file:///{img.replace(chr(92), '/')}]"
                await (send_group_msg(cq, group_id) if is_group else send_private_msg(cq, user_id))
        asyncio.create_task(_bg_test())
        return None

    return format_lang("cmd_wzq.m36", action=action)


# ─── 翻译 ───────────────────────────────────────────────────

async def cmd_translate(args, user_id, group_id, sender_name, is_group, bot_qq):
    """
    翻译 /~tr <目标语言> <文本>
      /~tr en 你好
      /~tr 中文 hello
      /~tr jp こんにちは
    """
    if len(args) < 2:
        return format_lang("cmd_translate.m1")

    # 语言别名映射
    LANG_MAP = {
        "en": "English", "英文": "English", "英语": "English", "english": "English",
        "zh": "Chinese", "中文": "Chinese", "汉语": "Chinese", "chinese": "Chinese",
        "jp": "Japanese", "ja": "Japanese", "日文": "Japanese", "日语": "Japanese", "japanese": "Japanese",
        "kr": "Korean", "ko": "Korean", "韩文": "Korean", "韩语": "Korean", "korean": "Korean",
        "fr": "French", "法文": "French", "法语": "French", "french": "French",
        "de": "German", "德文": "German", "德语": "German", "german": "German",
    }
    target = args[0].lower()
    lang = LANG_MAP.get(target)
    if not lang:
        return format_lang("cmd_translate.m2", target=target)

    text = " ".join(args[1:])

    logger.info("翻译: %s → %s, text='%s'", text, lang, text)

    # 用廉价模型做翻译
    from services.llm import call_llm
    try:
        result = await call_llm(
            model_cfg=get_config().judge_model,
            messages=[{
                "role": "user",
                "content": f"Translate the following text to {lang}. Output ONLY the translation, no explanation, no quotation marks.\n\n{text}",
            }],
            max_tokens=1200,
            temperature=0.1,
        )
        translation = result.strip().strip('"').strip("'").strip()
        return f"[{lang}] {translation}"
    except Exception as e:
        logger.error("翻译失败: %s", e)
        return format_lang("translate.fail")


# ─── 围棋 ────────────────────────────────────────────────────

async def cmd_go(args, user_id, group_id, sender_name, is_group, bot_qq):
    """围棋 9/13/19 路 /~go [start [难度] [尺寸]|坐标|pass|board|resign|link]（群聊/私聊均可用）"""
    from modules import go_game as G
    from services.sender import send_group_msg, send_private_msg

    cfg = get_config()
    chat_id = group_id if is_group else user_id

    async def _send(msg: str):
        await (send_group_msg(msg, group_id) if is_group else send_private_msg(msg, user_id))

    async def _send_board():
        img = await G.render_board(chat_id)
        if img:
            await _send(f"[CQ:image,file=file:///{img.replace(chr(92), '/')}]")
        return img

    if not args:
        return (
            format_lang("cmd_go.m1")
        )

    action = args[0].lower()

    if action in ("start", "开始", "开局"):
        diff_raw = ""
        for tok in args[1:]:
            if not diff_raw and G.resolve_difficulty(tok):
                diff_raw = tok
            else:
                opts = " / ".join(v["label"] for v in G.DIFFICULTIES.values())
                return format_lang("cmd_go.m2", tok=tok, opts=opts)
        r = G.start_game(user_id, chat_id, diff_raw or G.DEFAULT_DIFFICULTY)
        if not r.startswith("ok"):
            return r
        label = r.split(":", 1)[1] if ":" in r else format_lang("cmd_go.m3")
        gid = group_id if is_group else 0
        uname = cfg.get_display_name(str(user_id), group_id=gid)
        ann = await _announce_room("go", chat_id, [(user_id, uname, "黑")], is_group,
                                   group_id, me_uid=user_id, mode_text=format_lang("cmd_go.m4", label=label))
        try:
            img = await G.render_board(chat_id)
            cq = f"[CQ:image,file=file:///{img.replace(chr(92), '/')}]" if img else ""
            await _send(format_lang("cmd_go.m5", BOARD_SIZE=G.BOARD_SIZE, BOARD_SIZE2=G.BOARD_SIZE, label=label, ann=ann, cq=cq))
            return None
        except Exception as e:
            return format_lang("cmd_go.m6", e=e)

    # ── 邀请群友对弈（双人） ──
    if action in ("duel", "对战", "挑战"):
        if len(args) < 2:
            return format_lang("cmd_go.m7")
        opp = _parse_opponent(args[1], group_id if is_group else 0, cfg)
        if not opp:
            return format_lang("cmd_go.m8", args=args[1])
        if opp == user_id:
            return format_lang("cmd_go.m9")
        diff_raw = ""
        for tok in args[2:]:
            if not diff_raw and G.resolve_difficulty(tok):
                diff_raw = tok
            else:
                return format_lang("cmd_go.m10", tok=tok)
        r = G.start_game(user_id, chat_id, diff_raw or G.DEFAULT_DIFFICULTY,
                         opponent_id=opp)
        if not r.startswith("ok"):
            return r
        gid = group_id if is_group else 0
        n1 = cfg.get_display_name(str(user_id), group_id=gid)
        n2 = cfg.get_display_name(str(opp), group_id=gid)
        ann = await _announce_room("go", chat_id,
                                   [(user_id, n1, "黑"), (opp, n2, "白")], is_group,
                                   group_id, me_uid=user_id, mode_text=format_lang("cmd_go.m11"))
        try:
            img = await G.render_board(chat_id)
            cq = f"[CQ:image,file=file:///{img.replace(chr(92), '/')}]" if img else ""
            await _send(format_lang("cmd_go.m12", BOARD_SIZE=G.BOARD_SIZE, BOARD_SIZE2=G.BOARD_SIZE, n1=n1, ann=ann, cq=cq))
            return None
        except Exception as e:
            return format_lang("cmd_go.m13", e=e)

    if action in ("link", "链接", "网页", "房间"):
        return _link_text("go", chat_id, user_id, G.get_game(chat_id) is not None)

    if action in ("board", "棋盘", "查看"):
        game = G.get_game(chat_id)
        if not game:
            return format_lang("cmd_go.m14")
        await _send_board()
        return format_lang("cmd_go.m15", move_count=game.get('move_count', 0))

    if action in ("pass", "停一手", "过"):
        ok, msg, _ = G.do_pass(user_id, chat_id)
        if not ok:
            return msg
        game = G.get_game(chat_id)
        if game and game.get("status") == "finished":
            tail = G.end_game(chat_id)
            await _send_board()
            return f"{msg}\n{tail}"
        await _send_board()
        return msg

    if action in ("resign", "认输"):
        return G.resign_game(user_id, chat_id)

    # 落子
    ok, msg, need_render = G.make_move(user_id, chat_id, " ".join(args))
    if need_render:
        await _send_board()
    return msg


# ─── 中国象棋 ────────────────────────────────────────────────

async def cmd_xq(args, user_id, group_id, sender_name, is_group, bot_qq):
    """中国象棋 /~xq [start [难度]|走法|board|resign|history]（群聊/私聊均可用）"""
    # 注：原代码 import 的 _build_svg / INIT_BOARD 两个名字在模块里并不存在
    # （实际是 _board_to_svg / build_initial_svg）—— 这是 /~xq 一直 ImportError 的根因
    from modules.chinese_chess import (
        start_game, make_move, resign_game, show_board, show_history, get_game,
        build_initial_svg, _svg_to_png, _ROOT, wait_render,
        resolve_difficulty, DEFAULT_DIFFICULTY, DIFFICULTIES,
    )
    from services.sender import send_group_msg, send_private_msg

    cfg = get_config()

    # 私聊也能玩：对局按 chat_id 隔离（私聊时用 user_id）
    chat_id = group_id if is_group else user_id

    async def _send(msg: str):
        await (send_group_msg(msg, group_id) if is_group else send_private_msg(msg, user_id))

    if not args:
        return (
            format_lang("cmd_xq.m1")
        )

    action = args[0].lower()

    if action == "start":
        diff_raw = args[1] if len(args) > 1 else ""
        if diff_raw and not resolve_difficulty(diff_raw):
            opts = " / ".join(v["label"] for v in DIFFICULTIES.values())
            return format_lang("cmd_xq.m2", diff_raw=diff_raw, opts=opts)
        result = start_game(user_id, chat_id, diff_raw or DEFAULT_DIFFICULTY)
        if not result.startswith("ok"):
            return result
        label = result.split(":", 1)[1] if ":" in result else format_lang("cmd_xq.m3")
        gid = group_id if is_group else 0
        uname = cfg.get_display_name(str(user_id), group_id=gid)
        ann = await _announce_room("xq", chat_id, [(user_id, uname, "红")], is_group,
                                   group_id, me_uid=user_id, mode_text=format_lang("cmd_xq.m4", label=label))
        try:
            svg = build_initial_svg()
            out = str(_ROOT / "data" / "img_temp" / f"xq_{chat_id}.png")
            await _svg_to_png(svg, out)
            cq = f"[CQ:image,file=file:///{out.replace(chr(92), '/')}]"
            await _send(format_lang("cmd_xq.m5", label=label, ann=ann, cq=cq))
            return None
        except Exception as e:
            return format_lang("cmd_xq.m6", e=e)

    # ── 邀请群友对弈（双人） ──
    if action in ("duel", "对战", "挑战"):
        if len(args) < 2:
            return format_lang("cmd_xq.m7")
        opp = _parse_opponent(args[1], group_id if is_group else 0, cfg)
        if not opp:
            return format_lang("cmd_xq.m8", args=args[1])
        if opp == user_id:
            return format_lang("cmd_xq.m9")
        diff_raw = ""
        for tok in args[2:]:
            if resolve_difficulty(tok):
                diff_raw = tok
                break
        result = start_game(user_id, chat_id, diff_raw or DEFAULT_DIFFICULTY, opponent_id=opp)
        if not result.startswith("ok"):
            return result
        label = result.split(":", 1)[1] if ":" in result else format_lang("cmd_xq.m10")
        gid = group_id if is_group else 0
        n1 = cfg.get_display_name(str(user_id), group_id=gid)
        n2 = cfg.get_display_name(str(opp), group_id=gid)
        ann = await _announce_room("xq", chat_id,
                                   [(user_id, n1, "红"), (opp, n2, "黑")], is_group,
                                   group_id, me_uid=user_id, mode_text=format_lang("cmd_xq.m11"))
        try:
            svg = build_initial_svg()
            out = str(_ROOT / "data" / "img_temp" / f"xq_{chat_id}.png")
            await _svg_to_png(svg, out)
            cq = f"[CQ:image,file=file:///{out.replace(chr(92), '/')}]"
            await _send(format_lang("cmd_xq.m12", n1=n1, label=label, ann=ann, cq=cq))
            return None
        except Exception as e:
            return format_lang("cmd_xq.m13", e=e)

    if action == "board":
        msg, img = show_board(chat_id)
        if img:
            await wait_render()   # 渲染是异步的，不等就会发出上一手的旧盘面
            cq = f"[CQ:image,file=file:///{img.replace(chr(92), '/')}]"
            await _send(cq)
        return msg

    if action in ("link", "链接", "网页", "房间"):
        return _link_text("xq", chat_id, user_id, get_game(chat_id) is not None)

    if action == "resign":
        return resign_game(user_id, chat_id)

    if action == "history":
        return show_history(chat_id)

    # 走棋
    notation = " ".join(args)
    ok, msg, img = make_move(user_id, chat_id, notation)
    if img:
        try:
            await wait_render()   # 渲染是异步的，不等就会发出上一手的旧盘面
            cq = f"[CQ:image,file=file:///{img.replace(chr(92), '/')}]"
            await _send(cq)
        except Exception:
            pass
    return msg


# ── 倒计时存储（★ v2.3.89 补：从 KOOK 端移植 cmd_countdown 时漏了这两个函数，
#    `/~countdown` 一直 NameError；帮助文本里保留着该指令，说明是有意保留的）──
_COUNTDOWN_FILE = Path(__file__).resolve().parent.parent / "data" / "countdowns.json"


def _load_countdowns() -> list:
    """读取倒计时列表（data/countdowns.json）"""
    if _COUNTDOWN_FILE.exists():
        try:
            return json.loads(_COUNTDOWN_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save_countdowns(data: list):
    """写回倒计时列表"""
    _COUNTDOWN_FILE.parent.mkdir(parents=True, exist_ok=True)
    _COUNTDOWN_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                               encoding="utf-8")


async def cmd_countdown(args, user_id, group_id, sender_name, is_group, bot_qq):
    """
    倒计时 /~countdown
      add <日期> <事件>     添加倒计时
      list                  查看所有
      del <编号>            删除
      无参数                 显示最近的
    """
    import datetime as _dt

    if not args:
        items = _load_countdowns()
        if not items:
            return format_lang("countdown.empty_list")
        lines = [format_lang("countdown.header_recent")]
        now = _dt.datetime.now()
        for i, item in enumerate(items, 1):
            try:
                target = _dt.datetime.strptime(item["date"], "%Y-%m-%d")
                days = (target - now).days
                days_str = format_lang(
                    "countdown.days_past" if days < 0 else "countdown.days_left",
                    n=abs(days))
            except Exception:
                days_str = "?"
            lines.append(format_lang("countdown.item", i=i, date=item["date"],
                                     event=item["event"], days=days_str))
        return "\n".join(lines)

    action = args[0].lower()

    if action == "list":
        items = _load_countdowns()
        if not items:
            return format_lang("countdown.empty_single")
        lines = [format_lang("countdown.header_list")]
        now = _dt.datetime.now()
        for i, item in enumerate(items, 1):
            try:
                target = _dt.datetime.strptime(item["date"], "%Y-%m-%d")
                days = (target - now).days
                days_str = format_lang(
                    "countdown.days_past" if days < 0 else "countdown.days_left",
                    n=abs(days))
            except Exception:
                days_str = "?"
            lines.append(format_lang("countdown.item", i=i, date=item["date"],
                                     event=item["event"], days=days_str))
        return "\n".join(lines)

    if action == "del" or action == "delete":
        if len(args) < 2:
            return format_lang("countdown.usage_del")
        try:
            idx = int(args[1]) - 1
        except ValueError:
            return format_lang("countdown.delete_invalid_id")
        items = _load_countdowns()
        if idx < 0 or idx >= len(items):
            return format_lang("countdown.delete_out_of_range")
        removed = items.pop(idx)
        _save_countdowns(items)
        return format_lang("countdown.deleted", date=removed["date"],
                           event=removed["event"])

    if action == "add":
        if len(args) < 3:
            return format_lang("countdown.usage_add")
        date_str = args[1]
        event = " ".join(args[2:])
    else:
        # 快捷模式: /~countdown 2026-12-25 圣诞节
        date_str = args[0]
        event = " ".join(args[1:])

    # 验证日期
    try:
        _dt.datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return format_lang("countdown.bad_date", value=date_str)

    items = _load_countdowns()
    items.append({"date": date_str, "event": event})
    _save_countdowns(items)

    target = _dt.datetime.strptime(date_str, "%Y-%m-%d")
    days = (target - _dt.datetime.now()).days
    days_str = format_lang("countdown.days_left" if days >= 0 else "countdown.days_past",
                           n=abs(days))
    return format_lang("countdown.added", date=date_str, event=event, days=days_str)



# --- 排行榜处理 ---

# 榜单周期缓存：board_id -> (periods_list, fetched_at)（10 分钟有效）
_LB_PERIOD_CACHE: dict = {}
_LB_PERIOD_TTL = 600


async def _align_board_period(board_id: str, want: str) -> str:
    """按榜单真实支持的 periods 对齐周期，避免撞 API 400。
    例: arena-modern-global-elo 只有 ALLTIME，想查 WEEKLY 时降级为 ALLTIME。"""
    global _LB_PERIOD_CACHE
    now = time.time()
    cached = _LB_PERIOD_CACHE.get(board_id)
    if cached and now - cached[1] < _LB_PERIOD_TTL:
        periods = cached[0]
    else:
        from services import wdsj_api as api
        boards = await api.query_leaderboards()
        if boards:
            periods = next((b.get("periods", []) for b in boards if b.get("id") == board_id), [])
            if not periods:
                periods = ["ALLTIME", "MONTHLY", "WEEKLY", "DAILY", "SEASON"]
            _LB_PERIOD_CACHE[board_id] = (periods, now)
        else:
            return want  # 列表拿不到就按用户原意走（反正 400 会回来）
    if want in periods:
        return want
    # 降级顺序: ALLTIME > SEASON > MONTHLY > WEEKLY > DAILY
    for cand in ("ALLTIME", "SEASON", "MONTHLY", "WEEKLY", "DAILY"):
        if cand in periods:
            return cand
    return want


async def _handle_wdsj_dual(args, is_group, group_id, user_id):
    """双模式横屏战绩卡: /~wdsj dual <玩家名> — 起床战争 + 竞技场 一图双段"""
    import re as _re
    from services import wdsj_api as api
    from services.sender import send_group_msg, send_private_msg
    from pathlib import Path as _P

    # 玩家名（忽略 img 之类后缀，支持绑定回退）
    parts = [a for a in args if a.lower() not in ("img", "pic", "card", "图片")]
    player = " ".join(parts).strip() if parts else (_get_bound_player(user_id) or "")
    if not player:
        return (format_lang("_handle_wdsj_dual.m1"))
    # 误把模式名当玩家名（如 /~wdsj bw me）→ 提示正确用法
    if parts and len(parts) == 1 and api.resolve_template(parts[0]):
        return (format_lang("_handle_wdsj_dual.m2", player=player, parts=parts[0]))

    await (send_group_msg(format_lang("_handle_wdsj_dual.m3", player=player), group_id) if is_group
           else send_private_msg(format_lang("_handle_wdsj_dual.m4", player=player), user_id))

    # 三项并行：两个模式查询 + 皮肤头像下载（头像内联后渲染不再等外部网络）
    bw, ar, head_uri = await asyncio.gather(
        api.query_player_stats(player, "bedwars-stats"),
        api.query_player_stats(player, "arena-stats"),
        api.fetch_player_head_data_uri(player),
        return_exceptions=False,
    )
    if not bw and not ar:
        return format_lang("wdsj.player_not_found", player=player, template=format_lang("_handle_wdsj_dual.m5"))

    html = api.build_dual_card_html(bw, ar, head_data_uri=head_uri)

    # 卡片宽 1740，需大视口 + 元素截图
    ts = __import__("datetime").datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = _re.sub(r"[^\w.\-]", "_", player)[:24]
    out = str(_P(__file__).resolve().parent.parent / "data" / "img_temp" / f"wdsj_dual_{safe}_{ts}.jpg")
    try:
        from modules.changelog import _ensure_browser
        browser = await _ensure_browser()
        page = await browser.new_page(viewport={"width": 2400, "height": 1400})  # 卡片宽 2200，视口须 >= 卡片宽
        await page.set_content(html)
        await page.wait_for_load_state("domcontentloaded")   # 头像已内联，无需等网络
        await page.wait_for_timeout(350)
        el = await page.query_selector(".card")
        if el:
            await el.screenshot(path=out, type="jpeg", quality=95)
        else:
            await page.screenshot(path=out, full_page=True, type="jpeg", quality=95)
        await page.close()
    except Exception as e:
        logger.warning("wdsj dual 卡片渲染失败: %s", e)
        return format_lang("_handle_wdsj_dual.m6", e=e)

    cq = f"[CQ:image,file=file:///{out.replace(chr(92), '/')}]"
    await (send_group_msg(cq, group_id) if is_group else send_private_msg(cq, user_id))
    return None


async def _handle_wdsj_lb(args, is_group, group_id, user_id):
    import os
    from services import wdsj_api as api
    from services.sender import send_group_msg, send_private_msg
    from core.config import get_config

    if not args or args[0].lower() in ("help",):
        return "\n".join(["排行榜用法: /~wdsj lb <榜单名> [周期] [img]",
                          "  双词: bw win, kbw tnt, sw kill ...",
                          "  周期: alltime/month/week/day  末尾+img=卡片图片"])

    # 先发提示
    from utils.format_lang import format_lang
    tip = format_lang("wdsj.lb_searching")
    await (send_group_msg(tip, group_id) if is_group 
           else send_private_msg(tip, user_id))

    # 检测 img 模式
    want_img = args[-1].lower() in ("img", "pic", "card", "图片")
    lb_args = args[:-1] if want_img else args[:]
    # 支持双词简写
    if len(lb_args) >= 2:
        bid = api.resolve_board_shorthand(lb_args[0], lb_args[1])
        if bid:
            board_id = bid
            period = (lb_args[2] if len(lb_args) > 2 else "ALLTIME").upper()
        else:
            board_id = api.resolve_board(lb_args[0])
            period = (lb_args[1] if len(lb_args) > 1 else "ALLTIME").upper()
    elif lb_args:
        board_id = api.resolve_board(lb_args[0])
        period = "ALLTIME"
    else:
        return format_lang("_handle_wdsj_lb.m1")
    board_id = board_id or lb_args[0]
    period = period if period in api.PERIOD_LABELS else {
        "MONTH": "MONTHLY", "WEEK": "WEEKLY", "DAY": "DAILY", "ALL": "ALLTIME"
    }.get(period, "ALLTIME")

    # ★ 周期智能对齐（榜单只支持部分周期时降级，避免 400）
    period = await _align_board_period(board_id, period)

    data = await api.query_leaderboard(board_id, period)
    if not data:
        return format_lang("wdsj.lb_fail", board=board_id)

    # 图片模式
    if want_img:
        cfg = get_config()
        from pathlib import Path as _Path

        ts = __import__("datetime").datetime.now().strftime("%Y%m%d_%H%M%S")
        fname = f"wdsj_lb_{board_id.replace(chr(47),'_')}_{ts}.png"
        out = str(_Path(__file__).resolve().parent.parent / "data" / "img_temp" / fname)

        # ★ v2.3.26: 优先 Pillow 直绘（1:1 复刻同模板）。测试项 pillow_card 控制，
        #   失败/关闭时回退下面的 Chromium 分支。纯 CPU 调用要丢线程池。
        try:
            from modules.features import is_enabled as _feat_on
            if _feat_on("pillow_card"):
                from services.leaderboard_card import save_leaderboard_card
                _loop = asyncio.get_running_loop()
                await _loop.run_in_executor(
                    None, lambda: save_leaderboard_card(data, out, cfg.bot_name))
                cq = f"[CQ:image,file=file:///{out.replace(chr(92), '/')}]"
                await (send_group_msg(cq, group_id) if is_group
                       else send_private_msg(cq, user_id))
                logger.info("排行榜卡用 Pillow 绘制完成: %s", _Path(out).name)
                return None
        except Exception as _e:
            logger.warning("排行榜卡 Pillow 绘制失败 → 回退 Chromium: %s", _e)

        html = api.build_leaderboard_html(data, cfg.bot_name)
        from modules.changelog import _ensure_browser
        try:
            browser = await _ensure_browser()
            page = await browser.new_page(viewport={"width": 440, "height": 600})
            await page.set_content(html)
            # ★ v2.3.23: 排行榜卡 head 用外链头像，networkidle 必要时限 1.5s 兜底，
            #   避免网络抖动时无限空等（DOM 就绪即开始倒计时截图）
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=3000)
            except Exception:
                pass
            await page.wait_for_timeout(400)
            await page.screenshot(path=out, full_page=True)
            await page.close()
            cq = f"[CQ:image,file=file:///{out.replace(chr(92), '/')}]"
            await (send_group_msg(cq, group_id) if is_group else send_private_msg(cq, user_id))
            return None
        except Exception as e:
            return format_lang("_handle_wdsj_lb.m2", e=e)

    # 文字模式
    board = data["board"]
    entries = data.get("entries", [])
    period_label = api.PERIOD_LABELS.get(data.get("type", "ALLTIME"), "总榜")
    lines = [f'{board.get("group","")} {board.get("displayName", board_id)} ({period_label})', ""]
    for e in entries:
        lines.append(f'  #{e["rank"]:>3}  {e["owner"]:<16} {e["value"]} {board.get("unit","")}')
    if not entries:
        lines.append(format_lang("_handle_wdsj_lb.m3"))
    return "\n".join(lines)


# ─── 洛花星雨战绩查询 ────────────────────────────────────────

_WDSJ_PLAYER_FILE = Path(__file__).resolve().parent.parent / "data" / "wdsj_player_name.json"


def _load_wdsj_bindings() -> dict:
    if _WDSJ_PLAYER_FILE.exists():
        try:
            return json.loads(_WDSJ_PLAYER_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_wdsj_bindings(data: dict):
    _WDSJ_PLAYER_FILE.parent.mkdir(parents=True, exist_ok=True)
    _WDSJ_PLAYER_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _get_bound_player(user_id: int) -> str | None:
    bindings = _load_wdsj_bindings()
    return bindings.get(str(user_id))


async def _handle_wdsj_bind(args: list, user_id: int) -> str:
    if not args:
        player = _get_bound_player(user_id)
        if player:
            return format_lang("_handle_wdsj_bind.m1", player=player)
        return format_lang("_handle_wdsj_bind.m2")
    player = args[0]
    bindings = _load_wdsj_bindings()
    bindings[str(user_id)] = player
    _save_wdsj_bindings(bindings)
    return format_lang("_handle_wdsj_bind.m3", player=player)


# ─── 洛花星雨战绩查询 ──────────────────────────────────────

def _build_wdsj_summary(data: dict) -> str:
    """从 WDSJ API 返回中提取简短摘要"""
    pi = data.get("player", {})
    display = data.get("_template_display", format_lang("common.unknown_mode"))
    hdr = data.get("headerCards", [])
    lines = [format_lang("_build_wdsj_summary.m1", name=pi.get('name', '?'), display=display)]
    for c in hdr[:6]:
        v = c["value"]
        if c.get("hint"):
            v = f"{v} ({c['hint']})"
        lines.append(f"  {c['label']}: {v}")
    return "\n".join(lines)


async def cmd_wdsj(args, user_id, group_id, sender_name, is_group, bot_qq):
    """
    洛花星雨战绩查询 /~wdsj <模板> <玩家名> — v2.3.20 起默认发官方图片，text 结尾要文字版
    别名: bw=起床战争 kbw=击退战场 sw=空岛战争 kp=职业战争 ...
    """
    from services import wdsj_api as api
    from services.sender import send_group_msg, send_private_msg

    if not args or args[0].lower() == "help":
        # v2.3.81: 渲染新版液态玻璃帮助卡（覆盖全部子指令，替换旧 MD 渲染版）
        try:
            cfg = get_config()
            html = api.build_wdsj_help_card_html(cfg.bot_name)
            from modules.changelog import _ensure_browser
            browser = await _ensure_browser()
            page = await browser.new_page(viewport={"width": 560, "height": 100})
            await page.set_content(html)
            await page.wait_for_timeout(500)
            await page.set_viewport_size({"width": 560, "height": 100})
            out = str(Path(__file__).resolve().parent.parent / "data" / "img_temp" / f"wdsj_help_{int(time.time())}.png")
            await page.screenshot(path=out, full_page=True)
            await page.close()
            cq = f"[CQ:image,file=file:///{out.replace(chr(92), '/')}]"
            await (send_group_msg(cq, group_id) if is_group else send_private_msg(cq, user_id))
            return None
        except Exception as e:
            logger.warning("wdsj help 卡片渲染失败: %s", e)
            return "\n".join([
                "洛花星雨战绩查询 /~wdsj",
                "  <模式> <玩家>            战绩(默认发图片)",
                "  <模式> <玩家> text       战绩(文字版)",
                "  lb <榜> [周期] [img]     排行榜",
                "  me / <玩家名> me         双模式横屏卡(起床+竞技场)",
                "  boards                   简写速查",
                "  list                     模式别名",
                "简写: bw/kbw/sw/kp 周期: all/month/week/day"
            ])

    action = args[0].lower()

    # ★ 手动采集（仅 admin）
    if action == "collect":
        from core.config import get_config as _get_cfg
        if not _get_cfg().is_admin(user_id, group_id):
            return format_lang("cmd_wdsj.m1")
        from services.sender import send_group_msg as _sgm
        await _sgm(format_lang("cmd_wdsj.m2"), group_id)
        from services.wdsj_tracker import daily_stats_collect
        import time as _t
        t0 = _t.time()
        failed = await daily_stats_collect()  # v2.0.4s: 返回 3 轮重试后仍失败名单
        elapsed = _t.time() - t0
        if failed:
            names = sorted({p for p, _t in failed})
            return format_lang("cmd_wdsj.m3", elapsed=format(elapsed, ".1f"), names=len(names)) + "、".join(names)
        return format_lang("cmd_wdsj.m4", elapsed=format(elapsed, ".1f"))

    # 绑定玩家名
    if action in ("bd", "bind", "绑定"):
        if args[1:] and args[1] in ("list", "列表"):
            bindings = _load_wdsj_bindings()
            if not bindings:
                return format_lang("cmd_wdsj.m5")
            lines = [format_lang("cmd_wdsj.m6")]
            seen = set()
            for qq, name in sorted(bindings.items(), key=lambda x: x[1].lower()):
                if name not in seen:
                    seen.add(name)
                    lines.append(f"  {name}")
            return "\n".join(lines)
        return await _handle_wdsj_bind(args[1:], user_id)

    # 排行榜分支
    if action in ("lb", "排行榜", "ldb"):
        return await _handle_wdsj_lb(args[1:], is_group, group_id, user_id)

    if action in ("boards", "榜单", "榜"):
        # ★ 带游戏过滤：/~wdsj boards bw → 只看起床战争的榜单（v2.3.81，用户反馈原表没看懂）
        game_raw = (args[1] if len(args) > 1 else "").strip()
        if game_raw and game_raw.lower() not in ("all", "全部"):
            tid = api.resolve_template(game_raw) or ""
            prefix = tid.replace("-stats", "") if tid else ""
            cn = api.TEMPLATES.get(tid, game_raw)
            rows = [(a, bid) for a, bid in api.BOARD_ALIASES.items()
                    if bid.startswith(prefix) or bid.startswith(cn)]
            if rows:
                lines = [format_lang("cmd_wdsj.m7", cn=cn), ""]
                lines += [f"  {a:<8s} = {bid}" for a, bid in sorted(rows, key=lambda x: x[1])]
                lines += ["", format_lang("cmd_wdsj.m8"),
                          format_lang("cmd_wdsj.m9")]
                return "\n".join(lines)
        lines = [format_lang("cmd_wdsj.m10")]
        lines.append("")
        # 按游戏分组的简写
        groups = {
            "起床战争(bw)": [("win","胜"),("kill",format_lang("cmd_wdsj.m11")),("beds",format_lang("cmd_wdsj.m12")),("fk",format_lang("cmd_wdsj.m13")),("1k",format_lang("cmd_wdsj.m14"))],
            "击退战场(kbw)": [("kill",format_lang("cmd_wdsj.m15")),("dead",format_lang("cmd_wdsj.m16")),("tnt","TNT"),("arrow",format_lang("cmd_wdsj.m17")),("rod",format_lang("cmd_wdsj.m18")),("jp",format_lang("cmd_wdsj.m19"))],
            "空岛战争(sw)": [("kill",format_lang("cmd_wdsj.m20")),("win","胜"),("dead",format_lang("cmd_wdsj.m21")),("1k",format_lang("cmd_wdsj.m22"))],
            "职业战争(kp)": [("kill",format_lang("cmd_wdsj.m23")),("xp",format_lang("cmd_wdsj.m24"))],
            "其他": [("pt",format_lang("cmd_wdsj.m25")),("cp",format_lang("cmd_wdsj.m26")),("title",format_lang("cmd_wdsj.m27")),("guild",format_lang("cmd_wdsj.m28")),("dg win",format_lang("cmd_wdsj.m29")),("cw win",format_lang("cmd_wdsj.m30")),("cw kill",format_lang("cmd_wdsj.m31")),("has win",format_lang("cmd_wdsj.m32"))],
        }
        for group, items in groups.items():
            suffix = " " + group
            parts = []
            for short, name in items:
                parts.append(f"{short}={name}")
            lines.append(f"{suffix}: {', '.join(parts)}")
        return "\n".join(lines)

    if action == "list":
        lines = [format_lang("cmd_wdsj.m33")]
        for tid, name in api.TEMPLATES.items():
            alias = [a for a, t in api.ALIASES.items() if t == tid]
            a_str = alias[0] if alias else tid
            lines.append(f"  {a_str} = {tid} ({name})")
        lines.append("")
        lines.append(format_lang("cmd_wdsj.m34"))
        lines.append(format_lang("cmd_wdsj.m35"))
        for a, bid in sorted(api.BOARD_ALIASES.items()):
            lines.append(f"  {a:6s} = {bid}")
        return "\n".join(lines)

    # ★ 群内排名
    if action in ("rank", "排名", "群排名"):
        mode_key = (args[1] if len(args) > 1 else "bw_kills").strip().lower()
        from services.wdsj_tracker import build_group_rank
        tip = format_lang("cmd_wdsj.m36", mode_key=mode_key)
        await (send_group_msg(tip, group_id) if is_group
               else send_private_msg(tip, user_id))
        text_result, image_path = await build_group_rank(group_id, mode_key)
        if image_path:
            cq = f"[CQ:image,file=file:///{image_path.replace(chr(92), '/')}]"
            await (send_group_msg(cq, group_id) if is_group
                   else send_private_msg(cq, user_id))
            return None
        return text_result

    # ★ 今日击杀排名（图片渲染）
    # ★ 日榜 (默认起床，/wdsj daily are = 竞技场)
    if action in ("daily", "今日", "今天"):
        from services.wdsj_tracker import build_daily_rankings, build_arena_daily_rankings
        from datetime import datetime, date as _date
        import re

        mode = "bw"
        label_date = None
        for a in args[1:]:
            a_lower = a.lower().strip()
            if a_lower in ("are", "arena", "竞技", "竞技场"):
                mode = "are"
            elif re.match(r'^\d{1,2}[-/]\d{1,2}$', a):  # 7-20 or 07/20
                m = re.match(r'^(\d{1,2})[/-](\d{1,2})$', a)
                label_date = f"{datetime.now().year}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
            elif re.match(r'^\d{4}-\d{2}-\d{2}$', a):  # 2026-07-20
                label_date = a
            elif re.match(r'^\d{4}$', a):  # 0720
                label_date = f"{datetime.now().year}-{int(a[:2]):02d}-{int(a[2:]):02d}"

        if mode == "are":
            # ★ v2.3.24: 日榜渲染 ~1-3s，先回执避免用户以为没反应（原来全程静默）
            _is_push0 = len(args) >= 2 and args[-1].lower() in ("send", "push", "推送", "发送")
            if not _is_push0:
                await (send_group_msg(format_lang("cmd_wdsj.m37"), group_id) if is_group
                       else send_private_msg(format_lang("cmd_wdsj.m38"), user_id))
            rows, today, time_start, time_end, fb = build_arena_daily_rankings(label_date=label_date)
        else:
            from datetime import date as _date, timedelta as _td
            # ★ push 模式强制用跨天（昨天 0:01 → 今天 0:01）
            is_push = len(args) >= 2 and args[-1].lower() in ("send", "push", "推送", "发送")
            if is_push and not label_date:
                label_date = (_date.today() - _td(days=1)).isoformat()
            # ★ 查询过去日期时自动用跨天模式
            use_cross = label_date and label_date != _date.today().isoformat()
            # ★ v2.3.24: 先回执（push 模式是后台推送，不用回执）
            if not is_push:
                _d = label_date or _date.today().isoformat()
                await (send_group_msg(format_lang("cmd_wdsj.m39", _d=_d), group_id) if is_group
                       else send_private_msg(format_lang("cmd_wdsj.m40", _d=_d), user_id))
            rows, today, time_start, time_end, new_players, fb = build_daily_rankings(
                label_date=label_date, cross_day=use_cross)

            if not rows:
                now = datetime.now()
                label = today or format_lang("cmd_wdsj.m41")
                # 节奏/时隙列表取自配置（4h / 12h），别硬编码
                from services.wdsj_tracker import (
                    next_slot as _wdsj_next_slot, slots_hint as _wdsj_slots_hint,
                )
                if today == _date.today().isoformat():
                    hint = format_lang("cmd_wdsj.m42", M=_wdsj_next_slot(now).strftime('%H:%M'))
                else:
                    hint = ""
                return format_lang("cmd_wdsj.m43", label=label, _wdsj_slots_hint=_wdsj_slots_hint(), hint=hint)

        out_path = None
        # ★ v2.3.26: 起床战争日榜优先走 Pillow 绘制（服务器实测 79ms vs Chromium
        #   872ms，快 11 倍；一比一复刻，平均像素差 2.5%）。由测试项 pillow_card
        #   控制，可在 /~key 一键回退；任何异常都自动回退 Chromium，不影响可用性。
        #   注意：Pillow 是纯 CPU 同步调用，必须丢线程池，否则阻塞事件循环。
        try:
            from modules.features import is_enabled as _feat_on
            _use_pillow = _feat_on("pillow_card")   # 起床/竞技都走 Pillow
        except Exception:
            _use_pillow = False
        if _use_pillow:
            try:
                # 起床战争与竞技场是两套模板（暖橙 / 冷蓝）→ 两个渲染入口
                if mode == "are":
                    from services.wdsj_card_pillow import save_arena_daily_card as _saver
                    _payload = _arena_daily_payload(rows, today, time_start, time_end)
                    _prefix = "wdsj_arena"
                else:
                    from services.wdsj_card_pillow import save_daily_rank_card as _saver
                    _payload = _daily_rank_payload(rows, today, new_players,
                                                   time_start, time_end)
                    _prefix = "wdsj_daily"
                _ts = time.strftime("%Y%m%d_%H%M%S")
                _out = str(Path(__file__).resolve().parent.parent
                           / "data" / "img_temp" / f"{_prefix}_{_ts}.jpg")
                _loop = asyncio.get_running_loop()
                await _loop.run_in_executor(None, lambda: _saver(_payload, _out))
                out_path = _out
                logger.info("日榜卡用 Pillow 绘制完成: %s", Path(_out).name)
            except Exception as _e:
                logger.warning("Pillow 日榜卡绘制失败 → 回退 Chromium: %s", _e)
                out_path = None

        if not out_path:
            if mode == "are":
                html = _build_arena_daily_html(rows, today, time_start, time_end)
            else:
                html = _build_daily_rank_html(rows, today, new_players, time_start, time_end)
            prefix = "wdsj_arena" if mode == "are" else "wdsj_daily"
            out_path = await _render_html_to_png(html, prefix)

        if not out_path:
            return format_lang("cmd_wdsj.m44")
        cq = f"[CQ:image,file=file:///{out_path.replace(chr(92), chr(47))}]"

        # ★ 推送模式：发到当前群（仅 is_group 时）
        if len(args) >= 2 and args[-1].lower() in ("send", "push", "推送", "发送"):
            if not is_group:
                return format_lang("cmd_wdsj.m45")
            await send_group_msg(cq, group_id)
            return None

        await (send_group_msg(cq, group_id) if is_group else send_private_msg(cq, user_id))
        return None

    # ★ 双模式横屏卡（起床战争 + 竞技场）
    #    主用法: /~wdsj me（用绑定玩家） | /~wdsj <玩家名> me（指定玩家）
    #    兼容旧写法: /~wdsj dual <玩家名>
    _ME_WORDS = ("me", "我", format_lang("cmd_wdsj.m46"), "my")
    if action in ("dual", "double", "双模式", "双段") or args[-1].lower() in _ME_WORDS:
        if action in ("dual", "double", "双模式", "双段"):
            name_args = args[1:]
        else:
            name_args = args[:-1]
        return await _handle_wdsj_dual(name_args, is_group, group_id, user_id)

    # ★ 折线图
    if action in ("trend", "趋势", "走势", "折线"):
        from services.wdsj_tracker import generate_trend_chart
        player = None
        metric = "bw_kills"
        for a in args[1:]:
            a_lower = a.lower().strip()
            if a_lower in ("bw_kills", "bw_wins", "bw_finals", "bw_deaths", "arena_kills"):
                metric = a_lower
            else:
                player = a
        if not player:
            player = _get_bound_player(user_id)
        if not player:
            return format_lang("cmd_wdsj.m47")
        
        label = {"bw_kills":"击杀","bw_wins":"胜场","bw_finals":"最终击杀","bw_deaths":"死亡","arena_kills":"竞技场击杀"}.get(metric, metric)
        tip = format_lang("cmd_wdsj.m48", player=player, label=label)
        await (send_group_msg(tip, group_id) if is_group
               else send_private_msg(tip, user_id))
        
        img = generate_trend_chart(player, metric)
        if img:
            cq = f"[CQ:image,file=file:///{img.replace(chr(92), '/')}]"
            await (send_group_msg(cq, group_id) if is_group
                   else send_private_msg(cq, user_id))
            return None
        return format_lang("cmd_wdsj.m49", player=player, label=label)

    if len(args) < 2:
        # 尝试从绑定中查找
        player = _get_bound_player(user_id)
        if player:
            args = [args[0], player] + (args[1:] if len(args) > 1 else [])
        if len(args) < 2:
            return format_lang("cmd_wdsj.m50")

    # 提取输出模式标记（在绑定补齐之后）
    # v2.3.20: 默认发官方图片卡片（去文字刷屏）；img/pic/card/图片 兼容旧写法（等同默认）；
    # text/文字/notext 显式要文字版
    _last = args[-1].lower()
    if _last in ("img", "pic", "card", "图片", "text", "文字", "notext", "文本"):
        want_img = _last not in ("text", "文字", "notext", "文本")
        player_parts = args[1:-1]
    else:
        want_img = True
        player_parts = args[1:]
    # 如果只有模板+img，没有玩家名，尝试绑定
    if not player_parts or all(p in ("img", "pic", "card", "图片") for p in player_parts):
        player = _get_bound_player(user_id)
        if player:
            player_parts = [player]
    player = " ".join(player_parts)
    if not player:
        return format_lang("cmd_wdsj.m51")

    template_id = api.resolve_template(action)
    if not template_id:
        return format_lang("cmd_wdsj.m52", action=action)

    display = api.TEMPLATES.get(template_id, template_id)
    logger.info("查询wdsj战绩: player=%s template=%s img=%s", player, template_id, want_img)

    tip = format_lang("wdsj.player_searching", player=player, template=display)
    await (send_group_msg(tip, group_id) if is_group
           else send_private_msg(tip, user_id))

    data = await api.query_player_stats(player, template_id)
    if not data:
        return format_lang("wdsj.player_not_found", player=player, template=display)

    # 图片模式：下载官方 PNG
    if want_img:
        snapshot = data.get("snapshotKey", "")
        if not snapshot:
            return "官方图片尚未生成喵~ 用 /~wdsj {} {} text 看文字数据".format(action, player)
        image_url = f"/api/v1/images/{snapshot}"

        # 下载到项目 img_temp 目录（NapCat 可访问）
        from pathlib import Path as _Path
        out_dir = _Path(__file__).resolve().parent.parent / "data" / "img_temp"
        out_dir.mkdir(parents=True, exist_ok=True)
        save_path = str(out_dir / f"wdsj_{snapshot}.png")

        ok = await api.download_stats_image(image_url, save_path)
        if ok:
            cq = f"[CQ:image,file=file:///{save_path.replace(chr(92), '/')}]"
            await (send_group_msg(cq, group_id) if is_group else send_private_msg(cq, user_id))
            # ★ 缓存数据：引用此图时直接返回文字数据，不调视觉模型
            from services.wdsj_cache import store as wdsj_store
            data["_template_display"] = display
            summary = _build_wdsj_summary(data)
            wdsj_store(snapshot, player, template_id, summary)
            return None
        else:
            return format_lang("wdsj.img_download_fail") + format_lang("cmd_wdsj.m53")

    # 文字模式：完整数据
    pi = data.get("player", {})
    vals = data.get("values", {})
    labels = data.get("labels", {})
    hdr = data.get("headerCards", [])
    smr = data.get("summaryCards", [])

    lines = [format_lang("cmd_wdsj.m54", name=pi.get('name', '?'), display=display)]

    for c in hdr:
        v = c["value"]
        if c.get("hint"): v = f"{v} ({c['hint']})"
        lines.append(f"  {c['label']}: {v}")

    if smr:
        lines.append("")
        for c in smr:
            lines.append(f"  {c['label']}: {c['value']}")

    if vals:
        lines.append("")
        for k, v in vals.items():
            label = labels.get(k, k)
            lines.append(f"  {label}: {v}")

        return "\n".join(lines)

# ─── /~owner 卡片渲染 ───────────────────────────────────

def _build_owner_help_md() -> str:
    return (
        "**/~owner** — 配置管理（仅主人可用）\n\n"
        "---\n\n"
        "### 配置文件\n"
        "| 指令 | 说明 |\n"
        "|------|------|\n"
        "| `/~owner list <域>` | 列出配置：`bot` / `adapter` / `roles` |\n"
        "| `/~owner get <路径>` | 读取：`get bot.reply_threshold` |\n"
        "| `/~owner set <路径> <值>` | 设置：`set bot.reply_threshold 5` |\n"
        "\n"
        "### 数据文件\n"
        "| 指令 | 说明 |\n"
        "|------|------|\n"
        "| `/~owner data get <名> [键]` | 读取：`data get fav` 或 `data get fav g_123_456` |\n"
        "| `/~owner data set <名> <键> <值>` | 设置：`data set fav g_123_456 100` |\n"
        "| `/~owner data del <名> <键>` | 删除某条记录 |\n"
        "| `/~owner data reset <名\\|all>` | 重置：`data reset fav` / `data reset all` |\n"
        "| 可用数据：`fav` `luck` `countdown` `reminders` `recall` `wzq` |\n"
        "\n"
        "### 快捷白名单\n"
        "| 指令 | 说明 |\n"
        "|------|------|\n"
        "| `/~owner wl add group <群号>` | 添加群白名单 |\n"
        "| `/~owner wl add private <QQ>` | 添加私聊白名单 |\n"
        "| `/~owner wl remove group <群号>` | 移除群白名单 |\n"
        "| `/~owner wl remove private <QQ>` | 移除私聊白名单 |\n"
        "| `/~owner wl show` | 查看白名单 |\n"
        "| `/~owner wl welcome set <群> <语>` | 设置入群欢迎语 |\n"
        "| `/~owner wl welcome del <群>` | 删除入群欢迎语 |\n"
        "\n"
        "### 运气管理\n"
        "| 指令 | 说明 |\n"
        "|------|------|\n"
        "| `/~owner luck list` | 查看今日全员运气值 |\n"
        "| `/~owner luck set <QQ> <0~100>` | 修改某人的运气值 |\n"
        "| `/~owner luck del <QQ>` | 删除某人的运气记录 |\n"
        "\n"
        "### 画图 / 视频配额\n"
        "| 指令 | 说明 |\n"
        "|------|------|\n"
        "| `/~owner draw get [QQ]` | 查看画图用量，不填 QQ 看全部 |\n"
        "| `/~owner draw set <QQ> <N>` | 设每日画图上限（默认 5）|\n"
        "| `/~owner draw reset` | 重置今日画图用量 |\n"
        "| `/~owner video get\\|set\\|reset` | 视频配额管理（默认 4）|\n"
        "\n"
        "### 指令白名单\n"
        "| 指令 | 说明 |\n"
        "|------|------|\n"
        "| `/~owner cmd add <指令>` | 添加：`cmd add weather` |\n"
        "| `/~owner cmd remove <指令>` | 移除 |\n"
        "| `/~owner cmd list` | 查看白名单指令 |\n"
        "| `/~owner cmd clear` | 清空白名单（所有指令仅主人可用）|\n"
    )


def _build_owner_help_html(md: str) -> str:
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#1a1a2e;color:#e0e0e0;font-family:"Microsoft YaHei","PingFang SC",sans-serif;padding:28px;width:680px}}
.header{{background:linear-gradient(135deg,#f97316,#dc2626);color:#fff;border-radius:12px;padding:20px 24px;margin-bottom:20px}}
.header h1{{font-size:22px;margin-bottom:4px}}
.header .sub{{font-size:13px;opacity:.85}}
.section{{margin-bottom:18px}}
.section h3{{font-size:15px;color:#fb923c;border-left:3px solid #f97316;padding-left:10px;margin-bottom:8px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{background:rgba(249,115,22,.15);color:#fb923c;text-align:left;padding:6px 10px;font-weight:600}}
td{{padding:5px 10px;border-bottom:1px solid rgba(255,255,255,.06)}}
code{{background:rgba(249,115,22,.12);color:#fb923c;padding:1px 6px;border-radius:4px;font-size:12px}}
tr:hover td{{background:rgba(249,115,22,.06)}}
.footer{{text-align:center;font-size:11px;color:#666;margin-top:16px;padding-top:12px;border-top:1px solid rgba(255,255,255,.08)}}
</style></head><body>
<div class="header"><h1>🤖 配置管理 /~owner</h1><div class="sub">仅主人可用 | 幻梦 QQ Bot</div></div>
<div class="section"><h3>⚙️ 配置文件</h3><table><tr><th>指令</th><th>说明</th></tr>
<tr><td><code>/~owner list &lt;域&gt;</code></td><td>列出配置：<code>bot</code> / <code>adapter</code> / <code>roles</code></td></tr>
<tr><td><code>/~owner get &lt;路径&gt;</code></td><td>读取配置值，如 <code>get bot.reply_threshold</code></td></tr>
<tr><td><code>/~owner set &lt;路径&gt; &lt;值&gt;</code></td><td>设置配置值，如 <code>set bot.reply_threshold 5</code></td></tr>
</table></div>
<div class="section"><h3>📂 数据文件</h3><table><tr><th>指令</th><th>说明</th></tr>
<tr><td><code>/~owner data get &lt;名&gt; [键]</code></td><td>读取数据文件</td></tr>
<tr><td><code>/~owner data set &lt;名&gt; &lt;键&gt; &lt;值&gt;</code></td><td>设置数据</td></tr>
<tr><td><code>/~owner data del &lt;名&gt; &lt;键&gt;</code></td><td>删除记录</td></tr>
<tr><td><code>/~owner data reset &lt;名|all&gt;</code></td><td>重置数据文件</td></tr>
<tr><td colspan="2">可用数据：<code>fav</code> <code>luck</code> <code>countdown</code> <code>reminders</code> <code>recall</code> <code>wzq</code></td></tr>
</table></div>
<div class="section"><h3>🛡️ 快捷白名单</h3><table><tr><th>指令</th><th>说明</th></tr>
<tr><td><code>/~owner wl add group &lt;群号&gt;</code></td><td>添加群白名单</td></tr>
<tr><td><code>/~owner wl add private &lt;QQ&gt;</code></td><td>添加私聊白名单</td></tr>
<tr><td><code>/~owner wl remove group &lt;群号&gt;</code></td><td>移除群白名单</td></tr>
<tr><td><code>/~owner wl remove private &lt;QQ&gt;</code></td><td>移除私聊白名单</td></tr>
<tr><td><code>/~owner wl show</code></td><td>查看白名单</td></tr>
<tr><td><code>/~owner wl welcome set &lt;群&gt; &lt;语&gt;</code></td><td>设置入群欢迎语</td></tr>
<tr><td><code>/~owner wl welcome del &lt;群&gt;</code></td><td>删除入群欢迎语</td></tr>
</table></div>
<div class="section"><h3>🍀 运气管理</h3><table><tr><th>指令</th><th>说明</th></tr>
<tr><td><code>/~owner luck list</code></td><td>查看今日全员运气值</td></tr>
<tr><td><code>/~owner luck set &lt;QQ&gt; &lt;0~100&gt;</code></td><td>修改运气值</td></tr>
<tr><td><code>/~owner luck del &lt;QQ&gt;</code></td><td>删除运气记录</td></tr>
</table></div>
<div class="section"><h3>🎨 画图 / 视频配额</h3><table><tr><th>指令</th><th>说明</th></tr>
<tr><td><code>/~owner draw get [QQ]</code></td><td>查看画图用量，不填 QQ 看全部</td></tr>
<tr><td><code>/~owner draw set &lt;QQ&gt; &lt;N&gt;</code></td><td>设每日画图上限（默认 5）</td></tr>
<tr><td><code>/~owner draw reset</code></td><td>重置今日画图用量</td></tr>
<tr><td><code>/~owner video get|set|reset</code></td><td>视频配额管理（默认 4）</td></tr>
</table></div>
<div class="section"><h3>🔒 指令白名单</h3><table><tr><th>指令</th><th>说明</th></tr>
<tr><td><code>/~owner cmd add &lt;指令&gt;</code></td><td>添加指令到白名单</td></tr>
<tr><td><code>/~owner cmd remove &lt;指令&gt;</code></td><td>从白名单移除</td></tr>
<tr><td><code>/~owner cmd list</code></td><td>查看白名单指令</td></tr>
<tr><td><code>/~owner cmd clear</code></td><td>清空（仅主人可用所有指令）</td></tr>
</table></div>
<div class="footer">幻梦 QQ Bot | /~owner 配置管理</div>
</body></html>"""

def _handle_owner_cmd(args):
    roles = load_roles_config()
    wl = roles.get("command_whitelist", [])
    if not isinstance(wl, list): wl = []
    if not args:
        return format_lang("_handle_owner_cmd.m1")
    sub = args[0].lower()
    if sub in ("list", "show"):
        if not wl: return format_lang("_handle_owner_cmd.m2")
        return format_lang("_handle_owner_cmd.m3") + "\n  ".join(wl)
    if sub == "clear":
        roles["command_whitelist"] = []
        save_roles_config(roles)
        return format_lang("_handle_owner_cmd.m4")
    if sub in ("add", "remove") and len(args) < 2:
        return format_lang("_handle_owner_cmd.m5", sub=sub)
    cmd_name = args[1].lower().lstrip("/~#")
    if sub == "add":
        if cmd_name in wl: return format_lang("_handle_owner_cmd.m6", cmd_name=cmd_name)
        wl.append(cmd_name)
        roles["command_whitelist"] = wl; save_roles_config(roles)
        return format_lang("_handle_owner_cmd.m7", cmd_name=cmd_name)
    if sub == "remove":
        if cmd_name not in wl: return format_lang("_handle_owner_cmd.m8", cmd_name=cmd_name)
        wl.remove(cmd_name)
        roles["command_whitelist"] = wl; save_roles_config(roles)
        return format_lang("_handle_owner_cmd.m9", cmd_name=cmd_name)
    return format_lang("_handle_owner_cmd.m10", sub=sub)


# ─── 管理员 ──────────────────────────────────────────────────

def _toml_write_in_section(path: Path, section: str, key: str,
                           value_literal: str | None) -> bool:
    """在 toml 的 `[section]` 里设置/删除一个键，**只动目标行**，保留其余内容与注释。

    ⚠️ 不要用 `toml.dumps()` 整文件重写 —— 那会把所有注释和排版全丢掉
    （服务器 bot_config.toml 有 16 行注释）。value_literal=None 表示删除该键。
    返回是否写入了文件。
    """
    lines = path.read_text(encoding="utf-8").split("\n") if path.exists() else []
    hdr = f"[{section}]"
    sec_start = next((i for i, ln in enumerate(lines) if ln.strip() == hdr), None)

    if sec_start is None:
        if value_literal is None:
            return False
        while lines and not lines[-1].strip():
            lines.pop()
        lines += ["", hdr, f"{key} = {value_literal}"]
    else:
        sec_end = len(lines)
        for j in range(sec_start + 1, len(lines)):
            s = lines[j].strip()
            if s.startswith("[") and s.endswith("]"):
                sec_end = j
                break
        pat = re.compile(rf"^\s*{re.escape(key)}\s*=")
        hit = next((j for j in range(sec_start + 1, sec_end) if pat.match(lines[j])), None)
        if value_literal is None:
            if hit is None:
                return False
            del lines[hit]
        elif hit is not None:
            lines[hit] = f"{key} = {value_literal}"
        else:
            lines.insert(sec_start + 1, f"{key} = {value_literal}")

    path.write_text("\n".join(lines), encoding="utf-8")
    return True


# ── 故障推送（目前只有 DeepSeek 提供 RSS/状态页，故监控范围仅限它）────
_FAULT_DOT = {"red": "🔴", "yellow": "🟡", "green": "🟢"}

# 当前这轮 DeepSeek 故障的实况（**兜底用**：正常情况下 `fault test` 运行时直接读官方
# feed 拿最新状态，拉取失败/超时才落到这里这份手抄快照，2026-10-15 09:2x 更新）。
_FAULT_DEFAULT = {
    "started": "2026-10-15 08:11",
    "status": format_lang("mod.m38"),
    "components": (format_lang("mod.m39")),
    "symptom": format_lang("mod.m40"),
}


_DS_FEED_URL = "https://status.deepseek.com/feed.rss"

# 国内访问 status.deepseek.com 的三重阻断（2026-10-15 实测）：
#   ① DNS 被分线路指到阿里云**中国**节点 → 该站没 ICP 备案，HTTP 明文直接返回
#      "Non-compliance ICP Filing" 403 页
#   ② HTTPS 在 TLS 握手时被 RST（SNI 阻断）
#   ③ DoH（1.1.1.1:443）也超时
# 但 **UDP 53 → 1.1.1.1 是通的**：用国外 DNS 拿到 statuspage.flashduty.com 的
# **香港** NLB 节点（47.76.103.98 等），对该 IP 直连（SNI/Host 仍用真域名）→ HTTP 200。
_DS_FEED_DNS = "1.1.1.1"


def _dns_a(name: str, server: str = _DS_FEED_DNS, timeout: float = 5.0) -> list:
    """极简 DNS A 记录查询（UDP 53，纯 stdlib，不依赖 dig/dnspython）。"""
    import random as _rd
    import socket as _s
    import struct as _st

    tid = _rd.randint(0, 65535)
    q = _st.pack(">HHHHHH", tid, 0x0100, 1, 0, 0, 0)
    for lab in name.split("."):
        q += bytes([len(lab)]) + lab.encode()
    q += b"\x00" + _st.pack(">HH", 1, 1)
    s = _s.socket(_s.AF_INET, _s.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.sendto(q, (server, 53))
        data, _ = s.recvfrom(4096)
    finally:
        s.close()

    ancount = _st.unpack(">H", data[6:8])[0]
    i = 12
    while data[i] != 0:          # 跳过 question 的域名
        i += data[i] + 1
    i += 5                        # \0 + type + class
    out = []
    for _ in range(ancount):
        if data[i] & 0xC0:        # 压缩指针
            i += 2
        else:
            while data[i] != 0:
                i += data[i] + 1
            i += 1
        rtype, _, _, rdlen = _st.unpack(">HHIH", data[i:i + 10])
        i += 10
        if rtype == 1 and rdlen == 4:
            out.append(".".join(str(b) for b in data[i:i + 4]))
        i += rdlen
    return out


def _fetch_feed_via_ip(ip: str, path: str, timeout: float = 10.0) -> str:
    """对指定 IP 直连拉 feed：SNI/Host 用真域名（证书校验正常），绕开国内 DNS 污染。"""
    import http.client as _hc
    import socket as _s
    import ssl as _ssl

    ctx = _ssl.create_default_context()
    raw = _s.create_connection((ip, 443), timeout=timeout)
    tls = ctx.wrap_socket(raw, server_hostname="status.deepseek.com")
    conn = _hc.HTTPSConnection("status.deepseek.com", 443, timeout=timeout)
    conn.sock = tls
    try:
        conn.request("GET", path, headers={"User-Agent": "Mozilla/5.0",
                                           "Accept": "*/*"})
        r = conn.getresponse()
        body = r.read()
        if r.status != 200:
            raise RuntimeError(f"HTTP {r.status}")
        return body.decode("utf-8", "ignore")
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _parse_ds_feed(xml: str) -> dict:
    """从 feed XML 里解析最新一条。"""
    import html as _html

    m = re.search(r"<item>(.*?)</item>|<entry>(.*?)</entry>", xml, re.S)
    if not m:
        raise ValueError(format_lang("_parse_ds_feed.m1"))
    block = m.group(1) or m.group(2)

    def _tag(name: str) -> str:
        mm = re.search(rf"<{name}[^>]*>(.*?)</{name}>", block, re.S | re.I)
        return _html.unescape(mm.group(1)).strip() if mm else ""

    title = _tag("title")
    desc = _tag("description") or _tag("summary") or _tag("content")
    link = _tag("link")
    pub = _tag("pubDate") or _tag("updated") or _tag("published")

    # ⚠️ description 是 HTML 转义的：先解实体，再剥标签。
    #    剥标签必须用**空格**而不是 \n —— `<strong>Status:</strong> identified`
    #    若换成 \n 会把 "Status:" 和它的值拆到两行，status_word 就抓不到（已踩过）。
    text = _html.unescape(desc)
    text = re.sub(r"</p\s*>|<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    lines = [re.sub(r"\s+", " ", l).strip() for l in text.split("\n") if l.strip()]

    status_word, components, status_text = "", "", ""
    for l in lines:
        mm = re.match(r"Status:\s*\**\s*([A-Za-z]+)", l)
        if mm and not status_word:
            status_word = mm.group(1).lower()
        mm = re.match(r"Affected components:\s*(.+)", l)
        if mm and not components:
            components = mm.group(1).strip().replace(", ", "、")
        if (not status_text and not l.startswith("Status:")
                and not l.startswith("Affected components:")
                and re.search(r"[\u4e00-\u9fff]", l)):
            status_text = l.rstrip("。")

    started = ""
    if pub:
        import email.utils as _eu
        from datetime import timedelta as _td, timezone as _tz
        try:
            started = _eu.parsedate_to_datetime(pub).astimezone(
                _tz(_td(hours=8))).strftime("%Y-%m-%d %H:%M")
        except Exception:
            started = ""

    return {"title": title, "status_word": status_word, "status_text": status_text,
            "components": components, "started": started, "link": link}


_DS_STATUS_LEVEL = {
    # feed 里的 Status 词 → (圆圈, 是否已恢复)
    "resolved": ("green", True),
    "monitoring": ("yellow", False),
    "identified": ("red", False),
    "investigating": ("red", False),
}


async def _fetch_deepseek_status(timeout: float = 8.0):
    """拉 DeepSeek 状态页 feed，解析最新一条（**不手抄，运行时读官方 feed**）。

    国内访问被三重阻断（ICP 403 / SNI 重置 / DoH 超时），所以：
      ① 国外 DNS（UDP 53 → 1.1.1.1）解析到香港节点
      ② 对该 IP 直连（SNI/Host 用真域名，证书校验正常）
      ③ feed.rss 失败再试 feed.atom；NLB 节点可用性会翻转，失败重试
    返回 dict: {title, status_word, status_text, components, started, link}；
    **失败返回 None**（调用方回退 _FAULT_DEFAULT，fail-open）。
    """
    import asyncio as _aio

    def _get() -> str:
        last = None
        for path in ("/feed.rss", "/feed.atom"):
            for _attempt in range(2):
                ips = _dns_a("status.deepseek.com", _DS_FEED_DNS, timeout=timeout)
                if not ips:
                    raise RuntimeError(format_lang("_get.m1"))
                for ip in ips:
                    try:
                        return _fetch_feed_via_ip(ip, path, timeout=timeout)
                    except Exception as e:
                        last = RuntimeError(f"{type(e).__name__}: {e} [{ip} {path}]")
        raise last or RuntimeError(format_lang("_get.m2"))

    try:
        xml = await _aio.wait_for(_aio.to_thread(_get), timeout=timeout * 2 + 4)
    except Exception as e:
        logger.warning("拉取 DeepSeek 状态 feed 失败（回退内置默认）: %s", e)
        return None

    try:
        return _parse_ds_feed(xml)
    except Exception as e:
        logger.warning("解析 DeepSeek 状态 feed 失败（回退内置默认）: %s", e)
        return None

def _build_fault_notice(level: str = "red", *, started: str = "", status: str = "",
                        components: str = "", symptom: str = "",
                        recovered: bool = False) -> str:
    """拼一条故障推送文本（按群发送）。

    level: red=不可用 / yellow=降级；recovered=True 时强制 green。
    圆圈颜色让严重程度一眼可辨；全文不用句号（用户 2026-10-15 要求）。
    """
    dot = "🟢" if recovered else _FAULT_DOT.get(level, "🔴")
    head = format_lang("_build_fault_notice.m1") if recovered \
        else format_lang("_build_fault_notice.m2")
    lines = [format_lang("_build_fault_notice.m3", dot=dot), "", head,
             format_lang("_build_fault_notice.m4"), ""]
    if started:
        lines.append(format_lang("_build_fault_notice.m5", started=started))
    if status:
        lines.append(format_lang("_build_fault_notice.m6", status=status))
    if components:
        lines.append(format_lang("_build_fault_notice.m7", components=components))
    # 恢复通知里不写「现象」——那是描述故障期的，放在"已恢复"语境下读着像还在故障
    if symptom and not recovered:
        lines.append(format_lang("_build_fault_notice.m8", symptom=symptom))
    lines += ["", format_lang("_build_fault_notice.m9") if recovered else format_lang("_build_fault_notice.m10"),
              format_lang("_build_fault_notice.m11")]
    return "\n".join(lines)


async def cmd_owner(args, user_id, group_id, sender_name, is_group, bot_qq):
    """
    配置管理 /~owner <action> ...
    
    config get/set/list  — 读写配置
    data get/set/del/reset — 管理数据文件
    wl add/remove        — 白名单快捷操作
    """
    cfg = get_config()
    if not cfg.is_admin(user_id, group_id):
        return format_lang("cmd_owner.m1")

    if not args or (len(args) == 1 and args[0].lower() == "help"):
        md = _build_owner_help_md()
        from modules.changelog import render_card_to_image
        from services.sender import send_group_msg, send_private_msg
        import uuid

        html = _build_owner_help_html(md)

        filename = f"owner_{uuid.uuid4().hex[:8]}.jpg"
        img_path = await render_card_to_image(html, filename, width=680)
        if not img_path:
            return format_lang("cmd_owner.m2")

        normalized = img_path.replace("\\", "/")
        cq = f"[CQ:image,file=file:///{normalized}]"
        if is_group:
            await send_group_msg(cq, group_id)
        else:
            await send_private_msg(cq, user_id)
        return None

    from modules import admin

    action = args[0].lower()

    # ── config list ──
    if action == "list":
        section = args[1] if len(args) > 1 else "bot"
        return admin.config_list(section)

    # ── config get ──
    if action == "get":
        if len(args) < 2:
            return format_lang("cmd_owner.m3")
        return admin.config_get(args[1])

    # ── config set ──
    if action == "set":
        if len(args) < 3:
            return format_lang("cmd_owner.m4")
        return admin.config_set(args[1], " ".join(args[2:]))

    # ── data 子命令 ──
    if action == "data":
        if len(args) < 3:
            return format_lang("cmd_owner.m5")
        sub = args[1].lower()
        if sub == "get":
            key = args[3] if len(args) > 3 else ""
            # ★ fav key 自动纠正
            if args[2] == "fav" and key and key.isdigit():
                uid = int(key)
                key = f"g{group_id}:{uid}" if is_group else f"p:{uid}"
            return admin.data_get(args[2], key)
        if sub == "set":
            if len(args) < 5:
                return format_lang("cmd_owner.m6")
            key = args[3]
            # ★ fav key 自动纠正
            if args[2] == "fav" and key.isdigit():
                uid = int(key)
                key = f"g{group_id}:{uid}" if is_group else f"p:{uid}"
                logger.info("fav key 自动纠正: %s → %s", args[3], key)
            return admin.data_set(args[2], key, " ".join(args[4:]))
        if sub == "del":
            key = args[3] if len(args) > 3 else "*"
            # ★ fav key 自动纠正
            if args[2] == "fav" and key != "*" and key.isdigit():
                uid = int(key)
                key = f"g{group_id}:{uid}" if is_group else f"p:{uid}"
            return admin.data_delete(args[2], key)
        if sub == "reset":
            return admin.data_reset(args[2])
        return format_lang("cmd_owner.m7", sub=sub)

    # ── luck 子命令 ──
    if action == "luck":
        sub = args[1].lower() if len(args) > 1 else "list"
        if sub == "list":
            return admin.luck_list()
        if sub == "set" and len(args) >= 4:
            return admin.luck_set(args[2], args[3])
        if sub == "del" and len(args) >= 3:
            return admin.luck_del(args[2])
        return format_lang("cmd_owner.m8")

    # ── files 子命令 ──
    if action == "files":
        path = args[1] if len(args) > 1 else "data/update_log.md"
        return admin.read_project_file(path)

    # ── cmd 子命令（指令白名单）──
    if action == "cmd":
        return _handle_owner_cmd(args[1:])

    # ── wl 子命令 ──
    if action == "wl":
        if len(args) < 2:
            return (
                format_lang("cmd_owner.m9")
            )
        sub = args[1].lower()
        if sub == "cmd":
            return admin.wl_cmd_manage(args[2:])
        if sub == "welcome":
            return admin.wl_welcome_manage(args[2:])
        if sub == "show":
            return admin.show_whitelists()
        if sub == "add" and len(args) >= 4:
            result = admin.whitelist_add(args[2], int(args[3]))
            if len(args) >= 5 and args[4].lower() == "atonly" and args[2] == "group":
                admin.group_set(int(args[3]), "at_only", "true")
                result += format_lang("cmd_owner.m10")
            return result
        if sub == "remove" and len(args) >= 4:
            return admin.whitelist_remove(args[2], int(args[3]))
        if sub in ("gset", "set") and len(args) >= 4 and args[2].isdigit():
            gid = int(args[2])
            mode = args[3].lower()
            if mode in ("atonly", "aton", "@仅"):
                return admin.group_set(gid, "at_only", "true")
            if mode == "default":
                return admin.group_set(gid, "at_only", "false")
            return format_lang("cmd_owner.m11", gid=gid, mode=mode)
        return format_lang("cmd_owner.m12")

    # ── wdsj 推送群管理 ──
    if action == "wdsj":
        import toml
        from pathlib import Path
        _cfg_path = Path(__file__).resolve().parent.parent / "config" / "bot_config.toml"
        if len(args) < 2:
            return (format_lang("cmd_owner.m13"))
        sub = args[1].lower()
        if sub in ("interval", "节奏"):
            data = toml.load(_cfg_path) if _cfg_path.exists() else {}
            try:
                cur = int(data.get("wdsj", {}).get("collect_interval") or 12)
            except Exception:
                cur = 12
            if cur not in (4, 12):
                cur = 12

            def _desc(h: int) -> str:
                return format_lang("_desc.m1") if h == 4 else format_lang("_desc.m2")

            if len(args) < 3 or args[2].lower() in ("show", "get"):
                return (format_lang("cmd_owner.m14", cur=cur, cur2=_desc(cur)))
            try:
                v = int(args[2])
            except ValueError:
                return format_lang("cmd_owner.m15")
            if v not in (4, 12):
                return format_lang("cmd_owner.m16")
            # ⚠️ 只改目标行、保留注释；不要用 toml.dumps 整文件重写。
            #    无需"热更新内存"：bg_tasks 的采集循环与推送都直接读这个 toml
            #    （BotConfig 也没有 .config 属性，写内存会 AttributeError）。
            _toml_write_in_section(_cfg_path, "wdsj", "collect_interval", str(v))
            return (format_lang("cmd_owner.m17", v=v, v2=_desc(v)))
        if sub == "show" or sub == "groups":
            data = toml.load(_cfg_path) if _cfg_path.exists() else {}
            gs = data.get("wdsj", {}).get("target_groups", [])
            if gs:
                return format_lang("cmd_owner.m18", gs=', '.join((str(g) for g in gs)))
            return format_lang("cmd_owner.m19")
        if sub == "set":
            if len(args) < 3:
                return format_lang("cmd_owner.m20")
            gs = [int(x.strip()) for x in args[2].split(",") if x.strip().isdigit()]
            arr = "[" + ", ".join(str(g) for g in gs) + "]"
            # ⚠️ 只改目标行、保留注释；不要用 toml.dumps 整文件重写（无需写内存，见上）
            _toml_write_in_section(_cfg_path, "wdsj", "target_groups", arr)
            return format_lang("cmd_owner.m21", gs=', '.join((str(g) for g in gs)))
        if sub == "clear":
            _toml_write_in_section(_cfg_path, "wdsj", "target_groups", None)
            return format_lang("cmd_owner.m22")
        return format_lang("cmd_owner.m23", sub=sub)

    # ── 故障推送管理（按群开关）────────────────────────────────────
    # 说明：目前只有 DeepSeek 提供 RSS/状态页，其他服务商无从自动判定，
    #      所以监控范围仅限 DeepSeek；这里是"手动触发 + 按群开关"。
    if action == "fault":
        if len(args) < 2:
            return (format_lang("cmd_owner.m24"))
        sub = args[1].lower()
        # ⚠️ 必须在本分支内**自己导入**：cmd_owner 里 wdsj 分支有 `import toml` 和
        #    `from pathlib import Path`，这会让 toml / Path 成为**整个函数的局部变量**；
        #    直接在本分支用会 UnboundLocalError（已踩过两次）。用别名彻底绕开。
        import toml as _toml
        from pathlib import Path as _P
        _cfg_path = _P(__file__).resolve().parent.parent / "config" / "bot_config.toml"

        def _fault_groups() -> list:
            try:
                d = _toml.load(_cfg_path) if _cfg_path.exists() else {}
            except Exception:
                return []
            return [int(g) for g in (d.get("fault", {}).get("notify_groups") or [])
                    if str(g).strip().lstrip("-").isdigit()]

        if sub == "show":
            gs = _fault_groups()
            if gs:
                return format_lang("cmd_owner.m25", gs=', '.join((str(g) for g in gs)))
            return format_lang("cmd_owner.m26")

        if sub in ("on", "off"):
            if len(args) < 3 or not args[2].strip().isdigit():
                return format_lang("cmd_owner.m27", sub=sub)
            g = int(args[2].strip())
            gs = _fault_groups()
            if sub == "on":
                if g in gs:
                    return format_lang("cmd_owner.m28", g=g)
                gs.append(g)
            else:
                if g not in gs:
                    return format_lang("cmd_owner.m29", g=g)
                gs = [x for x in gs if x != g]
            # ⚠️ 只改目标行、保留注释；不要用 toml.dumps 整文件重写
            _toml_write_in_section(_cfg_path, "fault", "notify_groups",
                                   "[" + ", ".join(str(x) for x in gs) + "]" if gs else None)
            return format_lang("cmd_owner.fault_push",
                               state=(format_lang("fault.state_on") if sub == "on"
                                      else format_lang("fault.state_off")), g=g)

        if sub == "test":
            # ⚠️ 同样必须用别名：cmd_owner 的 help 分支（约 3284 行）有
            #    `from services.sender import send_group_msg`，这会让它成为整个函数的
            #    局部变量，本分支直接用会 UnboundLocalError（toml / Path 同理会炸）。
            from services.sender import send_group_msg as _send_group_msg
            if len(args) > 2 and args[2].strip().isdigit():
                gid = int(args[2].strip())
            elif is_group:
                gid = group_id
            else:
                return format_lang("cmd_owner.m30")
            # ★ 运行时直接读官方 feed（不手抄状态），拉取失败才回退 _FAULT_DEFAULT
            info = await _fetch_deepseek_status()
            if info:
                lvl, recovered = _DS_STATUS_LEVEL.get(info["status_word"], ("red", False))
                txt = _build_fault_notice(
                    lvl, started=info["started"], status=info["status_text"],
                    components=info["components"], symptom=_FAULT_DEFAULT["symptom"],
                    recovered=recovered)
            else:
                txt = _build_fault_notice("red", **_FAULT_DEFAULT)
            try:
                await _send_group_msg(txt, gid)
            except Exception as e:
                return format_lang("cmd_owner.m31", gid=gid, e=e)
            return format_lang("cmd_owner.m32", gid=gid)

        return format_lang("cmd_owner.m33", sub=sub)

    # ── draw/video 配额管理 ──
    if action in ("draw", "video"):
        from core.plugin.cores import load_plugin_core
        _core_agnes = load_plugin_core("agnes")
        owner_quota_get = _core_agnes.owner_quota_get
        owner_quota_set = _core_agnes.owner_quota_set
        owner_quota_reset = _core_agnes.owner_quota_reset
        if len(args) < 2:
            return format_lang("cmd_owner.m34", action=action, action2=action, action3=action, action4=action)
        sub = args[1].lower()
        if sub == "get":
            qq = args[2] if len(args) > 2 else ""
            return owner_quota_get(action, qq)
        if sub == "set":
            if len(args) < 4:
                return format_lang("cmd_owner.m35", action=action)
            owner_quota_set(action, args[2], int(args[3]))
            return format_lang("cmd_owner.m36", args=args[2], action=action, args2=args[3])
        if sub == "reset":
            owner_quota_reset(action)
            return format_lang("cmd_owner.m37", action=action)
        return format_lang("cmd_owner.m38", action=action, sub=sub)

    # ── 兼容旧指令 ──
    if action in ("glist", "gadd", "gremove", "padd", "premove", "gset"):
        return await _owner_legacy(args, action, admin)

    return format_lang("cmd_owner.m39", action=action)


# ─── 记忆查询 ────────────────────────────────────────────────

async def cmd_memory(args, user_id, group_id, sender_name, is_group, bot_qq):
    """
    三层记忆查询 /~memory [瞬时|短时|长时|搜索 <关键词>]
    """
    from modules import stm
    from modules.memory import read_long_memory, search_long_memory

    chat_id = group_id if is_group else user_id

    if not args:
        return (
            format_lang("cmd_memory.m1")
        )

    action = args[0].lower()

    if action == "working":
        from core.context_manager import get_context
        ctx = get_context()
        msgs = ctx.get_history(chat_id, 15)
        if not msgs:
            return format_lang("cmd_memory.m2")
        lines = [format_lang("cmd_memory.m3", msgs=len(msgs))]
        for m in msgs:
            lines.append(f"  [{m.get('tag','?')}] {m.get('sender','')}: {m.get('content','')[:80]}")
        return "\n".join(lines)

    if action in ("short", "stm", "短时"):
        return stm.summarize(chat_id)

    if action in ("long", "ltm", "长时"):
        return read_long_memory(chat_id)

    if action == "search":
        if len(args) < 2:
            return format_lang("cmd_memory.m4")
        kw = " ".join(args[1:])
        s = stm.search(chat_id, kw)
        l = search_long_memory(chat_id, kw)
        parts = []
        if s:
            parts.append(format_lang("cmd_memory.m5"))
            for e in s:
                t = __import__("time").strftime("%H:%M", __import__("time").localtime(e["time"]))
                parts.append(f"  [{t}] {e['content'][:100]}")
        if l:
            if parts: parts.append("")
            parts.append(format_lang("cmd_memory.m6"))
            parts.append(l)
        return "\n".join(parts) if parts else format_lang("cmd_memory.m7", kw=kw)

    if action == "clear":
        return stm.clear(chat_id)

    return format_lang("cmd_memory.m8", action=action)


# --- 昵称同步 ---

async def cmd_nickname(args, user_id, group_id, sender_name, is_group, bot_qq):
    if not args or args[0].lower() != 'update':
        return format_lang("cmd_nickname.m1")
    from modules.nickname_sync import sync_and_report
    chat_id = group_id if is_group else user_id
    return await sync_and_report(chat_id=chat_id, is_group=is_group)


# --- 好友请求审批 ---

async def cmd_friend_add(args, user_id, group_id, sender_name, is_group, bot_qq):
    """/#添加 [wl] — 批准好友请求，可选加白名单"""
    from modules.friend_request import get_latest_pending, approve_request

    req = get_latest_pending()
    if not req:
        return format_lang("cmd_friend_add.m1")

    add_wl = args and args[0].lower() == "wl"
    return await approve_request(req["flag"], add_whitelist=add_wl)


async def cmd_friend_reject(args, user_id, group_id, sender_name, is_group, bot_qq):
    """/#拒绝 — 拒绝好友请求"""
    from modules.friend_request import get_latest_pending, reject_request

    req = get_latest_pending()
    if not req:
        return format_lang("cmd_friend_reject.m1")

    return await reject_request(req["flag"])


async def cmd_friend_list(args, user_id, group_id, sender_name, is_group, bot_qq):
    """/#好友列表 — 查看所有待处理好友请求"""
    from modules.friend_request import list_pending
    return list_pending()


async def _owner_legacy(args, action, admin):
    """兼容旧版 owner 子命令"""
    if action == "glist":
        return admin.show_whitelists()
    if action == "gadd" and len(args) >= 2 and args[1].isdigit():
        return admin.whitelist_add("group", int(args[1]))
    if action == "gremove" and len(args) >= 2 and args[1].isdigit():
        return admin.whitelist_remove("group", int(args[1]))
    if action == "padd" and len(args) >= 2 and args[1].isdigit():
        return admin.whitelist_add("private", int(args[1]))
    if action == "premove" and len(args) >= 2 and args[1].isdigit():
        return admin.whitelist_remove("private", int(args[1]))
    if action == "gset" and len(args) >= 2 and args[1].isdigit():
        gid = int(args[1])
        sub = args[2].lower() if len(args) > 2 else ""
        if not sub or sub == "show":
            return admin.config_get(f"adapter.group_settings.{gid}")
        if sub == "reply" and len(args) > 3 and args[3].isdigit():
            return admin.group_set(gid, "reply_threshold", args[3])
        if sub == "atonly":
            return admin.group_set(gid, "at_only", "true")
        if sub == "default":
            return admin.config_set(f"adapter.group_settings.{gid}", "null")
    return format_lang("_owner_legacy.m1", action=action)

async def cmd_restart(args, user_id, group_id, sender_name, is_group, bot_qq):
    """远程重启 /~restart"""
    from core.config import get_config
    cfg = get_config()
    if not cfg.is_admin(user_id, group_id if is_group else 0):
        return format_lang("cmd_restart.m1")
    from services.sender import send_group_msg, send_private_msg
    if is_group:
        await send_group_msg(format_lang("cmd_restart.m2"), group_id)
    else:
        await send_private_msg(format_lang("cmd_restart.m3"), user_id)
    import os
    os._exit(0)


# ════════════════════════════════════════════════════════════
#  指令注册表 & 分发器
# ════════════════════════════════════════════════════════════

async def cmd_sys(args, user_id, group_id, sender_name, is_group, bot_qq):
    """/~sys [card|shot|shotdesk] — PC 状态/截屏"""
    from services.pc_status import build_sys_card_html, format_pc_status, request_screenshot
    from modules.changelog import render_card_to_image
    from services.sender import send_group_msg, send_private_msg
    from core.config import get_config
    import uuid, base64, tempfile

    sub = (args[0].lower() if args else "")
    # ★ v2.3.24 通用化: owner 名不再硬编码 "Trusler"，取 roles.toml 的 qq_name_map
    #   （admin_qq 对应的昵称），换部署改配置即可，不用动代码
    _cfg = get_config()
    owner = _cfg.get_display_name(_cfg.admin_qq) or format_lang("cmd_sys.m1")

    # 截屏
    if sub in ("shot", "shotdesk"):
        # ★ v2.3.24: 截屏最长等 30s，先回执避免用户以为没反应（原来全程静默）
        await (send_group_msg(format_lang("cmd_sys.m2"), group_id) if is_group
               else send_private_msg(format_lang("cmd_sys.m3"), user_id))
        b64 = await request_screenshot(timeout=30.0)
        if not b64:
            return format_lang("cmd_sys.m4")
        from pathlib import Path as _Path
        tmp = _Path(__file__).resolve().parent.parent / "data" / "img_temp" / f"shot_{uuid.uuid4().hex[:8]}.jpg"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        import binascii
        try:
            data = binascii.a2b_base64(b64)
        except Exception as e:
            return format_lang("cmd_sys.m5", e=e)
        tmp.write_bytes(data)
        cq = f"[CQ:image,file=file://{tmp.as_posix()}]"
        if is_group:
            await send_group_msg(cq, group_id)
        else:
            await send_private_msg(cq, user_id)
        return None

    # HTML 卡片
    if sub == "card":
        html = build_sys_card_html(owner=owner, bot_name=_cfg.bot_name)
        if html:
            filename = f"sys_{uuid.uuid4().hex[:8]}.jpg"
            img_path = await render_card_to_image(html, filename, width=760)
            if img_path:
                normalized = img_path.replace("\\", "/")
                cq = f"[CQ:image,file=file:///{normalized}]"
                if is_group:
                    await send_group_msg(cq, group_id)
                else:
                    await send_private_msg(cq, user_id)
                return None
        return format_pc_status(owner=owner)

    # 默认：纯文字
    return format_pc_status(owner=owner)


async def cmd_phone(args, user_id, group_id, sender_name, is_group, bot_qq):
    """/~phone [status] — 手机实时状态（由配套 App 经 TCP 长连接上报）"""
    from services.phone_status import format_phone_status
    sub = (args[0].lower() if args else "")
    # 预留子命令扩展（如 card 卡片），当前仅返回文本
    return format_phone_status()


async def cmd_dbsearch(args, user_id, group_id, sender_name, is_group, bot_qq):
    """检索聊天历史 /~回顾 <关键词> — 基于 SQLite 全文索引（ADDITIVE）"""
    try:
        from db.store import get_search_store
    except Exception:
        return format_lang("cmd_dbsearch.m1")
    store = get_search_store()
    if not store.available:
        return format_lang("cmd_dbsearch.m2")
    if not args:
        c = store.count()
        return format_lang("cmd_dbsearch.m3", c=c)
    query = " ".join(args)
    chat_id = group_id if is_group else None
    rows = store.search_messages(query, chat_id=chat_id, limit=8)
    if not rows:
        return format_lang("cmd_dbsearch.m4", query=query)
    lines = [format_lang("cmd_dbsearch.m5", query=query, rows=len(rows))]
    for r in rows:
        name = r.get("name") or str(r.get("user_id", "?"))
        ts = r.get("ts") or 0
        try:
            tstr = time.strftime("%m-%d %H:%M", time.localtime(ts))
        except Exception:
            tstr = ""
        content = str(r.get("content", ""))[:80].replace("\n", " ")
        lines.append(f"  [{tstr}] {name}: {content}")
    return "\n".join(lines)


async def cmd_plugin(args, user_id, group_id, sender_name, is_group, bot_qq):
    """/~plugin — 插件管理：list/status/install/unload/reload/pack/import/update"""
    try:
        from core.plugin import get_plugin_manager
        from modules import plugin_share as ps
    except Exception as e:
        return format_lang("cmd_plugin.m1", e=e)
    mgr = get_plugin_manager()

    if not args or args[0].lower() in ("list", "ls", "status"):
        mgr.discover()
        rows = mgr.list()
        if not rows:
            lines = [format_lang("cmd_plugin.m2")]
        else:
            lines = [format_lang("cmd_plugin.m3")]
            for r in rows:
                mark = {"enabled": "✅", "disabled": "⏸️", "error": "❌", "loaded": "📦", "discovered": "🗂️"}.get(r["state"], "•")
                line = f"  {mark} {r['name']}@{r['version']} [{r['state']}]"
                if r.get("error"):
                    line += f" err={r['error']}"
                lines.append(line)
        lines.append(format_lang("cmd_plugin.m4"))
        return "\n".join(lines)

    sub = args[0].lower()
    rest = args[1:]

    async def _install_from(hmp_path, overwrite: bool) -> str:
        ok, msg, conflict = ps.unpack_hmp(hmp_path, overwrite=overwrite)
        if not ok:
            if conflict:
                name = conflict.get("name")
                await mgr.unload(name) if name else None
                ok2, msg2, _ = ps.unpack_hmp(hmp_path, overwrite=True)
                if not ok2:
                    return msg2
                ok3, msg3 = await ps.load_local_plugin(name)
                return msg3
            return msg
        name = conflict["name"] if conflict else ps.peek_hmp_name(hmp_path)
        if not name:
            return format_lang("_install_from.m1")
        ok3, msg3 = await ps.load_local_plugin(name)
        return msg3

    if sub in ("install", "装"):
        if not rest:
            return format_lang("cmd_plugin.m5")
        target = rest[0]
        if ps.is_hmp_url(target):
            ok, msg = ps.download_hmp(target)
            if not ok:
                return msg
            target = str(ps._down_dir() / ps.local_filename_for(target))
            return await _install_from(ps._down_dir() / ps.local_filename_for(target), overwrite=False)
        # 本地 _down 已有同名包？
        local = ps._down_dir() / f"{target}{ps.HMP_EXT}"
        if local.is_file():
            return await _install_from(local, overwrite=False)
        # 走插件库
        ok, info, err = ps.lib_latest(target)
        if not ok:
            return format_lang("cmd_plugin.m6", err=err)
        url = ps.lib_download_url(target)
        ok, msg = ps.download_hmp(url)
        if not ok:
            return msg
        return await _install_from(ps._down_dir() / ps.local_filename_for(url), overwrite=False)

    if sub in ("import", "导入"):
        if not rest or not ps.is_hmp_url(rest[0]):
            return format_lang("cmd_plugin.m7")
        url = rest[0]
        ok, msg = ps.download_hmp(url)
        if not ok:
            return msg
        return await _install_from(ps._down_dir() / ps.local_filename_for(url), overwrite=False)

    if sub in ("unload", "卸"):
        if not rest:
            return format_lang("cmd_plugin.m8")
        ok, msg = await mgr.unload(rest[0])
        return msg if ok else format_lang("cmd_plugin.m9", msg=msg)

    if sub in ("reload", "重载"):
        if not rest:
            return format_lang("cmd_plugin.m10")
        ok, msg = await mgr.reload(rest[0])
        return msg if ok else format_lang("cmd_plugin.m11", msg=msg)

    if sub in ("pack", "打包"):
        if not rest:
            return format_lang("cmd_plugin.m12")
        ok, msg, path = ps.pack_plugin(rest[0])
        if ok and path:
            try:
                from services.sender import send_file
                await send_file(str(path), group_id if is_group else user_id, is_group)
                return format_lang("cmd_plugin.m13", msg=msg)
            except Exception:
                return msg
        return msg

    if sub in ("update", "更新"):
        if not rest:
            # 列出可更新项
            mgr.discover()
            lines = [format_lang("cmd_plugin.m14")]
            for r in mgr.list():
                ok, info, err = ps.lib_latest(r["name"])
                if ok:
                    remote = str(info.get("version") or "0.0.0")
                    local = r.get("version") or "0.0.0"
                    if ps.compare_versions(remote, local) > 0:
                        lines.append(f"  ⬆️ {r['name']}: {local} → {remote}（/~plugin update {r['name']}）")
                    else:
                        lines.append(format_lang("cmd_plugin.m15", name=r['name'], local=local))
                else:
                    lines.append(format_lang("cmd_plugin.m16", name=r['name'], err=err))
            return "\n".join(lines)
        name = rest[0]
        ok, info, err = ps.lib_latest(name)
        if not ok:
            return format_lang("cmd_plugin.m17", err=err)
        url = ps.lib_download_url(name)
        ok, msg = ps.download_hmp(url)
        if not ok:
            return msg
        hmp = ps._down_dir() / ps.local_filename_for(url)
        await mgr.unload(name)
        ok, msg, _ = ps.unpack_hmp(hmp, overwrite=True)
        if not ok:
            return msg
        ok, msg = await ps.load_local_plugin(name)
        return msg

    return format_lang("cmd_plugin.m18", sub=sub)


async def cmd_apy(args, user_id, group_id, sender_name, is_group, bot_qq):
    """/~apy <token> 同意|拒绝 — 响应插件人工审批"""
    try:
        from core.config import get_config
        if not get_config().is_admin(user_id, group_id):
            return format_lang("cmd_apy.m1")
        if len(args) < 2:
            return format_lang("cmd_apy.m2")
        from core.plugin.api import resolve_approval
        approved = args[1] in ("同意", "yes", "y", "1", "approve", "ok")
        return resolve_approval(args[0], approved)
    except Exception as e:
        return format_lang("cmd_apy.m3", e=e)


# ════════════════════════════════════════════════════════════
#  实验测试项开关 /~key
# ════════════════════════════════════════════════════════════
async def cmd_key(args, user_id, group_id, sender_name, is_group, bot_qq):
    """实验测试项开关（全员可用，非管理员需先兑换许可码）

    用法:
      /~key                      列出全部测试项与当前状态
      /~key 兑换 <许可码>         兑换许可码换取使用权
      /~key <code>               激活该测试项
      /~key <code> off           恢复默认行为（关闭）
      /~key <code> reset         恢复为注册表默认值
    """
    cfg = get_config()
    is_admin = cfg.is_admin(user_id, group_id)

    from modules import licenses
    from modules.features import FEATURES, list_features, resolve, reset, set_enabled

    # ── 兑换许可码（任何人可兑换，不需要先有权限）──
    if args and args[0] in ("兑换", "激活码", "许可", "redeem", "cdk"):
        if len(args) < 2:
            return format_lang("cmd_key.m1")
        res = licenses.redeem(args[1], user_id)
        if not res.get("ok"):
            return res.get("msg", format_lang("common.redeem_failed"))
        return (
            format_lang("cmd_key.m2", msg=res['msg'], desc=res['desc'])
        )

    # ── 权限校验：管理员免码，其他人必须有有效许可 ──
    if not is_admin:
        st = licenses.check(user_id)
        if not st.get("ok"):
            reason = {
                "expired": "你的许可已经过期了",
                "used_up": "你的许可次数用完了",
            }.get(st.get("reason"), "你还没有许可码")
            kind = "、".join(f'{v["label"]}（{v["desc"]}）' for v in licenses.TYPES.values())
            return (
                format_lang("cmd_key.m3", reason=reason, kind=kind)
            )

    # 无参 → 列表
    if not args:
        items = list_features()
        if not items:
            return format_lang("cmd_key.m4")
        lines = [format_lang("cmd_key.m5")]
        for it in items:
            code = it["code"]
            state = format_lang("cmd_key.m6") if it["on"] else format_lang("cmd_key.m7")
            mark = "" if it["default"] else format_lang("cmd_key.m8")
            alias = "/".join(it["aliases"][:3])
            lines.append(f"· {code}（{alias}）— {state}{mark}")
            lines.append(f"     {it['desc']}")
            if not it["on"]:
                lines.append(format_lang("cmd_key.m9", affects=it['affects']))
        if not is_admin:
            lines.append("")
            lines.append(format_lang("cmd_key.m10", user_id=licenses.describe(user_id)))
        lines.append("")
        lines.append(format_lang("cmd_key.m11"))
        return "\n".join(lines)

    code = resolve(args[0])
    if not code:
        known = "、".join(FEATURES.keys())
        return format_lang("cmd_key.m12", known=known)

    action = (args[1].lower() if len(args) > 1 else "on")
    meta = FEATURES[code]

    if action in ("on", "开", "启用", "激活", "1", "true"):
        set_enabled(code, True)
        tail = ""
        if not is_admin:
            licenses.consume(user_id)     # 一次性许可在这里扣次数
            tail = f"\n（{licenses.describe(user_id)}）"
        return format_lang("cmd_key.m13", code=code, desc=meta['desc'], tail=tail, code2=code)
    if action in ("off", "关", "关闭", "恢复", "0", "false"):
        set_enabled(code, False)
        tail = ""
        if not is_admin:
            licenses.consume(user_id)
            tail = f"\n（{licenses.describe(user_id)}）"
        return format_lang("cmd_key.m14", code=code, affects=meta['affects'], tail=tail)
    if action in ("reset", "重置", "默认"):
        reset(code)
        default_state = format_lang("cmd_key.m15") if meta.get("default") else format_lang("cmd_key.m16")
        return format_lang("cmd_key.m17", code=code, default_state=default_state)

    return format_lang("cmd_key.m18", action=action, code=code, code2=code, code3=code)


async def cmd_slang(args, user_id, group_id, sender_name, is_group, bot_qq):
    """/~slang — 黑话查询与待审队列（/~slang <词> | pending | ok N | no N）"""
    from services import slang_lookup as SL

    sub = (args[0].strip() if args else "").lower()

    if not sub or sub in ("help", "帮助"):
        return (
            "黑话查证（/~slang）\n"
            "· /~slang <词>  查这个词（本地词库 → 萌娘 → 联网词源）\n"
            "· /~slang pending  看待审队列（bot 自己查到的结论）\n"
            "· /~slang ok <序号>  收录进词库（仅管理员）\n"
            "· /~slang no <序号>  丢弃（仅管理员）\n"
            f"当前待审 {SL.pending_count()} 条"
        )

    if sub in ("pending", "待审", "列表"):
        return SL.format_pending(10)

    if sub in ("ok", "yes", "收录", "过"):
        if not get_config().is_admin(user_id):
            return "只有主人能审词库喵~"
        try:
            idx = int(args[1])
        except (IndexError, ValueError):
            return "用法：/~slang ok <序号>（序号看 /~slang pending）"
        ok, msg = SL.approve(idx)
        return msg

    if sub in ("no", "reject", "丢弃", "删"):
        if not get_config().is_admin(user_id):
            return "只有主人能审词库喵~"
        try:
            idx = int(args[1])
        except (IndexError, ValueError):
            return "用法：/~slang no <序号>"
        ok, msg = SL.reject(idx)
        return msg

    # 其余情况：把剩下的参数当词查（支持带空格的说法，取整段）
    term = " ".join(args).strip()
    try:
        res = await SL.explain(term)
    except Exception as e:
        logger.error("查黑话失败: %s", e, exc_info=True)
        return f"查「{term}」失败了喵~ {e}"
    return SL.format_explain(res)


async def cmd_motd(args, user_id, group_id, sender_name, is_group, bot_qq):
    """/~motd <服务器地址>[:端口] — Minecraft 服务器状态卡（原生协议 ping + HTML 渲染）"""
    if not args:
        return (format_lang("cmd_motd.m1"))

    address = args[0].strip()
    port_arg = int(args[1]) if len(args) > 1 and args[1].isdigit() else None
    if len(address) > 100 or "/" in address or " " in address:
        return format_lang("cmd_motd.m2")

    from services.sender import send_group_msg, send_private_msg
    await (send_group_msg(format_lang("cmd_motd.m3", address=address), group_id) if is_group
           else send_private_msg(format_lang("cmd_motd.m4", address=address), user_id))

    from services.motd_card import make_card
    try:
        png, status, err = await asyncio.wait_for(make_card(address, port_arg), timeout=30)
    except asyncio.TimeoutError:
        return format_lang("cmd_motd.m5")
    except Exception as e:
        import traceback
        logger.warning("motd 卡生成异常: %s", e)
        # ★ 报错必须带完整 traceback（用户 2026-10-03 明确：全栈扔出来，行级定位）
        return format_lang("cmd_motd.m6", format_exc=traceback.format_exc())

    if not png:
        return (format_lang("cmd_motd.m7") + address + format_lang("cmd_motd.m8") + err)

    cq = f"[CQ:image,file=file:///{png.as_posix()}]"
    await (send_group_msg(cq, group_id) if is_group else send_private_msg(cq, user_id))

    # 延迟清理临时图（发送端上传可能较慢，给足时间）
    async def _clean():
        await asyncio.sleep(120)
        try:
            if png.exists():
                png.unlink()
        except Exception:
            pass
    asyncio.create_task(_clean())
    return None


async def cmd_run(args, user_id, group_id, sender_name, is_group, bot_qq,
                  raw_message: str = "", cmd_text: str = ""):
    """/~run <py|cpp|sh|描述> — 沙箱真实执行代码并返回运行输出（移植自 KOOK .run）"""
    import re as _re
    import tempfile as _tf
    import zipfile as _zip
    from pathlib import Path as _Path
    from core.config import get_config
    cfg = get_config()

    # 还原完整原始文本（多行代码保真）：优先 cmd_text（管道路由的完整指令文本，
    # 实测带换行）；raw_message（NapCat CQ 串）生产环境可能丢换行，只作回退
    raw = _re.sub(r"\[CQ:[^\]]*\]", "", cmd_text or "").strip()
    m = _re.match(r"^(?:/~|/#)?run(?:\s+|$)", raw)
    if not m:
        raw = _re.sub(r"\[CQ:[^\]]*\]", "", raw_message or "").strip()
        m = _re.match(r"^(?:/~|/#)?run(?:\s+|$)", raw)
    rest = raw[m.end():].strip() if m else " ".join(args)

    m2 = _re.match(r"^(py|python|cpp|c\+\+|sh|shell|bash)(?:\s+|$)", rest, _re.I)
    lang = m2.group(1).lower() if m2 else None
    if lang in ("python",):
        lang = "py"
    elif lang in ("c++",):
        lang = "cpp"
    elif lang in ("shell", "bash"):
        lang = "sh"
    if m2:
        rest = rest[m2.end():].strip()

    if not rest:
        return (format_lang("cmd_run.m1"))

    # sh 终端命令仅管理员（与 KOOK 一致）
    if lang == "sh" and not cfg.is_admin(user_id, group_id):
        return format_lang("cmd_run.m2")

    # ── 无语言前缀 → 按描述生成代码再运行 ──
    if lang is None:
        from services.llm import call_llm
        gen = await call_llm(
            model_cfg=cfg.reply_model,
            messages=[
                {"role": "system", "content": format_lang("cmd_run.m3")},
                {"role": "user", "content": rest[:3000]},
            ],
            max_tokens=2000, temperature=0.3, timeout=60.0,
        )
        if not gen or not gen.strip():
            return format_lang("cmd_run.m4")
        code = _re.sub(r"^```\w*\n?", "", gen.strip())
        code = _re.sub(r"\n?```$", "", code).strip()
        lang, rest = "py", code

    from core.sandbox import run_python, compile_and_run_cpp, run_shell, collect_artifacts, cleanup
    from services.sender import send_group_msg, send_private_msg

    tmp = _Path(_tf.mkdtemp(prefix="bot_run_"))
    try:
        if lang == "py":
            res = await run_python(rest, cwd=tmp, max_output=4000, timeout=15)
        elif lang == "cpp":
            res = await compile_and_run_cpp({"main.cpp": rest}, cwd=tmp, max_output=4000, timeout=15)
        else:
            res = await run_shell(rest, cwd=tmp, max_output=4000, timeout=15)
    except Exception as e:
        logger.warning("/~run 执行异常: %s", e)
        cleanup(tmp)
        return format_lang("cmd_run.sandbox_fail",
                           etype=type(e).__name__, emsg=str(e)[:200])

    out_parts = []
    if res.get("stdout"):
        out_parts.append(res["stdout"].strip())
    if res.get("stderr"):
        out_parts.append(f"[stderr]\n{res['stderr'].strip()}")
    text = "\n\n".join(out_parts) or format_lang("cmd_run.m5")
    if res.get("timed_out"):
        text += format_lang("cmd_run.m6")
    if res.get("returncode") not in (0, None):
        text += format_lang("cmd_run.m7", returncode=res['returncode'])

    # ── 产物收集与发送（CQ file）——失败/超时不发产物（编译失败的残留文件没有意义）──
    send_msgs = []
    failed = bool(res.get("timed_out")) or (res.get("returncode") not in (0, None))
    artifacts = [] if failed else collect_artifacts(tmp)
    _archives = [a for a in artifacts if a.suffix.lower() in (".zip", ".tar", ".gz", ".tgz", ".7z", ".rar", ".bz2")]
    if _archives:
        artifacts = _archives
    if artifacts:
        try:
            files_to_send = artifacts[:5]
            if len(artifacts) > 1:
                zip_path = tmp / "artifacts.zip"
                with _zip.ZipFile(str(zip_path), "w", _zip.ZIP_DEFLATED) as zf:
                    for f in artifacts:
                        zf.write(str(f), f.name)
                files_to_send = [zip_path]
            for f in files_to_send:
                cq = f"[CQ:file,file=file:///{f.as_posix()},name={f.name}]"
                await (send_group_msg(cq, group_id) if is_group else send_private_msg(cq, user_id))
            send_msgs.append(format_lang("cmd_run.m8", files_to_send=len(files_to_send)))
        except Exception as e:
            send_msgs.append(format_lang("cmd_run.m9", e=e))
    cleanup(tmp)

    result = format_lang("cmd_run.m10", text=text)
    if send_msgs:
        result += "\n\n" + "\n".join(send_msgs)
    logger.info("/~run 完成: lang=%s rc=%s", lang, res.get("returncode"))
    return result


def _lazy(plugin: str, attr: str):
    """按需加载插件核心里的指令 handler（2026-10-26）。

    已迁入插件的内置模块不再在 modules/ 留文件，但要兼容 COMMAND_MAP 里按名调用，
    这里返回一个同签名的 async 代理，第一次调用时才 import 插件核心。
    """
    async def _call(args, user_id, group_id, sender_name, is_group, bot_qq):
        from core.plugin.cores import load_plugin_core
        fn = getattr(load_plugin_core(plugin), attr)
        return await fn(args, user_id, group_id, sender_name, is_group, bot_qq)
    _call.__name__ = f"_lazy_{plugin}_{attr}"
    return _call


COMMAND_MAP: dict[str, callable] = {
    "help":       cmd_help,
    "restart":    cmd_restart,
    "favlist":    cmd_favlist,
    "info":       cmd_info,
    "search":     cmd_search,  # LLM CALL 调用用
    "search_web":  cmd_search,  # FC 工具名别名
    "s":          cmd_search,  # 兼容 lang.toml 帮助文本中的 /~s 用法
    "read":       cmd_read,
    "whois":      cmd_whois,   # ★ 域名 WHOIS 查询
    "域名":        cmd_whois,
    "write_code":  cmd_write_code,  # ★ 代码生成
    "ignore":      cmd_ignore,      # ★ 忽略用户
    "unignore":    cmd_unignore,    # ★ 解除忽略
    "reload":     cmd_reload,
    "update":     _cmd_update,
    "upd":        _cmd_update,    # 短别名
    "添加关系":   cmd_add_relation,
    "resetfav":   cmd_reset_fav,
    "updateinfo": cmd_update_info,
    "up":         cmd_update_info,
    "box":        cmd_box,
    "testsys":    cmd_testsys,
    "testok":     cmd_testok,
    "jsonraw":    cmd_jsonraw,
    "md":         cmd_md,
    "draw":       cmd_draw,
    "绘画":       cmd_draw,
    "video":      cmd_video,
    "视频":       cmd_video,
    "img2video":  cmd_img2video,
    "图生视频":   cmd_img2video,
    "img":        cmd_img,
    # img18 已移除注册（R18 指令下线），cmd_img18 逻辑保留待后续启用
    "eq":         cmd_eq,
    "地震":       cmd_eq,
    "luck":       cmd_luck,
    "op":         cmd_op,        # ★ OP 权限管理
    "persona":    cmd_persona,   # ★ 私聊人格
    "人格":       cmd_persona,
    "主人":       cmd_master,    # ★ 私聊主人
    "sleep":      cmd_sleep,     # ★ 睡觉模式
    "含蓄":       cmd_hanxu,     # ★ 含蓄叙述风格
    "叙事":       cmd_hanxu,
    "recall":     cmd_recall,
    "remind":     cmd_remind,
    "提醒":       cmd_remind,
    "抽":         cmd_chou,
    "stats":      cmd_stats,
    "统计":       cmd_stats,
    "unstats":    cmd_unstats,
    "setstats":   cmd_setstats,
    "leave":      cmd_leave,
    "preset":     cmd_preset,
    "analyze":    cmd_analyze,
    "owner":      cmd_owner,
    "memory":     cmd_memory,
    "nickname":   cmd_nickname,
    "添加":       cmd_friend_add,
    "拒绝":       cmd_friend_reject,
    "好友列表":   cmd_friend_list,
    "watch":      cmd_watch,
    "spec":       cmd_watch,
    "观战":       cmd_watch,
    "wzq":        cmd_wzq,
    "五子棋":     cmd_wzq,
    "xq":         cmd_xq,
    "象棋":       cmd_xq,
    "go":         cmd_go,
    "围棋":       cmd_go,
    "tr":         cmd_translate,
    "翻译":       cmd_translate,
    "countdown":  cmd_countdown,
    "倒计时":     cmd_countdown,
    "wdsj":       cmd_wdsj,
    "balance":    cmd_balance,
    "cost":       cmd_cost,
    "tokens":     cmd_tokens,
    "cache":      cmd_cache,
    "ctx":        cmd_ctx,
    "note":       cmd_note,
    "tuflevel":   cmd_tuflevel,
    "tuf谱面":  cmd_tuflevel,
    "tufsearch":  cmd_tuf_search,
    "tufd":       cmd_tufd,
    "tufpage":    cmd_tufpage,
    "sys":        cmd_sys,
    "pc":         cmd_sys,
    "phone":      cmd_phone,
    # ── SQLite 全文检索（聊天回溯）──
    "dbsearch":   cmd_dbsearch,
    "回顾":       cmd_dbsearch,
    # ── 黑话查证 / 待审词库 ──
    "slang":      cmd_slang,
    "梗":         cmd_slang,
    "黑话":       cmd_slang,
    # ── 插件系统 ──
    "plugin":     cmd_plugin,
    "插件":       cmd_plugin,
    "apy":        cmd_apy,          # 插件审批回执
    # ── 管理员代发（/~say）──
    # ── 实验测试项开关（仅管理员）──
    "key":        cmd_key,
    "测试项":     cmd_key,
    # ── 跨聊天记忆关联（单一英文入口 + 中文别名）──
    "mlink":      cmd_mlink,
    "记忆关联":   cmd_mlink,
    # ── 赞赏码 / 赞助名单 ──
    "reward":     _lazy("reward", "cmd_reward"),
    "赞赏":       _lazy("reward", "cmd_reward"),
    "赞助":       _lazy("reward", "cmd_reward"),
    "sponsor":    _lazy("reward", "cmd_reward"),
    # ── 服务器功耗 / 电费（仅管理员）──
    # ── Steam 状态 / 价格 ──
    "steam":      cmd_steam,
    "在干嘛":     cmd_steam_doing,
    # ── MC 服务器状态卡 ──
    "motd":       cmd_motd,
    "mc状态":     cmd_motd,
    # ── 沙箱真实执行（移植自 KOOK .run）──
    "run":        cmd_run,
}


# ── 重命令（含网络请求 / 图片渲染，单条耗时数秒级）────────────────
# ★ v2.3.64：这些命令会被 core/queues.py 以旁路并发方式执行，**不占用本群 worker**。
#   起因：per-group worker 串行 + 命令内 API/渲染全程 await → 同群多人同时查战绩要排队，
#   且查询期间同群其他消息的回复被一起堵住（用户实测"卡回复"）。
#   只列真正重的主命令；进程控制类（restart/reload/update）**故意不在列**，保持串行。
HEAVY_COMMANDS: frozenset[str] = frozenset({
    # 战绩 / 游戏数据
    "wdsj", "steam", format_lang("mod.m41"),
    # MC 服务器状态卡（网络查询 + Chromium 渲染，数秒级）
    "motd", format_lang("mod.m42"),
    # 沙箱执行（编译/运行，数秒级）
    "run",
    # 天气 / 地震 / NASA / PGR
    format_lang("mod.m43"), "weather", "eq", format_lang("mod.m44"), "nasa", "oldphi", "phi",
    # 生成类（图/视频/语音）
    "draw", format_lang("mod.m45"), "video", format_lang("mod.m46"), "voice", format_lang("mod.m47"),
    "img2video", format_lang("mod.m48"), "img",
    # 谱面查询
    "tuflevel", format_lang("mod.m49"), "tufsearch", "tufd", "tufpage",
    # 抽卡 / 观战
    "抽", "watch", "spec", format_lang("mod.m50"),
    # 需要现拉数据的展示类
    "power", format_lang("mod.m51"), format_lang("mod.m52"),
    "stats", format_lang("mod.m53"), "dbsearch", format_lang("mod.m54"),
    "reward", format_lang("mod.m55"), format_lang("mod.m56"), "sponsor",
    "sys", "pc", "phone",
})


# ★ v2.3.83：插件声明的重命令（manifest limits.heavy=true）。由 PluginCapability 在注册时写入，
#   卸载时移除。与 HEAVY_COMMANDS 并列参与 is_heavy_command 判定。
EXTRA_HEAVY_COMMANDS: set[str] = set()


def is_heavy_command(text: str) -> bool:
    """判断指令文本是否属于重命令（→ core/queues.py 旁路并发执行）。"""
    t = (text or "").strip()
    if t.startswith("/~"):
        t = t[2:]
    elif t.startswith("/#"):
        t = t[2:]
    elif t.startswith("/") and len(t) > 1 and t[1] not in "~#/ ":
        t = t[1:]
    else:
        return False
    t = t.strip()
    if not t:
        return False
    name = t.split()[0].lower()
    # ★ v2.3.83：插件可在 manifest 声明 limits.heavy=true，注册时写入 EXTRA_HEAVY_COMMANDS，
    #   迁移后仍保持"重命令走旁路并发、不占用同群 worker"的原有语义。
    return name in HEAVY_COMMANDS or name in EXTRA_HEAVY_COMMANDS


async def handle_command(
    text: str,
    user_id: int,
    group_id: int,
    sender_name: str,
    is_group: bool,
    bot_qq: int,
    raw_message: str = "",
) -> str | None:
    """
    指令分发器。
    
    解析前缀(/~/ /#/) → 提取命令名和参数 → 查表 → 调用处理器 → 返回结果。
    
    Returns:
        - 文本字符串: 需要作为普通消息发送
        - "__SYS_TEST_CARD__": 需要由调用方构建并发送卡片
        - None: 不需要回复
    """
    # 解析前缀和命令部分
    if text.startswith("/~"):
        prefix = "/~"
        cmd_part = text[2:].strip()
    elif text.startswith("/#"):
        prefix = "/#"
        cmd_part = text[2:].strip()
    elif text.startswith("/") and len(text) > 1 and text[1] not in "~#/ ":
        prefix = "/"
        cmd_part = text[1:].strip()
    else:
        return None

    if not cmd_part:
        logger.warning("空指令内容: '%s'", text)
        return format_lang("error.command_error")

    # /# 前缀需要管理员权限 + 仅私聊
    if prefix == "/#":
        if is_group:
            logger.warning("/#指令在群聊中使用被拒绝 user=%d", user_id)
            return format_lang("error.permission_denied")
        from core.config import get_config
        cfg = get_config()
        roles = load_roles_config()
        if not cfg.is_admin(user_id, group_id):
            logger.warning("非管理员使用/#指令 user=%d", user_id)
            return format_lang("error.permission_denied")

    # 提取命令名和参数
    parts = cmd_part.split()
    cmd_name = parts[0].lower()
    args = parts[1:] if len(parts) > 1 else []

    logger.debug("指令解析: prefix=%s cmd=%s args=%s user=%d", prefix, cmd_name, args, user_id)

    handler = COMMAND_MAP.get(cmd_name)
    if not handler:
        logger.warning("未知指令: '%s%s'", prefix, cmd_name)
        return format_lang("error.unknown_command", prefix=prefix, cmd=cmd_name)

    # 执行处理器
    try:
        # 尝试传入 raw_message（img2video 等需要）
        import inspect
        sig = inspect.signature(handler)
        kwargs = {}
        if "raw_message" in sig.parameters:
            kwargs["raw_message"] = raw_message
        if "cmd_text" in sig.parameters:
            kwargs["cmd_text"] = text
        result = await handler(args, user_id, group_id, sender_name, is_group, bot_qq, **kwargs)
        # 来源标注：插件注册的指令（bridge 带 __plugin__ 属性）
        src = ""
        if getattr(handler, "__plugin__", None):
            src = format_lang("handle_command.m1", __plugin__=handler.__plugin__)
        if result is not None:
            # 打印完整返回内容（不截断），方便排障
            result_str = str(result)
            logger.info("指令执行完成 [%s]%s: %s", cmd_name, src, result_str)
        else:
            logger.info("指令执行完成 [%s]%s: 无返回值（已自行发送）", cmd_name, src)
        return result
    except Exception as e:
        err_type = type(e).__name__
        err_msg = str(e)
        logger.error("指令执行异常 [%s]: %s: %s", cmd_name, err_type, err_msg, exc_info=True)
        return format_lang("error.command_error_detail", cmd=cmd_name, err=err_type, msg=err_msg)


# ------每日排名HTML渲染------
def _daily_rank_payload(rows, today, new_players, time_start="", time_end=""):
    """组装起床战争日榜卡的数据载荷。

    ★ v2.3.25 抽出为独立函数：HTML 模板注入与 Pillow 渲染器共用同一份数据，
      避免两个渲染路径各拼一遍导致显示不一致（对比验证时曾因此得出错误结论）。
      调用方若要 Pillow 渲染，直接把本函数返回值传给 wdsj_card_pillow。
    """
    import html as _html
    from datetime import datetime

    def esc(x):
        return _html.escape(str(x), quote=True)

    now = datetime.now()
    # 节奏取自配置（4h / 12h），不要硬编码 —— 否则卡片上的「下一轮」会与实际调度不符
    from services.wdsj_tracker import next_slot as _wdsj_next_slot
    next_time = _wdsj_next_slot(now).strftime("%H:%M")

    out_rows = []
    for i, item in enumerate(rows, 1):
        name, diffs, kd_val = item[0], item[1], item[2]
        kd = diffs.get("kills", 0)
        fd = diffs.get("finalKills", 0)
        wd = diffs.get("wins", 0)
        dd = diffs.get("deaths", 0)
        out_rows.append({
            "rank": i,
            "name": name,
            "cells": {
                "name": esc(name),
                "kills": f"+{kd}" if kd > 0 else "0",
                "final": f"+{fd}" if fd > 0 else "",
                "wins": f"+{wd}" if wd > 0 else "0",
                "deaths": f"+{dd}" if dd > 0 else "0",
                "kd": f"{kd_val:.2f}",
            },
        })

    return {
        "date": today,
        "range": f"{time_start} → {time_end}" if (time_start or time_end) else "",
        "when": format_lang("_daily_rank_payload.m1"),
        "rows": out_rows,
        "newPlayers": list(new_players or []),
        "nextTime": next_time,
        "brand": _bot_name(),
    }


def _build_outage_notice_html(today: str = "", downtime: str = ""):
    """服务器异常补位公告卡（v2.3.81）：与日榜卡同款暖橙主题/结构。

    日榜数据缺失（如停机导致基线快照缺失）时，用它替代日报发出。
    """
    import time as _time
    from datetime import datetime
    from services.card_base import inject_stamp
    now = datetime.now()
    # 缺失轮次文案随配置节奏走（4h→6 轮 / 12h→2 轮），别硬编码
    from services.wdsj_tracker import collect_slots as _wdsj_slots, slots_hint as _wdsj_slots_hint
    _slots_txt = format_lang("_build_outage_notice_html.m1", _wdsj_slots_hint=_wdsj_slots_hint(), _wdsj_slots=len(_wdsj_slots()))
    label = today or now.strftime("%Y-%m-%d")
    down = downtime or "10-06 ~21:00 → 10-07 21:45"
    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><title>服务器异常 · 战绩日报</title>
<style>
  *{{margin:0;padding:0;box-sizing:border-box}}
  body{{font-family:"PingFang SC","Microsoft YaHei","Noto Sans CJK SC",sans-serif;-webkit-font-smoothing:antialiased;
    width:620px;padding:22px;
    background:radial-gradient(640px 340px at 8% -10%, rgba(255,172,132,.6), transparent 60%), radial-gradient(320px 220px at 98% -4%, rgba(255,206,186,.45), transparent 62%), linear-gradient(160deg,#fdf4ec 0%,#f9ece2 45%,#f3eff2 100%)}}
  .wrap{{border-radius:24px;padding:22px 24px 16px;border:1px solid rgba(255,255,255,.9);
    backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);
    box-shadow:0 14px 34px rgba(120,90,70,.10), inset 0 1px 0 rgba(255,255,255,.95);
    background:rgba(255,250,245,.78)}}
  .head{{display:flex;align-items:center;gap:13px;margin-bottom:16px}}
  .hic{{width:46px;height:46px;border-radius:13px;display:flex;align-items:center;justify-content:center;
    flex:none;box-shadow:inset 0 0 0 1px rgba(0,0,0,.05);
    background:linear-gradient(135deg, rgba(255,164,130,.46), rgba(238,90,58,.22));
    font-size:24px;font-weight:900;color:#fff}}
  .title{{font-size:21px;font-weight:800;letter-spacing:.5px;color:#4a2f26}}
  .sub{{font-size:12.5px;margin-top:3px;color:#9a8a80;font-variant-numeric:tabular-nums}}
  .when{{margin-left:auto;text-align:right;font-size:12px;color:#9a8a80;line-height:1.5}}
  .info{{padding:4px 0}}
  .irow{{display:flex;gap:14px;padding:9px 6px;font-size:13.5px;align-items:baseline;
    border-top:1px solid rgba(215,190,175,.3)}}
  .irow:first-child{{border-top:none}}
  .irow .d{{flex:0 0 120px;color:#9a8a80}}
  .irow .v{{color:#4a3d35;font-weight:600}}
  .irow .v b{{color:#c2432c}}
  .note{{margin-top:14px;padding:11px 14px;border-radius:12px;font-size:12.5px;line-height:1.7;
    background:rgba(255,214,190,.5);color:#8a5a3a}}
  .foot{{margin-top:14px;display:flex;align-items:center;gap:8px;font-size:12px;color:#a2958a;
    font-variant-numeric:tabular-nums}}
  .foot .sep{{flex:1;height:1px;background:linear-gradient(90deg,transparent,rgba(150,130,110,.25),transparent)}}
  .pill{{padding:2px 8px;border-radius:6px;background:rgba(255,214,190,.5);color:#8a5a3a;font-weight:700}}
</style></head>
<body>
<div class="wrap">
  <div class="head">
    <div class="hic">!</div>
    <div>
      <div class="title">服务器异常 · 战绩日报未产出</div>
      <div class="sub">{label} · 日榜数据缺失说明</div>
    </div>
    <div class="when">洛花星雨 Nexus<br>起床战争</div>
  </div>
  <div class="info">
    <div class="irow"><span class="d">停机时段</span><span class="v"><b>{down}</b></span></div>
    <div class="irow"><span class="d">缺失轮次</span><span class="v">{_slots_txt}</span></div>
    <div class="irow"><span class="d">影响范围</span><span class="v">当日战绩增量未采集，日榜无法统计</span></div>
    <div class="irow"><span class="d">恢复时间</span><span class="v">次日 0:01 起正常产出，无需任何操作</span></div>
  </div>
  <div class="note">此卡为<b>补位公告</b>，替代当日战绩日报。停机期间的游戏数据无法追溯，望周知。</div>
  <div class="foot">
    <span class="pill">补位公告</span>
    <span class="sep"></span>
    <span>Generated by 幻梦bot</span>
  </div>
</div>
</body></html>"""
    return inject_stamp(html, _time.time() * 1000)

def _build_daily_rank_html(rows, today, new_players, time_start="", time_end=""):
    """起床战争日榜（模板 data/templates/daily_rank_card.html + 数据注入）"""
    import json as _json
    import time as _time
    from pathlib import Path as _P

    _t0 = _time.time() * 1000              # v2.3.32 页脚「渲染时间」起点
    payload = _daily_rank_payload(rows, today, new_players, time_start, time_end)
    # v2.3.22: 模板标题固定为"今日增量"，统计区间可能是昨日跨天（自动回退时）
    #           → 用 date 字段覆盖标题显示真实归属日期
    tpl = (_P(__file__).resolve().parent.parent / "data" / "templates" / "daily_rank_card.html").read_text(encoding="utf-8")
    js = _json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    html = tpl.replace("/*__DAILY_DATA__*/null", js, 1)
    # 副标题已含 date+range（D.date + " · " + D.range），无需额外处理
    from services.card_base import inject_stamp
    return inject_stamp(html, _t0)


def _bot_name() -> str:
    """当前 bot 名称（用于卡片署名）"""
    try:
        from core.config import get_config
        return get_config().bot_name or format_lang("_bot_name.m1")
    except Exception:
        return format_lang("_bot_name.m2")


def _arena_daily_payload(rows, today, time_start="", time_end=""):
    """组装竞技场日榜卡的数据载荷。

    ★ v2.3.25 与 _daily_rank_payload 对称抽出：HTML 注入与 Pillow 渲染共用同一份数据。
    """
    import html as _html
    from datetime import datetime

    def esc(x):
        return _html.escape(str(x), quote=True)

    now = datetime.now()
    # 节奏取自配置（4h / 12h），不要硬编码 —— 否则卡片上的「下一轮」会与实际调度不符
    from services.wdsj_tracker import next_slot as _wdsj_next_slot
    next_time = _wdsj_next_slot(now).strftime("%H:%M")

    out_rows = []
    for i, item in enumerate(rows, 1):
        name, diffs, kd_val, div = item[0], item[1], item[2], item[3]
        name, div = esc(name), esc(div or "—")
        kd = diffs.get("kills", 0)
        wd = diffs.get("wins", 0)
        ld = diffs.get("losses", 0)
        dd = diffs.get("deaths", 0)
        out_rows.append({
            "rank": i,
            "name": name,
            "cells": {
                "name": name,
                "div": div,
                "kills": f"+{kd}" if kd > 0 else "0",
                "wins": f"+{wd}" if wd > 0 else "0",
                "losses": f"+{ld}" if ld > 0 else "0",
                "deaths": f"+{dd}" if dd > 0 else "0",
                "kd": f"{kd_val:.2f}",
            },
        })

    return {
        "date": today,
        "range": f"{time_start} → {time_end}" if (time_start or time_end) else "",
        "when": format_lang("_arena_daily_payload.m1"),
        "rows": out_rows,
        "newPlayers": [],
        "nextTime": next_time,
        "brand": _bot_name(),
    }


def _build_arena_daily_html(rows, today, time_start="", time_end=""):
    """竞技场日榜（模板 data/templates/daily_arena_card.html + 数据注入）"""
    import json as _json
    import time as _time
    from pathlib import Path as _P

    _t0 = _time.time() * 1000              # v2.3.32 页脚「渲染时间」起点
    payload = _arena_daily_payload(rows, today, time_start, time_end)
    tpl = (_P(__file__).resolve().parent.parent / "data" / "templates" / "daily_arena_card.html").read_text(encoding="utf-8")
    js = _json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    html = tpl.replace("/*__DAILY_DATA__*/null", js, 1)
    from services.card_base import inject_stamp
    return inject_stamp(html, _t0)

async def _render_html_to_png(html, prefix, width=740, height=900):
    import time
    ts = time.strftime("%Y%m%d_%H%M%S")
    # JPEG 比 PNG 快约 3 倍（实测 0.55s vs 1.69s）且体积小 40%，卡片渐变/小图标质量无可见损失
    out = str(Path(__file__).resolve().parent.parent / "data" / "img_temp" / f"{prefix}_{ts}.jpg")
    from modules.changelog import _ensure_browser
    try:
        # ★ v2.3.23: 整体加 20s 超时兜底——渲染卡死（浏览器无响应/页面池耗尽）时
        #   不让命令通道无限阻塞（per-group worker 单条消息总超时 200s 太长，用户等不起）
        async def _do():
            browser = await _ensure_browser()
            page = await browser.new_page(viewport={"width": width, "height": height})
            await page.set_content(html)
            # ★ v2.3.23 提速: networkidle → domcontentloaded。卡片 HTML 是内联样式+内联图标，
            #   无外部网络依赖，networkidle 会空等网络空闲（图片加载/长连接场景可等数秒）。
            #   domcontentloaded 在 DOM 就绪即截图，配合下面 300ms 兜底足够渲染内联样式。
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(300)
            # 截 body 元素：自动贴合卡片宽度与内容高度，避免出现大片背景留白
            el = await page.query_selector("body")
            if el:
                await el.screenshot(path=out, type="jpeg", quality=95)
            else:
                await page.screenshot(path=out, full_page=True, type="jpeg", quality=95)
            await page.close()
            return out
        return await asyncio.wait_for(_do(), timeout=20.0)
    except asyncio.TimeoutError:
        logger = __import__("logging").getLogger("wdsj")
        logger.error("卡片渲染超时(>20s)放弃: prefix=%s", prefix)
        return None
    except Exception:
        return None

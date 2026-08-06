from __future__ import annotations

import asyncio
import re
from pathlib import Path

# ── 回复后处理：去重括号动作、修正语气分裂 ──
_PARREN_ACTION = re.compile(r'[(（][^)）]*[)）]')

def _clean_reply(text: str) -> str:
    """修复语气分裂：连续多个括号动作描述只保留第一个"""
    # 找末尾连续括号动作
    matches = list(_PARREN_ACTION.finditer(text))
    if len(matches) >= 2:
        # 检查是否连续（无文字间隔）
        consecutive = True
        for i in range(1, len(matches)):
            between = text[matches[i-1].end():matches[i].start()]
            if between.strip():
                consecutive = False
                break
        if consecutive:
            # 只保留第一个
            text = text[:matches[0].start()] + text[matches[0].start():matches[0].end()].strip()
    return text

from core.logger import get_logger
from core.config import get_config
from utils.format_lang import format_lang
from modules.judge import should_respond
from modules.memory import (
    get_top_memories,
    maybe_save_memory,
    load_memories as _load_memories_for_context,
    search_msglog,
)
from modules.fav import update_fav, get_fav
from modules.commands import handle_command
from modules.search import auto_search_if_needed
from services.llm import generate_multi_reply, generate_multi_reply_with_tools
from services.sender import send_sentences, send_by_chat_type, send_raw_group, send_raw_user

logger = get_logger("pipeline")

# ------工具函数------
def _clean_name(name):
    return re.sub(r'[\u200b\u200c\u200d\u200e\u200f\u202a-\u202e\u2060-\u2069\ufeff]+', '', str(name))


# ------戳一戳------
async def handle_poke_event(sender_name, user_id, chat_id, is_group):
    from core.context_manager import get_context_mgr
    cfg = get_config()
    ctx = get_context_mgr()

    sender_name = _clean_name(sender_name)

    from modules.fav import ensure_fav
    ensure_fav(chat_id, user_id, is_group)

    system_msg = format_lang("poke.message", name=sender_name, bot_name=cfg.bot_name)
    ctx.append_to_context(chat_id, f"[系统] {system_msg}")
    logger.info("🐾 戳一戳回复流程启动: from=%s chat=%d", sender_name, chat_id)

    related_memories = get_top_memories(system_msg, ctx.get_context(chat_id), chat_id=chat_id)
    fav_val = get_fav(chat_id, user_id, is_group)
    fav_info = f"当前{sender_name}对你的好感度：{fav_val}/100"

    extra_parts = []
    from datetime import datetime
    now = datetime.now()
    now_str = now.strftime("%Y年%m月%d日 %H:%M:%S") + f".{now.microsecond // 1000:03d}"
    weekdays = "日一二三四五六"
    now_str += f" 周{weekdays[int(now.strftime('%w'))]}"
    extra_parts.append(f"当前时间：{now_str}")

    from modules.preset import get_preset
    active_preset = get_preset(chat_id)
    if active_preset:
        extra_parts.append(f"【系统注入指令 — 你必须严格遵守，优先级高于人设】\n{active_preset}")
    if related_memories:
        extra_parts.append(related_memories)
    extra_parts.append(fav_info)

    poke_rules = [
        "【戳一戳规则：只用 1 句简短回应，不要展开话题，不要超过 20 字】",
        "【禁止重复：绝对不要说摸头很舒服、摸摸头、被摸了之类的前一次用过的句式，每次必须想全新的回应】",
        "【随机语气：可以从疑惑、开心、害羞、吓一跳、嫌弃、淡定中随机选一种情绪回应】",
        "【禁止调用任何工具/指令/搜索，只输出纯文本回复】",
    ]

    try:
        from modules.op import get_mode, get_sleep_prompt_rule, get_narrative_prompt_rule
        mode = get_mode(chat_id)
        if mode == "sleeping":
            poke_rules = [get_sleep_prompt_rule(chat_id)]
        elif mode == "narrative":
            poke_rules = [get_narrative_prompt_rule()]
    except ImportError:
        pass
    extra_parts.extend(poke_rules)

    buffer_snapshot = list(ctx.get_buffer(chat_id))

    sentences, fav_change, llm_calls, face_cq, mood, mood_detail, action, at_qq, mode_switch, origin, actor, _ = await generate_multi_reply_with_tools(
        msg_history=ctx.get_context(chat_id),
        speaker_name=sender_name,
        current_msg=f"[系统] {system_msg}",
        bot_name=cfg.bot_name,
        system_prompt=cfg.system_prompt,
        reply_model=cfg.reply_model,
        is_group=is_group,
        extra_info="\n".join(extra_parts),
        max_tokens=None,
        user_id=user_id, group_id=chat_id if is_group else 0, bot_qq=cfg.bot_qq,
    )

    if sentences:
        # 静默去除 [FACE:xxx] 残留文本，LLM 不该输出这个
        sentences = [re.sub(r'\[FACE:[^\]]*\]?', '', s).strip() for s in sentences]
        sentences = [_clean_reply(s) for s in sentences]
        sentences = [s for s in sentences if s]
        if not sentences:
            sentences = ["喵~"]

        task = asyncio.create_task(send_sentences(
            sentences, chat_id, is_group,
            user_id=user_id if not is_group else None,
        ))
        ctx.set_active_send_task(chat_id, task)

        update_fav(chat_id, user_id, 1, is_group)
        logger.info("戳一戳回复完成: %d句 fav+1", len(sentences))

        await maybe_save_memory(system_msg, sentences[0], sender_name, chat_id, user_id, buffer_snapshot)


# ------消息处理主入口------
async def process_message(msg_type, msg_content, chat_id, sender_name, user_id, is_group, bot_qq,
                          raw_event=None, raw_message="", quoted_msg="", error_report=None):
    from core.context_manager import get_context_mgr
    cfg = get_config()
    ctx = get_context_mgr()

    sender_name = _clean_name(sender_name)

    # 首次对话自动注册好感度
    from modules.fav import ensure_fav
    ensure_fav(chat_id if is_group else user_id, user_id, is_group)

    # ------清洗不可见字符------
    import re as _re
    if msg_type == "文字" and msg_content:
        _invisible = _re.compile(
            r'[\u200b\u200c\u200d\u200e\u200f\u202a\u202b\u202c\u202d\u202e'
            r'\u2060-\u2064\u2066-\u2069\ufeff]+'
        )
        cleaned = _invisible.sub('', msg_content)
        if cleaned != msg_content:
            logger.info("[chat=%d] 清洗不可见字符: %d → %d 字符", chat_id, len(msg_content), len(cleaned))
            msg_content = cleaned
        if not msg_content.strip():
            logger.info("[chat=%d] 消息全为不可见字符，跳过处理", chat_id)
            return

    # ------管理员提示词注入------
    if msg_type == "文字" and "{{" in msg_content:
        role_tag = cfg.get_user_tag(user_id)
        if role_tag == "admin":
            from modules.preset import extract_preset_from_message, set_preset, clear_preset
            preset_text, cleaned = extract_preset_from_message(msg_content)
            if preset_text:
                if preset_text.lower() == "reset":
                    cleared = clear_preset(chat_id)
                    reply = "提示词已重置喵~ (｡･ω･｡)" if cleared else "当前没有注入的提示词喵~"
                else:
                    set_preset(chat_id, preset_text)
                    reply = f"提示词已注入喵~ ({len(preset_text)}字) 🔒"
                if is_group:
                    await send_by_chat_type(reply, chat_id, is_group=True)
                else:
                    await send_by_chat_type(reply, chat_id, is_group=False, user_id=user_id)
                if cleaned:
                    msg_content = cleaned
                else:
                    return

    # ------引用消息注入------
    if quoted_msg:
        quote_line = f"[引用原文] {quoted_msg}"
        ctx.append_to_context(chat_id, quote_line)
        logger.info("📎 引用消息已注入上下文 [%d]: '%s'...", chat_id, quoted_msg[:50])

    # ------上下文记录------
    role_tag = cfg.get_user_tag(user_id, chat_id if is_group else 0)
    if not is_group and role_tag not in ("admin",):
        try:
            from modules.op import is_private_master
            if is_private_master(user_id):
                role_tag = "admin"
        except ImportError:
            pass
    display_name = cfg.get_display_name(user_id, chat_id)
    fav_val = get_fav(chat_id, user_id, is_group)

    # ★ 好感度 -100 → 直接忽略
    if fav_val <= -100:
        logger.warning("好感度-100忽略: uid=%d(%s) chat=%d fav=%d", user_id, sender_name, chat_id, fav_val)
        return

    context_line = f"[{role_tag}] {display_name}[fav={fav_val}]: {msg_content}"

    buffer_text = f"{sender_name}: {msg_content}"
    if quoted_msg:
        buffer_text = f"{sender_name} (回复「{quoted_msg[:80]}」): {msg_content}"
    ctx.append_to_buffer(chat_id, buffer_text)

    from modules.stm import add_entry as stm_add
    stm_add(chat_id, role_tag, f"{sender_name}: {msg_content}", sender_name)

    # ------指令拦截------
    # /~ /# / 三种前缀都触发指令
    import re as _re
    cmd_match = _re.match(r'(/(?:~|#|(?=[a-zA-Z])))\s*(\S[\s\S]*?)(?:\s*$|\s*\n)', msg_content)
    if not cmd_match:
        cmd_match = _re.match(r'(/(?:~|#|(?=[a-zA-Z])))(\S[\s\S]*)', msg_content)
    if cmd_match:
        full_cmd = cmd_match.group(0)
        logger.info("指令拦截: '%s' from=%s", full_cmd, sender_name)
        await _handle_command_route(full_cmd, user_id, chat_id, sender_name, is_group, bot_qq, raw_message=raw_message)
        return

    if msg_type != "文字":
        logger.debug("非文字消息(type=%s)，不进入回复管道", msg_type)
        return

    # ★ 指令和非文字消息不写入 LLM 上下文（避免污染）
    # 只有进入回复管道的消息才写入 group_context
    ctx.append_to_context(chat_id, context_line)

    # ------回复判断------
    should_reply = False
    import re as _re
    is_mentioned = bool(_re.search(rf'@{bot_qq}(?!\d)', msg_content))
    group_setting = cfg.group_settings.get(chat_id, {}) if is_group else {}
    at_only = group_setting.get("at_only", False)
    custom_threshold = group_setting.get("reply_threshold")

    if not is_group:
        should_reply = True
        logger.debug("私聊消息 → 直接回复")
    elif at_only:
        should_reply = is_mentioned
    elif is_mentioned:
        should_reply = True
        logger.info("@机器人检测 → 直接回复")
    elif is_group and re.search(r"@\S+", msg_content):
        logger.debug("@他人消息 [群%d] → SKIP", chat_id)
    else:
        should_reply = await should_respond(
            msg_content, msg_type, sender_name, chat_id,
            ctx.get_context(chat_id), cfg.bot_name, bot_qq,
            reply_threshold_override=custom_threshold,
        )

    if not should_reply:
        return

    # ------自忽略机制------
    from services.self_ignore import is_ignored, ignore_user, remaining_seconds
    if is_group and is_ignored(user_id):
        logger.info("用户%d在忽略列表中，跳过回复 (%ds后解除)", user_id, remaining_seconds(user_id))
        return


    # ------记忆检索+好感度+上下文组装------
    full_msg = f"[{role_tag}] {sender_name}发了: {msg_content}"
    related_memories = get_top_memories(msg_content, ctx.get_context(chat_id), chat_id=chat_id)
    fav_val = get_fav(chat_id, user_id, is_group)

    arch_context = ""
    arch_keywords = ["版本", "更新", "更新日志", "架构", "能力", "配置", "changelog", "version", "模型", "model"]
    msg_for_arch = msg_content[-200:].lower()
    if any(kw in msg_for_arch for kw in arch_keywords):
        try:
            from core.config import get_architecture_context
            arch_context = get_architecture_context()
            logger.debug("架构上下文已注入 (%d chars)", len(arch_context))
        except Exception:
            pass

    extra_info_parts = []
    from datetime import datetime
    now = datetime.now()
    now_str = now.strftime("%Y年%m月%d日 %H:%M:%S") + f".{now.microsecond // 1000:03d}"
    weekdays = "日一二三四五六"
    now_str += f" 周{weekdays[int(now.strftime('%w'))]}"
    extra_info_parts.append(f"当前时间：{now_str}")

    # 节假日信息
    try:
        from modules.holiday import get_today_holiday_text
        holiday_text = get_today_holiday_text()
        if holiday_text:
            extra_info_parts.append(holiday_text)
    except Exception:
        pass

    from modules.preset import get_preset
    active_preset = get_preset(chat_id)
    if active_preset:
        extra_info_parts.append(f"【系统注入指令 — 你必须严格遵守，优先级高于人设】\n{active_preset}")
    if related_memories:
        extra_info_parts.append(related_memories)

    if not related_memories or len(related_memories) < 300:
        try:
            msglog_context = get_msglog_context(msg_content, ctx.get_context(chat_id), chat_id)
            if msglog_context:
                extra_info_parts.append(msglog_context)
        except Exception:
            pass

    try:
        img_keywords = ["图", "照片", "截图", "表情", "什么", "是谁", "这是"]
        has_img_ref = any(k in msg_content[-20:] for k in img_keywords) or msg_content == "[图片]"
        has_img_in_recent = any("[图片]" in l for l in ctx.get_context(chat_id)[-5:] if isinstance(l, str))
        if has_img_ref or has_img_in_recent:
            from services.image_api import get_recent_image_descriptions
            recent_imgs = get_recent_image_descriptions(chat_id=chat_id, limit=2)
            if recent_imgs:
                img_lines = ["【本群最近图片描述（如果还没识别完则为空，不知道就直说不知道）】"]
                for img in recent_imgs:
                    img_lines.append(f"- {img.get('desc', '?')[:100]} (来自 {img.get('author', '?')})")
                extra_info_parts.append("\n".join(img_lines))
    except Exception:
        pass

    extra_info_parts.append(f"当前{sender_name}对你的好感度：{fav_val}/100")

    if is_group:
        at_list = _build_at_list(ctx.get_context(chat_id), cfg)
        if at_list:
            extra_info_parts.append(at_list)

    if arch_context:
        extra_info_parts.append(arch_context)

    if is_group:
        group_ops = cfg.group_owners.get(chat_id, [])
        if group_ops:
            op_names = [cfg.get_display_name(q, chat_id) for q in group_ops]
            op_list = "、".join(f"{n}({q})" for n, q in zip(op_names, group_ops))
            master_name = cfg.get_display_name(cfg.admin_qq)
            extra_info_parts.append(
                f"【主人提示】你的真正主人是{master_name}，"
                f"同时{op_list}也在这个群拥有主人权限。对他们要用对主人一样的语气和态度。"
            )

    try:
        from modules.op import get_mode, get_sleep_prompt_rule, get_narrative_prompt_rule
        mode = get_mode()
        if mode == "sleeping":
            extra_info_parts.append(get_sleep_prompt_rule(chat_id))
        elif mode == "narrative":
            extra_info_parts.append(get_narrative_prompt_rule())
    except ImportError:
        pass

    extra_info = "\n".join(extra_info_parts)

    # ── 用户画像注入 ──
    try:
        from core.user_profile import build_profile_text
        profile_text = build_profile_text(user_id)
        if profile_text:
            extra_info += f"\n\n【发言者画像】\n{profile_text}"
    except ImportError:
        pass

    if extra_info:
        logger.info("额外信息: 记忆=%d字 搜索=%d字", len(related_memories), 0)

    # ------错误报告处理------
    if error_report:
        logger.info("🔧 检测到错误报告，临时隔离上下文...")
        from modules.error_report import build_error_report_prompt
        full_msg = build_error_report_prompt(sender_name=sender_name, log_content=error_report, original_msg=msg_content)
        msg_history_for_llm = []
        extra_info_for_llm = ""
        ctx.append_to_context(chat_id, f"[错误报告] {sender_name} 上传了 Minecraft 错误报告，请求分析")
        logger.info("🔧 上下文已隔离（旧上下文保留，LLM 调用暂不使用）")
    else:
        msg_history_for_llm = ctx.get_context(chat_id)
        extra_info_for_llm = extra_info

    # ------系统提示词+工具代理------
    # cfg.system_prompt 结构: # 核心人格\n{core}\n---\n# 侧面人格\n{side}\n---\n# 固定身份\n{identity}\n---\n{self_awareness}
    # persona 注入策略（全替换模式）：
    #   - per-user persona dict {core, side, identity} 非空 → JSON 编码后用 PERSONA::: 标记，
    #     _build_system_text 检测到后用三段构造 header，禁用 face_lib/private_tone/play_mode
    #   - 保留 format_rules/command_tools/fav/anti_repeat（功能段）
    #   - 同步设置记忆 override：私聊有 persona 时用专属记忆文件
    #   - 无 per-user persona 时回退 [private_persona] 基底，再回退默认
    system_prompt_for_llm = cfg.system_prompt
    if not is_group:
        try:
            from modules.op import get_persona, get_persona_memory_id
            from modules.memory import set_persona_override
            custom = get_persona(user_id, cfg.private_persona_version)
            if custom:
                # 设置记忆覆盖（persona 专属记忆文件）
                memory_id = get_persona_memory_id(user_id)
                set_persona_override(user_id, memory_id)
                # persona JSON 编码后用 PERSONA::: 标记注入
                import json as _json
                persona_json = _json.dumps(custom, ensure_ascii=False)
                system_prompt_for_llm = f"PERSONA:::{persona_json}:::{cfg.system_prompt}"
                logger.debug("私聊人格注入(全替换) [%d]: core=%s...", user_id, custom.get("core", "")[:40])
            else:
                # 无 persona：清除记忆覆盖
                set_persona_override(user_id, None)
                if cfg.private_persona_core or cfg.private_identity:
                    # 全局 [private_persona] 基底：替换 core/side/identity，保留 self_awareness
                    private_core = cfg.private_persona_core or cfg.personality_core
                    parts = [f"# 核心人格\n{private_core}"]
                    if cfg.private_persona_side:
                        parts.append(f"# 侧面人格\n{cfg.private_persona_side}")
                    ident = cfg.private_identity or cfg.identity
                    parts.append(f"# 固定身份\n{ident}")
                    parts.append(cfg._build_self_awareness())
                    system_prompt_for_llm = "\n---\n".join(parts)
                    logger.debug("私聊使用 [private_persona] 基底")
        except ImportError:
            pass

    # 工具预选/执行代理已删除：inject_tool_system / try_tool_select / get_tool_status
    # 三个函数在 core/tools.py 中不存在，ImportError 被静默吞掉，整段是死代码。
    # 工具选择/执行由 generate_multi_reply_with_tools 中的 FC Agent 全权处理。

    # ------Agent写作路由------
    try:
        from utils.writing import is_writing_request, generate_and_send_file
        if await is_writing_request(msg_content, msg_history_for_llm):
            logger.info("写作请求检测: from=%s", sender_name)
            handled = await generate_and_send_file(
                msg=full_msg if not is_group else msg_content,
                msg_history=msg_history_for_llm,
                speaker_name=sender_name,
                chat_id=chat_id,
                is_group=is_group,
                user_id=user_id,
            )
            if handled:
                logger.info("写作管道处理完成: chat=%d", chat_id)
                return
            logger.info("写作管道回退 → 走正常生成")
    except ImportError:
        pass

    # ------编程路由（长代码题直接走 write_code，不经过 FC）------
    import re as _re
    _CODE_HINTS = [
        r"使用\s*c\+\+", r"编程解决", r"写(个|代码|程序).*题",
        r"编写程序", r"#include", r"\.cpp", r"交互题",
        r"时间复杂度", r"std::", r"using namespace",
    ]
    _code_detected = any(_re.search(p, msg_content) for p in _CODE_HINTS)
    if _code_detected and len(msg_content) > 500:
        # 太长的题直接认怂，16岁看不懂
        if len(msg_content) > 2000:
            logger.info("编程请求过长 %d字，认怂", len(msg_content))
            from services.sender import send_group_msg, send_private_msg
            msg = "呜…这题好难喵，我看不懂~( ＞﹏＜ )"
            if is_group:
                await send_group_msg(msg, chat_id)
            else:
                await send_private_msg(msg, user_id)
            return
        logger.info("编程请求检测: from=%s len=%d → 走代码生成管道", sender_name, len(msg_content))
        try:
            from core.tools import _write_code
            lang = "c++" if any(k in msg_content.lower() for k in ("c++", "cpp", "#include")) else "python"
            if "javascript" in msg_content.lower() or "js" in msg_content.lower():
                lang = "javascript"
            result = await _write_code(
                language=lang, description=msg_content[:3000],
                user_id=user_id, group_id=chat_id, sender_name=sender_name,
                is_group=is_group, bot_qq=bot_qq,
            )
            logger.info("代码生成管道完成: chat=%d result=%s", chat_id, result[:80])
            return
        except Exception as e:
            logger.warning("代码生成管道失败: %s，回退正常生成", e)

    # ------JSON LLM生成------
    logger.info("开始生成回复: speaker=%s chat=%d", sender_name, chat_id)
    sentences, fav_change, llm_calls, face_cq, mood, mood_detail, action, at_qq, mode_switch, origin, actor, _ = await generate_multi_reply_with_tools(
        msg_history=msg_history_for_llm, speaker_name=sender_name, current_msg=full_msg,
        bot_name=cfg.bot_name, system_prompt=system_prompt_for_llm, reply_model=cfg.reply_model,
        is_group=is_group, extra_info=extra_info_for_llm,
        max_tokens=None,
        user_id=user_id, group_id=chat_id if is_group else 0, bot_qq=bot_qq,
    )

    if not sentences:
        error_lines = [
            "呜呜，回复生成失败了喵~",
            f"错误: LLM返回空内容",
            f"时间: {now.strftime('%H:%M:%S')}",
            f"对话者: {sender_name}",
            f"上下文: {len(msg_history_for_llm)}轮",
            "请联系管理员 @Trusler 解决喵~",
        ]
        await send_by_chat_type("\n".join(error_lines), chat_id if is_group else chat_id,
                               is_group=True if is_group else False,
                               user_id=user_id if not is_group else None)
        logger.warning("LLM 未返回有效句子，已发送失败提示")
        return

    # ------垃圾过滤------
    _ctx_pattern = re.compile(r'^\[(admin|friend|群友)\]\s+\S+:\s')
    filtered = []
    for s in sentences:
        if _ctx_pattern.match(s):
            logger.warning("LLM 回显上下文格式，已过滤: '%s'", s[:60])
            continue
        filtered.append(s)
    if not filtered:
        filtered.append(format_lang("bot.fallback_reply", name=cfg.bot_name))
    sentences = filtered
    sentences = [_clean_reply(s) for s in sentences]

    # actor 始终以真实发送者为准，不信任 LLM 输出
    if origin == "user" and (not actor or actor.get("qq") != user_id):
        actor = {"name": sender_name, "qq": user_id}

    # ------FILE文件处理------
    _file_re = re.compile(r'\[FILE:(.+?)\](.*?)\[/FILE\]', re.DOTALL)
    for i, s in enumerate(sentences):
        m = _file_re.search(s)
        if m:
            fname = m.group(1).strip()
            content = m.group(2).strip()
            if not fname.endswith('.txt'):
                fname += '.txt'
            import tempfile
            fpath = Path(tempfile.gettempdir()) / fname
            fpath.write_text(content, encoding='utf-8')
            logger.info("[FILE] 创建: %s (%d字)", fname, len(content))
            fpath_str = str(fpath).replace('\\', '/')
            sentences[i] = f"[CQ:file,file=file:///{fpath_str},name={fname}]"
            async def _clean():
                await asyncio.sleep(30)
                try: fpath.unlink()
                except: pass
            asyncio.create_task(_clean())

    # ------scan replies for inline /~commands ------
    if sentences:
        new_lines = []
        _cmd_re = _re.compile(r'/(?:\~|\#|(?=[a-zA-Z]))\s*(\w+)(?:\s+\[?([^\]]*)\]?)?')
        for _line in sentences:
            _line = str(_line) if _line else ""
            _added = 0
            for _m in _cmd_re.finditer(_line):
                _cn = _m.group(1).strip()
                _ca = (_m.group(2) or "").strip()
                if _cn:
                    # Only intercept real commands, not random text matching the pattern
                    from modules.commands import COMMAND_MAP as _CM
                    if _cn in _CM:
                        llm_calls.append({"name": _cn, "args": _ca})
                        _added += 1
                        logger.info("从回复中自动提取CALL: /~%s %s", _cn, _ca)
            if _added:
                # Strip out the command text to avoid sending it as literal text
                _cleaned = _cmd_re.sub("", _line).strip()
                if _cleaned:
                    new_lines.append(_cleaned)
            else:
                new_lines.append(_line)
        sentences = new_lines

    # ------CALL执行------
    executed_calls = []
    call_results = []
    call_texts = []  # 延迟执行的发文件类 CALL
    if llm_calls:
        for call in llm_calls:
            if not isinstance(call, dict):
                continue
            cmd_name = str(call.get("name", "")).strip().lstrip("~")
            cmd_args = str(call.get("args", "")).strip()
            if not cmd_name:
                continue
            from modules.commands import COMMAND_MAP
            if cmd_name not in COMMAND_MAP:
                err_msg = f"指令 /~{cmd_name} 不存在喵~\n请联系管理员 @Trusler"
                await send_by_chat_type(err_msg, chat_id if is_group else chat_id,
                                       is_group=True, user_id=None)
                logger.warning("JSON CALL 无效: %s", cmd_name)
                continue
            # 追踪：有人叫bot执行 → 用actor的QQ；bot自己执行 → 用bot_qq
            caller_id = user_id
            caller_name = sender_name
            if isinstance(actor, dict) and actor.get("qq"):
                caller_id = int(actor["qq"])
                caller_name = actor.get("name", sender_name)
            elif origin == "bot":
                caller_id = bot_qq
                caller_name = cfg.bot_name

            call_text = f"/~{cmd_name} {cmd_args}".strip()
            logger.info("JSON CALL: %s (by=%s origin=%s)", call_text, caller_name, origin)
            # write_code 类发文件指令延迟执行，等文字先发送
            _send_file_cmds = ("write_code",)
            if cmd_name in _send_file_cmds:
                call_results.append(None)  # 占位，稍后填充
                call_texts.append((call_text, len(call_results) - 1, caller_id, caller_name))
            else:
                try:
                    result = await handle_command(call_text, caller_id, chat_id, caller_name, is_group, bot_qq, raw_message)
                    call_results.append(result)
                except Exception as e:
                    logger.warning("JSON CALL执行失败 [%s]: %s", cmd_name, e)
                    err_msg = f"指令 /~{cmd_name} 执行失败喵~\n错误: {str(e)[:200]}\n请联系管理员 @Trusler"
                    await send_by_chat_type(err_msg, chat_id if is_group else chat_id,
                                           is_group=True, user_id=None)
                    call_results.append(f"[CALL错误] {e}")
            executed_calls.append(call_text.split(" ")[0])

    is_at_me = raw_message and f"[CQ:at,qq={bot_qq}]" in raw_message
    combined_reply = " || ".join(sentences)
    if executed_calls and is_group and is_at_me:
        first_call_idx = -1
        for i, s in enumerate(sentences):
            if re.search(r'\[CALL:', s):
                first_call_idx = i
                break
        if first_call_idx > 0:
            logger.debug("丢弃 CALL 前的 %d 句闲聊（@优先）", first_call_idx)
            sentences = sentences[first_call_idx:]

    combined_reply = re.sub(r'\[CALL:[^\]]+\]', '', combined_reply).strip()
    if executed_calls:
        hints = []
        for i, c in enumerate(set(executed_calls)):
            name = c.lstrip("/~")
            if name == "search" and i < len(call_results) and call_results[i]:
                r = call_results[i]
                count = len(r.split("\n")) if r else 0
                hints.append(f"搜索:{count}结果")
            elif name == "read" and i < len(call_results) and call_results[i]:
                r = call_results[i]
                l = len(r) if r else 0
                hints.append(f"读取:{l}字")
            else:
                hints.append(c.lstrip("/~"))
        call_hint = "、".join(hints)
        logger.info("CALL执行: %s", call_hint)
        ctx.append_to_context(chat_id, f"[系统] 已调用: {call_hint}")

    # ------表情处理------
    if not face_cq:
        # 静默去除 [FACE:xxx] 残留
        combined_reply = re.sub(r'\[FACE:[^\]]*\]?', '', combined_reply).strip()

    sentences = [s for s in combined_reply.split(" || ") if s.strip()]
    if not sentences:
        sentences = ["喵~"]
    _face_cq_for_later = face_cq

    # ------上下文回写------
    _context_reply = re.sub(r'\s*\[系统\]\s*已调用:\s*\S+', '', " || ".join(sentences)).strip()
    # ★ CQ 码简化写入上下文（避免 [CQ:image,file=...] 污染 LLM 上下文）
    _context_reply = re.sub(r'\[CQ:image[^\]]*\]', '[图片]', _context_reply)
    _context_reply = re.sub(r'\[CQ:face[^\]]*\]', '[表情]', _context_reply)
    _context_reply = re.sub(r'\[CQ:at[^\]]*\]', '@', _context_reply)
    _context_reply = re.sub(r'\[CQ:[^\]]*\]', '[消息]', _context_reply)
    ctx.append_to_context(chat_id, f"{cfg.bot_name}: {_context_reply}")

    for s in sentences:
        _s_clean = re.sub(r'\s*\[系统\]\s*已调用:\s*\S+', '', s).strip()
        if _s_clean:
            ctx.append_to_buffer(chat_id, f"{cfg.bot_name}: {_s_clean}")

    # ------action动作------
    if action:
        action_text = f"({action})"
        if sentences:
            sentences[-1] = sentences[-1] + action_text
        else:
            sentences.append(action_text)

    # ------@处理：把 @QQ号 替换为 [CQ:at]------
    if at_qq and is_group:
        at_cq = f"[CQ:at,qq={at_qq}]"
        at_text = f"@{at_qq}"
        for i in range(len(sentences)):
            sentences[i] = sentences[i].replace(at_text, at_cq)

    # ------mode切换------
    if mode_switch and mode_switch in ("normal", "sleeping", "narrative"):
        try:
            from modules.op import _load_modes, _save_modes
            modes = _load_modes()
            if mode_switch == "normal":
                modes.pop(str(chat_id), None)
            else:
                modes[str(chat_id)] = {"mode": mode_switch, "since": __import__("time").time()}
            _save_modes(modes)
            logger.info("LLM主动切换模式: chat=%d → %s", chat_id, mode_switch)
        except Exception:
            pass

    # ------发送------
    old_task = ctx.cancel_old_task(chat_id)
    if old_task:
        logger.debug("取消旧发送任务 chat=%d", chat_id)

    task = asyncio.create_task(send_sentences(
        sentences, chat_id, is_group,
        user_id=user_id if not is_group else None,
    ))
    ctx.set_active_send_task(chat_id, task)

    if _face_cq_for_later:
        async def _send_face_after():
            await task
            await send_by_chat_type(_face_cq_for_later, chat_id if is_group else chat_id,
                                   is_group=True if is_group else False,
                                   user_id=user_id if not is_group else None)
        asyncio.create_task(_send_face_after())

    # ------CALL结果回发------
    if call_results:
        async def _send_call_results():
            await task
            # 等待文字全部发出后，再执行发文件类 CALL
            for call_text, idx, caller_id, caller_name in call_texts:
                try:
                    result = await handle_command(call_text, caller_id, chat_id, caller_name, is_group, bot_qq, raw_message)
                    call_results[idx] = result
                except Exception as e:
                    logger.warning("延迟CALL执行失败: %s", e)
                    call_results[idx] = f"[CALL错误] {e}"
            is_search_or_read = any(c.lstrip("/~") in ("search", "read") for c in executed_calls)
            for i, r in enumerate(call_results):
                if not r:
                    continue
                if isinstance(r, str) and r.startswith("__EQ_CARD__:"):
                    png = r.split(":", 1)[1]
                    cq = f"[CQ:image,file=file:///{png.replace(chr(92), '/')}]"
                    await send_by_chat_type(cq, chat_id if is_group else chat_id,
                                           is_group=True if is_group else False,
                                           user_id=user_id if not is_group else None)
                elif not is_search_or_read:
                    if isinstance(r, str) and r.startswith("[CALL错误]"):
                        continue
                    short = r[:200] + "..." if len(r) > 200 else r
                    logger.info("CALL结果: %s", short[:80])

            if call_results[0]:
                effective_result = call_results[0]
                is_call_error = isinstance(effective_result, str) and effective_result.startswith("[CALL错误]")
                ctx_text = effective_result[:200] if not is_call_error else f"[执行失败] {effective_result[:200]}"
                ctx.append_to_context(chat_id, f"[系统] 调用结果: {ctx_text}")
                try:
                    from services.llm import call_llm as raw_llm, _build_system_text
                    follow_sys = _build_system_text(cfg.bot_name, cfg.system_prompt, is_group)
                    # 搜索总结 → 用事实型 prompt，避免猫娘人设的"每句≤40字"压缩摘要
                    if is_search_or_read:
                        follow_sys = "你是一个信息总结助手。请把搜索结果按事件分段完整列出时间线、数据、影响，不要遗漏细节。禁止使用 Markdown 格式（不要 ## ** 表格 | 等标记），纯文本输出。"
                    if is_call_error:
                        err_detail = effective_result.replace("[CALL错误]", "").strip()
                        prompt = (
                            "系统调用的功能执行失败，请诚实告知用户。\n"
                            "规则：一句话简短告诉用户操作失败并给出正确用法提示，不要编造数据。\n"
                            f"失败原因: {err_detail[:300]}"
                        )
                        max_t = 120
                    elif is_search_or_read:
                        prompt = (
                            "你刚才搜索了以下内容，请按时间线或主题分段总结给用户。\n"
                            "规则：纯文本输出，不要JSON，不要Markdown（禁止 ## ** | 表格等标记）。\n"
                            "按事件分段列出，每个事件写时间+地点+经过+影响。\n"
                            "禁止只说感想、禁止只给一句话、禁止用'好家伙'等感叹代替事实。把具体数据全部列出来。\n"
                            f"搜索结果:\n{effective_result[:4000]}"
                        )
                        max_t = None  # 不限 token
                    else:
                        prompt = (
                            "上面是调用结果，用一句话自然回应。纯文本，不要JSON。\n"
                            f"结果: {effective_result[:500]}"
                        )
                        max_t = 200
                    follow = await raw_llm(cfg.reply_model, [
                        {"role": "system", "content": follow_sys},
                        {"role": "user", "content": prompt},
                    ], max_tokens=max_t, temperature=0.7, timeout=15.0)
                    if follow and follow.strip():
                        f_text = follow.strip()
                        # 兜底：如果 LLM 仍然输出 JSON，提取文本
                        if f_text.startswith('{') and '"replies"' in f_text:
                            try:
                                import json
                                extracted = json.loads(f_text)
                                if isinstance(extracted, dict) and "replies" in extracted:
                                    f_text = "。".join(extracted["replies"])
                            except Exception:
                                pass
                        f_text = f_text[:300]
                        f_text = re.sub(r'[\[［]fav:\s*[+-]?\d+[\]］]', '', f_text).strip()
                        ctx.append_to_context(chat_id, f"{cfg.bot_name}: {f_text}")
                        await send_by_chat_type(f_text, chat_id if is_group else chat_id,
                                               is_group=True if is_group else False,
                                               user_id=user_id if not is_group else None)
                except Exception as e:
                    logger.debug("追加回复失败: %s", e)
        asyncio.create_task(_send_call_results())

    # ------好感度------
    if fav_change != 0:
        update_fav(chat_id, user_id, fav_change, is_group)
        logger.info("好感度调整: %s %+d (chat=%d)", sender_name, fav_change, chat_id)

    # ------自动记忆------
    buffer_snapshot = list(ctx.get_buffer(chat_id))
    await maybe_save_memory(msg_content, sentences[0] if sentences else "", sender_name, chat_id, user_id, buffer_snapshot)

    logger.info("✅ 管道处理完成: %d句 sent chat=%d", len(sentences), chat_id)

    # ── 后台提取用户画像（不阻塞）──
    asyncio.ensure_future(_async_extract_profile(user_id, sender_name, msg_content))


# ------用户画像后台提取------
async def _async_extract_profile(user_id: int, sender_name: str, msg: str):
    """后台异步提取用户画像，不阻塞主流程"""
    try:
        from core.user_profile import extract_from_message, update_profile
        extracted = await extract_from_message(user_id, sender_name, msg)
        if extracted:
            # 防幻觉：用户名/facts 不可能是长句子或指令
            for field in ("name", "facts"):
                val = extracted.get(field, "")
                if isinstance(val, list):
                    filtered = [v for v in val if isinstance(v, str) and len(v) < 20 and not any(
                        kw in v for kw in ("查询", "帮我", "我叫", "战绩", "域名", "搜索", "什么", "怎么", "/~")
                    )]
                    if not filtered:
                        extracted.pop(field, None)
                    else:
                        extracted[field] = filtered
                elif isinstance(val, str) and val:
                    if len(val) > 15 or any(kw in val for kw in ("查询", "帮我", "域名", "搜索", "什么", "怎么")):
                        extracted.pop(field, None)
            if not extracted:
                return
            update_profile(user_id, extracted)
            from core.logger import get_logger
            get_logger("pipeline").info("画像更新: uid=%d new=%s", user_id,
                                       {k: extracted[k] for k in sorted(extracted.keys())[:3]})
    except Exception:
        pass


# ------指令路由------
async def _handle_command_route(text, user_id, group_id, sender_name, is_group, bot_qq, raw_message=""):
    from core.config import get_config
    cfg = get_config()
    result = await handle_command(text, user_id, group_id, sender_name, is_group, bot_qq, raw_message=raw_message)
    if result is None:
        return
    if result == "__SYS_TEST_CARD__":
        await _send_test_card(group_id if is_group else user_id, is_group, user_id)
        return
    if isinstance(result, str) and result.startswith("__EQ_CARD__:"):
        png_path = result.split(":", 1)[1]
        cq = f"[CQ:image,file=file:///{png_path.replace(chr(92), '/')}]"
        if is_group:
            await send_by_chat_type(cq, group_id, is_group=True)
        else:
            await send_by_chat_type(cq, group_id, is_group=False, user_id=user_id)
        return
    clean_result = re.sub(r'\[(admin|friend|群友)\]', '', result)
    if is_group:
        await send_by_chat_type(clean_result, group_id, is_group=True)
    else:
        await send_by_chat_type(clean_result, group_id, is_group=False, user_id=user_id)


# ------测试卡片------
async def _send_test_card(chat_id, is_group, user_id):
    from utils.format_lang import format_lang
    md = format_lang("testsys.card_markdown")
    button = {
        "id": "btn_test",
        "render_data": {"label": format_lang("testsys.button_label"), "visited_label": format_lang("testsys.button_visited"), "style": 1},
        "action": {"type": 2, "permission": {"type": 2}, "data": "/~testok", "enter": True, "unsupport_tips": format_lang("testsys.unsupport_tip", default="当前版本不支持此按钮")},
    }
    keyboard = {"rows": [{"buttons": [button]}]}
    card = {"type": "markdown", "data": {"markdown": {"content": md}, "keyboard": keyboard}}
    logger.info("发送测试卡片: chat=%d is_group=%s", chat_id, is_group)
    if is_group:
        await send_raw_group(card, chat_id)
    else:
        await send_raw_user(card, user_id)


# ------msglog搜索------
def get_msglog_context(current_msg, context, chat_id):
    try:
        if len(current_msg) < 3:
            return ""
        query_parts = [current_msg]
        for line in context[-3:]:
            content = line.split(": ", 1)[-1] if ": " in line else line
            if len(content) > 6:
                query_parts.append(content)
        query = " ".join(query_parts[-3:])
        return search_msglog(chat_id, query, limit=6, max_scan=300)
    except Exception:
        return ""


def _build_at_list(context, cfg):
    import re
    seen = {}
    for line in reversed(context[-30:]):
        m = re.match(r'\[(admin|friend|群友)\]\s+(.+?):', line)
        if m:
            name = m.group(2).strip()
            for uid, n in cfg.qq_name_map.items():
                if n == name and uid not in seen:
                    seen[str(uid)] = name
                    break
        if len(seen) >= 8:
            break
    if not seen:
        return ""
    lines = ["【可 @ 的用户（用 [CQ:at,qq=QQ号] 格式）】"]
    for uid, name in seen.items():
        lines.append(f"  {name}: QQ={uid}")
    return "\n".join(lines)

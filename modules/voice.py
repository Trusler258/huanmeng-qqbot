"""
语音回复 /~voice <对话> — 调用主 LLM 管道 → 情绪映射音色变体 → 串行 TTS 合成 → 顺序发语音
- v2.3.71: 合成后端切 SenseAudio 云端 API(https://api.senseaudio.cn), 不再依赖第二台电脑 GPU 节点
- v2.3.72: 默认音色切女声 羞涩甜妹 female_0023(已购, 3 情绪变体: 平稳/开心/傲娇)
- v2.3.73: instruct 链路删除(云端不消费, 纯浪费一次 LLM 调用/句) — 情绪直接映射
  voice_id 变体(官方调参版), 全局参数 speed=1.15/pitch=2 用户实测定档
- 两层正交: 情绪在 voice_id(官方层), speed/pitch 为全局叠加参数, 互不干扰
- 可用音色: 羞涩甜妹×3 / 沙哑青年 / 儒雅道长 / 萌娃A/B
- 私聊自动注入自定义人格
"""

import asyncio
import json
import re
import uuid
from pathlib import Path

from core.config import get_config as _get_cfg
from core.context_manager import get_context_mgr
from core.logger import get_logger

logger = get_logger("voice")

DEFAULT_SPEAKER = "羞涩甜妹"
SPEAKERS = ["羞涩甜妹", "羞涩甜妹开心", "羞涩甜妹傲娇", "沙哑青年", "儒雅道长", "萌娃A", "萌娃B"]

# 情绪 -> 音色变体(官方调参版: 开心/傲娇有独立 voice_id, 其余用平稳变体)
_MOOD_SPEAKER = {
    "开心": "羞涩甜妹开心", "高兴": "羞涩甜妹开心", "兴奋": "羞涩甜妹开心",
    "愉快": "羞涩甜妹开心", "欢喜": "羞涩甜妹开心",
    "撒娇": "羞涩甜妹傲娇", "傲娇": "羞涩甜妹傲娇",
}


def _mood_to_speaker(mood: str) -> str:
    """句子情绪 -> 羞涩甜妹情绪变体(失落/生气等无变体, 回落平稳)"""
    m = (mood or "").strip()
    for key, spk in _MOOD_SPEAKER.items():
        if key in m:
            return spk
    return DEFAULT_SPEAKER


def _inject_persona(system_prompt: str, user_id: int, is_group: bool) -> str:
    """私聊人格注入"""
    if is_group:
        return system_prompt

    cfg = _get_cfg()
    try:
        from modules.op import get_persona, get_persona_memory_id
        from modules.memory import set_persona_override
        custom = get_persona(user_id, cfg.private_persona_version)
        if custom:
            memory_id = get_persona_memory_id(user_id)
            set_persona_override(user_id, memory_id)
            persona_json = json.dumps(custom, ensure_ascii=False)
            logger.debug("voice 私聊人格注入 [%d]: core=%s...", user_id, custom.get("core", "")[:40])
            return f"PERSONA:::{persona_json}:::{system_prompt}"
        else:
            set_persona_override(user_id, None)
            if cfg.private_persona_core or cfg.private_identity:
                private_core = cfg.private_persona_core or cfg.personality_core
                parts = [f"# 核心人格\n{private_core}"]
                if cfg.private_persona_side:
                    parts.append(f"# 侧面人格\n{cfg.private_persona_side}")
                ident = cfg.private_identity or cfg.identity
                parts.append(f"# 固定身份\n{ident}")
                parts.append(cfg._build_self_awareness())
                return "\n---\n".join(parts)
    except Exception as e:
        logger.warning("voice persona 注入失败: %s", e)
    return system_prompt


def _inject_voice_mode(system_prompt: str) -> str:
    """注入语音模式提示词"""
    voice_hint = (
        "\n\n【语音模式】"
        "当前回复将被转换为语音播放，请遵守以下规则：\n"
        "1. 禁用括号动作描写（如(摇了摇尾巴)）\n"
        "2. 禁用颜文字符号（如~♬✨QAQ）\n"
        "3. 不要在傍晚说\"还没睡\"\"熬夜\"——18:00-22:00 只是晚上，不是深夜\n"
        "4. 只输出适合朗读的纯文本，简短自然\n"
        "5. 需要读公式时用 LaTeX 格式输出（如 $E=mc^2$），语音合成支持口语化朗读公式"
    )
    return system_prompt + voice_hint


def _clean_text_for_tts(text: str) -> str:
    """兜底清理"""
    text = re.sub(r'[（(][^()（）]+[)）]', '', text)
    text = re.sub(r'[~～♬✨♪♫★☆※]+', '', text)
    text = re.sub(r'\b(?:QAQ|OwO|OuO|QwQ|TwT|TAT|QAQ|qwq|owo|ouo)\b', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


async def _synth_one(text: str, mood: str, speaker: str) -> tuple[Path | None, str]:
    """单句合成(情绪映射在调用方完成, speaker 为最终音色名)"""
    from services.tts import synthesize_voice
    wav_path, err = await synthesize_voice(text, speaker=speaker)
    return wav_path, err


async def cmd_voice(args, user_id, group_id, sender_name, is_group, bot_qq):
    """ /~voice <对话内容> — LLM 回复 → 情绪映射变体 + 串行合成 → 顺序发语音 """
    if not args:
        return ("喵?你想让我说什么?用法:\n"
                "  /~voice <文本>         自动情绪合成语音(按句情绪切音色变体)\n"
                "  /~voice list           列出可选音色\n"
                "  /~voice <音色> <文本>  指定音色(固定, 不随情绪切)")

    if args[0].lower() in ("list", "列表", "音色"):
        return "可选音色:\n" + "\n".join(f"  {s}" for s in SPEAKERS)

    # 解析参数(显式指定音色则固定, 否则按句情绪自动切变体)
    explicit_speaker = None
    if args[0] in SPEAKERS and len(args) >= 2:
        explicit_speaker = args[0]
        text = " ".join(args[1:])
    else:
        text = " ".join(args)

    if len(text) > 200:
        return f"太长了喵~ 最多 200 字,你给了 {len(text)} 字"

    cfg = _get_cfg()
    ctx = get_context_mgr()
    chat_id = group_id if is_group else user_id

    # 1. 获取上下文
    history = ctx.get_context(chat_id) or []
    extra_parts = []
    try:
        from modules.memory import get_top_memories
        mem = get_top_memories(text, history, chat_id=chat_id)
        if mem:
            extra_parts.append(f"【记忆】{mem}")
    except Exception:
        pass
    extra = "\n".join(extra_parts)
    role_tag = "[admin]" if user_id == cfg.admin_qq else "[friend]" if user_id in cfg.friend_qqs else "[群友]"

    # 2. 注入私聊人格 + 语音模式
    system_prompt = _inject_persona(cfg.system_prompt, user_id, is_group)
    system_prompt = _inject_voice_mode(system_prompt)

    # 3. 调用主 LLM 生成回复
    from services.llm import generate_multi_reply
    replies, _, _, _, _, mood_detail, _, _, _, _, _, _ = await generate_multi_reply(
        msg_history=history,
        speaker_name=f"{role_tag} {sender_name}",
        current_msg=text,
        bot_name=cfg.bot_name,
        system_prompt=system_prompt,
        reply_model=cfg.reply_model,
        is_group=is_group,
        extra_info=extra,
    )

    if not replies:
        return "没有生成回复喵~"

    # 4. 逐句清理 + 收集情绪
    cleaned: list[tuple[str, str]] = []  # (clean_text, mood)
    mood_list = mood_detail if isinstance(mood_detail, list) and mood_detail else []
    for i, r in enumerate(replies):
        r = re.sub(r'\[CQ:[^\]]+\]', '', r)
        r = re.sub(r'\[(?:FACE|CALL|EQ_CARD|IMG)[^\]]*\]', '', r)
        r = _clean_text_for_tts(r)
        if not r.strip():
            continue
        m = mood_list[i] if i < len(mood_list) and mood_list[i] else "平静"
        cleaned.append((r, m))

    if not cleaned:
        return "生成的回复不适合转语音喵~"

    # 5. 合成(云端 API 无状态, 无需检查节点连接)
    from services.tts import synthesize_voice, cleanup_wav

    # 6. 串行合成 + 顺序发送(按句情绪切变体; 显式指定音色则固定)
    from services.sender import send_group_msg, send_private_msg
    send = send_group_msg if is_group else send_private_msg
    to = group_id if is_group else user_id

    wav_paths = []
    for text, mood in cleaned:
        spk = explicit_speaker or _mood_to_speaker(mood)
        logger.info("voice 合成: mood=%s → %s | text=%s...", mood, spk, text[:30])
        wav_path, err = await synthesize_voice(text, speaker=spk)
        if not wav_path:
            logger.warning("voice 合成失败: %s | text=%s", err, text[:30])
            continue
        cq = f"[CQ:record,file=file:///{wav_path.as_posix()}]"
        await send(cq, to)
        wav_paths.append(wav_path)

    # 7. 延迟清理
    for wav_path in wav_paths:
        asyncio.create_task(cleanup_wav(wav_path, delay=30))

    return None

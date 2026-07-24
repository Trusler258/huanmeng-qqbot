"""语音回复 /~voice <对话> — 调用主 LLM 管道 → Edge TTS (rate/pitch/vol 情绪控制)"""

import uuid
from pathlib import Path

from core.config import get_config as _get_cfg
from core.context_manager import get_context_mgr

_OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "img_temp"

# 情绪 → rate / pitch / volume（基线提高：童声尖锐+语速快）
_MOOD_PARAMS = {
    "开心":   ("+35%", "+28Hz", "+5%"),
    "愉快":   ("+30%", "+26Hz", "+3%"),
    "兴奋":   ("+45%", "+32Hz", "+10%"),
    "难过":   ("+5%",  "+20Hz", "-10%"),
    "悲伤":   ("+0%",  "+18Hz", "-15%"),
    "生气":   ("+40%", "+30Hz", "+8%"),
    "愤怒":   ("+50%", "+34Hz", "+12%"),
    "恐惧":   ("+30%", "+26Hz", "-5%"),
    "害怕":   ("+25%", "+24Hz", "-8%"),
    "平静":   ("+15%", "+24Hz", "+1%"),
    "无奈":   ("+10%", "+22Hz", "+1%"),
    "不满":   ("+15%", "+24Hz", "+3%"),
    "害羞":   ("+25%", "+26Hz", "-5%"),
    "惊讶":   ("+35%", "+32Hz", "+5%"),
    "傲娇":   ("+25%", "+26Hz", "+3%"),
    "期待":   ("+30%", "+28Hz", "+5%"),
    "温柔":   ("+12%", "+24Hz", "-3%"),
    "好奇":   ("+28%", "+26Hz", "+3%"),
    "担心":   ("+10%", "+22Hz", "-5%"),
    "委屈":   ("+8%",  "+20Hz", "-8%"),
}


async def cmd_voice(args, user_id, group_id, sender_name, is_group, bot_qq):
    """ /~voice <对话内容> """
    if not args:
        return "喵？你想让我说什么？/~voice <对话内容>"

    text = " ".join(args)
    if len(text) > 200:
        return f"太长了喵~ 最多 200 字，你给了 {len(text)} 字"

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

    # 2. 调用主 LLM 管道
    from services.llm import generate_multi_reply
    replies, _, _, _, _, mood_detail, _, _, _, _, _ = await generate_multi_reply(
        msg_history=history,
        speaker_name=f"{role_tag} {sender_name}",
        current_msg=text,
        bot_name=cfg.bot_name,
        system_prompt=cfg.system_prompt,
        reply_model=cfg.reply_model,
        is_group=is_group,
        extra_info=extra,
    )

    if not replies:
        return "没有生成回复喵~"

    full_text = "".join(replies)

    # 3. 情绪 → rate/pitch/volume（取第一句的情绪；mood_detail 为数组时用索引 0）
    mood = "平静"
    if isinstance(mood_detail, list) and mood_detail:
        mood = mood_detail[0]
    rate, pitch, vol = _MOOD_PARAMS.get(mood, ("+15%", "+24Hz", "+1%"))

    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_file = _OUT_DIR / f"voice_{uuid.uuid4().hex[:8]}.mp3"

    try:
        import edge_tts
        tts = edge_tts.Communicate(full_text, voice="zh-CN-XiaoxiaoNeural",
                                    rate=rate, pitch=pitch, volume=vol)
        await tts.save(str(out_file))
    except Exception as e:
        return f"语音合成失败喵: {e}"

    # 4. 发送语音 + 文字
    from services.sender import send_group_msg, send_private_msg
    send = send_group_msg if is_group else send_private_msg
    to = group_id if is_group else user_id

    cq = f"[CQ:record,file=file:///{out_file}]"
    await send(cq, to)
    await send(f"♪ {full_text}", to)
    return None

"""语音回复 /~voice <对话> — LLM JSON → 分句情感 → SSML 情绪控制 → Edge TTS"""

import json
import uuid
from pathlib import Path

from services.llm import call_llm
from core.config import get_config as _get_cfg

_OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "img_temp"

# 用户中文情绪 → SSML <mstts:express-as style=""> + 物理参数
_MOOD_PARAMS = {
    "开心":   ("cheerful",    "+15%", "+4Hz",  "+5%"),
    "愉快":   ("cheerful",    "+10%", "+3Hz",  "+3%"),
    "兴奋":   ("cheerful",    "+25%", "+8Hz",  "+10%"),
    "难过":   ("sad",         "-15%", "-4Hz",  "-10%"),
    "悲伤":   ("sad",         "-20%", "-6Hz",  "-15%"),
    "生气":   ("angry",       "+20%", "+6Hz",  "+8%"),
    "愤怒":   ("angry",       "+30%", "+10Hz", "+12%"),
    "恐惧":   ("fearful",     "+10%", "+2Hz",  "-5%"),
    "害怕":   ("fearful",     "+5%",  "+1Hz",  "-8%"),
    "平静":   ("calm",        "-5%",  "0Hz",   "0%"),
    "无奈":   ("disgruntled", "-10%", "-2Hz",  "-5%"),
    "不满":   ("disgruntled", "-5%",  "0Hz",   "+3%"),
    "害羞":   ("cheerful",    "+5%",  "+2Hz",  "-5%"),
    "惊讶":   ("cheerful",    "+15%", "+8Hz",  "+5%"),
    "傲娇":   ("disgruntled", "+5%",  "+3Hz",  "+3%"),
    "期待":   ("cheerful",    "+10%", "+4Hz",  "+5%"),
    "温柔":   ("calm",        "-8%",  "+1Hz",  "-3%"),
    "好奇":   ("cheerful",    "+8%",  "+3Hz",  "+3%"),
    "担心":   ("sad",         "-10%", "-2Hz",  "-5%"),
    "委屈":   ("sad",         "-12%", "-3Hz",  "-8%"),
}


def _build_ssml(sentences: list[dict], voice: str = "zh-CN-XiaoxiaoNeural") -> str:
    """根据分句情感构建 SSML"""
    parts = [f'<speak xmlns:mstts="http://www.w3.org/2001/mstts"><voice name="{voice}">']
    for s in sentences:
        text = s.get("text", "")
        mood = s.get("mood", "平静")
        style, rate, pitch, vol = _MOOD_PARAMS.get(mood, ("calm", "0%", "0Hz", "0%"))
        parts.append(
            f'<prosody rate="{rate}" pitch="{pitch}" volume="{vol}">'
            f'<mstts:express-as style="{style}">{text}</mstts:express-as>'
            f'</prosody>'
        )
    parts.append("</voice></speak>")
    return "\n".join(parts)


async def cmd_voice(args, user_id, group_id, sender_name, is_group, bot_qq):
    """
    /~voice <对话内容>   LLM 猫娘回复 → 分句情感 → SSML 情绪控制 → QQ 语音
    """
    if not args:
        return "喵？你想让我说什么？/~voice <对话内容>"

    text = " ".join(args)
    if len(text) > 200:
        return f"太长了喵~ 最多 200 字，你给了 {len(text)} 字"

    cfg = _get_cfg()
    personality = cfg.personality_core or "你是名为幻梦的猫娘，语气软萌可爱。"
    bot_name = cfg.bot_name or "幻梦"

    # 1. LLM 生成分句 JSON
    sys_prompt = (
        f"你是猫娘{bot_name}，根据对话生成自然可爱的猫娘回复。"
        "输出纯 JSON：{\"sentences\":[{\"text\":\"分句1\",\"mood\":\"开心\"},{\"text\":\"分句2\",\"mood\":\"好奇\"}]}"
        "情绪可选: 开心 愉快 兴奋 难过 悲伤 生气 愤怒 恐惧 害怕 平静 无奈 不满 害羞 惊讶 傲娇 期待 温柔 好奇 担心 委屈"
        "每句话都要配合理情绪，至少1句最多5句。回复要口语化、带喵尾音。只输出JSON不要其他内容。"
    )
    result = await call_llm(
        cfg.reply_model,
        [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": f"{sender_name} 对你说：「{text}」。你的性格: {personality}"},
        ],
        max_tokens=500,
        temperature=0.8,
        timeout=20.0,
    )
    result = result.strip()

    # 清理 markdown / 思考块
    for tag in ("```json", "```", " response response"):
        result = result.replace(tag, "").strip()
    idx = result.find("{")
    if idx == -1:
        return f"LLM 回复格式异常喵: {result[:80]}"
    result = result[idx:]
    end = result.rfind("}") + 1
    if end > 0:
        result = result[:end]

    try:
        data = json.loads(result)
    except json.JSONDecodeError:
        return f"JSON 解析失败喵: {result[:80]}"

    sentences = data.get("sentences", [])
    if not sentences:
        sentences = [{"text": data.get("reply", result), "mood": "开心"}]

    if not sentences:
        return "生成内容为空喵~"

    # 2. 构建 SSML → Edge TTS
    ssml = _build_ssml(sentences)

    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_file = _OUT_DIR / f"voice_{uuid.uuid4().hex[:8]}.mp3"

    try:
        import edge_tts
        tts = edge_tts.Communicate(ssml=ssml)
        await tts.save(str(out_file))
    except Exception as e:
        return f"语音合成失败喵: {e}"

    # 3. 发送语音 + 文字
    cq = f"[CQ:record,file=file:///{out_file}]"
    full_text = "".join(s.get("text", "") for s in sentences)
    mood_line = " · ".join(f'{s.get("mood","?")}: {s.get("text","")}' for s in sentences)

    from services.sender import send_group_msg, send_private_msg
    send = send_group_msg if is_group else send_private_msg
    to = group_id if is_group else user_id
    await send(cq, to)
    await send(f"♪ {full_text}", to)
    return None

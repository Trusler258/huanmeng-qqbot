"""语音合成 /~voice <文本> — Edge TTS"""

import asyncio
import uuid
from pathlib import Path

_OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "img_temp"


async def cmd_voice(args, user_id, group_id, sender_name, is_group, bot_qq):
    """
    /~voice <文本>                       默认中文女声晓晓
    /~voice zh-CN-YunxiNeural <文本>     指定语音
    /~voice list                         列出可用语音
    """
    if not args:
        return "喵？你想让我说什么？/~voice <文本>\n例如 /~voice 主人最好了喵~"

    if len(args) == 1 and args[0].lower() == "list":
        from core.config import get_config
        cfg = get_config()
        return "常用语音:\n  zh-CN-XiaoxiaoNeural  女·晓晓(默认)\n  zh-CN-YunxiNeural     男·云希\n  zh-CN-XiaoyiNeural    女·晓伊\n  zh-CN-YunjianNeural   男·云健\n  zh-CN-YunxiaNeural    男·云夏\n  ja-JP-NanamiNeural    日·七海\n  en-US-JennyNeural     英·Jenny\n更多: edge-tts --list-voices"

    # 解析语音名和文本
    voice = "zh-CN-XiaoxiaoNeural"
    text_start = 0
    if args[0].startswith("zh-") or args[0].startswith("ja-") or args[0].startswith("en-"):
        voice = args[0]
        text_start = 1
    text = " ".join(args[text_start:])

    if not text.strip():
        return "要给点文字喵~"

    if len(text) > 200:
        return f"太长了喵~ 最多 200 字，你给了 {len(text)} 字"

    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_file = _OUT_DIR / f"voice_{uuid.uuid4().hex[:8]}.mp3"

    try:
        import edge_tts
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(str(out_file))
    except Exception as e:
        return f"语音合成失败喵: {e}"

    cq = f"[CQ:record,file=file:///{out_file}]"
    from services.sender import send_group_msg, send_private_msg
    if is_group:
        await send_group_msg(cq, group_id)
    else:
        await send_private_msg(cq, user_id)
    return None

"""
SenseAudio TTS 云端合成 — 替代原 Qwen3-TTS GPU 节点(v2.3.71)
- POST https://api.senseaudio.cn/v1/t2a_v2, Bearer 鉴权
- 坑1: HTTP 永远 200, 真实错误在 base_resp.status_code/status_msg
- 坑2: data.audio 是十六进制编码不是 base64, binascii.unhexlify 解码
- 坑3: get_voice 列出的音色 != 账号可用音色(实测 34 列出仅 4 可用, 其余全 403)
- 保留 synthesize_voice/cleanup_wav 接口不变, voice.py 调用点无需大改
- v2.3.73: 全局参数 speed=1.15/pitch=2(用户实测定档); instruct 参数移除(情绪走 voice_id 变体)
"""
from __future__ import annotations

import asyncio
import binascii
import os
import uuid
from pathlib import Path

from core.logger import get_logger

logger = get_logger("tts")

_SA_URL = "https://api.senseaudio.cn/v1/t2a_v2"
_SA_MODEL = "sensenova-tts-2.0"
_WAV_DIR = Path(__file__).resolve().parent.parent / "data" / "tts_temp"

# 账号实测可用音色(2026-10-01): Free 普通 4 个 + 已购 羞涩甜妹(3 情绪变体)
VOICE_IDS = {
    "羞涩甜妹": "female_0023_a",      # 平稳
    "羞涩甜妹开心": "female_0023_b",  # 开心
    "羞涩甜妹傲娇": "female_0023_c",  # 傲娇
    "沙哑青年": "male_0018_a",
    "儒雅道长": "male_0004_a",
    "萌娃A": "child_0001_a",
    "萌娃B": "child_0001_b",
}

# 旧 GPU 节点音色名 -> 云端最接近音色(向后兼容, 女声优先映射羞涩甜妹)
_LEGACY_MAP = {
    "Vivian": "羞涩甜妹", "Serena": "羞涩甜妹", "Ono_Anna": "羞涩甜妹",
    "Sohee": "羞涩甜妹", "Uncle_Fu": "儒雅道长",
    "Dylan": "儒雅道长", "Eric": "沙哑青年", "Ryan": "沙哑青年",
    "Aiden": "沙哑青年",
}


def _resolve_voice_id(speaker: str) -> str:
    name = _LEGACY_MAP.get(speaker, speaker)
    return VOICE_IDS.get(name, VOICE_IDS["羞涩甜妹"])


def _get_api_key() -> str:
    return os.environ.get("SENSEAUDIO_API_KEY", "")


async def synthesize_voice(
    text: str,
    speaker: str = "羞涩甜妹",
    timeout: float = 30.0,
) -> tuple[Path | None, str]:
    """调用 SenseAudio 云端 API 合成语音(无状态, 并发安全)

    语音模式两层正交: 情绪在 voice_id(官方调参版), speed/pitch 为全局叠加参数。
    全局参数为用户实测定档: speed=1.15, pitch=2(2026-10-01)。
    """
    if not text.strip():
        return None, "文本为空"

    api_key = _get_api_key()
    if not api_key:
        return None, "SENSEAUDIO_API_KEY 未配置(放 config/.env)"

    voice_id = _resolve_voice_id(speaker)
    payload = {
        "model": _SA_MODEL,
        "text": text,
        "stream": False,
        "voice_setting": {"voice_id": voice_id, "speed": 1.15, "vol": 1, "pitch": 2},
        "audio_setting": {
            "format": "mp3", "sample_rate": 32000,
            "bitrate": 128000, "channel": 1,
        },
    }

    import httpx
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                _SA_URL,
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )
    except Exception as e:
        return None, f"请求失败: {e}"

    if resp.status_code != 200:
        return None, f"HTTP {resp.status_code}"

    try:
        data = resp.json()
    except Exception as e:
        return None, f"响应解析失败: {e}"

    # 坑1: HTTP 永远 200, 真实错误在 base_resp
    base = data.get("base_resp", {})
    code = base.get("status_code", -1)
    if code != 0:
        return None, f"SenseAudio 错误 {code}: {base.get('status_msg', '未知')}"

    audio_hex = data.get("data", {}).get("audio", "")
    if not audio_hex:
        return None, "返回数据为空"

    try:
        # 坑2: 十六进制编码, 不是 base64
        mp3_bytes = binascii.unhexlify(audio_hex)
    except Exception as e:
        return None, f"音频解码失败: {e}"

    _WAV_DIR.mkdir(parents=True, exist_ok=True)
    mp3_path = _WAV_DIR / f"voice_{uuid.uuid4().hex[:8]}.mp3"
    mp3_path.write_bytes(mp3_bytes)

    ei = data.get("extra_info", {})
    logger.info("TTS 合成成功: %s (%s 字符, %dKB, 音色=%s)",
                mp3_path.name, ei.get("usage_characters", "?"),
                len(mp3_bytes) // 1024, voice_id)
    return mp3_path, ""


async def cleanup_wav(wav_path: Path, delay: float = 30):
    await asyncio.sleep(delay)
    try:
        if wav_path.exists():
            wav_path.unlink()
    except Exception:
        pass

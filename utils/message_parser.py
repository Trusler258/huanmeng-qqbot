"""
消息解析器（原 type_analysis.py）
- 解析 OneBot 11 标准事件 JSON
- 支持 array 段格式 和 raw_message CQ 码格式
- 返回统一元组: (消息类型, 内容, 对话ID, 发送者昵称)
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

# ── 媒体类型映射 ────────────────────────────────────────────
_MEDIA_TYPES: Dict[str, str] = {
    "image":   "图片",
    "record":  "语音",
    "voice":   "语音",
    "video":   "视频",
    "file":    "文件",
    "forward": "转发",
}

_MEDIA_CQ: Dict[str, str] = {
    "[CQ:image":  "图片",
    "[CQ:record": "语音",
    "[CQ:voice":  "语音",
    "[CQ:video":  "视频",
    "[CQ:file":   "文件",
}


def _safe_json_load(msg: str) -> Optional[Dict[str, Any]]:
    """安全解析 JSON，失败返回 None"""
    try:
        return json.loads(msg)
    except Exception:
        return None


def _is_message_event(event: Dict[str, Any]) -> bool:
    """检查是否为消息事件（兼容 NapCat 缺失 post_type 的情况）"""
    if event.get("post_type") == "message":
        return True
    # NapCat 偶发不携带 post_type，但有 message_type
    return bool(event.get("message_type"))


def _extract_sender_name(sender: Dict[str, Any]) -> str:
    """从 sender 字段提取显示名称，优先级: card > nickname > user_id > 空"""
    name = sender.get("card") or sender.get("nickname") or sender.get("user_id") or ""
    return str(name)


def _parse_array_segments(segments: List[Dict[str, Any]]) -> Tuple[str, str, Optional[int], str]:
    """
    解析 array 格式的 message 段。
    Returns:
        (消息类型, 文本内容或媒体URL, 引用消息ID, 媒体前的文字前缀)
    """
    text_parts: List[str] = []
    media_url = ""
    reply_msg_id = None   # ★ 新增：引用消息 ID

    for seg in segments:
        seg = seg or {}
        t = seg.get("type")
        data = seg.get("data", {}) or {}

        if t == "text":
            text_parts.append(str(data.get("text", "")))
        elif t == "at":
            qq = data.get("qq")
            if qq:
                text_parts.append(f"@{qq}")
        elif t == "reply":  # ★ 新增：提取引用消息段
            rid = data.get("id")
            if rid:
                try:
                    reply_msg_id = int(rid)
                except (ValueError, TypeError):
                    pass
        elif t == "face":
            pass  # 忽略表情
        elif t in _MEDIA_TYPES:
            if t == "image":
                media_url = data.get("url", "")
            elif t == "forward":
                # 合并转发：取 forward_id 作为 content，供 dispatcher 调用 get_forward_msg
                media_url = data.get("id", "")
            # ★ 如果有前面的文字，保留在 text_prefix 里
            text_prefix = " ".join(text_parts).strip()
            return _MEDIA_TYPES[t], media_url, reply_msg_id, text_prefix

    return "文字", "".join(text_parts).strip(), reply_msg_id, ""


def _parse_raw_message(raw: str) -> Tuple[str, str, Optional[int], str]:
    """
    解析 raw_message 的 CQ 码格式。
    Returns:
        (消息类型, URL或原文, 引用消息ID, 媒体前的文字前缀)
    """
    # ★ 提取 CQ:reply
    reply_id = None
    reply_match = re.search(r'\[CQ:reply,id=(\d+)\]', raw)
    if reply_match:
        try:
            reply_id = int(reply_match.group(1))
        except (ValueError, TypeError):
            pass

    for cq, msg_type in _MEDIA_CQ.items():
        if cq in raw:
            text_prefix = raw[:raw.index(cq)].replace("[CQ:reply,id=...]", "").strip() if cq in raw else ""
            url_start = raw.find("url=")
            if url_start != -1:
                url_end = raw.find("]", url_start)
                if url_end != -1:
                    url = raw[url_start + 4:url_end]
                    return msg_type, url, reply_id, text_prefix
            return msg_type, "", reply_id, text_prefix
    return "文字", raw, reply_id, ""


def parse_msg(msg: str) -> Optional[Tuple[str, str, int, str, Optional[int], str]]:
    """
    解析一条完整的 OneBot 事件消息。

    Args:
        msg: WebSocket 收到的原始 JSON 字符串

    Returns:
        (消息类型, 消息内容, 群号/用户ID, 发送者昵称, 引用消息ID, 媒体前文字前缀)
        或 None（非消息事件时）
    """
    event = _safe_json_load(msg)
    if not event or not _is_message_event(event):
        return None

    # ── 提取基础字段 ──
    message_type = event.get("message_type", "")
    group_id_raw = event.get("group_id") if message_type == "group" else event.get("user_id") or 0
    try:
        group_id = int(group_id_raw)
    except Exception:
        group_id = 0

    sender = event.get("sender", {}) or {}
    name = _extract_sender_name(sender)

    # ── 根据 message_format 选择解析方式 ──
    segments = event.get("message")
    message_format = event.get("message_format")
    raw_message = event.get("raw_message", "") or ""

    if isinstance(segments, list) and message_format == "array":
        msg_type, content, reply_id, text_prefix = _parse_array_segments(segments)
    else:
        msg_type, content, reply_id, text_prefix = _parse_raw_message(raw_message)

    return msg_type, content, group_id, name, reply_id, text_prefix

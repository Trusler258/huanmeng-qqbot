"""LLM 回复总开关（文件开关，重启不失效）。

## 为什么需要

2026-10-28 用户要求：应对 QQ 平台政策，**暂停机器人的 LLM 对话回复**，
但指令（`/~wdsj`、`/~help` 这类）必须照常执行。
原来的 `/~unstats` 那套是内存开关、按群、重启即失效，不适合做"全局停服"。

## 用法

- 关：`/~llm off`（管理员），或直接 `touch data/llm_reply_disabled`
- 开：`/~llm on`（管理员），或删掉该文件
- 查：`/~llm` / `/~llm status`

开关状态只看**文件是否存在**，文件里存一份 since/by 便于排查"谁什么时候关的"。
关闭时 `core/pipeline.py` 在「回复判断」之前直接 return——
判定不跑、LLM 不调、消息不发，连 token 都省了。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from core.logger import get_logger

logger = get_logger("llm_switch")

_ROOT = Path(__file__).resolve().parent.parent
FLAG_FILE = _ROOT / "data" / "llm_reply_disabled"


def is_llm_enabled() -> bool:
    """全站 LLM 是否可用（默认开启，开关文件存在即全关）。

    2026-10-31 扩义：原来只拦「回复」，现在连记忆压缩、会话摘要、用户画像、
    工具里的代码生成一起停——用户要求「LLM 完全禁用，不是删代码」。
    真正的收口在 services.llm.call_llm，所有调用方都会经过它。
    """
    return not FLAG_FILE.exists()


# 兼容旧名（pipeline 等处还在用）
is_llm_reply_enabled = is_llm_enabled


def state() -> dict:
    """当前状态：{"enabled": bool, "since": "YYYY-mm-dd HH:MM:SS", "by": uid}。"""
    if not FLAG_FILE.exists():
        return {"enabled": True, "since": "", "by": 0}
    try:
        data = json.loads(FLAG_FILE.read_text(encoding="utf-8") or "{}")
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    try:
        by = int(data.get("by") or 0)
    except (TypeError, ValueError):
        by = 0
    return {"enabled": False, "since": str(data.get("since") or ""), "by": by}


def set_llm_reply_enabled(on: bool, by: int = 0) -> bool:
    """切换开关，返回「状态是否真的发生了变化」。

    已开还想开 / 已关还想关 → 返回 False，调用方据此回不同文案。
    """
    if on:
        if not FLAG_FILE.exists():
            return False
        try:
            FLAG_FILE.unlink()
        except OSError as e:
            logger.error("删除 LLM 开关文件失败: %s", e, exc_info=True)
            return False
        logger.info("LLM 回复已开启（操作者 %s）", by)
        return True

    if FLAG_FILE.exists():
        return False
    try:
        FLAG_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {"since": time.strftime("%Y-%m-%d %H:%M:%S"), "by": by}
        FLAG_FILE.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as e:
        logger.error("写入 LLM 开关文件失败: %s", e, exc_info=True)
        return False
    logger.info("LLM 回复已关闭（操作者 %s）", by)
    return True

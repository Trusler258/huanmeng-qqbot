"""
幻梦的笔记本 — LLM 主动维护的长期记忆

与 modules/memory.py 的区别：
- memory.py 是被动摘要（自动从群聊提取压缩）
- 本模块是主动笔记（LLM 判断值得记才写，如群内关系/称呼/约定/黑话）

存储：data/notes/<chat_id>.md，一行一条 `- [MM-DD] 内容`
上限 MAX_NOTES 条，超出自动淘汰最旧（保留文件不无限膨胀）

用法：
- LLM 通过 calls 调 note 指令写入（见 main_skill.md 笔记本规则）
- 用户可用 /~note 查看/增删
- 上下文注入见 core/pipeline.py 的 extra_info 组装
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from core.logger import get_logger

logger = get_logger("notes")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
NOTES_DIR = DATA_DIR / "notes"

MAX_NOTES = 100          # 单群笔记上限，超出淘汰最旧
INJECT_LIMIT = 40        # 注入上下文的最大条数（省 token，最新的优先）
LINE_MAX = 200           # 单条笔记最大长度


def _note_file(chat_id) -> Path:
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    return NOTES_DIR / f"{chat_id}.md"


def _load_lines(chat_id) -> list[str]:
    f = _note_file(chat_id)
    if not f.exists():
        return []
    try:
        return [ln.strip() for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except Exception as e:
        logger.warning("读取笔记失败 %s: %s", chat_id, e)
        return []


def _save_lines(chat_id, lines: list[str]) -> None:
    try:
        _note_file(chat_id).write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception as e:
        logger.warning("写入笔记失败 %s: %s", chat_id, e)


def add_note(chat_id, text: str) -> str:
    """追加一条笔记，返回给调用方（LLM/用户）的提示语"""
    text = (text or "").strip().replace("\n", " ")
    if not text:
        return "笔记内容为空，没记"
    if len(text) > LINE_MAX:
        text = text[:LINE_MAX] + "…"

    lines = _load_lines(chat_id)
    tag = datetime.now().strftime("%m-%d")
    entry = f"- [{tag}] {text}"

    # 完全重复的不重复记
    if any(text in ln for ln in lines):
        return f"这条已经记过了：{text}"

    lines.append(entry)
    if len(lines) > MAX_NOTES:
        lines = lines[-MAX_NOTES:]
    _save_lines(chat_id, lines)
    logger.info("新增笔记 chat=%s: %s", chat_id, text[:60])
    return f"已记下：{text}"


def load_notes(chat_id, limit: int = INJECT_LIMIT) -> str:
    """加载笔记文本用于注入上下文；无笔记返回空串"""
    lines = _load_lines(chat_id)
    if not lines:
        return ""
    total = len(lines)
    shown = lines[-limit:]
    header = f"【你的笔记本】你之前记下的 {total} 条群内信息（需要时可增删，用 calls 调 note 指令）："
    return header + "\n" + "\n".join(shown)


def list_notes(chat_id) -> str:
    """给用户看的笔记列表"""
    lines = _load_lines(chat_id)
    if not lines:
        return "笔记本还是空的~ 说「记一下xxx」我就会记下来"
    out = [f"【笔记本】共 {len(lines)} 条："]
    out.extend(f"{i}. {ln.lstrip('- ')}" for i, ln in enumerate(lines, 1))
    return "\n".join(out)


def delete_note(chat_id, index: int) -> str:
    """按序号删除（序号来自 list_notes，从 1 开始）"""
    lines = _load_lines(chat_id)
    if not lines:
        return "笔记本是空的"
    if index < 1 or index > len(lines):
        return f"序号超出范围（当前 {len(lines)} 条）"
    removed = lines.pop(index - 1)
    _save_lines(chat_id, lines)
    return f"已删除：{removed.lstrip('- ')}"


def clear_notes(chat_id) -> str:
    lines = _load_lines(chat_id)
    n = len(lines)
    _save_lines(chat_id, [])
    return f"已清空 {n} 条笔记"


def note_count(chat_id) -> int:
    return len(_load_lines(chat_id))


async def cmd_note(args, user_id, group_id, sender_name, is_group, bot_qq):
    """笔记本指令 /~note

    /~note              查看笔记
    /~note <内容>       手动记一条
    /~note del <序号>   删除某条
    /~note clear        清空（仅管理员）
    """
    chat_id = group_id if is_group else user_id
    a = list(args or [])

    if not a:
        return list_notes(chat_id)

    head = a[0].lower()
    if head in ("del", "delete", "删除", "-"):
        if len(a) < 2 or not a[1].isdigit():
            return "用法: /~note del <序号>（序号用 /~note 查看）"
        return delete_note(chat_id, int(a[1]))
    if head in ("clear", "清空"):
        from core.config import get_config
        cfg = get_config()
        allowed = set()
        for v in (getattr(cfg, "admin_qq", 0), bot_qq):
            try:
                if v:
                    allowed.add(int(v))
            except (ValueError, TypeError):
                pass
        try:
            uid = int(user_id)
        except (ValueError, TypeError):
            uid = 0
        if uid not in allowed:
            return "清空笔记本需要管理员权限"
        return clear_notes(chat_id)

    # 其余情况：手动记一条
    return add_note(chat_id, " ".join(a))

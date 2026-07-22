"""
群消息撤回记录模块 v3
- 所有消息写入 msglog_{群号}.jsonl（一行一条）
- 撤回时标记 recalled=true（原文永久保留）
- /~recall 只读标记过的记录，再也不丢内容
- 图片自动下载保存，撤回可原图重现
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from core.logger import get_logger

logger = get_logger("recall")

_ROOT = Path(__file__).resolve().parent.parent
_MSGLOG_DIR = _ROOT / "data" / "msglog"
_IMAGE_DIR = _ROOT / "data" / "recall_images"
_MAX_PER_GROUP = 500   # 每群最多保留 500 条消息
_RECALL_TTL = 14 * 86400  # 撤回记录保留 14 天（未撤回消息 7 天后自动清理）


def _msglog_path(group_id: int) -> Path:
    _MSGLOG_DIR.mkdir(parents=True, exist_ok=True)
    return _MSGLOG_DIR / f"msglog_{group_id}.jsonl"


def _save_image(image_url: str, entry: dict):
    """下载图片到本地，把路径写回 entry"""
    try:
        _IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        url_hash = hashlib.md5(image_url.encode()).hexdigest()[:12]
        img_path = _IMAGE_DIR / f"recall_{url_hash}.jpg"
        if img_path.exists():
            entry["img_path"] = str(img_path)
            return
        import httpx
        resp = httpx.get(image_url, timeout=10)
        resp.raise_for_status()
        img_path.write_bytes(resp.content)
        entry["img_path"] = str(img_path)
    except Exception as e:
        logger.debug("下载图片失败: %s", str(e)[:40])
        entry["img_url"] = image_url


def record_incoming_message(group_id: int, user_id: int, message_id: int,
                            msg_type: str, msg_content: str, image_url: str = ""):
    """录制一条群消息到 msglog 文件（一行 JSONL）"""
    entry = {
        "msg_id": int(message_id),
        "time": int(time.time()),
        "user_id": user_id,
        "type": msg_type,
        "content": msg_content,
        "recalled": False,
    }
    path = _msglog_path(group_id)
    # 图片：异步下载保存到本地
    if msg_type == "图片" and image_url:
        _save_image(image_url, entry)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("写入 msglog 失败 group=%d: %s", group_id, e)


def mark_recalled(group_id: int, message_id: int, operator_id: int, user_id: int = 0):
    """标记一条消息为已撤回（原地修改 msglog 文件）"""
    msg_id = int(message_id)
    path = _msglog_path(group_id)

    # 如果没有任何消息记录（重启后首次撤回），创建幽灵条目
    if not path.exists():
        phantom = {
            "msg_id": msg_id,
            "time": int(time.time()) - 60,
            "user_id": user_id,
            "type": "text",
            "content": "[重启后未录制]",
            "recalled": True,
            "recalled_by": operator_id,
            "recall_time": int(time.time()),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(phantom, ensure_ascii=False) + "\n", encoding="utf-8")
        logger.info("撤回标记(幽灵): group=%d msg_id=%d op=%d", group_id, msg_id, operator_id)
        return

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        found = False
        new_lines = []
        for line in lines:
            try:
                entry = json.loads(line)
            except Exception:
                new_lines.append(line)
                continue
            # ★ msg_id=0 的条目（bot 消息 fire-and-forget）不参与撤回匹配
            # 因为多条 bot 消息会共用 msg_id=0，会错误地匹配
            entry_mid = int(entry.get("msg_id", 0))
            if entry_mid > 0 and entry_mid == msg_id:
                entry["recalled"] = True
                entry["recalled_by"] = operator_id
                entry["recall_time"] = int(time.time())
                recalled_content = entry.get("content", "")
                found = True
            new_lines.append(json.dumps(entry, ensure_ascii=False))
        if found:
            path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
            logger.info("撤回: group=%d msg_id=%d op=%d content='%s'",
                       group_id, msg_id, operator_id, recalled_content[:30])
        else:
            logger.debug("撤回(幽灵): group=%d msg_id=%d op=%d",
                       group_id, msg_id, operator_id)
            phantom = {
                "msg_id": msg_id,
                "time": int(time.time()) - 60,
                "user_id": user_id,
                "type": "text",
                "content": "[原文未录制]",
                "recalled": True,
                "recalled_by": operator_id,
                "recall_time": int(time.time()),
            }
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(phantom, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("标记撤回失败 group=%d: %s", group_id, e)


def get_recent_recalls(group_id: int, count: int = 10) -> list[dict]:
    """获取指定群的最近 N 条撤回记录（最新在前）"""
    path = _msglog_path(group_id)
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        recalled = []
        for line in lines:
            try:
                entry = json.loads(line)
            except Exception:
                continue
            if entry.get("recalled"):
                recalled.append(entry)
        # 最新在前
        recalled.sort(key=lambda r: r.get("recall_time", r["time"]), reverse=True)
        return recalled[:count]
    except Exception as e:
        logger.warning("读取撤回记录失败 group=%d: %s", group_id, e)
        return []


def flush_buffer():
    """退出时清理过期消息（保留最近 7 天未撤回 + 14 天已撤回）"""
    now = int(time.time())
    cutoff_normal = now - 7 * 86400   # 未撤回保留 7 天
    cutoff_recalled = now - _RECALL_TTL  # 已撤回保留 14 天

    if not _MSGLOG_DIR.exists():
        return
    for f in _MSGLOG_DIR.iterdir():
        if not f.name.startswith("msglog_") or not f.name.endswith(".jsonl"):
            continue
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
            kept = []
            for line in lines:
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                t = entry.get("time", 0)
                if entry.get("recalled"):
                    if t > cutoff_recalled:
                        kept.append(line)
                else:
                    if t > cutoff_normal:
                        kept.append(line)
            f.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
        except Exception as e:
            logger.warning("清理 msglog 失败 %s: %s", f.name, e)
    logger.debug("msglog 清理完成")

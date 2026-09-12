"""消息中心：全文检索（SQLite FTS5）/ msglog 浏览 / 撤回记录"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from panel import auth, config
from panel.security import sanitize_text

CST = timezone(timedelta(hours=8))


def _normalize_fts_query(text: str) -> str:
    """把用户输入规整为安全的 FTS5 查询串。

    与 `db/store.py` 的 `_normalize_fts_query` 保持同一规则：
    去掉会破坏 FTS 语法的字符（引号、星号、括号、冒号等），
    只保留中日韩 / 字母数字 / 空格。否则用户输入一个 `"` 就会让 MATCH 抛异常。
    """
    cleaned = re.sub(r"[^\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7afa-zA-Z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", cleaned).strip()

router = APIRouter(prefix="/messages", tags=["消息"], dependencies=[Depends(auth.require_user)])

DATA = config.DATA_DIR
MSGLOG = DATA / "msglog"

# 数据库路径候选。`data/search.db` 是 db/store.py 的真实落点（v2.1.x），
# 其余为历史/兼容路径——store.py 换路径时这里跟着加即可。
_DB_CANDIDATES = [
    DATA / "search.db",
    DATA / "huanmeng.db",
    config.ROOT / "huanmeng.db",
    DATA / "store.db",
]


def _db_path() -> Path | None:
    for p in _DB_CANDIDATES:
        if p.exists():
            return p
    return None


@router.get("/search", summary="全文检索历史消息")
async def search(
    q: str = Query("", max_length=100),
    chat: str = Query("", max_length=20),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """优先走 FTS5（trigram），不可用时降级 LIKE。

    表结构以 `db/store.py` 为准（这是唯一数据源，别处不要另立一份）：
        messages(id INTEGER PK, chat_id INTEGER, user_id INTEGER,
                 name TEXT, content TEXT, ts REAL)
        messages_fts(content, content='messages', content_rowid='id', tokenize='trigram')
    """
    db = _db_path()
    if db is None:
        return {"ok": False, "error": "未找到消息数据库", "items": [], "engine": "none"}

    if not q and not chat:
        sql = "SELECT * FROM messages ORDER BY id DESC LIMIT ? OFFSET ?"
        params: list = [limit, offset]
        engine = "list"
    elif q:
        cleaned = _normalize_fts_query(q)
        if not cleaned:
            return {"ok": False, "error": "查询词无有效字符", "items": [], "engine": "fts5"}
        # ⚠️ 与 db/store.py 对齐：trigram 分词器对 <3 字的查询无效，
        # 那边也是 `len(q) >= 3` 才走 FTS，否则退回 LIKE。
        # 这里必须同规则，否则短词搜索会静默返回 0 条。
        use_fts = len(cleaned) >= 3
        if use_fts:
            sql = """
                SELECT m.* FROM messages m
                JOIN messages_fts f ON f.rowid = m.id
                WHERE messages_fts MATCH ?
                ORDER BY m.id DESC LIMIT ? OFFSET ?
            """
            params = [cleaned, limit, offset]
            engine = "fts5"
        else:
            sql = "SELECT * FROM messages WHERE content LIKE ? ORDER BY id DESC LIMIT ? OFFSET ?"
            params = [f"%{cleaned}%", limit, offset]
            engine = "like-short"
    else:
        if not chat.isdigit():
            raise HTTPException(400, "chat 必须是数字")
        sql = "SELECT * FROM messages WHERE chat_id = ? ORDER BY id DESC LIMIT ? OFFSET ?"
        params = [int(chat), limit, offset]
        engine = "by-chat"

    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        con.row_factory = sqlite3.Row
        try:
            rows = [dict(r) for r in con.execute(sql, params).fetchall()]
        except sqlite3.OperationalError:
            # FTS 表不存在 / 语法问题 → 降级 LIKE
            if not q:
                raise
            like = f"%{q}%"
            sql2 = "SELECT * FROM messages WHERE content LIKE ? ORDER BY id DESC LIMIT ? OFFSET ?"
            rows = [dict(r) for r in con.execute(sql2, [like, limit, offset]).fetchall()]
            engine = "like-fallback"
        con.close()
    except Exception as e:
        return {"ok": False, "error": f"查询失败: {e}", "items": [], "engine": engine}

    for r in rows:
        if isinstance(r.get("content"), str):
            r["content"] = sanitize_text(r["content"])[:3000]
        if isinstance(r.get("name"), str):
            r["name"] = sanitize_text(r["name"])[:100]

    return {"ok": True, "engine": engine, "count": len(rows), "items": rows, "db": db.name}


@router.get("/stats", summary="数据库概况")
async def db_stats():
    db = _db_path()
    if db is None:
        return {"ok": False, "error": "未找到消息数据库"}
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        total = con.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        by_chat = [
            {"chat_id": str(r[0]), "count": r[1]}
            for r in con.execute(
                "SELECT chat_id, COUNT(*) c FROM messages GROUP BY chat_id ORDER BY c DESC LIMIT 30"
            ).fetchall()
        ]
        span = con.execute(
            "SELECT MIN(ts), MAX(ts) FROM messages"
        ).fetchone()
        con.close()

        def _fmt(ts) -> str:
            try:
                return datetime.fromtimestamp(float(ts), CST).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                return ""

        return {
            "ok": True, "db": db.name, "size": db.stat().st_size,
            "total": total, "tables": tables, "by_chat": by_chat,
            "oldest": _fmt(span[0]) if span else "",
            "newest": _fmt(span[1]) if span else "",
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── msglog：bot 实际发出的每条消息 ─────────────────────────

@router.get("/msglog/files", summary="msglog 文件列表")
async def msglog_files():
    if not MSGLOG.exists():
        return {"ok": True, "items": []}
    items = []
    for p in sorted(MSGLOG.glob("*.jsonl")):
        try:
            st = p.stat()
            items.append({
                "chat_id": p.stem.replace("msglog_", ""),
                "file": p.name,
                "size": st.st_size,
                "mtime": datetime.fromtimestamp(st.st_mtime, CST).strftime("%Y-%m-%d %H:%M:%S"),
            })
        except Exception:
            pass
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return {"ok": True, "items": items}


@router.get("/msglog/{chat_id}", summary="读某会话 bot 发出的消息")
async def msglog_read(
    chat_id: str,
    limit: int = Query(100, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    q: str = Query("", max_length=100),
):
    """⚠️ 排查「某功能到底发出去没有」必须查这里，主日志里没有。"""
    if not chat_id.isdigit():
        raise HTTPException(400, "会话 ID 必须是数字")
    path = MSGLOG / f"msglog_{chat_id}.jsonl"
    if not path.exists():
        return {"ok": True, "chat_id": chat_id, "items": [], "total": 0, "exists": False}

    rows = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if q:
                    blob = json.dumps(obj, ensure_ascii=False)
                    if q not in blob:
                        continue
                rows.append(obj)
    except Exception as e:
        raise HTTPException(500, f"读取失败: {e}")

    total = len(rows)
    rows = list(reversed(rows))  # 新的在前
    page = rows[offset: offset + limit]
    for r in page:
        if isinstance(r, dict):
            for k in ("text", "content", "message", "msg"):
                if k in r and isinstance(r[k], str):
                    r[k] = sanitize_text(r[k])[:3000]

    return {"ok": True, "chat_id": chat_id, "exists": True,
            "total": total, "items": page}

"""
SQLite + FTS5 全文检索数据层（移植自 huanmeng-kook-bot 的 db/ 思路，适配 qqbot）

目标：把聊天记录 / 长期记忆结构化存储到 SQLite，并用 FTS5 做高效全文检索，
替代纯 JSONL 关键词回溯（modules/memory.py 的 search_msglog），支持跨消息排名。

设计原则（与 qqbot 现有存储一致）：
- 与 JSON 存储并存，不破坏现有 fav.json / memory_*.md / context_cache.json。
- 仅作为"检索加速层"，所有写操作失败都不影响聊天主流程。
- FTS5 不可用时降级为 SQL LIKE 子串检索（对中文同样有效）。
- trigram 分词器对中文子串友好；低于 3 字的查询走 LIKE。
"""

from __future__ import annotations

import re
import sqlite3
import threading
import time
from pathlib import Path

from core.logger import get_logger

logger = get_logger("db")

_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "search.db"
_LOCK = threading.RLock()

_FTS_AVAILABLE: bool | None = None


def fts5_available() -> bool:
    """探测 SQLite 是否支持 FTS5（含 trigram 分词器）。"""
    global _FTS_AVAILABLE
    if _FTS_AVAILABLE is not None:
        return _FTS_AVAILABLE
    try:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE VIRTUAL TABLE t USING fts5(x, tokenize='trigram')")
        conn.close()
        _FTS_AVAILABLE = True
    except Exception as e:
        logger.warning("SQLite FTS5(trigram) 不可用，搜索降级为 LIKE: %s", e)
        _FTS_AVAILABLE = False
    return _FTS_AVAILABLE


def _normalize_fts_query(text: str) -> str:
    """把用户输入规整为安全的 FTS5 查询串（trigram 子串匹配）。"""
    # 去掉会破坏 FTS 语法的特殊字符，保留中日韩/字母数字/空格
    cleaned = re.sub(r'[^\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7afa-zA-Z0-9\s]', ' ', text)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


class SearchStore:
    """聊天记录 / 记忆的全文检索存储。available=False 时所有方法安全降级。"""

    def __init__(self, db_path: Path = _DB_PATH):
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._fts = fts5_available()
        if not self._fts:
            logger.info("搜索数据库将以 LIKE 模式运行（无 FTS5）")
            # 即便没有 FTS5，仍可用普通表 + LIKE
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._init_schema()
        except Exception as e:
            logger.warning("搜索数据库初始化失败，降级为无索引: %s", e)
            self._conn = None

    def _init_schema(self):
        assert self._conn is not None
        cur = self._conn.cursor()
        cur.execute(
            "CREATE TABLE IF NOT EXISTS messages("
            "id INTEGER PRIMARY KEY, chat_id INTEGER, user_id INTEGER, "
            "name TEXT, content TEXT, ts REAL)"
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_msg_chat ON messages(chat_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_msg_ts ON messages(ts)")
        if self._fts:
            try:
                cur.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts "
                    "USING fts5(content, content='messages', content_rowid='id', tokenize='trigram')"
                )
            except Exception:
                # 极老版本无 trigram，退回普通 fts5（中文需逐字匹配）
                cur.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts "
                    "USING fts5(content, content='messages', content_rowid='id')"
                )
        cur.execute(
            "CREATE TABLE IF NOT EXISTS memory(id INTEGER PRIMARY KEY, chat_id INTEGER, content TEXT)"
        )
        if self._fts:
            cur.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts "
                "USING fts5(content, content='memory', content_rowid='id', tokenize='trigram')"
            )
        self._conn.commit()

    @property
    def available(self) -> bool:
        return self._conn is not None

    # ── 写入 ──────────────────────────────────────────

    def index_message(self, chat_id: int, user_id: int, name: str, content: str, ts: float | None = None):
        if not self.available:
            return
        if not content or content.startswith("[CQ:"):
            return
        try:
            ts = ts or time.time()
            with _LOCK:
                cur = self._conn.cursor()
                cur.execute(
                    "INSERT INTO messages(chat_id,user_id,name,content,ts) VALUES(?,?,?,?,?)",
                    (chat_id, user_id, name, content, ts),
                )
                rid = cur.lastrowid
                if self._fts:
                    cur.execute("INSERT INTO messages_fts(rowid, content) VALUES(?,?)", (rid, content))
                self._conn.commit()
        except Exception as e:
            logger.warning("索引消息失败: %s", e)

    def index_memory(self, chat_id: int, content: str):
        if not self.available or not content:
            return
        try:
            with _LOCK:
                cur = self._conn.cursor()
                cur.execute("INSERT INTO memory(chat_id,content) VALUES(?,?)", (chat_id, content))
                rid = cur.lastrowid
                if self._fts:
                    cur.execute("INSERT INTO memory_fts(rowid, content) VALUES(?,?)", (rid, content))
                self._conn.commit()
        except Exception as e:
            logger.warning("索引记忆失败: %s", e)

    # ── 检索 ──────────────────────────────────────────

    def search_messages(self, query: str, chat_id: int | None = None, limit: int = 10) -> list[dict]:
        if not self.available:
            return []
        q = query.strip()
        if not q:
            return []
        try:
            with _LOCK:
                cur = self._conn.cursor()
                if self._fts and len(q) >= 3:
                    fts_q = _normalize_fts_query(q)
                    if fts_q:
                        if chat_id is not None:
                            rows = cur.execute(
                                "SELECT m.content, m.name, m.user_id, m.ts FROM messages_fts f "
                                "JOIN messages m ON m.id=f.rowid "
                                "WHERE messages_fts MATCH ? AND m.chat_id=? ORDER BY m.ts DESC LIMIT ?",
                                (fts_q, chat_id, limit),
                            ).fetchall()
                        else:
                            rows = cur.execute(
                                "SELECT m.content, m.name, m.user_id, m.ts FROM messages_fts f "
                                "JOIN messages m ON m.id=f.rowid "
                                "WHERE messages_fts MATCH ? ORDER BY m.ts DESC LIMIT ?",
                                (fts_q, limit),
                            ).fetchall()
                        if rows:
                            return _rows_to_msgs(rows)
                # 降级：LIKE 子串（中文同样可用）
                like = f"%{q}%"
                if chat_id is not None:
                    rows = cur.execute(
                        "SELECT content, name, user_id, ts FROM messages "
                        "WHERE content LIKE ? AND chat_id=? ORDER BY ts DESC LIMIT ?",
                        (like, chat_id, limit),
                    ).fetchall()
                else:
                    rows = cur.execute(
                        "SELECT content, name, user_id, ts FROM messages "
                        "WHERE content LIKE ? ORDER BY ts DESC LIMIT ?",
                        (like, limit),
                    ).fetchall()
                return _rows_to_msgs(rows)
        except Exception as e:
            logger.warning("FTS/LIKE 消息搜索失败: %s", e)
            return []

    def search_memory(self, query: str, chat_id: int | None = None, limit: int = 10) -> list[str]:
        if not self.available:
            return []
        q = query.strip()
        if not q:
            return []
        try:
            with _LOCK:
                cur = self._conn.cursor()
                like = f"%{q}%"
                if chat_id is not None:
                    rows = cur.execute(
                        "SELECT content FROM memory WHERE content LIKE ? AND chat_id=? "
                        "ORDER BY id DESC LIMIT ?",
                        (like, chat_id, limit),
                    ).fetchall()
                else:
                    rows = cur.execute(
                        "SELECT content FROM memory WHERE content LIKE ? ORDER BY id DESC LIMIT ?",
                        (like, limit),
                    ).fetchall()
                return [r[0] for r in rows]
        except Exception as e:
            logger.warning("记忆搜索失败: %s", e)
            return []

    def count(self) -> int:
        if not self.available:
            return 0
        with _LOCK:
            return self._conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]

    def clear(self):
        if not self.available:
            return
        with _LOCK:
            self._conn.execute("DELETE FROM messages")
            self._conn.execute("DELETE FROM memory")
            if self._fts:
                self._conn.execute("DELETE FROM messages_fts")
                self._conn.execute("DELETE FROM memory_fts")
            self._conn.commit()


def _rows_to_msgs(rows) -> list[dict]:
    return [{"content": r[0], "name": r[1], "user_id": r[2], "ts": r[3]} for r in rows]


_store: SearchStore | None = None


def get_search_store() -> SearchStore:
    global _store
    if _store is None:
        _store = SearchStore()
    return _store

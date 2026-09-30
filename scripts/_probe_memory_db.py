#!/usr/bin/env python3
"""查记忆在 SQLite 里的真实结构 —— 面板记忆页要按这个重写

用法（服务器）: python3 scripts/_probe_memory_db.py
"""
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "huanmeng.db"


def main():
    if not DB.exists():
        print("DB 不存在:", DB)
        return
    print("DB: %s  %.1f MB" % (DB, DB.stat().st_size / 1048576))
    c = sqlite3.connect(str(DB))
    cur = c.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tabs = [r[0] for r in cur.fetchall()]
    print("表(%d): %s" % (len(tabs), tabs))
    print()

    if "memories" in tabs:
        cur.execute("PRAGMA table_info(memories)")
        print("memories 列:", [r[1] for r in cur.fetchall()])
        cur.execute("SELECT COUNT(*) FROM memories")
        print("memories 总条数:", cur.fetchone()[0])
        for col in ("conversation_id", "user_id", "memory_type", "status", "source"):
            cur.execute("SELECT %s, COUNT(*) FROM memories GROUP BY %s ORDER BY 2 DESC LIMIT 8"
                        % (col, col))
            print("  按 %s: %s" % (col, cur.fetchall()))
        cur.execute("SELECT id, conversation_id, user_id, memory_type, status, source,"
                    " importance, length(content), substr(content,1,60) FROM memories"
                    " ORDER BY id DESC LIMIT 5")
        print("\n  最近 5 条:")
        for r in cur.fetchall():
            print("   ", r)
        # 一条完整样例，看内容形态
        cur.execute("SELECT content FROM memories ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        if row:
            print("\n  最新一条全文:\n   ", row[0][:400].replace("\n", " | "))
    print()

    for t in ("conversations", "messages", "memory_links", "user_profiles"):
        if t in tabs:
            cur.execute("SELECT COUNT(*) FROM %s" % t)
            n = cur.fetchone()[0]
            print("%s 条数: %d" % (t, n))
            if t == "conversations":
                cur.execute("SELECT conversation_id, conversation_type, title,"
                            " datetime(created_at,'unixepoch','localtime'),"
                            " datetime(updated_at,'unixepoch','localtime')"
                            " FROM conversations ORDER BY updated_at DESC LIMIT 5")
                for r in cur.fetchall():
                    print("    ", r)
            if t == "memory_links":
                cur.execute("SELECT link_type, COUNT(*) FROM memory_links GROUP BY link_type")
                print("     按类型:", cur.fetchall())
    print()

    # 旧 .md 记忆文件现状（面板若还在读它，就是"落后"的根源）
    mds = sorted(ROOT.glob("data/memory_*.md"))
    print("旧 data/memory_*.md 文件数: %d" % len(mds))
    for f in mds[:6]:
        lines = sum(1 for _ in f.open(encoding="utf-8", errors="replace"))
        print("   %-34s %6d 行  %.1f MB" % (f.name, lines, f.stat().st_size / 1048576))


if __name__ == "__main__":
    _env = ROOT / "config" / ".env"
    if _env.exists():
        for _line in _env.read_text(encoding="utf-8").splitlines():
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                k, v = _line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    sys.path.insert(0, str(ROOT))
    main()

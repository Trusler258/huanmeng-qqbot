"""
一键迁移脚本：把现有 data/msglog/*.jsonl 聊天记录回溯索引到 SQLite 全文检索库。

用法（在 qqbot 目录下）:
    python -m db.migrate            # 增量回溯（已存在的行会重复，慎重复跑）
    python -m db.migrate --reset    # 先清空 search.db 的 messages/memory 再回溯

设计：仅作为 ADDITIVE 回填，不删除任何 msglog / memory_*.md 文件。
失败（FTS5 不可用 / 文件损坏）时自动降级，单条跳过，不影响其余。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 让脚本在 `python db/migrate.py` 直接运行时也能 import 包
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from db.store import get_search_store  # noqa: E402

_MSGLOG_DIR = _ROOT / "data" / "msglog"


def _iter_msglog_files() -> list[Path]:
    if not _MSGLOG_DIR.exists():
        return []
    return sorted(_MSGLOG_DIR.glob("msglog_*.jsonl"))


def migrate(reset: bool = False) -> dict:
    store = get_search_store()
    if not store.available:
        print("[WARN] 搜索数据库不可用（FTS5 缺失或初始化失败），迁移无效果。")
        return {"indexed": 0, "skipped": 0, "files": 0}

    if reset:
        store.clear()
        print("[OK] 已清空现有检索库（messages/memory），准备重新回溯。")

    total_indexed = 0
    total_skipped = 0
    files = _iter_msglog_files()
    print(f"发现 {len(files)} 个 msglog 文件，开始回溯...")

    for path in files:
        chat_id_str = path.stem.replace("msglog_", "")
        try:
            chat_id = int(chat_id_str)
        except ValueError:
            print(f"  [SKIP] 无法从文件名解析 chat_id: {path.name}")
            continue

        n_before = store.count()
        skipped = 0
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    skipped += 1
                    continue
                if entry.get("recalled"):
                    skipped += 1
                    continue
                content = entry.get("content", "")
                if not content or content.startswith("[CQ:") or content.startswith("["):
                    # 跳过 CQ 码 / 占位符（图片/文件/合并转发等）
                    skipped += 1
                    continue
                user_id = int(entry.get("user_id", 0) or 0)
                ts = entry.get("time")
                try:
                    store.index_message(chat_id, user_id, str(user_id), content, ts=ts)
                except Exception:
                    skipped += 1
        n_after = store.count()
        indexed = n_after - n_before
        total_indexed += indexed
        total_skipped += skipped
        print(f"  [OK] {path.name}: 索引 +{indexed} 条, 跳过 {skipped} 条")

    summary = {"indexed": total_indexed, "skipped": total_skipped, "files": len(files)}
    print(f"\n迁移完成: 索引 {total_indexed} 条, 跳过 {total_skipped} 条, 文件 {len(files)} 个。")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="回溯 msglog 到 SQLite 检索库")
    parser.add_argument("--reset", action="store_true", help="迁移前先清空检索库")
    args = parser.parse_args()
    migrate(reset=args.reset)

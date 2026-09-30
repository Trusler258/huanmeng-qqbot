"""记忆双写（md → SQLite 镜像）回归测试（2026-09-30）

用户决定：面板读写 DB，同时让 bot 的写入也进 DB（DB = md 的实时镜像；
bot 的读取路径不动，仍读 md → 不会失忆）。

验证：
  1. `append_memory` 落盘后**一定**调用 `_sync_memory_to_db`，
     且传入的 content 与 md 里那一行**逐字一致**
     （这是面板"改 DB + 改 md 对应行"能一一对应的前提，不一致就找不到行）
  2. 重复行在入库之前就 return（md 与 DB 都不会重复）
  3. DB 未初始化时 `_db_ready()` False、`_sync_memory_to_db` 静默返回
  4. **入库失败不影响 md**（md 已写完；失败只 warning，下次启动 backfill 幂等补齐）
  5. 无事件循环时走 `asyncio.run` 分支，不抛异常

用法（本地）: python tests/_test_v2369_memory_dualwrite.py
"""
from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS = 0
FAIL = 0


def ok(msg):
    global PASS
    PASS += 1
    print("  [OK] %s" % msg)


def bad(msg):
    global FAIL
    FAIL += 1
    print("  [FAIL] %s" % msg)


def main() -> None:
    import modules.memory as M

    tmp = Path(tempfile.mkdtemp(prefix="memtest_"))
    orig_dir = M.MEMORY_DIR
    orig_sync = M._sync_memory_to_db
    orig_add = M._db_add_memory
    try:
        M.MEMORY_DIR = tmp
        M._memory_cache.clear()

        print("=== 1. append_memory 落盘 + 同步入库（content 必须逐字一致）===")
        calls = []

        def spy_sync(chat_id, line, source="live"):
            calls.append((chat_id, line, source))

        M._sync_memory_to_db = spy_sync
        chat = 123456
        line = "- [10:18:27] quleyu: 好感动, 好感度 (2026-09-30)"
        M.append_memory(chat, line)

        f = tmp / ("memory_%d.md" % chat)
        if f.exists():
            ok("md 文件已写出")
        else:
            bad("md 文件没写出来")
        body = f.read_text(encoding="utf-8")
        # 文件里有文件头（format_lang memory.file_header），只校验目标行在里面
        if line in body:
            ok("md 里含该行")
        else:
            bad("md 里找不到该行：\n%s" % body[:200])

        if len(calls) == 1:
            ok("调用了 1 次 _sync_memory_to_db")
        else:
            bad("调用次数异常：%d" % len(calls))
        if calls and calls[0][1] == line:
            ok("入库 content 与写入 md 的行**逐字一致**（面板按 content 匹配才找得到）")
        else:
            bad("入库 content 与 md 行不一致：%r" % (calls[0][1] if calls else None))
        if calls and calls[0][0] == chat:
            ok("conversation_id 正确（%d）" % chat)
        else:
            bad("conversation_id 错了")
        if calls and calls[0][2] == "live":
            ok("source 标为 live（面板可区分「实时」与「历史回填」）")
        else:
            bad("source 不是 live：%r" % (calls[0][2] if calls else None))

        print()
        print("=== 2. 重复行不入库（去重在入库之前 return）===")
        calls.clear()
        M.append_memory(chat, line)
        if not calls:
            ok("重复行没有再次入库")
        else:
            bad("重复行也入库了：%r" % calls)

        print()
        print("=== 3. DB 未初始化 → 静默返回 ===")
        M._sync_memory_to_db = orig_sync
        real_ready = M._db_ready()
        print("      （本机 db.initialized=%s）" % real_ready)
        try:
            M._sync_memory_to_db(chat, "- 测试行", "live")
            ok("_sync_memory_to_db 在 DB 未就绪时不抛异常")
        except Exception as e:
            bad("抛异常了：%s: %s" % (type(e).__name__, e))

        print()
        print("=== 4. 入库失败不影响 md（只 warning）===")
        calls.clear()

        async def boom(chat_id, l, source="live"):
            raise RuntimeError("模拟 DB 挂了")

        M._db_add_memory = boom
        M._sync_memory_to_db = orig_sync
        M._db_ready = lambda: True          # 假装 DB 就绪，骗过前置判断
        line2 = "- [11:00:00] 测试者: 入库会失败的一行 (2026-09-30)"
        try:
            M.append_memory(chat, line2)
            ok("入库抛异常时 append_memory 不抛（记忆不能丢）")
        except Exception as e:
            bad("append_memory 被入库异常带崩：%s: %s" % (type(e).__name__, e))
        body2 = f.read_text(encoding="utf-8")
        if line2 in body2:
            ok("该行仍写进了 md（md 写入不受入库影响）")
        else:
            bad("md 里没有该行，md 写入被影响了")

        print()
        print("=== 5. 无事件循环时走 asyncio.run 分支 ===")
        M._db_add_memory = orig_add
        M._db_ready = lambda: True
        hit = []

        async def fake_add(chat_id, l, source="live"):
            hit.append((chat_id, l))

        M._db_add_memory = fake_add
        try:
            M._sync_memory_to_db(chat, "- 无循环分支", "live")
            ok("_sync_memory_to_db 在无事件循环时正常返回")
        except Exception as e:
            bad("无循环分支异常：%s: %s" % (type(e).__name__, e))
        if hit and hit[0][1] == "- 无循环分支":
            ok("确实走到了 _db_add_memory（asyncio.run 分支）")
        else:
            bad("没走到入库：%r" % hit)

        print()
        print("=== 6. 有事件循环时是后台任务（不阻塞记忆写入）===")
        hit.clear()

        async def scenario():
            M._sync_memory_to_db(chat, "- 循环内分支", "live")
            # 刚创建时任务未跑完，说明没同步阻塞
            n_immediate = len(hit)
            await asyncio.sleep(0.05)
            return n_immediate, len(hit)

        try:
            n0, n1 = asyncio.run(scenario())
            if n0 == 0 and n1 == 1:
                ok("循环内不阻塞（创建后立即返回），随后任务跑完（%d → %d）" % (n0, n1))
            else:
                bad("行为不符：立即=%d 稍后=%d" % (n0, n1))
        except Exception as e:
            bad("循环内分支异常：%s: %s" % (type(e).__name__, e))

        print()
        print("=== 7. 源码级不变量（防止后人改成「先入库后落盘」或漏掉 source）===")
        src = Path(ROOT, "modules/memory.py").read_text(encoding="utf-8")
        save_idx = src.index("    save_memories_to_file(chat_id, memories)\n", src.index("def append_memory"))
        sync_idx = src.index("_sync_memory_to_db(chat_id, new_line", save_idx)
        if save_idx < sync_idx:
            ok("顺序正确：先落盘 md，再同步 DB")
        else:
            bad("顺序反了：DB 同步在 md 落盘之前")
        if 'source="live"' in src:
            ok("实时写入标记 source=live")
        else:
            bad("没标记 source=live")
        if 'source="backfill"' in src:
            ok("回填仍标记 source=backfill（面板可区分时间可信度）")
        else:
            bad("回填标记丢了")

    finally:
        M.MEMORY_DIR = orig_dir
        M._sync_memory_to_db = orig_sync
        M._db_add_memory = orig_add
        M._memory_cache.clear()
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print("结果: %d passed, %d failed" % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()

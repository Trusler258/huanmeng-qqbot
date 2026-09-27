"""v2.3.64 重命令旁路并发 + 日报并发 回归测试

验证：
  1. is_heavy_command 判定（重命令 True / 进程控制与轻指令 False）
  2. 同群多条重命令**并发**执行（原来串行）
  3. 同群普通消息仍然**串行**（保持原行为，不引入乱序）
  4. _detached 标记不会泄漏进 process_message 的 kwargs
  5. 群之间本来就并发（per-group worker）
  6. 日报并发函数存在、归档路径与原实现一致

用法（本地）:
  python tests/_test_v2364_concurrency.py
"""
from __future__ import annotations

import asyncio
import sys
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PASS = 0
FAIL = 0


def ok(msg: str) -> None:
    global PASS
    PASS += 1
    print(f"  \033[32mOK\033[0m   {msg}")


def bad(msg: str) -> None:
    global FAIL
    FAIL += 1
    print(f"  \033[31mFAIL\033[0m {msg}")


# ── 用桩替换 core.pipeline ──
# 本测试只验证队列调度，不需要真管道。**必须无条件打桩**：服务器上 `core.pipeline`
# 能成功 import，会把 config/LLM/db 整套依赖拉起来（实测会卡住不返回）。
# core.queues 只在函数内部 `from core.pipeline import process_message`，
# 所以模块级换掉 sys.modules 条目即可生效。
_stub = types.ModuleType("core.pipeline")


async def _pm(**kw):   # 占位，main() 里按用例替换
    return None


_stub.process_message = _pm
sys.modules["core.pipeline"] = _stub

import core.queues as q  # noqa: E402


async def main() -> None:
    print("=== 1. is_heavy_command 判定 ===")
    try:
        from modules.commands import is_heavy_command
        cases = [
            ("/~wdsj bw trusler", True),
            ("/~wdsj lb bw img", True),
            ("/~steam 76561198000000000", True),
            ("/~天气 广州", True),
            ("/~stats", True),
            ("/~power", True),
            ("/#添加", False),        # 好友审批：轻，保持串行
            ("/~help", False),
            ("/~ping", False),
            ("/~restart", False),     # 进程控制：故意串行
            ("/~reload", False),
            ("/~update", False),
            ("/~wzq", False),
            ("普通聊天没有前缀", False),
            ("", False),
        ]
        for text, want in cases:
            got = is_heavy_command(text)
            if got == want:
                ok(f"{text!r} → {got}")
            else:
                bad(f"{text!r} → {got}（期望 {want}）")
    except Exception as e:
        bad(f"is_heavy_command 导入/执行异常: {type(e).__name__}: {e}")

    print("\n=== 2. 同群多条重命令应并发（不排队）===")
    events: list[tuple[str, float]] = []

    async def fake_process_message(**kw):
        mid = str(kw.get("msg_content", ""))[-3:]
        events.append((f"s{mid}", time.monotonic()))
        await asyncio.sleep(0.6)
        events.append((f"e{mid}", time.monotonic()))

    sys.modules["core.pipeline"].process_message = fake_process_message

    for i in range(3):
        await q.enqueue_message(
            chat_id=900001, msg_type="文字", msg_content=f"/~wdsj bw p{i}",
            sender_name="tester", user_id=1, is_group=True, bot_qq=1,
            raw_event={}, raw_message="", quoted_msg="", error_report=None,
            is_command=True, _detached=True,
        )
    # 等到 3 个事件全部跑完（每条 0.6s；并发总跨度应 ~0.6s，串行需 ~1.8s）
    for _ in range(40):
        if len(events) >= 6:
            break
        await asyncio.sleep(0.1)

    starts = [t for tag, t in events if tag.startswith("s")]
    ends = [t for tag, t in events if tag.startswith("e")]
    if len(starts) == 3 and len(ends) == 3:
        span = max(ends) - min(starts)
        if span < 1.2:
            ok(f"3 条重命令执行总跨度 {span:.2f}s（< 1.2s → 并发；串行需 ~1.8s）")
        else:
            bad(f"3 条重命令执行总跨度 {span:.2f}s，疑似仍在串行")
        if max(starts) < min(ends):
            ok("三条重命令执行区间相互重叠（真并发）")
        else:
            bad(f"区间未重叠 starts={starts} ends={ends}")
    else:
        bad(f"事件数不对: starts={len(starts)} ends={len(ends)}（应为 3/3）")

    print("\n=== 3. 同群普通消息仍串行（保持原行为）===")
    seq: list[str] = []

    async def fake_serial(**kw):
        mid = str(kw.get("msg_content", ""))[-1:]
        seq.append(f"s{mid}")
        await asyncio.sleep(0.2)
        seq.append(f"e{mid}")

    sys.modules["core.pipeline"].process_message = fake_serial

    t0 = time.monotonic()
    for i in range(3):
        await q.enqueue_message(
            chat_id=900002, msg_type="文字", msg_content=f"闲聊{i}",
            sender_name="tester", user_id=1, is_group=True, bot_qq=1,
            raw_event={}, raw_message="", quoted_msg="", error_report=None,
            is_command=False,
        )
    await asyncio.sleep(1.0)
    elapsed = time.monotonic() - t0
    # 串行 → 完整配对 s0 e0 s1 e1 s2 e2
    expect = ["s0", "e0", "s1", "e1", "s2", "e2"]
    if seq == expect:
        ok(f"普通消息严格串行 {seq}（耗时 {elapsed:.2f}s）")
    else:
        bad(f"普通消息顺序异常: {seq}（期望 {expect}）")

    print("\n=== 4. _detached 标记不泄漏进 process_message ===")
    leaked = []

    async def fake_inspect(**kw):
        if "_detached" in kw:
            leaked.append(True)

    sys.modules["core.pipeline"].process_message = fake_inspect
    await q.enqueue_message(
        chat_id=900003, msg_type="文字", msg_content="/~wdsj bw x",
        sender_name="t", user_id=1, is_group=True, bot_qq=1,
        raw_event={}, raw_message="", quoted_msg="", error_report=None,
        is_command=True, _detached=True,
    )
    await asyncio.sleep(0.5)
    if not leaked:
        ok("_detached 已在 worker 内 pop，未传给 process_message")
    else:
        bad("_detached 泄漏进 process_message kwargs")

    print("\n=== 5. 不同群之间并发 ===")
    pair: list[tuple[str, float]] = []

    async def fake_cross(**kw):
        gid = kw.get("chat_id")
        pair.append((f"s{gid}", time.monotonic()))
        await asyncio.sleep(0.4)
        pair.append((f"e{gid}", time.monotonic()))

    sys.modules["core.pipeline"].process_message = fake_cross
    gids = [910001, 910002, 910003]
    for gid in gids:
        await q.enqueue_message(
            chat_id=gid, msg_type="文字", msg_content="闲聊",
            sender_name="t", user_id=1, is_group=True, bot_qq=1,
            raw_event={}, raw_message="", quoted_msg="", error_report=None,
            is_command=False,
        )
    for _ in range(30):
        if len(pair) >= 6:
            break
        await asyncio.sleep(0.1)
    _s = [t for tag, t in pair if tag.startswith("s")]
    _e = [t for tag, t in pair if tag.startswith("e")]
    if len(_s) == 3 and len(_e) == 3:
        span = max(_e) - min(_s)
        if span < 0.8:
            ok(f"3 个群并发完成 总跨度 {span:.2f}s（每群独立 worker；串行需 ~1.2s）")
        else:
            bad(f"3 个群总跨度 {span:.2f}s，疑似串行")
    else:
        bad(f"跨群事件数不对: s={len(_s)} e={len(_e)}")

    print("\n=== 6. 日报并发实现检查 ===")
    try:
        import inspect
        import modules.stats as st
        if hasattr(st, "_send_one_daily_report") and hasattr(st, "_DAILY_CONCURRENCY"):
            src = inspect.getsource(st.midnight_report_loop)
            if "asyncio.gather" in src and "_send_one_daily_report" in src:
                ok("midnight_report_loop 已改为 gather 并发下发")
            else:
                bad("midnight_report_loop 未使用 gather")
            src2 = inspect.getsource(st._send_one_daily_report)
            if "asyncio.sleep(2)" not in src2:
                ok("已移除逐个群之间的 sleep(2) 串行节流")
            else:
                bad("仍残留 sleep(2)")
        else:
            bad("缺少 _send_one_daily_report / _DAILY_CONCURRENCY")
    except Exception as e:
        bad(f"stats 检查异常: {type(e).__name__}: {e}")

    # shutdown_queues 已加固（py3.10 首次 cancel 可能未送达 → 内部会自动重试一次）
    print(f"  ... 收尾 shutdown_queues（worker={len(q._group_tasks)}）")
    try:
        await asyncio.wait_for(q.shutdown_queues(), timeout=10)
        print("  ... 收尾完成")
    except asyncio.TimeoutError:
        bad("shutdown_queues 超时（加固后仍卡住）")

    print(f"\n=== 结果: {PASS} passed, {FAIL} failed ===")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    asyncio.run(main())

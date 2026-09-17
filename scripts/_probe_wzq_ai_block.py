# -*- coding: utf-8 -*-
"""实测：五子棋 AI 计算期间事件循环是否被卡住（GIL 争用）。

用户现象：「下棋的时候要等到机器人思考完才显示我下的棋」。
前端是 1 秒轮询 `/state`，按理 1 秒内就该看到自己的落子；
但 AI（expert）算 8s 期间若事件循环被 starve，`/state` 就排在后面 → 感觉卡住。

假设：`_AI_EXECUTOR` 是单线程 ThreadPoolExecutor，`_ai_expert` 是纯 Python CPU 密集，
`run_in_executor` 并不能真正释放 GIL → 事件循环只能每 5ms 抢到一小片。

做法：起一个 5ms 心跳协程测「实际间隔」，同时跑 ai_move_async，看最大滞后。

跑法：python3 scripts/_probe_wzq_ai_block.py
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules import wzq as W  # noqa: E402

CHAT = 999999999
TICK = 0.005          # 期望心跳间隔


async def heartbeat(stop: asyncio.Event, lags: list):
    """每 5ms 醒一次，记录实际间隔（滞后 = 实际 - 期望）"""
    prev = time.perf_counter()
    while not stop.is_set():
        await asyncio.sleep(TICK)
        now = time.perf_counter()
        lags.append(now - prev - TICK)
        prev = now


async def main():
    diff = sys.argv[1] if len(sys.argv) > 1 else "expert"
    W._games.pop(CHAT, None)
    msg = W.create_duel_ai(CHAT, 12345, diff)
    print("建局:", msg)
    g = W.get_game(CHAT)
    if not g:
        print("建局失败")
        return 1

    # 让人先落一子（触发 AI 应手）
    ok, m = W.web_move(CHAT, 12345, 7, 7)
    print("人落子:", ok, m)

    stop = asyncio.Event()
    lags = []
    hb = asyncio.create_task(heartbeat(stop, lags))

    t0 = time.perf_counter()
    await W.ai_move_async(CHAT)
    dt = time.perf_counter() - t0

    stop.set()
    await hb

    g = W.get_game(CHAT)
    print("\nAI 难度 %s 耗时 %.2fs，落子后 status=%s" % (diff, dt, getattr(g, "status", "?")))
    print("心跳样本 %d 个（期望 %.0fms）" % (len(lags), TICK * 1000))
    if lags:
        srt = sorted(lags)
        n = len(srt)
        print("  最大滞后 %.0f ms" % (srt[-1] * 1000))
        print("  P50 %.0f ms / P95 %.0f ms / P99 %.0f ms"
              % (srt[n // 2] * 1000, srt[int(n * 0.95)] * 1000,
                 srt[int(n * 0.99)] * 1000))
        over = [x for x in srt if x > 0.05]
        print("  滞后 >50ms 的次数: %d (%.1f%%)" % (len(over), len(over) / n * 100))
        print("  滞后 >200ms 的次数: %d" % len([x for x in srt if x > 0.2]))
    print("\n结论:", "事件循环被明显卡住（GIL 争用）" if lags and sorted(lags)[-1] > 0.2
          else "事件循环基本正常")

    W._games.pop(CHAT, None)
    return 0


sys.exit(asyncio.get_event_loop().run_until_complete(main()))

# -*- coding: utf-8 -*-
"""实测：五子棋人机对局里，人落子后多久能在 /state 里看到（v2.3.38 排查）。

用户现象：「下棋的时候要等到机器人思考完才显示我下的棋」。
前端逻辑（wzq_web.html::play）是 `await POST /move` → `await refresh()`，
另有 1 秒定时轮询兜底。所以只要 /move 返回快、/state 不被阻塞，就该立刻可见。

本探针在真实服务（59400）上：
  1. 用 create_duel_ai 建一局人机（人执黑）
  2. POST 一手人的落子，量响应耗时
  3. 高频轮询 /state，记录「人的子可见」与「AI 的子出现」各自的时间点
  4. 顺带量 /state 的响应耗时分布（看是否被 AI 计算拖慢）

跑法：python3 scripts/_probe_wzq_move_latency.py [difficulty]
"""
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path("/root/bot") if Path("/root/bot").exists() else Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from modules import wzq as W  # noqa: E402
from services.game_web import ensure_room, make_token  # noqa: E402

BASE = "http://127.0.0.1:59400"
CHAT = 991002          # 测试房间
HUMAN = 4002
R1, C1 = 7, 7          # 人的第 1 手
R2, C2 = 8, 8          # 人的第 2 手


def board_of(st):
    return st.get("board") or []


def has_stone(st, r, c):
    b = board_of(st)
    return bool(b and len(b) > r and b[r][c])


def main():
    diff = sys.argv[1] if len(sys.argv) > 1 else "expert"

    # ── 1. 建局 ──────────────────────────────────────────────
    W._games.pop(CHAT, None)
    msg = W.create_duel_ai(CHAT, HUMAN, diff)
    print("建局:", msg)
    code = ensure_room("wzq", CHAT)
    tok = make_token(code, HUMAN)
    print("房间:", code, " 难度:", diff)

    c = httpx.Client(timeout=30, trust_env=False)
    H = {"Content-Type": "application/json"}

    st0 = c.get(f"{BASE}/api/r/{code}/state?t={tok}").json()
    print("初始 status=%s turn=%s move_count=%s" % (st0.get("status"), st0.get("turn"), st0.get("move_count")))

    # ── 2. 人落子，量 POST 耗时 ───────────────────────────────
    t0 = time.perf_counter()
    r = c.post(f"{BASE}/api/r/{code}/move?t={tok}", json={"r": R1, "c": C1}, headers=H).json()
    post_ms = (time.perf_counter() - t0) * 1000
    print("\nPOST /move -> %s  耗时 %.0f ms" % (json.dumps(r, ensure_ascii=False), post_ms))

    # ── 3. 高频轮询，看人的子何时可见 ─────────────────────────
    t_start = time.perf_counter()
    t_human = t_ai = None
    lat = []
    ai_stone_rc = None
    while time.perf_counter() - t_start < 25:
        ts = time.perf_counter()
        try:
            st = c.get(f"{BASE}/api/r/{code}/state?t={tok}").json()
        except Exception as e:
            print("  /state 异常:", e)
            break
        lat.append((time.perf_counter() - ts) * 1000)
        el = (time.perf_counter() - t_start) * 1000

        if t_human is None and has_stone(st, R1, C1):
            t_human = el
            print("  [%6.0f ms] 人的子可见（move_count=%s）" % (el, st.get("move_count")))
        if t_human and st.get("move_count", 0) >= 2 and t_ai is None:
            t_ai = el
            b = board_of(st)
            for rr in range(len(b)):
                for cc in range(len(b[rr])):
                    if b[rr][cc] == 2:
                        ai_stone_rc = (rr, cc)
                        break
                if ai_stone_rc:
                    break
            print("  [%6.0f ms] AI 的子出现 %s（move_count=%s）" % (el, ai_stone_rc, st.get("move_count")))
            break
        time.sleep(0.08)

    c.close()

    print("\n" + "=" * 58)
    if t_human is None:
        print("!! 人的子始终没在 /state 里出现（异常）")
        return 1
    print("人的子可见耗时      : %.0f ms" % t_human)
    print("AI 应手完成耗时     : %s" % ("%.0f ms" % t_ai if t_ai else "未在 25s 内完成"))
    print("POST /move 耗时     : %.0f ms" % post_ms)
    if lat:
        s = sorted(lat)
        print("/state 延迟 样本%d  P50 %.0f / P95 %.0f / max %.0f ms"
              % (len(s), s[len(s) // 2], s[int(len(s) * 0.95)], s[-1]))
    print()
    if t_ai and t_human > t_ai * 0.8:
        print("结论: 人的子几乎和 AI 一起出现 —— 确实要等 AI 思考完（复现用户现象）")
    else:
        print("结论: 人的子在 AI 应手前就已可见（未复现「要等 AI 思考完」）")
    print("=" * 58)

    W._games.pop(CHAT, None)
    return 0


sys.exit(main())

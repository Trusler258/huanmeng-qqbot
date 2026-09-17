# -*- coding: utf-8 -*-
"""五子棋三个问题的回归测试（v2.3.38）。

覆盖：
  1. 品牌名不再是英文缩写（GOMOKU/GO/XIANGQI → 五子棋/围棋/象棋）
  2. 终局检测：人下出五连后，**房间必须归档**（= 播报流程真的跑了）
     —— 原来的 bug：`_finish_sig()` 写在 `web_move()` 之后，拿到的是
        走完后的 "finished"，`_finished_now` 的 `sig == ("playing",)` 永不成立
  3. 前端乐观渲染的接线：三个页面都埋了 myTurnNow / rollback

跑法（服务器，服务已在 59400）：python3 scripts/_test_wzq_finish.py
"""
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path("/root/bot") if Path("/root/bot").exists() else Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from modules import wzq as W  # noqa: E402
from services import game_web as GW  # noqa: E402

BASE = "http://127.0.0.1:59400"
TPL = Path(__file__).resolve().parent.parent / "data" / "templates"
CHAT = 991003          # 测试房间
BLACK, WHITE = 4002, 4006

PASS, FAIL = [], []


def ck(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("OK" if cond else "FAIL", name, ("  " + str(extra)) if extra else ""))


def test_brand_names():
    print("\n=== 1. 品牌名（英文缩写 → 中文）===")
    want = {"wzq_web.html": "五子棋", "go_web.html": "围棋", "xq_web.html": "象棋"}
    for fn, cn in want.items():
        f = TPL / fn
        if not f.exists():
            ck("%s 存在" % fn, False)
            continue
        t = f.read_text(encoding="utf-8")
        ck("%s 品牌行是中文「%s」" % (fn, cn), '<span class="tp">%s</span>' % cn in t)
        ck("%s 不含英文缩写" % fn,
           all(x not in t for x in ("GOMOKU", ">XIANGQI<", '<span class="tp">GO</span>')))


def test_root_cause_unit():
    """钉死根因：sig 取成走完后的值（finished）时，_finished_now 永远返回空。

    ⚠️ 必须在棋局已终局之后调用 —— _finished_now 内部走 _load_game → web_reload
       从盘上读，盘上没有棋局时直接返回 (None, "")，会把用例误判成失败
       （第一版就栽在这：单元用例排在 e2e 之前，那时盘上还没有棋局）。
    """
    print("\n=== 2. 根因单元验证（sig 时机）===")
    W.web_reload()                       # 跨进程：从盘上覆盖读
    g = W.get_game(CHAT)
    ck("前置：棋局在盘上且已终局", getattr(g, "status", "") == "finished",
       getattr(g, "status", ""))
    _g, res_bad = GW._finished_now("wzq", CHAT, ("finished",))
    ck("sig=('finished',) → 返回空（= 原 bug 不播报不归档）", res_bad == "", repr(res_bad))
    _g2, res_ok = GW._finished_now("wzq", CHAT, ("playing",))
    ck("sig=('playing',) → 能读到终局文案", bool(res_ok), repr(res_ok))


def test_finish_archives_room():
    """端到端：黑方下出五连 → 房间必须被归档（播报流程真的跑了）"""
    print("\n=== 3. 终局后房间归档（走真实 HTTP）===")
    W._games.pop(CHAT, None)
    msg = W.create_duel(CHAT, BLACK, WHITE)
    W.accept_duel(CHAT, WHITE)
    code = GW.ensure_room("wzq", CHAT)
    tb = GW.make_token(code, BLACK)
    tw = GW.make_token(code, WHITE)
    print("   建局:", msg, "房间:", code)

    c = httpx.Client(timeout=30, trust_env=False)
    H = {"Content-Type": "application/json"}

    # 黑走中线 (7,7)..(11,11)，白走别处不挡 → 黑第 5 手五连
    seq = [("b", 7, 7), ("w", 0, 0), ("b", 8, 8), ("w", 0, 1),
           ("b", 9, 9), ("w", 0, 2), ("b", 10, 10), ("w", 0, 3),
           ("b", 11, 11)]
    last = None
    for who, r, cc in seq:
        tok = tb if who == "b" else tw
        last = c.post(f"{BASE}/api/r/{code}/move?t={tok}",
                      json={"r": r, "c": cc}, headers=H).json()
        if not last.get("ok"):
            ck("落子 %s(%d,%d) 成功" % (who, r, cc), False, last)
            break
    print("   最后一手:", json.dumps(last, ensure_ascii=False))

    # 终局流程是后台任务，给它一点时间
    import time
    archived = False
    for _ in range(40):          # 最多等 4s
        time.sleep(0.1)
        if GW.room_of(code) is None:
            archived = True
            break
    c.close()

    # ⚠️ 跨进程：落子发生在 bot 进程，本进程的 _games 是陈旧的 →
    #    必须先 web_reload() 从盘上覆盖读，否则读到的还是 "playing"
    W.web_reload()
    g = W.get_game(CHAT)
    ck("棋局已 finished", getattr(g, "status", "") == "finished", getattr(g, "status", ""))
    ck("黑方获胜", getattr(g, "winner", 0) == 1, getattr(g, "winner", 0))
    ck("房间已归档（= 播报流程跑过）", archived,
       "room_of(%s)=%s" % (code, GW.room_of(code)))

    W._games.pop(CHAT, None)


def test_frontend_wiring():
    print("\n=== 4. 前端乐观渲染接线 ===")
    for fn in ("wzq_web.html", "go_web.html", "xq_web.html"):
        f = TPL / fn
        t = f.read_text(encoding="utf-8") if f.exists() else ""
        ck("%s 有乐观渲染注释" % fn, "乐观渲染" in t)
        ck("%s 有 rollback()" % fn, "function rollback()" in t)
    t = (TPL / "wzq_web.html").read_text(encoding="utf-8")
    ck("wzq 声明 myTurnNow", "let myTurnNow" in t)
    ck("wzq render 里赋值 myTurnNow", "myTurnNow = myTurn;" in t)
    t2 = (TPL / "go_web.html").read_text(encoding="utf-8")
    ck("go 声明 myTurnNow", "let myTurnNow" in t2)
    ck("go render 里赋值 myTurnNow", "myTurnNow = myTurn;" in t2)
    t3 = (TPL / "xq_web.html").read_text(encoding="utf-8")
    ck("xq 用 parseSq 解析 UCI", "parseSq(uci.slice(0, 2))" in t3)


def main():
    test_brand_names()
    # 顺序要紧：先跑 e2e 造出「已终局」的棋局，单元验证才能从盘上读到它
    try:
        test_finish_archives_room()
    except Exception as e:
        ck("终局归档端到端未异常", False, "%s: %s" % (type(e).__name__, e))
    try:
        test_root_cause_unit()
    except Exception as e:
        ck("根因单元验证未异常", False, "%s: %s" % (type(e).__name__, e))
    test_frontend_wiring()

    print("\n" + "=" * 56)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  -", x)
    print("=" * 56)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())

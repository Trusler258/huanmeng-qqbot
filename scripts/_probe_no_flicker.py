# -*- coding: utf-8 -*-
"""实测：乐观渲染后棋子**不该闪回**原位（v2.3.39）。

用户报的现象：「象棋乐观渲染后，棋子会时不时的闪回之前的位置又回来」。

根因：乐观画子后、服务端确认到手之前，**1 秒轮询里那些在落子之前发出的请求**
会晚到（本页走 CF Tunnel，单请求 1.3~1.6s），带回手数更少的旧盘面
→ 把刚挪过去的子擦掉，下一次轮询再画回来 = 闪回。

修复（三个棋页面都加了）：
  1. `pendingMinMoves`：乐观之后服务端至少该有 curMoves+1 手；
     在那之前到达的状态一律丢弃（带 6s 兜底超时）
  2. `stateSeq/appliedSeq`：丢弃「比已渲染过的更旧」的响应（并发请求乱序到达）

本探针把 GET /state 人为延迟（复现隧道环境），落子后高频采样盘面，
断言「子一旦到位就不再消失」。

跑法：python3 scripts/_probe_no_flicker.py [延迟ms]
"""
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path("/root/bot") if Path("/root/bot").exists() else Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from modules import chinese_chess as X  # noqa: E402
from services.game_web import ensure_room, make_token  # noqa: E402

BASE = "http://127.0.0.1:59400"
CHAT = 991005
RED, BLACK = 4003, 4007
FROM, TO = "b0", "c2"        # 红马二进三（合法开局手）

RESULTS = []


def ck(name, cond, extra=""):
    RESULTS.append(cond)
    print("  [%s] %s%s" % ("OK" if cond else "FAIL", name, ("  " + str(extra)) if extra else ""))


async def main():
    delay = int(sys.argv[1]) if len(sys.argv) > 1 else 1500

    games = X._load_games()
    games.pop(str(CHAT), None)
    X._save_games(games)
    print("建局:", X.start_game(RED, CHAT, "困难", opponent_id=BLACK))   # 双人局，红先手
    code = ensure_room("xq", CHAT)
    tok = make_token(code, RED)
    print("房间 %s  /state 模拟延迟 %d ms\n" % (code, delay))

    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path="/usr/bin/chromium-browser",
                                    args=["--no-sandbox", "--disable-dev-shm-usage"])
        pg = await b.new_page(viewport={"width": 1300, "height": 950})

        # ★ 关键：要模拟「服务端早求值、响应包晚到」，才等价于真实隧道环境。
        #   若只延迟请求（route.continue_ 前 sleep），服务端是在包到达时才求值的，
        #   旧轮询照样能读到新盘面 → 根本复现不出闪回（第一版就栽在这，
        #   对照组都测不出闪回，等于白测）。
        #   route.fetch() 先真发请求（服务端此刻求值）→ sleep → fulfill 返回旧包。
        async def _slow(route):
            if route.request.method == "GET":
                try:
                    resp = await route.fetch()
                except Exception:
                    await route.continue_()
                    return
                await asyncio.sleep(delay / 1000)
                await route.fulfill(response=resp)
            else:
                await route.continue_()
        await pg.route("**/api/r/**", _slow)

        await pg.goto(f"{BASE}/r/{code}?t={tok}", wait_until="domcontentloaded")
        await pg.wait_for_timeout(2600)

        async def piece_at(sq):
            return await pg.evaluate(
                """(sq) => { const a = parseSq(sq); const ch = gridCache[a.r][a.c];
                            return ch ? ch : ''; }""", sq)

        src = await piece_at(FROM)
        ck("起点 %s 上有红子（走法合法前提）" % FROM, bool(src), repr(src))
        if not src:
            await b.close()
            return 1

        rc_from = await pg.evaluate("(sq) => parseSq(sq)", FROM)
        rc_to = await pg.evaluate("(sq) => parseSq(sq)", TO)
        el1 = await pg.query_selector('.hit[data-r="%d"][data-c="%d"]' % (rc_from["r"], rc_from["c"]))
        el2 = await pg.query_selector('.hit[data-r="%d"][data-c="%d"]' % (rc_to["r"], rc_to["c"]))
        ck("找到起点格 / 终点格", el1 is not None and el2 is not None)

        await el1.click()          # 选中
        await pg.wait_for_timeout(150)
        await el2.click()          # 走子（触发乐观渲染）

        # ★ 对照组：关掉「防闪回」守卫，验证闪回确实会复现 ——
        #   否则无法区分"守卫生效"与"这个场景本来就闪不了"。
        #   pendingMinMoves 是在乐观渲染时设上的，这里落地后立刻清零即可让它失效。
        control = "--control" in sys.argv
        if control:
            await pg.evaluate("pendingMinMoves = 0; pendingUntil = 0;")
            print("  (对照组：已关闭 pendingMinMoves 守卫)")

        t0 = time.perf_counter()
        first_seen, flickers = None, []
        while time.perf_counter() - t0 < 9:
            now_to = await piece_at(TO)
            el = (time.perf_counter() - t0) * 1000
            if now_to and first_seen is None:
                first_seen = el
                print("  [%6.0f ms] 终点首次出现子 %r" % (el, now_to))
            if first_seen is not None and not now_to:
                flickers.append(el)
                print("  [%6.0f ms] !! 闪回：终点的子消失了" % el)
            await pg.wait_for_timeout(60)

        await pg.screenshot(path="/tmp/_xq_no_flicker.png")
        await b.close()

    print()
    ck("子成功到位", first_seen is not None,
       ("首次出现于 %.0f ms" % first_seen) if first_seen else "从未出现")
    if control:
        # 对照组期望「出现闪回」；没闪说明这个场景本身测不出来，结论不可信
        ck("[对照组] 关掉守卫后应出现闪回", bool(flickers),
           ("闪回 %d 次，最早 %.0f ms" % (len(flickers), flickers[0])) if flickers
           else "未闪回 —— 说明该场景无法复现，上面的『不闪回』结论不成立")
    else:
        ck("到位后不再闪回", not flickers,
           ("闪回 %d 次: %s" % (len(flickers), flickers[:4])) if flickers else "")

    games = X._load_games()
    games.pop(str(CHAT), None)
    X._save_games(games)

    bad = sum(1 for x in RESULTS if not x)
    print("\n" + "=" * 56)
    print("通过 %d，失败 %d" % (len(RESULTS) - bad, bad))
    print("=" * 56)
    return 1 if bad else 0


sys.exit(asyncio.get_event_loop().run_until_complete(main()))

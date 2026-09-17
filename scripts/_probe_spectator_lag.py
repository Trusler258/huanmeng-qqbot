# -*- coding: utf-8 -*-
"""实测：观战端「对手落子 → 自己看到」的延迟（v2.3.39+）。

用户报「观战模式也是一样（要等）」。
观战者没有本地动作可乐观渲染，只能靠轮询 —— 所以延迟 = 轮询间隔 + 网络往返。
本页走 CF Tunnel（实测单请求首字节 1.25~1.56s），
`setInterval(refresh, 1000)` 在 1.3s 往返下会有「1s 空转」，
有效延迟可到 2.3s。

本探针把 /state 的**响应**延迟 1300ms（route.fetch + sleep + fulfill，
模拟"服务端早求值、响应包晚到"），然后用 API 落一手，
量观战端 DOM 里出现那颗子要多久。

跑法：python3 scripts/_probe_spectator_lag.py [延迟ms]
"""
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path("/root/bot") if Path("/root/bot").exists() else Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from modules import wzq as W  # noqa: E402
from services.game_web import ensure_room, make_token, SPECTATOR  # noqa: E402

import os
BASE = os.environ.get("GAME_WEB_BASE", "http://127.0.0.1:59400")
CHAT = 991006
BLACK, WHITE = 4002, 4006


async def main():
    delay = int(sys.argv[1]) if len(sys.argv) > 1 else 1300
    W._games.pop(CHAT, None)
    W.create_duel(CHAT, BLACK, WHITE)
    W.accept_duel(CHAT, WHITE)
    code = ensure_room("wzq", CHAT)
    print("房间 %s  /state 响应延迟 %d ms" % (code, delay))

    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path="/usr/bin/chromium-browser",
                                    args=["--no-sandbox", "--disable-dev-shm-usage"])
        pg = await b.new_page(viewport={"width": 1200, "height": 900})

        async def _slow(route):
            resp = await route.fetch()              # 先真发，服务端此刻求值
            await asyncio.sleep(delay / 1000)       # 响应包晚到
            await route.fulfill(response=resp)
        await pg.route("**/api/r/**", _slow)

        spec_tok = make_token(code, SPECTATOR)
        await pg.goto(f"{BASE}/r/{code}?t={spec_tok}", wait_until="domcontentloaded")
        await pg.wait_for_timeout(3000)             # 等首轮渲染

        role = await pg.inner_text("#chip-role")
        n0 = await pg.eval_on_selector_all(".stone", "els => els.length")
        print("观战端角色=%r 初始石头=%d" % (role.strip(), n0))
        if "观战" not in role:
            print("!! 这页不是观战身份，测的不是观战路径")

        # 用 API 落一手（不走页面，避免引入落子端变量）
        c = httpx.Client(timeout=30, trust_env=False)
        tb = make_token(code, BLACK)
        t_apply = time.perf_counter()
        r = c.post(f"{BASE}/api/r/{code}/move?t={tb}",
                   json={"r": 7, "c": 7}, headers={"Content-Type": "application/json"}).json()
        c.close()
        print("落子:", r)

        # 量观战端看到那颗子要多久
        seen = None
        while time.perf_counter() - t_apply < 12:
            n = await pg.eval_on_selector_all(".stone", "els => els.length")
            if n > n0:
                seen = (time.perf_counter() - t_apply) * 1000
                break
            await pg.wait_for_timeout(40)

        await pg.screenshot(path="/tmp/_spectator_lag.png")
        await b.close()

    W._games.pop(CHAT, None)

    print("\n" + "=" * 56)
    if seen is None:
        print("!! 观战端 12s 内没看到落子")
        return 1
    print("对手落子 → 观战端看到: %.0f ms" % seen)
    print("（其中网络单程 ~%d ms，其余是轮询间隔造成的空转）" % (delay // 2))
    print("=" * 56)
    return 0


sys.exit(asyncio.get_event_loop().run_until_complete(main()))

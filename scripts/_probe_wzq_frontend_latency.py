# -*- coding: utf-8 -*-
"""实测前端：真人点落子后，石头多久出现在 DOM 里（v2.3.38 排查）。

后端已证明没问题（POST /move 10ms、/state 85ms 就能看到人的子），
所以「要等 AI 思考完才显示」若真的存在，只可能在前端渲染。

做法：建一局人机 → Playwright 打开房间页 → 点一个交叉点 →
轮询 DOM 里 .stone 的数量，记录「第 1 个子出现」与「第 2 个子出现」的时间。

跑法：python3 scripts/_probe_wzq_frontend_latency.py [difficulty]
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path("/root/bot") if Path("/root/bot").exists() else Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from modules import wzq as W  # noqa: E402
from services.game_web import ensure_room, make_token  # noqa: E402

BASE = "http://127.0.0.1:59400"
HUMAN = 4002
CHAT = 991002


async def main():
    diff = sys.argv[1] if len(sys.argv) > 1 else "expert"
    W._games.pop(CHAT, None)
    W.create_duel_ai(CHAT, HUMAN, diff)
    code = ensure_room("wzq", CHAT)
    tok = make_token(code, HUMAN)
    url = f"{BASE}/r/{code}?t={tok}"
    print("房间 %s 难度 %s\n页面 %s" % (code, diff, url))

    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path="/usr/bin/chromium-browser",
                                    args=["--no-sandbox", "--disable-dev-shm-usage"])
        pg = await b.new_page(viewport={"width": 1200, "height": 900})
        logs = []
        pg.on("console", lambda m: logs.append(m.text))

        # ★ 模拟 Cloudflare Tunnel 的往返延迟（实测首字节 1.25~1.56s）：
        #   给每个响应加 1300ms。乐观渲染若生效，石头仍应立刻出现。
        DELAY = int(sys.argv[2]) if len(sys.argv) > 2 else 1300
        if DELAY > 0:
            async def _slow(route):
                await asyncio.sleep(DELAY / 1000)
                await route.continue_()
            await pg.route("**/api/r/**", _slow)
            print("已模拟链路延迟 %d ms/请求" % DELAY)

        await pg.goto(url, wait_until="domcontentloaded")
        await pg.wait_for_timeout(1800)      # 等 buildBoard 完成

        brand = await pg.inner_text(".brand")
        print("品牌行:", repr(brand.strip()))
        n_hit = await pg.eval_on_selector_all(".hit", "els => els.length")
        print("交叉点数量:", n_hit)
        n0 = await pg.eval_on_selector_all(".stone", "els => els.length")
        print("起始石头数:", n0)

        import time
        # 点中心附近的交叉点
        el = await pg.query_selector('.hit[data-r="7"][data-c="7"]')
        if not el:
            el = await pg.query_selector(".hit")
        t0 = time.perf_counter()
        await el.click()

        t1 = t2 = None
        tip_seen = ""
        while time.perf_counter() - t0 < 20:
            n = await pg.eval_on_selector_all(".stone", "els => els.length")
            el_ms = (time.perf_counter() - t0) * 1000
            if t1 is None and n >= 1:
                t1 = el_ms
                tip_seen = await pg.inner_text("#tip")
                print("  [%6.0f ms] 出现第 1 个石头（tip=%r）" % (el_ms, tip_seen))
            if t1 is not None and n >= 2:
                t2 = el_ms
                print("  [%6.0f ms] 出现第 2 个石头（AI 应手完成）" % el_ms)
                break
            await pg.wait_for_timeout(40)

        await pg.screenshot(path="/tmp/_wzq_frontend.png", full_page=False)
        await b.close()

    print("\n" + "=" * 58)
    if t1 is None:
        print("!! 点了之后一直没有石头出现（前端异常）")
        return 1
    print("第 1 个石头（人的）出现: %.0f ms" % t1)
    print("第 2 个石头（AI 的）出现: %s" % ("%.0f ms" % t2 if t2 else "未出现"))
    if t2 and t1 > t2 * 0.8:
        print("结论: 复现 —— 人的子等到 AI 应手后才一起显示")
    else:
        print("结论: 未复现 —— 人的子先出现，AI 后应手")
    print("=" * 58)

    W._games.pop(CHAT, None)
    return 0


sys.exit(asyncio.get_event_loop().run_until_complete(main()))

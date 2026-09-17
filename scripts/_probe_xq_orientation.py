# -*- coding: utf-8 -*-
"""实测：象棋页的行朝向是否与卡片图/坐标体系一致（v2.3.39）。

权威基准（后端）：
  - `modules/cchess/svg.py` 里 `orientation = cchess.RED`，画子用
    `y = (9 - row_index) * SQUARE_SIZE` → **row_index 0（红方底线）在底部**
  - 页面 `sq(r,c) = FILE + (ROWS-1-r)`、`parseSq` 反之 → 页面索引 9 = rank 0

所以两边都应是「红方在下、rank 9 在上」。本探针打开象棋页，
检查：① 左侧行标签上→下是否是 9…0；② 红方（rank 0/1）的子是否在**下半盘**。

跑法：python3 scripts/_probe_xq_orientation.py
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path("/root/bot") if Path("/root/bot").exists() else Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from modules import chinese_chess as X  # noqa: E402
from services.game_web import ensure_room, make_token  # noqa: E402

BASE = "http://127.0.0.1:59400"
CHAT = 991010
RED, BLACK = 4003, 4007
PASS, FAIL = [], []


def ck(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("OK" if cond else "FAIL", name, ("  " + str(extra)) if extra else ""))


async def main():
    games = X._load_games()
    games.pop(str(CHAT), None)
    X._save_games(games)
    print("建局:", X.start_game(RED, CHAT, "困难", opponent_id=BLACK))
    code = ensure_room("xq", CHAT)
    tok = make_token(code, RED)

    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path="/usr/bin/chromium-browser",
                                    args=["--no-sandbox", "--disable-dev-shm-usage"])
        pg = await b.new_page(viewport={"width": 1300, "height": 950})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("console", lambda m: errs.append("console:" + m.text) if m.type == "error" else None)
        await pg.goto(f"{BASE}/r/{code}?t={tok}", wait_until="domcontentloaded")
        await pg.wait_for_timeout(2000)

        ck("无 JS 报错", not errs, errs[:2])

        labels = await pg.eval_on_selector_all(
            "#rlabels div", "els => els.map(e => e.textContent.trim())")
        print("   左侧行标签（上→下）:", labels)
        ck("顶行是 9、底行是 0（红方在下）",
           labels[:1] == ["9"] and labels[-1:] == ["0"],
           "%s ... %s" % (labels[:1], labels[-1:]))

        # 按上下半盘统计子数（不依赖颜色 —— 棋子是中文「車馬炮」，中文字符
        # 没有大小写，用 `ch === ch.toUpperCase()` 区分红黑会全部算成红，
        # 第一版就栽在这）。标准开局红黑各 16 子，且红在下半盘（rank 0-4）。
        info = await pg.evaluate("""() => {
          const els = [...document.querySelectorAll('.hit')];
          const tops = [...new Set(els.map(e => Math.round(e.getBoundingClientRect().top)))].sort((a,b)=>a-b);
          const n = tops.length;
          const low = [], high = [];
          for (const el of els) {
            const ch = el.textContent.trim();
            if (!ch) continue;
            const vis = tops.indexOf(Math.round(el.getBoundingClientRect().top));
            const rank = n - 1 - vis;          // 视觉行 -> rank（顶行 rank 9）
            (rank <= 4 ? low : high).push(rank);
          }
          return {n, low, high,
                  lowRanks: [...new Set(low)].sort((a,b)=>a-b),
                  highRanks: [...new Set(high)].sort((a,b)=>a-b)};
        }""")
        print("   下半盘(rank0-4) 子数=%d ranks=%s" % (len(info["low"]), info["lowRanks"]))
        print("   上半盘(rank5-9) 子数=%d ranks=%s" % (len(info["high"]), info["highRanks"]))
        ck("下半盘 16 子（红方全部在此）", len(info["low"]) == 16, len(info["low"]))
        ck("上半盘 16 子（黑方全部在此）", len(info["high"]) == 16, len(info["high"]))
        ck("下半盘是红方基线（含 rank0 的底线子）", 0 in info["lowRanks"], info["lowRanks"])

        await pg.screenshot(path="/tmp/_xq_orient.png")
        await b.close()

    games = X._load_games()
    games.pop(str(CHAT), None)
    X._save_games(games)

    bad = sum(1 for x in PASS + [not y for y in []] if not x)
    nfail = len(FAIL)
    print("\n" + "=" * 56)
    print("通过 %d，失败 %d" % (len(PASS) - 0, nfail))
    for x in FAIL:
        print("  -", x)
    print("=" * 56)
    return 1 if nfail else 0


sys.exit(asyncio.get_event_loop().run_until_complete(main()))

# -*- coding: utf-8 -*-
"""实测：棋局网页的行朝向是否与「坐标体系 / 终局卡片图」一致（v2.3.39 排查）。

背景：用户报「对局结束后发出来的棋盘上下是反的」。

已确认的权威基准：
  - `parse_coord("H8")` → 行索引 7（`row = 8 - 1`）→ **行索引 0 = 第 1 行**
  - `coord_label(r, c) = 字母 + (r + 1)`
  - 终局卡片 `render_board()` 用 `for r in reversed(range(15))` → 行15 在顶、行1 在底 ✅ 标准朝向

网页端可疑点（wzq_web.html::buildBoard）：
  - hit 元素：`top: r*cell`，`data-r = r`   → **行索引 0 画在最上面**
  - 行标签：`for r: rh += n - r`            → **顶行标签是 15**
  两者矛盾：下标 0 在最上面，但那一行标的是 15（而下标 0 按坐标体系是「第 1 行」）。
  ⇒ 板面整体被**上下镜像**了，同时标签还在假装没镜像。

本探针用 Playwright 点最上面一行，看服务端把这一步记成什么坐标，从而判定谁错了。

跑法：python3 scripts/_probe_board_orientation.py
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path("/root/bot") if Path("/root/bot").exists() else Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from modules import wzq as W  # noqa: E402
from services.game_web import ensure_room, make_token  # noqa: E402

BASE = "http://127.0.0.1:59400"
CHAT = 991004
BLACK, WHITE = 4002, 4006

PASS, FAIL = [], []


def ck(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("OK" if cond else "FAIL", name, ("  " + str(extra)) if extra else ""))


def backend_truth():
    """后端的权威朝向：行索引 0 = 第 1 行；卡片图把行 15 画在顶部"""
    print("\n=== 0. 后端权威基准 ===")
    ck("parse_coord('A1') → 行索引 0", W.parse_coord("A1") == (0, 0), W.parse_coord("A1"))
    ck("parse_coord('A15') → 行索引 14", W.parse_coord("A15") == (14, 0), W.parse_coord("A15"))
    ck("coord_label(0,0) == 'A1'", W.coord_label(0, 0) == "A1", W.coord_label(0, 0))
    ck("coord_label(14,0) == 'A15'", W.coord_label(14, 0) == "A15", W.coord_label(14, 0))
    tpl = (Path(__file__).resolve().parent.parent / "data" / "templates"
           / "wzq_board_card.html")
    if tpl.exists():
        t = tpl.read_text(encoding="utf-8")
        ck("卡片模板存在", True)


async def frontend_check():
    print("\n=== 1. 网页端：点最上面一行，服务端记成什么坐标 ===")
    W._games.pop(CHAT, None)
    W.create_duel(CHAT, BLACK, WHITE)
    W.accept_duel(CHAT, WHITE)
    code = ensure_room("wzq", CHAT)
    tok = make_token(code, BLACK)
    print("   房间:", code)

    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path="/usr/bin/chromium-browser",
                                    args=["--no-sandbox", "--disable-dev-shm-usage"])
        pg = await b.new_page(viewport={"width": 1300, "height": 950})
        await pg.goto(f"{BASE}/r/{code}?t={tok}", wait_until="domcontentloaded")
        await pg.wait_for_timeout(1800)

        # 页面左侧行标签（从上到下）
        labels = await pg.eval_on_selector_all("#rlabels div", "els => els.map(e => e.textContent.trim())")
        print("   左侧行标签（上→下）:", labels)
        ck("行标签从上到下是降序（15…1）", labels[:1] == ["15"] and labels[-1:] == ["1"],
           "%s ... %s" % (labels[0], labels[-1]))

        # 点「视觉上最上面一行」的第一个交叉点。
        # ⚠️ 不能用 data-r="0" 找顶行 —— 修复后 data-r=0 是**底行**（行1）。
        #    按 bounding_box 的最小 top 定位才是稳的（第一版探针就栽在这，
        #    把底行当顶行，结果误判成「修复没生效」）。
        tops = await pg.evaluate("""() => {
          const els = [...document.querySelectorAll('.hit')];
          const arr = els.map(e => ({r: +e.dataset.r, c: +e.dataset.c,
                                     top: e.getBoundingClientRect().top,
                                     left: e.getBoundingClientRect().left}));
          const minTop = Math.min(...arr.map(a => a.top));
          const minLeft = Math.min(...arr.filter(a => a.top === minTop).map(a => a.left));
          return arr.find(a => a.top === minTop && a.left === minLeft);
        }""")
        print("   视觉最上面一行最左边的格: data-r=%s data-c=%s" % (tops["r"], tops["c"]))
        ck("视觉顶行的 data-r 是 14（= 坐标行 15）", tops["r"] == 14, tops["r"])
        top = await pg.query_selector('.hit[data-r="%d"][data-c="%d"]' % (tops["r"], tops["c"]))
        await top.click()
        await pg.wait_for_timeout(2500)
        await pg.screenshot(path="/tmp/_board_orient.png")

        # 服务端把这一步记成什么坐标？读 /state 的 moves
        st = await pg.evaluate(
            "async (u) => (await fetch(u)).json()", f"{BASE}/api/r/{code}/state?t={tok}")
        mv = (st.get("moves") or [])
        ck("服务端记录到 1 手", len(mv) == 1, mv)
        got = mv[0]["label"] if mv else "?"
        print("   服务端记录的坐标:", got)

        # 页面上那个子出现在第几个**视觉行**（0 = 最上面）
        #   ⚠️ labels 是按视觉顺序排的，而 data-r 是数组下标（修复后二者相反），
        #      所以要取视觉位置而不是直接用 data-r。
        info = await pg.evaluate("""() => {
          const els = [...document.querySelectorAll('.hit')];
          const withStone = els.find(e => e.querySelector('.stone'));
          if (!withStone) return {r: -1, visual: -1};
          const tops = [...new Set(els.map(e => e.getBoundingClientRect().top))].sort((a,b)=>a-b);
          return {r: +withStone.dataset.r,
                  visual: tops.indexOf(withStone.getBoundingClientRect().top)};
        }""")
        print("   有子的 hit: data-r=%s 视觉行=%s" % (info["r"], info["visual"]))
        lab = labels[info["visual"]] if 0 <= info["visual"] < len(labels) else "?"
        print("   该视觉行显示的行号标签:", lab)

        await b.close()

    print()
    ck("点击最上面一行 → 坐标应为 A15（标签与坐标一致）", got == "A15",
       "实际 %s（若为 A1 说明板面相对坐标体系上下镜像了）" % got)
    ck("标签与坐标自洽", lab == got.lstrip("ABCDEFGHIJKLMNO"),
       "标签 %s vs 坐标 %s" % (lab, got))

    W._games.pop(CHAT, None)
    return got


def main():
    backend_truth()
    got = asyncio.get_event_loop().run_until_complete(frontend_check())
    print("\n" + "=" * 58)
    print("结论:", "网页板面与坐标体系**不一致**（上下镜像）" if got == "A1"
          else "网页板面与坐标体系一致（未复现）")
    print("通过 %d，失败 %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  -", x)
    print("=" * 58)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())

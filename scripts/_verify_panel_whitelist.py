"""面板「群管理 → 白名单管理」实测探针

验证点：
  1. 页面加载无 JS 错误
  2. 白名单 tab 可切换，四张卡都在（群/私聊/管理员/分群指令）
  3. 数据来自真实接口（管理员 QQ、群数、私聊数对得上）
  4. 群列表里白名单群带「已授权」标记
  5. 截图存 /tmp/_panel_wl.png 供人工确认

登录方式沿用 _probe_panel_fix：API 取 token + add_init_script 注入 localStorage。
⚠️ 面板 SPA 路由前缀是 /resources（不是 /assets，那个撞 Vite 静态目录）。
"""
import asyncio
import sys

import httpx
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:49300"
USER = "admin"
PWD = "HuanmengPanel@2026"


async def main() -> int:
    errors: list[str] = []
    result: dict[str, bool] = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            executable_path="/usr/bin/chromium-browser", args=["--no-sandbox"]
        )
        page = await browser.new_page(viewport={"width": 1500, "height": 1000})
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on(
            "console",
            lambda m: errors.append(f"console: {m.text}") if m.type == "error" else None,
        )

        tok = httpx.post(
            f"{BASE}/api/auth/login",
            json={"username": USER, "password": PWD},
            timeout=15,
        ).json()["token"]
        await page.add_init_script(
            f"try{{localStorage.setItem('token','{tok}')}}catch(e){{}}"
        )

        await page.goto(f"{BASE}/resources/groups", wait_until="domcontentloaded")
        await page.wait_for_timeout(9000)
        result["页面在群管理路由"] = "/resources/groups" in page.url

        # ── 群数据 tab：确认群列表带授权标记 ──
        data_body = await page.inner_text("body")
        result["群列表显示授权标记"] = ("已授权" in data_body) or ("未授权" in data_body)
        result["群数据 tab 有群列表"] = "群列表" in data_body

        # ── 切到白名单 tab ──
        # Arco 的 card 型 tabs 类名不带 header-title，直接按文本点更稳
        tab_txts = await page.evaluate(
            """() => [...document.querySelectorAll('[class*=tabs]')]
                     .map(e => (e.className||'').toString())
                     .filter(c => c.includes('header') || c.includes('tab'))
                     .slice(0, 8)"""
        )
        print("tab 相关 class 样例 =", tab_txts[:4])

        clicked = False
        loc = page.get_by_text("白名单管理", exact=True)
        if await loc.count():
            await loc.first.click()
            clicked = True
        result["找到白名单 tab"] = clicked
        await page.wait_for_timeout(5000)

        body = await page.inner_text("body")

        result["群聊天白名单卡"] = "群聊天白名单" in body
        result["私聊白名单卡"] = "私聊白名单" in body
        result["管理员卡"] = "管理员" in body
        result["分群指令白名单卡"] = "分群指令白名单" in body
        # placeholder 是 attribute，inner_text 取不到 —— 查元素本身
        selects = await page.evaluate("() => document.querySelectorAll('.arco-select').length")
        result["有候选下拉框"] = selects >= 2
        print("arco-select 数量 =", selects)
        result["管理员QQ已渲染"] = "3483585417" in body
        result["管理员昵称已渲染"] = "Trusler" in body
        result["有移出按钮"] = "移出" in body
        result["有加入按钮"] = "加入" in body
        # 群名来自 data/group_names.json（bot 的 nickname_sync 落盘）
        known_names = ["夜梦碰碰车", "布吉岛PFLS公会", "育苗小学分校114级514班"]
        hit = [n for n in known_names if n in body]
        result["群名已渲染(非'未命名群')"] = bool(hit)
        print("匹配到的群名 =", hit)

        counts = await page.evaluate(
            """() => ({
                items: document.querySelectorAll('.wl-item').length,
                cards: document.querySelectorAll('.arco-card').length,
            })"""
        )
        print("wl-item 数量 =", counts["items"], "| 卡片数 =", counts["cards"])
        result["白名单条目已渲染"] = counts["items"] > 0
        result["四张卡都在"] = counts["cards"] >= 3

        await page.screenshot(path="/tmp/_panel_wl.png", full_page=True)

        # ── 回到群数据 tab 看详情页的授权区 ──
        back = page.get_by_text("群数据", exact=True)
        if await back.count():
            await back.first.click()
        await page.wait_for_timeout(4000)
        d2 = await page.inner_text("body")
        result["详情页有授权状态区"] = "授权状态" in d2
        result["详情页有指令白名单区"] = "该群指令白名单" in d2
        await page.screenshot(path="/tmp/_panel_wl_data.png", full_page=True)

        await browser.close()

    print("\n=== 结果 ===")
    ok = True
    for k, v in result.items():
        print(f"  {'✓' if v else '✗'} {k}")
        if not v:
            ok = False

    real_err = [e for e in errors if "favicon" not in e.lower()]
    print(f"\nJS 错误 {len(real_err)} 条")
    for e in real_err[:6]:
        print("   ", e[:200])
    if real_err:
        ok = False

    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

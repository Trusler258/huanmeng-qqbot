# -*- coding: utf-8 -*-
"""面板「用户画像」页 UI 验收：DOM 断言 + 截图。

重点验证：
  1. 表格能出数据（不是空表）
  2. 显示的是**画像内容**而不是元数据（旧代码会把 qq/scope/group 拼成摘要）
  3. 分域标签存在（群 / 私聊）
  4. 群聊/私聊筛选真的生效
  5. 详情抽屉能打开并显示注入文本

⚠️ Arco 的 4 个 tab-pane 共用 `arco-tabs-pane` 类名、且非活动面板不卸载，
   所以前端在画像页加了 `.profile-tab` 作为稳定钩子。找不到它说明
   前端没更新（旧构建会把好感度表的行当画像行读，断言全错）。

跑法（服务器）：python3 scripts/_probe_panel_profile.py
"""
import asyncio
import sys

PWD = "HuanmengPanel@2026"
BASE = "http://127.0.0.1:49300"
PANE = ".profile-tab"
OK, BAD = [], []


def ck(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print("  [%s] %s%s" % ("OK" if cond else "FAIL", name, ("  " + str(extra)) if extra else ""))


async def main():
    import httpx
    from playwright.async_api import async_playwright

    tok = httpx.post(BASE + "/api/auth/login",
                     json={"username": "admin", "password": PWD}, timeout=15).json()["token"]
    print("登录成功")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            executable_path="/usr/bin/chromium-browser",
            args=["--no-sandbox", "--disable-dev-shm-usage"])
        page = await browser.new_page(viewport={"width": 1500, "height": 1000})
        await page.add_init_script(f"try{{localStorage.setItem('token','{tok}')}}catch(e){{}}")
        await page.goto(BASE + "/fun/social", wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)

        tabs = await page.query_selector_all(".arco-tabs-tab")
        target = None
        for t in tabs:
            if "画像" in (await t.inner_text()):
                target = t
        ck("找到「用户画像」标签页", target is not None,
           [await t.inner_text() for t in tabs])
        if target is None:
            await browser.close()
            return 1
        await target.click()
        await page.wait_for_timeout(1200)

        ck("页面含 .profile-tab 钩子（前端已更新）",
           await page.query_selector(PANE) is not None)

        async def body_rows():
            out = []
            for r in await page.query_selector_all(PANE + " .arco-table-tr"):
                if await r.query_selector(".arco-table-th"):
                    continue
                out.append((await r.inner_text()).replace("\n", " | ").strip())
            return out

        rows = await body_rows()
        ck("表格有数据行", len(rows) > 0, "%d 行" % len(rows))
        first = rows[0] if rows else ""
        print("     首行:", first[:140])
        ck("首行含分域标签（群/私聊）", ("群" in first or "私聊" in first), first[:40])
        ck("摘要显示画像内容而非元数据",
           not first.strip().startswith("qq") and "scope:" not in first)
        ck("不是好感度表的残留行", "| 存 | 删" not in first, first[:60])

        radios = await page.query_selector_all(PANE + " .arco-radio-button")
        ck("活动面板内有筛选按钮", len(radios) >= 3, "%d 个" % len(radios))
        priv = None
        for r in radios:
            if "私聊" in (await r.inner_text()):
                priv = r
        ck("存在私聊筛选按钮", priv is not None)
        if priv:
            await priv.click()
            await page.wait_for_timeout(1200)
            texts = await body_rows()
            ck("私聊筛选后只剩私聊", len(texts) > 0 and all("私聊" in t for t in texts),
               "%d 行" % len(texts))
            await page.screenshot(path="/tmp/_panel_profile_priv.png", full_page=True)
        for r in radios:
            if "全部" in (await r.inner_text()):
                await r.click()
        await page.wait_for_timeout(1200)

        detail = None
        for btn in await page.query_selector_all(PANE + " button"):
            if (await btn.inner_text()).strip() == "详情":
                detail = btn
                break
        ck("详情按钮可点", detail is not None)
        if detail:
            await detail.click()
            await page.wait_for_timeout(900)
            txt = await page.inner_text("body")
            ck("详情抽屉显示注入内容", "注入给 bot 的内容" in txt)
            ck("详情含分域信息", "会话" in txt and ("群 " in txt or "私聊" in txt))
            await page.screenshot(path="/tmp/_panel_profile_detail.png", full_page=True)

        await page.screenshot(path="/tmp/_panel_profile_list.png", full_page=True)
        await browser.close()

    print("\n通过 %d，失败 %d" % (len(OK), len(BAD)))
    for b in BAD:
        print("  -", b)
    return 0 if not BAD else 1


sys.exit(asyncio.get_event_loop().run_until_complete(main()))

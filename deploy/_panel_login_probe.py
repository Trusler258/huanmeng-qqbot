"""用服务器上的 Playwright 实测面板前端：抓控制台报错 + 复现"登录不跳转"

为什么用服务器端：本机 Chromium 起不来（exit 3），而服务器上 changelog 模块
本来就装了 Playwright + Chromium，直接复用。
"""

from __future__ import annotations

import asyncio
import sys

from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:49300"
PW = "HuanmengPanel@2026"


async def main() -> int:
    async with async_playwright() as p:
        # 复用 bot 自带的系统 Chromium（changelog 模块就用它渲染卡片），
        # 避免 playwright install 再下一份 headless shell
        browser = await p.chromium.launch(
            executable_path="/usr/bin/chromium-browser",
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        ctx = await browser.new_context()
        page = await ctx.new_page()

        console: list[str] = []
        errors: list[str] = []
        reqs: list[str] = []

        page.on("console", lambda m: console.append(f"[{m.type}] {m.text}"))
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on(
            "request",
            lambda r: reqs.append(f"{r.method} {r.url}"),
        )

        print("=== 1. 打开登录页 ===")
        await page.goto(BASE + "/", wait_until="load", timeout=30000)
        await page.wait_for_timeout(2500)
        print(f"  URL: {page.url}")
        print(f"  标题: {await page.title()}")

        print("\n=== 2. 填密码并点登录 ===")
        # 密码框（Arco 的 input[type=password]）
        box = page.locator("input[type=password]")
        print(f"  找到密码框: {await box.count()} 个")
        if await box.count() == 0:
            print("  ✗ 页面没渲染出密码框，先看下面控制台输出")
        else:
            await box.first.fill(PW)
            await page.wait_for_timeout(300)
            # 点登录按钮
            btn = page.locator("button[type=submit]")
            print(f"  找到提交按钮: {await btn.count()} 个")
            await btn.first.click()
            print("  已点击，等 6 秒观察是否跳转")
            await page.wait_for_timeout(6000)

        print("\n=== 3. 结果 ===")
        print(f"  点击后 URL: {page.url}")
        print(f"  是否离开登录页: {'是' if '/login' not in page.url else '否 ← 这就是问题'}")

        print("\n=== 4. 控制台输出（最后 25 条）===")
        for line in console[-25:]:
            print("   ", line[:300])
        if not console:
            print("    (无)")

        print("\n=== 5. 页面 JS 报错 ===")
        for e in errors:
            print("   ", e[:500])
        if not errors:
            print("    (无)")

        print("\n=== 6. 网络请求（最后 20 条）===")
        for r in reqs[-20:]:
            print("   ", r[:160])

        # 截图留证
        try:
            await page.screenshot(path="/tmp/panel_login_test.png", full_page=True)
            print("\n  截图: /tmp/panel_login_test.png")
        except Exception as e:
            print(f"  截图失败: {e}")

        await browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

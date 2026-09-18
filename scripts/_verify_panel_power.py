"""面板功耗页 Playwright 验证：登录 → 进 /panel/monitor/power → 截图 + JS错误检查"""
import asyncio, json, sys
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:49300"

async def main():
    errors = []
    async with async_playwright() as pw:
        b = await pw.chromium.launch(executable_path="/usr/bin/chromium-browser",
                                     args=["--no-sandbox"])
        pg = await b.new_page(viewport={"width": 1600, "height": 900})
        pg.on("pageerror", lambda e: errors.append(str(e)[:200]))
        pg.on("console", lambda m: errors.append(m.text[:200]) if m.type == "error" else None)

        # 登录：API 取 token + init script 注入（沿用 _probe_panel_fix 写法）
        import httpx
        tok = httpx.post(BASE + "/api/auth/login",
                         json={"username": "admin", "password": "HuanmengPanel@2026"},
                         timeout=15).json()["token"]
        await pg.add_init_script(f"try{{localStorage.setItem('token','{tok}')}}catch(e){{}}")

        # 直接路由到功耗页
        await pg.goto(BASE + "/monitor/power", wait_until="domcontentloaded")
        await pg.wait_for_timeout(12000)
        dbg = await pg.evaluate("""() => {
          const cs = [...document.querySelectorAll('.chart')];
          return cs.map(e => ({w: e.clientWidth, h: e.clientHeight,
                               canvas: !!e.querySelector('canvas'),
                               html: e.innerHTML.length}));
        }""")
        print("chart containers:", dbg)
        body = await pg.inner_text("body")
        url = pg.url
        await pg.screenshot(path="/tmp/_panel_power.png", full_page=False)

        # 关键内容检查
        checks = {
            "页面在功耗路由": "monitor/power" in url,
            "有功耗标题": ("功耗" in body),
            "有瓦数数据": ("W" in body and any(x in body for x in ("墙面", "CPU", "整机"))),
            "有电费": ("元" in body),
            "有图表canvas": len(await pg.query_selector_all("canvas")) >= 1,
            "无服务断连提示": ("服务未运行" not in body),
        }
        print("URL:", url)
        print("JS/console 错误数:", len(errors))
        for e in errors[:5]:
            print("  ERR:", e)
        ok = True
        for k, v in checks.items():
            print(("  ✓ " if v else "  ✗ ") + k)
            ok = ok and v
        print("RESULT:", "PASS" if ok and not errors else "CHECK")
        await b.close()

asyncio.run(main())

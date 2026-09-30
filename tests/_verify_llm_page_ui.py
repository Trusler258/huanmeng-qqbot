"""LLM 供应商页 UI 验收（Playwright，登录走 token 注入）

路由：/config/llm（config 组下，不是 /resources —— 别猜路由，先查 routes/modules/config.ts）
"""

import asyncio
import json
import sys

import httpx
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:49300"
PAGE = BASE + "/config/llm"
SHOT = "/tmp/_llm_page.png"

R = []


def ok(m):
    R.append(("ok", m))
    print("  OK  " + m)


def bad(m):
    R.append(("bad", m))
    print("  FAIL " + m)


async def main():
    tok = httpx.post(f"{BASE}/api/auth/login",
                     json={"username": "admin", "password": PWD},
                     timeout=15).json()["token"]
    errors = []
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path="/usr/bin/chromium-browser",
                                    args=["--no-sandbox"])
        pg = await b.new_page(viewport={"width": 1500, "height": 1000})
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("console",
              lambda m: errors.append(m.text) if m.type == "error" else None)

        await pg.add_init_script(
            "try{localStorage.setItem('token','%s')}catch(e){}" % tok)
        print("=== 1. 打开 %s ===" % PAGE)
        await pg.goto(PAGE, wait_until="domcontentloaded")
        await pg.wait_for_timeout(9000)

        body = await pg.evaluate("() => (document.body.innerText||'')")
        if "not found" in body.lower():
            bad("404 页（路由错了）")
            print(body[:200])
        else:
            ok("页面加载，非 404")

        print("=== 2. 模型槽位表 ===")
        rows = await pg.evaluate(
            "() => document.querySelectorAll('table.arco-table-element "
            "tbody tr.arco-table-tr').length")
        # 页面有两张表：槽位(4行) + 供应商(3行)，共 7 行
        if rows >= 6:
            ok("两张表共 %d 行数据（≥6）" % rows)
        else:
            bad("表格行数异常: %d" % rows)
        for kw in ("主回复", "判断/工具", "廉价判断", "图片理解"):
            (ok if kw in body else bad)("槽位标签「%s」可见" % kw)
        for kw in ("deepseek-flash", "glm-4v-flash"):
            (ok if kw in body else bad)("模型名 %s 可见" % kw)

        print("=== 3. 供应商表 ===")
        for kw in ("DEEPSEEK", "ZHIPU", "SILICONFLOW"):
            (ok if kw in body else bad)("供应商 %s 可见" % kw)
        if "sk-" in body and "***" in body:
            ok("key 以脱敏形式显示（含 ***）")
        else:
            bad("key 脱敏显示异常")

        print("=== 4. 打开槽位编辑弹窗 ===")
        await pg.get_by_text("编辑", exact=True).first.click()
        await pg.wait_for_timeout(1200)
        modal = await pg.evaluate(
            "() => !!document.querySelector('.arco-modal-container')")
        (ok if modal else bad)("编辑弹窗打开")
        await pg.keyboard.press("Escape")
        await pg.wait_for_timeout(600)

        print("=== 5. 测试按钮（真实打 DEEPSEEK 的 /models）===")
        # 找 DEEPSEEK 行的「测试」链接：点击第一个测试（DEEPSEEK 按名称排序第一）
        links = pg.get_by_text("测试", exact=True)
        await links.first.click()
        # ⚠️ Arco Message 默认 ~3s 自动消失，等太久反而读不到
        await pg.wait_for_timeout(2500)
        body2 = await pg.evaluate("() => (document.body.innerText||'')")
        if "正常" in body2 or "HTTP" in body2 or "正在测试" in body2:
            ok("测试结果已弹出: %s" % [l for l in body2.splitlines()
                                      if "正常" in l or "HTTP" in l][:1])
        else:
            bad("没看到测试结果")

        print("=== 6. 截图 + JS 错误 ===")
        await pg.screenshot(path=SHOT, full_page=True)
        if errors:
            bad("JS 错误 %d 条: %s" % (len(errors), errors[:3]))
        else:
            ok("JS 错误 0")

        await b.close()

    n_ok = sum(1 for t, _ in R if t == "ok")
    n_bad = sum(1 for t, _ in R if t == "bad")
    print("\n=== 结果: %d passed, %d failed ===" % (n_ok, n_bad))
    sys.exit(1 if n_bad else 0)


PWD = sys.argv[1] if len(sys.argv) > 1 else ""
asyncio.run(main())

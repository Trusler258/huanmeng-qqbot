"""新增供应商弹窗 UI 验收：真实打字 → 保存 → 清理 → 比对 .env 快照

背景（2026-09-30 用户报障）：名称输入框用了 :model-value 单向绑定，打字进不去，
校验却报「名称必填」。此探针防回归。
"""

import asyncio
import sys

import httpx
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:49300"
PAGE = BASE + "/config/llm"
NAME = "TESTUI_CHECK"

R = []


def ok(m):
    R.append(("ok", m))
    print("  OK  " + m)


def bad(m):
    R.append(("bad", m))
    print("  FAIL " + m)


def read_env():
    with open("/root/bot/config/.env", encoding="utf-8") as f:
        return f.read()


async def main():
    tok = httpx.post(f"{BASE}/api/auth/login",
                     json={"username": "admin", "password": PWD},
                     timeout=15).json()["token"]
    snap = read_env()
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
        await pg.goto(PAGE, wait_until="domcontentloaded")
        await pg.wait_for_timeout(9000)

        print("=== 1. 打开新增供应商弹窗 ===")
        await pg.get_by_text("新增供应商", exact=True).first.click()
        await pg.wait_for_timeout(1200)
        (ok if await pg.evaluate(
            "() => !!document.querySelector('.arco-modal-container')")
         else bad)("弹窗打开")

        print("=== 2. 逐字输入（Arco 受控 input 必须 type 而非 fill）===")
        inputs = pg.locator(".arco-modal:visible input.arco-input")
        name_box = inputs.nth(0)
        await name_box.click()
        await name_box.type(NAME, delay=60)
        got = await name_box.input_value()
        (ok if got == NAME else bad)(
            "名称输入框收到值: %r%s" % (got, "" if got == NAME else " ← 打字没进去！"))
        url_box = inputs.nth(1)
        await url_box.click()
        await url_box.type("https://example.invalid/v1", delay=30)
        key_box = inputs.nth(2)
        await key_box.click()
        await key_box.type("sk-uiroundtrip9988", delay=30)

        print("=== 3. 确定保存 ===")
        await pg.locator(".arco-modal:visible .arco-btn-primary").last.click()
        await pg.wait_for_timeout(2500)
        env2 = read_env()
        if ("TESTUI_CHECK_URL=https://example.invalid/v1" in env2
                and "TESTUI_CHECK_KEY=sk-uiroundtrip9988" in env2):
            ok("保存成功，.env 落盘两行")
        else:
            bad("保存失败或没落盘")

        print("=== 4. 清理 ===")
        # 直接调接口删（等价于页面删除按钮走的路径）
        import urllib.request
        req = urllib.request.Request(
            BASE + "/api/llm/providers/" + NAME, method="DELETE")
        req.add_header("Authorization", "Bearer " + tok)
        with urllib.request.urlopen(req, timeout=20) as r:
            (ok if r.status == 200 else bad)("接口删除 status=%d" % r.status)
        env3 = read_env()
        clean = [l for l in env3.splitlines() if not l.startswith(NAME + "_")]
        (ok if clean == snap.splitlines() else bad)(".env 恢复与操作前逐行一致")

        print("=== 5. JS 错误 ===")
        (ok if not errors else bad)("JS 错误 %d: %s" % (len(errors), errors[:2]))
        await b.close()

    n_ok = sum(1 for t, _ in R if t == "ok")
    n_bad = sum(1 for t, _ in R if t == "bad")
    print("\n=== 结果: %d passed, %d failed ===" % (n_ok, n_bad))
    sys.exit(1 if n_bad else 0)


PWD = sys.argv[1] if len(sys.argv) > 1 else ""
asyncio.run(main())

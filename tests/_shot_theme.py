"""截图三个关键页面看主题真实效果"""
from playwright.sync_api import sync_playwright
import sys

BASE = "http://127.0.0.1:49300"
OUT = "/tmp/shots"
import os
os.makedirs(OUT, exist_ok=True)

with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/usr/bin/chromium-browser", args=["--no-sandbox"])
    # 未登录上下文：登录页
    anon = b.new_context(viewport={"width": 1600, "height": 900}).new_page()
    anon.goto(f"{BASE}/login", wait_until="networkidle")
    anon.wait_for_timeout(1200)
    anon.screenshot(path=f"{OUT}/login.png", full_page=True)

    page = b.new_context(viewport={"width": 1600, "height": 900}).new_page()
    page.goto(f"{BASE}/login", wait_until="networkidle")
    page.fill('input[type="password"]', "HuanmengPanel@2026")
    page.keyboard.press("Enter")
    page.wait_for_url("**/dashboard/overview", timeout=15000)
    page.wait_for_timeout(2500)
    page.screenshot(path=f"{OUT}/overview.png", full_page=False)
    page.goto(f"{BASE}/config/editor", wait_until="networkidle")
    page.wait_for_timeout(2000)
    page.screenshot(path=f"{OUT}/config.png", full_page=False)
    # 顺手打印主题变量与菜单/导航栏的实际背景色
    info = page.evaluate("""() => {
        const cs = (sel, prop) => {
            const el = document.querySelector(sel);
            return el ? getComputedStyle(el)[prop] : 'NONE';
        };
        return {
            primary6: getComputedStyle(document.body).getPropertyValue('--primary-6').trim(),
            contentBg: cs('.layout-content', 'backgroundColor'),
            navbarBg: cs('.layout-navbar', 'backgroundImage') || cs('.navbar', 'backgroundColor'),
            siderBg: cs('.layout-sider', 'backgroundColor'),
            menuBg: cs('.arco-menu', 'backgroundColor'),
        };
    }""")
    print(info)
    b.close()
print("shots saved")

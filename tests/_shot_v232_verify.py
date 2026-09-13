# -*- coding: utf-8 -*-
"""v2.3.2 验证（同步版 playwright）：主题紫色化 + locate-config 定位输入框级"""
import json
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:49300"
PWD = "HuanmengPanel@2026"

def purple_ratio(path):
    try:
        from PIL import Image
    except ImportError:
        return -1
    im = Image.open(path).convert("RGB")
    px = im.load()
    w, h = im.size
    cnt = 0
    total = 0
    for y in range(0, h, 3):
        for x in range(0, w, 3):
            r, g, b = px[x, y]
            total += 1
            if 80 <= r <= 180 and g < 90 and 130 <= b <= 230:
                cnt += 1
    return cnt / max(total, 1)

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path="/usr/bin/chromium-browser",
                                    args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1600, "height": 900})

        # 1. 登录
        page.goto(BASE + "/login", wait_until="networkidle")
        page.fill('input[type="password"]', PWD)
        page.press('input[type="password"]', "Enter")
        page.wait_for_url("**/dashboard/**", timeout=15000)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1500)
        print("[1] login ok ->", page.url)

        # 2. 菜单选中态
        sel = page.evaluate("""() => {
            const els = document.querySelectorAll('.arco-menu-item.arco-menu-selected');
            return Array.from(els).map(e => ({text: e.textContent.trim().slice(0,12),
                color: getComputedStyle(e).color,
                bg: getComputedStyle(e).backgroundColor,
                shadow: getComputedStyle(e).boxShadow.slice(0,60)}));
        }""")
        print("[2] menu-selected:", json.dumps(sel, ensure_ascii=False))

        # 3. navbar 背景与标题
        nav = page.evaluate("""() => {
            const n = document.querySelector('.navbar');
            if (!n) return null;
            const s = getComputedStyle(n);
            return {bg: s.backgroundImage.slice(0,90) || s.backgroundColor,
                    title: n.querySelector('.arco-typography-title')?.textContent.trim()};
        }""")
        print("[3] navbar:", json.dumps(nav, ensure_ascii=False))

        # 4. 品牌条
        brand = page.evaluate("""() => {
            const b = document.querySelector('.brand-bar');
            return b ? {text: b.textContent.trim(),
                        bg: getComputedStyle(b).backgroundImage.slice(0,60)} : null;
        }""")
        print("[4] brand-bar:", json.dumps(brand, ensure_ascii=False))

        # 5. 截图
        page.screenshot(path="/tmp/shot_overview_232.png")

        # 6. 配置页菜单选中
        page.goto(BASE + "/config/editor", wait_until="networkidle")
        page.wait_for_timeout(2500)
        sel2 = page.evaluate("""() => Array.from(
            document.querySelectorAll('.arco-menu-item.arco-menu-selected'))
            .map(e => e.textContent.trim().slice(0,12))""")
        print("[6] config menu-selected:", sel2)
        page.screenshot(path="/tmp/shot_config_232.png")

        # 7. locate-config 端到端
        page.evaluate("""() => window.dispatchEvent(new CustomEvent('ai-locate-config-key',
            {detail: {path: 'personality', file: 'bot_config.toml'}}))""")
        page.wait_for_timeout(1800)
        loc = page.evaluate("""() => {
            const el = document.querySelector('.form-row.ai-highlight');
            const search = document.querySelector('.form-toolbar .arco-input-search input');
            return {highlight: el ? el.getAttribute('data-path') : null,
                    searchValue: search ? search.value : null,
                    hitRows: document.querySelectorAll('.form-row[data-path]').length};
        }""")
        print("[7] locate-config:", json.dumps(loc, ensure_ascii=False))

        # 8. 紫色像素占比
        r1 = purple_ratio("/tmp/shot_overview_232.png")
        r2 = purple_ratio("/tmp/shot_config_232.png")
        print(f"[8] purple ratio: overview={r1:.2%} config={r2:.2%}")

        browser.close()

main()

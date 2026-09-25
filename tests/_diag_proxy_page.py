# -*- coding: utf-8 -*-
"""诊断：dump /config/proxy 的表格与弹窗真实 DOM"""
import json
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:49300"
PWD = "HuanmengPanel@2026"

with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/usr/bin/chromium-browser",
                          args=["--no-sandbox"])
    pg = b.new_page(viewport={"width": 1600, "height": 1000})
    logs = []
    pg.on("console", lambda m: logs.append("%s: %s" % (m.type, m.text[:200])))
    pg.on("pageerror", lambda e: logs.append("PAGEERROR: %s" % str(e)[:300]))
    pg.on("response", lambda r: logs.append("HTTP %d %s" % (r.status, r.url[:90]))
          if "/api/steam-proxy" in r.url else None)

    pg.goto(BASE + "/login", wait_until="networkidle")
    pg.fill('input[type="password"]', PWD)
    pg.press('input[type="password"]', "Enter")
    pg.wait_for_url("**/dashboard/**", timeout=20000)
    pg.goto(BASE + "/config/proxy", wait_until="networkidle")
    pg.wait_for_timeout(3500)

    print("=" * 70)
    print("控制台/网络：")
    for l in logs:
        print("  ", l)

    print("=" * 70)
    print("表格相关 class 统计：")
    print(json.dumps(pg.evaluate("""() => {
        const out = {};
        for (const c of ['arco-table', 'arco-table-body', 'arco-table-tbody',
                         'arco-table-tr', 'arco-table-td', 'arco-table-th',
                         'arco-table-empty', 'arco-empty']) {
            out[c] = document.querySelectorAll('.' + c).length;
        }
        return out;
    }"""), ensure_ascii=False, indent=2))

    print("=" * 70)
    print("访问令牌卡片 innerText：")
    print(pg.evaluate("""() => {
        const cards = document.querySelectorAll('.arco-card');
        for (const c of cards) {
            const t = c.querySelector('.arco-card-header-title');
            if (t && t.textContent.includes('访问令牌')) return c.innerText.slice(0, 600);
        }
        return '(未找到卡片)';
    }"""))

    print("=" * 70)
    print("表格 HTML（前 1800 字符）：")
    print(pg.evaluate("""() => {
        const t = document.querySelector('.arco-table');
        return t ? t.outerHTML.slice(0, 1800) : '(无 .arco-table)';
    }"""))

    print("=" * 70)
    print("弹窗流程：")
    pg.click('button:has-text("生成新令牌")')
    pg.wait_for_timeout(900)
    print("  弹窗 HTML:", pg.evaluate(
        "() => { const m = document.querySelector('.arco-modal');"
        " return m ? m.outerHTML.slice(0, 1200) : '(无弹窗)'; }"))
    print("  input 数量:", pg.evaluate(
        "() => document.querySelectorAll('.arco-modal input').length"))
    print("  input 列表:", pg.evaluate("""() => Array.from(
        document.querySelectorAll('.arco-modal input')).map(i => ({
            cls: i.className, ph: i.placeholder, ro: i.readOnly}))"""))
    pg.screenshot(path="/tmp/diag_modal.png")
    b.close()

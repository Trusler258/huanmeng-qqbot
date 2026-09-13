"""回归：主题换色 + 配置页分层导航 + 悬浮 AI 助手（面板 v2.3.1）— v2 修正断言"""
import sys
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:49300"
PASSWD = "HuanmengPanel@2026"
results = []

def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'} | {name} | {detail}")

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path="/usr/bin/chromium-browser",
                                args=["--no-sandbox"])
    ctx = browser.new_context(viewport={"width": 1600, "height": 900})
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))

    page.goto(f"{BASE}/login", wait_until="networkidle")
    page.fill('input[type="password"]', PASSWD)
    page.keyboard.press("Enter")
    page.wait_for_url("**/dashboard/overview", timeout=15000)
    check("登录跳转概览", True)

    page.wait_for_timeout(1500)
    prim = page.evaluate("getComputedStyle(document.body).getPropertyValue('--primary-6').trim()")
    check("主题色 primary-6=purple", prim == "114,46,209", prim)
    fab = page.query_selector(".ai-fab")
    if fab:
        bg = fab.evaluate("el => getComputedStyle(el).backgroundImage")
        # 渐变端点是 primary-5(141,78,218) 与 primary-7(85,29,176)——紫色即为主题生效
        purple = "141, 78, 218" in bg and "85, 29, 176" in bg
        check("悬浮球渐变用紫色系主色", purple, bg[:90])
    else:
        check("悬浮球存在", False, "未找到 .ai-fab")

    # 概览页无回归（紫色主题下趋势图仍渲染）
    page.wait_for_timeout(1000)
    canvases = page.query_selector_all(".trend-chart canvas")
    check("概览趋势图仍渲染", len(canvases) > 0, f"{len(canvases)} canvas")

    fab.click()
    page.wait_for_selector(".ai-window", timeout=5000)
    check("悬浮窗展开", True)
    hdr = page.query_selector(".ai-window-header")
    box = hdr.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + 10)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] / 2 - 200, box["y"] + 10 + 80, steps=8)
    page.mouse.up()
    newbox = page.query_selector(".ai-window").bounding_box()
    check("悬浮窗可拖动", abs(newbox["x"] - box["x"]) > 100, f"x {box['x']:.0f}->{newbox['x']:.0f}")
    page.click(".ai-welcome-item")
    page.wait_for_selector(".ai-msg-action", timeout=40000)
    reply = page.inner_text(".ai-msg-assistant .ai-msg-bubble")
    check("助手回复有内容", len(reply.strip()) > 2, reply[:40])
    page.click(".ai-msg-action button")
    page.wait_for_timeout(1200)
    check("navigate 自动跳转", "/config/editor" in page.url, page.url)

    # 配置页分层导航
    page.wait_for_timeout(1000)
    cards = page.query_selector_all(".section-card")
    check("section 摘要卡片渲染", len(cards) > 0, f"{len(cards)} 张卡")
    if cards:
        cards[0].click()
        page.wait_for_timeout(400)
        rows = page.query_selector_all(".form-row")
        check("选中 section 只显示该段", len(rows) > 0, f"{len(rows)} 行")
        page.click(".crumb-item.root")
        page.wait_for_timeout(300)
        check("面包屑返回全部", len(page.query_selector_all(".section-card")) > 0)
    page.fill(".form-toolbar input", "名字")
    page.wait_for_timeout(500)
    check("跨段搜索命中", len(page.query_selector_all(".form-row")) > 0,
          f"命中 {len(page.query_selector_all('.form-row'))} 行")
    page.fill(".form-toolbar input", "")
    page.wait_for_timeout(300)

    # 源码模式：radio-group-button 是 label.arco-radio-button（非 arco-radio）
    clicked = page.evaluate("""() => {
        for (const label of document.querySelectorAll('label.arco-radio-button')) {
            if (label.textContent.includes('源码')) { label.click(); return true; }
        }
        return false;
    }""")
    check("点击源码模式", clicked)
    page.wait_for_selector(".code-editor", timeout=5000)
    ta_val = page.eval_on_selector(".code-editor", "el => el.value.length")
    check("源码模式 textarea 有原文", ta_val > 100, f"{ta_val} 字符")

    check("JS 零报错", len(errors) == 0, "; ".join(errors[:3]))
    browser.close()

fails = [r for r in results if not r[1]]
print(f"\n===== {len(results)-len(fails)}/{len(results)} PASS =====")
sys.exit(1 if fails else 0)

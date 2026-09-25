# -*- coding: utf-8 -*-
"""Steam 代理令牌页（面板 /config/proxy）验收：真机 Playwright + 真调 Worker

覆盖：
  1. 页面可达、菜单选中态、面包屑、卡片齐全
  2. 表格数据行数 == 后端令牌数（且能看到主令牌 / 备注 / 不可删除）
  3. 点「生成新令牌」→ 弹窗 → 填备注 → 生成 → 拿到明文令牌 + 转发文案
  4. **拿这个新令牌真调一次 Worker** ← 关键：证明面板改的东西真的生效了
  5. 关闭弹窗后表格 +1，.env 里确实多了一条（备注对得上）
  6. 「撤销」→ 表格回原值 → 新令牌立即 401，主令牌不受牵连
  7. 截图 + 紫色像素占比（主题一致性）

⚠️ 本脚本会真的改服务器 config/.env 并同步 Cloudflare，
   但 finally 里会把自己造的令牌删干净（断言最终数量 == 初始数量）。

踩坑备忘（Arco DOM）：
  · <tbody> **没有** .arco-table-tbody 类 → 必须 table.arco-table-element tbody tr.arco-table-tr
  · 生成按钮要等 Cloudflare 同步往返（有时 >3s）才切到结果态 → 等 .blob 出现，别用固定 sleep
"""
import re
import sys

import httpx
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:49300"
API = "http://127.0.0.1:59300/api"
PWD = "HuanmengPanel@2026"
WORKER = "https://steamapi.truslerweb.dpdns.org"
SID = "76561199427581023"
LABEL = "_自测_可删"
ROWS = "table.arco-table-element tbody tr.arco-table-tr"

ok_n = 0
fail_n = 0


def chk(name, cond, detail=""):
    global ok_n, fail_n
    if cond:
        ok_n += 1
        print("  [OK]   %s %s" % (name, detail))
    else:
        fail_n += 1
        print("  [FAIL] %s %s" % (name, detail))
    return bool(cond)


def worker_probe(token):
    """直接用令牌调 Worker，返回 (http_code, ok_flag)"""
    try:
        r = httpx.post(
            WORKER,
            headers={"X-Proxy-Token": token, "User-Agent": "Mozilla/5.0",
                     "Content-Type": "application/json"},
            json={"ops": [{"k": "api", "p": "IPlayerService/GetSteamLevel/v1/",
                           "q": {"steamid": SID}}]},
            timeout=40, trust_env=False,
        )
        if r.status_code != 200:
            return r.status_code, False
        d = r.json()
        res = d.get("r") or []
        return 200, bool(res and res[0].get("ok"))
    except Exception:
        return -1, False


def rows_of(page):
    return page.evaluate(
        "() => document.querySelectorAll('%s').length" % ROWS)


def purple_ratio(path):
    try:
        from PIL import Image
    except ImportError:
        return -1
    im = Image.open(path).convert("RGB")
    px = im.load()
    w, h = im.size
    cnt = tot = 0
    for y in range(0, h, 3):
        for x in range(0, w, 3):
            r, g, b = px[x, y]
            tot += 1
            if 80 <= r <= 180 and g < 90 and 130 <= b <= 230:
                cnt += 1
    return cnt / max(tot, 1)


def main():
    c = httpx.Client(timeout=60, trust_env=False)
    tk = c.post(API + "/auth/login",
                json={"username": "admin", "password": PWD}).json()["token"]
    H = {"Authorization": "Bearer " + tk}

    # 先清掉上一轮可能残留的自测令牌
    for t in c.get(API + "/steam-proxy/tokens", headers=H).json().get("tokens", []):
        if t.get("label") == LABEL:
            c.delete(API + "/steam-proxy/tokens/" + t["id"], headers=H)

    before = c.get(API + "/steam-proxy/tokens", headers=H).json()
    n_before = before.get("count")
    main_tok = next((t["token"] for t in before["tokens"] if t["primary"]), "")
    print("=" * 68)
    print("初始令牌 %s 个 | CF 连通 %s | CF 有 PROXY_TOKEN=%s / PROXY_TOKENS=%s"
          % (n_before, before.get("cf_ok"),
             before.get("cf_has_primary"), before.get("cf_has_extras")))
    print("=" * 68)

    created = ""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                executable_path="/usr/bin/chromium-browser", args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1600, "height": 1000})

            # ── 1. 登录 + 进页面 ──
            page.goto(BASE + "/login", wait_until="networkidle")
            page.fill('input[type="password"]', PWD)
            page.press('input[type="password"]', "Enter")
            page.wait_for_url("**/dashboard/**", timeout=20000)
            page.goto(BASE + "/config/proxy", wait_until="networkidle")
            page.wait_for_selector(ROWS, timeout=15000)
            page.wait_for_timeout(1200)
            print("[1] 页面渲染")

            crumbs = page.evaluate(
                "() => Array.from(document.querySelectorAll('.arco-breadcrumb-item'))"
                ".map(e => e.textContent.trim())")
            chk("面包屑", any("Steam" in x for x in crumbs), str(crumbs))

            sel = page.evaluate(
                "() => Array.from(document.querySelectorAll("
                "'.arco-menu-item.arco-menu-selected')).map(e => e.textContent.trim())")
            chk("菜单选中 = Steam 代理", any("Steam" in s for s in sel), str(sel))

            cards = page.evaluate(
                "() => Array.from(document.querySelectorAll('.arco-card-header-title'))"
                ".map(e => e.textContent.trim())")
            chk("卡片齐全", "访问令牌" in cards and "代理服务" in cards and "说明" in cards,
                str(cards))

            ep = page.evaluate(
                "() => document.body.innerText.includes('steamapi.truslerweb.dpdns.org')")
            chk("端点已从后端带出", ep)

            # /status 与 /tokens 字段不重叠，必须合并 —— 曾经只取 /tokens，
            # 导致 Worker 名/账号显示 "--"、CF 状态错显「异常」
            top = page.evaluate(
                "() => Array.from(document.querySelectorAll('.arco-descriptions'))"
                ".map(e => e.innerText).join('\\n')")
            chk("Worker 名已显示", "steamapi-truslerweb" in top)
            chk("CF 状态 = 正常", "正常" in top and "异常" not in top)
            chk("CF secret 名已列出",
                "PROXY_TOKEN" in top and "PROXY_TOKENS" in top)

            # ── 2. 表格 == 后端 ──
            print("[2] 令牌列表")
            r1 = rows_of(page)
            chk("表格行数 == 令牌数", r1 == n_before, "面板 %d / 后端 %s" % (r1, n_before))
            body = page.evaluate(
                "() => document.querySelector('.arco-table').innerText")
            chk("主令牌有标识 + 禁删", "主令牌" in body and "不可删除" in body)
            chk("额外令牌可撤销", "撤销" in body)
            chk("令牌默认脱敏", "…" in body)
            page.screenshot(path="/tmp/shot_proxy_page.png", full_page=True)

            # ── 3. 生成新令牌 ──
            print("[3] 生成新令牌")
            page.click('button:has-text("生成新令牌")')
            page.wait_for_selector(".arco-modal .arco-input", timeout=8000)
            page.fill(".arco-modal .arco-input", LABEL)
            page.click('.arco-modal button:has-text("生成")')
            # 等结果态（含转发文案），要容下 Cloudflare 同步往返
            page.wait_for_selector(".arco-modal .blob", timeout=60000)
            page.wait_for_timeout(400)

            blob = page.evaluate(
                "() => { const e = document.querySelector('.arco-modal .blob');"
                " return e ? e.textContent : ''; }")
            m = re.search(r"令牌：([A-Za-z0-9_-]{20,})", blob)
            created = m.group(1) if m else ""
            chk("弹窗出现明文令牌", len(created) >= 40, "%s…(%d 字符)"
                % (created[:16], len(created)))
            chk("转发文案含端点/请求头/示例",
                "X-Proxy-Token" in blob and WORKER in blob and "curl" in blob,
                "%d 字符" % len(blob))
            page.screenshot(path="/tmp/shot_proxy_modal.png")

            # ── 4. 真调 Worker（关键） ──
            code, flag = worker_probe(created)
            chk("新令牌真调 Worker 成功", code == 200 and flag,
                "HTTP %s ok=%s" % (code, flag))

            # ── 5. 关闭 → 表格 +1 / .env 落盘 ──
            print("[4] 落库与刷新")
            page.click('.arco-modal button:has-text("完成")')
            page.wait_for_timeout(2500)
            r2 = rows_of(page)
            chk("生成后表格 +1", r2 == n_before + 1, "%d → %d" % (r1, r2))
            chk("新行显示备注", LABEL in page.evaluate(
                "() => document.querySelector('.arco-table').innerText"))

            mid = c.get(API + "/steam-proxy/tokens", headers=H).json()
            got = [t for t in mid.get("tokens", []) if t["token"] == created]
            chk("后端 .env 已记上新令牌", len(got) == 1, "count=%s" % mid.get("count"))
            chk("备注原样保存", bool(got) and got[0]["label"] == LABEL, str(got[:1]))
            page.screenshot(path="/tmp/shot_proxy_full.png", full_page=True)

            # ── 6. 撤销（只点新令牌那一行） ──
            print("[5] 撤销")
            page.click('tr:has-text("%s") .arco-link:has-text("撤销")' % LABEL)
            page.wait_for_selector(".arco-popconfirm button", timeout=8000)
            page.click('.arco-popconfirm button:has-text("确定")')
            back = False
            for _ in range(20):
                page.wait_for_timeout(500)
                if rows_of(page) == n_before:
                    back = True
                    break
            chk("撤销后表格回原值", back, "%d → %d" % (r2, rows_of(page)))

            after = c.get(API + "/steam-proxy/tokens", headers=H).json()
            chk("后端 .env 已清除", after.get("count") == n_before,
                "count=%s" % after.get("count"))
            chk("朋友令牌还在（未被误删）",
                any(t["token"] == before["tokens"][-1]["token"]
                    for t in after.get("tokens", [])))

            code2, _ = worker_probe(created)
            chk("被撤销的令牌立即失效(401)", code2 == 401, "HTTP %s" % code2)
            mc, mf = worker_probe(main_tok)
            chk("主令牌未受牵连", mc == 200 and mf, "HTTP %s ok=%s" % (mc, mf))

            # ── 7. 主题 ──
            r = purple_ratio("/tmp/shot_proxy_full.png")
            chk("紫色主题一致(>0.1%)", r > 0.001, "%.2f%%" % (r * 100))

            browser.close()
    finally:
        try:
            cur = c.get(API + "/steam-proxy/tokens", headers=H).json()
            for t in cur.get("tokens", []):
                if t["token"] == created or t.get("label") == LABEL:
                    c.delete(API + "/steam-proxy/tokens/" + t["id"], headers=H)
            fin = c.get(API + "/steam-proxy/tokens", headers=H).json()
            print("-" * 68)
            print("清场后令牌数 = %s（初始 %s）" % (fin.get("count"), n_before))
        except Exception as e:
            print("清场失败，请手动检查：%s" % e)

    print("=" * 68)
    print("通过 %d / 失败 %d" % (ok_n, fail_n))
    print("=" * 68)
    sys.exit(1 if fail_n else 0)


main()

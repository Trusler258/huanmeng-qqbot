# -*- coding: utf-8 -*-
"""面板三处修复的 UI 验收（DOM 断言 + 截图）。

验的是本次修的三个页面：
  1. 群统计 —— 天数不再是 1，日期列表有数据
  2. 地震   —— 最近地震列表有数据（原来是 SSL 证书过期 + 接口废弃）
  3. 消息检索 —— Bot 发送记录（msglog）会话下拉有选项

⚠️ Arco 的 tab-pane 共用 `arco-tabs-pane` 类名且非活动面板不卸载，
   所以断言要限定在对应面板/容器内（沿用 v2.3.34 的 `.profile-tab` 经验）。

跑法：python3 scripts/_probe_panel_fix.py
"""
import asyncio
import re
import sys

PWD = "HuanmengPanel@2026"
BASE = "http://127.0.0.1:49300"
OK, BAD = [], []


def ck(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print("  [%s] %s%s" % ("OK" if cond else "FAIL", name, ("  " + str(extra)) if extra else ""))


async def main():
    import httpx
    from playwright.async_api import async_playwright

    tok = httpx.post(BASE + "/api/auth/login",
                     json={"username": "admin", "password": PWD}, timeout=15).json()["token"]

    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path="/usr/bin/chromium-browser",
                                    args=["--no-sandbox", "--disable-dev-shm-usage"])
        page = await b.new_page(viewport={"width": 1560, "height": 1000})
        await page.add_init_script(f"try{{localStorage.setItem('token','{tok}')}}catch(e){{}}")

        # ── 1. 群统计 ──────────────────────────────────────────
        print("\n=== 1. 群统计页 ===")
        await page.goto(BASE + "/fun/social", wait_until="domcontentloaded")
        await page.wait_for_timeout(1800)
        for t in await page.query_selector_all(".arco-tabs-tab"):
            if "群统计" in (await t.inner_text()):
                await t.click()
                break
        await page.wait_for_timeout(1500)

        # 左侧群列表：每个群显示「N 天」
        items = await page.query_selector_all(".pick-item")
        texts = [(await i.inner_text()).replace("\n", " ").strip() for i in items]
        ck("左侧有群列表", len(texts) > 0, "%d 个" % len(texts))
        print("     样本:", texts[:3])
        days = []
        for t in texts:
            try:
                days.append(int(t.split()[-2]))
            except Exception:
                pass
        ck("天数不再全是 1（读到归档）", any(d > 1 for d in days), "天数: %s" % days[:6])
        ck("存在 >30 天的群", any(d > 30 for d in days), "最大 %s" % (max(days) if days else 0))

        # 点第一个群 → 右侧日期列表
        if items:
            await items[0].click()
            await page.wait_for_timeout(1600)
            # ⚠️ 不能直接数 .arco-table-tr —— Arco 非活动面板不卸载，会数到
            #    好感度/幸运值那几张表。
            #    也不能只匹配含"统计"的卡片 —— 左侧那张叫「有统计的群」也含"统计"，
            #    会被先匹配到（第一版探针就栽在这，误报 0 行）。
            #    右侧卡片的标题形如「群 1058782600 统计」，用"群 <数字>"锚定。
            card = None
            for c in await page.query_selector_all(".arco-card"):
                head = await c.query_selector(".arco-card-header")
                if not head:
                    continue
                ht = (await head.inner_text()).strip()
                if re.match(r"^群\s*\d+\s*统计", ht):
                    card = c
                    break
            ck("找到「群 <号> 统计」卡片", card is not None)
            body = []
            if card:
                for r in await card.query_selector_all(".arco-table-tr"):
                    if await r.query_selector(".arco-table-th"):
                        continue
                    body.append((await r.inner_text()).replace("\n", " | ").strip())
            ck("右侧日期表格有数据", len(body) > 1, "%d 行" % len(body))
            print("     首行:", body[0][:90] if body else "(空)")
            ck("行内容是日期而非 QQ",
               bool(body) and "-" in body[0].split("|")[0],
               body[0].split("|")[0].strip() if body else "")
            ck("列不是好感度表的操作列", all("| 存 | 删" not in x for x in body[:3]))
        await page.screenshot(path="/tmp/_panel_fix_stats.png", full_page=True)

        # ── 2. 地震 ────────────────────────────────────────────
        print("\n=== 2. 地震页 ===")
        await page.goto(BASE + "/fun/earthquake", wait_until="domcontentloaded")
        await page.wait_for_timeout(3500)     # 该页会实时拉远端接口
        body_txt = await page.inner_text("body")
        ck("页面无拉取失败提示", "拉取失败" not in body_txt,
           [ln for ln in body_txt.split("\n") if "拉取失败" in ln][:1])
        ck("出现真实地震地点", any(k in body_txt for k in ("新疆", "青海", "云南", "四川", "台湾", "西藏")),
           "")
        ck("不再显示原始英文列名", "magnitude" not in body_txt and "location" not in body_txt)
        ck("表头已中文化", ("震级" in body_txt) and ("地点" in body_txt))
        await page.screenshot(path="/tmp/_panel_fix_eq.png", full_page=True)

        # ── 3. 消息检索 msglog 会话下拉 ─────────────────────────
        print("\n=== 3. 消息检索页（msglog 会话选择）===")
        await page.goto(BASE + "/data/messages", wait_until="domcontentloaded")
        await page.wait_for_timeout(2200)
        sel = None
        for s in await page.query_selector_all(".arco-select"):
            ph = await s.inner_text()
            if "选择会话" in ph or not ph.strip():
                sel = s
                break
        ck("找到会话下拉", sel is not None)
        if sel:
            await sel.click()
            await page.wait_for_timeout(1200)
            opts = await page.query_selector_all(".arco-select-option")
            labels = [(await o.inner_text()).strip() for o in opts]
            ck("下拉有选项", len(labels) > 0, "%d 个" % len(labels))
            print("     样本:", labels[:3])
            ck("选项不是空白", any(x for x in labels))
            ck("选项含群号/QQ", any(x[:6].isdigit() for x in labels if x), labels[:2])
            await page.screenshot(path="/tmp/_panel_fix_msglog.png", full_page=True)
        await b.close()

    print("\n" + "=" * 54)
    print("通过 %d，失败 %d" % (len(OK), len(BAD)))
    for x in BAD:
        print("  -", x)
    print("=" * 54)
    return 0 if not BAD else 1


sys.exit(asyncio.get_event_loop().run_until_complete(main()))

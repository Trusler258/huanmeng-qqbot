"""概览页趋势图验证：截图 + 读取折线图的接口数据是否多天有值

背景：bot 每天 0 点把昨天的统计文件移到 data/stats_archive/，
panel 的 /overview/trend 原来只扫 data/ 根目录 → 只能读到"今天"那一份，
表现为「折线图只有一天有数据，第二天那个点也变 0」。
本探针同时验证接口层（多天有值）和渲染层（canvas 出图）。
"""
import asyncio
import json
import sys

import httpx
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:49300"
API = "http://127.0.0.1:59300/api"


def check_api() -> tuple[bool, str]:
    tok = httpx.post(f"{API}/auth/login",
                     json={"username": "admin", "password": "HuanmengPanel@2026"},
                     timeout=15).json()["token"]
    t = httpx.get(f"{API}/overview/trend", params={"days": 14},
                  headers={"Authorization": "Bearer " + tok}, timeout=30).json()
    pts = t["data"]
    withdata = [p for p in pts if p["messages"] > 0]
    lines = [f"  {p['date']}  消息 {p['messages']:>5}  人数 {p['active_users']:>3}"
             for p in pts]
    print("接口返回近 14 天：")
    print("\n".join(lines))
    ok = len(withdata) >= 3
    return ok, f"有数据天数 = {len(withdata)}/{len(pts)}（要求 >= 3）"


async def main() -> int:
    ok_api, msg = check_api()
    print("\n接口层：", "PASS" if ok_api else "FAIL", "-", msg)

    errors: list[str] = []
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path="/usr/bin/chromium-browser",
                                    args=["--no-sandbox"])
        pg = await b.new_page(viewport={"width": 1500, "height": 1050})
        pg.on("pageerror", lambda e: errors.append(str(e)[:200]))
        pg.on("console", lambda m: errors.append(m.text[:200]) if m.type == "error" else None)

        tok = httpx.post(f"{API}/auth/login",
                         json={"username": "admin", "password": "HuanmengPanel@2026"},
                         timeout=15).json()["token"]
        await pg.add_init_script(f"try{{localStorage.setItem('token','{tok}')}}catch(e){{}}")

        await pg.goto(f"{BASE}/dashboard/overview", wait_until="domcontentloaded")
        await pg.wait_for_timeout(11000)

        canv = await pg.evaluate(
            """() => { const cs=[...document.querySelectorAll('canvas')];
                       return cs.map(c => ({w:c.clientWidth, h:c.clientHeight})); }"""
        )
        print(f"\n页面 canvas 数 = {len(canv)} -> {canv[:4]}")
        body = await pg.inner_text("body")
        for kw in ("消息趋势", "活跃", "近 14 天", "14 天"):
            if kw in body:
                print(f"  页面含关键词: {kw}")

        await pg.screenshot(path="/tmp/_panel_trend.png", full_page=True)
        await b.close()

    real = [e for e in errors if "favicon" not in e.lower()]
    print(f"\nJS 错误 {len(real)} 条")
    for e in real[:5]:
        print("   ", e)

    ok = ok_api and len(canv) > 0 and not real
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

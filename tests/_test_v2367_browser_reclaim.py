"""v2.3.67 Chromium 懒加载 + 空闲自动回收 回归测试

背景：_ensure_browser() 是全局单例且启动时预启动、之后永不释放 →
服务器常驻 19 个 chromium 进程 / 数百 MB，而近 7 天只截图 40 次（全是日报）。
改成懒加载 + 空闲 10 分钟回收。

用法（本地）:
  python tests/_test_v2367_browser_reclaim.py
"""
from __future__ import annotations

import asyncio
import inspect
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PASS = 0
FAIL = 0


def ok(msg: str) -> None:
    global PASS
    PASS += 1
    print(f"  \033[32mOK\033[0m   {msg}")


def bad(msg: str) -> None:
    global FAIL
    FAIL += 1
    print(f"  \033[31mFAIL\033[0m {msg}")


class FakeBrowser:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


class FakePW:
    def __init__(self):
        self.stopped = False

    async def stop(self):
        self.stopped = True


class FakePage:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


def reset(cl, browser=None, pw=None, busy=0, last=None):
    cl._browser = browser
    cl._playwright_instance = pw
    cl._browser_busy = busy
    cl._last_render_ts = last if last is not None else 0.0
    cl._page_pool.clear()


def main() -> None:
    import modules.changelog as cl

    print("=== 1. 空闲/忙碌判断 ===")
    reset(cl)
    if asyncio.run(cl.reclaim_browser()) is False:
        ok("没有浏览器实例 → 不回收（返回 False）")
    else:
        bad("无实例时竟然回收了")

    b = FakeBrowser()
    reset(cl, browser=b, last=time.time() - 5)          # 刚用过
    if asyncio.run(cl.reclaim_browser()) is False and not b.closed:
        ok(f"空闲 {cl.browser_idle_sec():.0f}s < {cl._BROWSER_IDLE_SEC:.0f}s → 不回收")
    else:
        bad("空闲不足却回收了")

    b2 = FakeBrowser()
    reset(cl, browser=b2, busy=1, last=time.time() - 9999)   # 很旧但有渲染在飞
    if asyncio.run(cl.reclaim_browser()) is False and not b2.closed:
        ok("有渲染在飞（busy>0）→ 即使超时也不回收")
    else:
        bad("渲染中却把浏览器关了（危险）")

    print("\n=== 2. 真正回收：页面池 + browser + playwright 全清 ===")
    b3, pw3 = FakeBrowser(), FakePW()
    pages = [FakePage(), FakePage()]
    reset(cl, browser=b3, pw=pw3, last=time.time() - (cl._BROWSER_IDLE_SEC + 10))
    cl._page_pool.extend(pages)
    r = asyncio.run(cl.reclaim_browser())
    if r is True and b3.closed and pw3.stopped and cl._browser is None \
            and cl._playwright_instance is None and not cl._page_pool:
        ok("空闲超时 → 页面池清空 + browser.close + playwright.stop + 单例置空")
    else:
        bad(f"回收不完整: r={r} browser_closed={b3.closed} pw_stopped={pw3.stopped} "
            f"pool={len(cl._page_pool)}")

    print("\n=== 3. force=True 跳过判断 ===")
    b4 = FakeBrowser()
    reset(cl, browser=b4, busy=1, last=time.time())
    if asyncio.run(cl.reclaim_browser(force=True)) is True and b4.closed:
        ok("force=True → 无视空闲/忙碌强制回收")
    else:
        bad("force=True 未生效")

    print("\n=== 4. touch_browser 重置计时 ===")
    reset(cl, browser=FakeBrowser(), last=time.time() - (cl._BROWSER_IDLE_SEC + 100))
    before = cl.browser_idle_sec()
    cl.touch_browser()
    after = cl.browser_idle_sec()
    if before > cl._BROWSER_IDLE_SEC and after < 1.0:
        ok(f"touch 后空闲计时归零（{before:.0f}s → {after:.2f}s）")
    else:
        bad(f"touch 未重置: {before:.0f}s → {after:.2f}s")

    print("\n=== 5. 截图外壳维护 busy/touch，且异常也会复位 ===")
    reset(cl)
    calls = {"inner": 0}

    async def fake_inner(*a, **kw):
        calls["inner"] += 1
        if cl._browser_busy != 1:
            raise AssertionError(f"渲染期间 busy 应为 1，实际 {cl._browser_busy}")
        return True

    orig = cl._screenshot_html_inner
    cl._screenshot_html_inner = fake_inner
    try:
        r = asyncio.run(cl._screenshot_html("<p/>", Path("/tmp/_t.png")))
        if r is True and cl._browser_busy == 0 and cl.browser_idle_sec() < 1.0:
            ok("正常路径：busy 归零 + touch 刷新")
        else:
            bad(f"正常路径异常: r={r} busy={cl._browser_busy}")

        async def boom(*a, **kw):
            raise RuntimeError("render boom")
        cl._screenshot_html_inner = boom
        try:
            asyncio.run(cl._screenshot_html("<p/>", Path("/tmp/_t.png")))
        except RuntimeError:
            pass
        if cl._browser_busy == 0:
            ok("内部抛异常时 busy 也会复位（不会永久卡住回收）")
        else:
            bad(f"异常路径 busy 未复位: {cl._browser_busy}")
    finally:
        cl._screenshot_html_inner = orig

    print("\n=== 6. bot.py 已不再预启动 Chromium ===")
    src = Path(__file__).resolve().parent.parent.joinpath("bot.py").read_text(encoding="utf-8")
    if "await _ensure_browser()" not in src:
        ok("bot.py 里已无启动期 _ensure_browser() 预启动")
    else:
        bad("bot.py 仍在启动时预启动 Chromium")
    if "browser_idle_loop" in src:
        ok("bot.py 已启动 browser_idle_loop 后台回收任务")
    else:
        bad("bot.py 未启动空闲回收任务")

    print("\n=== 7. 回收后能重新懒加载（_ensure_browser 有重建路径）===")
    es = inspect.getsource(cl._ensure_browser)
    if "_playwright_instance = await pw().start()" in es and "if _browser is not None and _browser.is_connected()" in es:
        ok("_ensure_browser 仍具备懒加载重建逻辑（回收后可自动重启）")
    else:
        bad("_ensure_browser 重建逻辑缺失")

    print(f"\n=== 结果: {PASS} passed, {FAIL} failed ===")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()

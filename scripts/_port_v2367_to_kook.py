#!/usr/bin/env python3
"""把 QQ bot v2.3.67 的「Chromium 懒加载 + 空闲回收」移植到 KOOK bot 的 changelog.py。

背景：实测 KOOK bot 的 Playwright 从 2026-09-14 挂了 14 天、10 个 chromium / 270MB。
其 modules/changelog.py 与 QQ bot v2.3.67 **之前**的版本逐字节相同，
只有两处平台差异（指令前缀 `/~`→`.`、图片 CQ 格式不同）——那些与本次改动无关。

所以这里只做**精确字符串替换**（不做行号定位，避免漂移），
只搬浏览器生命周期那 4 处，绝不碰平台相关的 CQ 拼装。

用法：
  1) 先 scp 服务器上的 changelog.py 到本地（或改 INPUT）
  2) python scripts/_port_v2367_to_kook.py <输入文件> <输出文件>
  3) 用 diff 复核输出，只应有预期改动
"""
import sys
from pathlib import Path

# ── 1. 补 import time ──
R1 = (
    "import asyncio\nimport os\nimport re\nfrom datetime import datetime",
    "import asyncio\nimport os\nimport re\nimport time\nfrom datetime import datetime",
)

# ── 2. 补模块级 logger ──
R2 = (
    "from core.logger import get_logger\n\n# 延迟导入（避免启动时加载重型依赖）\n_markdown_lib = None",
    "from core.logger import get_logger\n"
    "\n"
    "# ★ 补上模块级 logger（原先只在各函数内部各自 get_logger，模块级没有）。\n"
    "#   新增的回收逻辑是模块级函数，直接用 logger 会 NameError —— 而且会连锁炸在\n"
    "#   except 分支里（异常处理自己也抛），真出问题时连日志都留不下。\n"
    "logger = get_logger(\"changelog\")\n"
    "\n"
    "# 延迟导入（避免启动时加载重型依赖）\n"
    "_markdown_lib = None",
)

# ── 3. _ensure_browser 末尾 touch + 整个回收模块 ──
IDLE_BLOCK = '''

# ── Chromium 空闲自动回收（移植自 QQ bot v2.3.67）─────────────
# 背景：_ensure_browser() 是全局单例，且启动时还会预启动，之后永不释放。
#   实测本机常驻 10 个 chromium 进程 / 270MB，从 2026-09-14 起挂了 14 天；
#   更麻烦的是 **snap 版 chromium 会跑到自己的 snap.chromium.*.scope**，
#   不在服务的 cgroup 内 → `systemctl restart kook-bot` 根本杀不掉它们，只能一直攒。
# 方案：记录最后一次渲染时间，后台每 60s 检查；空闲超过 _BROWSER_IDLE_SEC 就整体关闭
#   （页面池 + browser + playwright instance 全清）。下次要渲染时 _ensure_browser()
#   懒加载重启（~1s，只在真需要时付一次）。
_BROWSER_IDLE_SEC = 600.0        # 空闲 10 分钟即回收
_browser_busy = 0                # 正在渲染的请求数
_last_render_ts: float = 0.0     # 最后一次"碰过浏览器"的时间


def touch_browser() -> None:
    """标记浏览器刚被使用过（回收定时器重置）"""
    global _last_render_ts
    _last_render_ts = time.time()


def browser_idle_sec() -> float:
    """距上次使用过去了多少秒（从未用过返回 0）"""
    if not _last_render_ts:
        return 0.0
    return time.time() - _last_render_ts


async def reclaim_browser(force: bool = False) -> bool:
    """关闭空闲的 Chromium 并释放内存；返回是否真的回收了。"""
    global _browser, _playwright_instance
    if _browser is None:
        return False
    if not force:
        if _browser_busy > 0:
            return False
        if browser_idle_sec() < _BROWSER_IDLE_SEC:
            return False
    idle = browser_idle_sec()
    try:
        async with _page_lock:
            for _p in _page_pool:
                try:
                    await _p.close()
                except Exception:
                    pass
            _page_pool.clear()
        try:
            await _browser.close()
        except Exception as e:
            logger.debug("[Playwright] browser.close 异常(忽略): %s", e)
        _browser = None
        if _playwright_instance is not None:
            try:
                await _playwright_instance.stop()
            except Exception as e:
                logger.debug("[Playwright] playwright.stop 异常(忽略): %s", e)
            _playwright_instance = None
        logger.info("[Playwright] 空闲 %.0fs → 已回收 Chromium，释放内存", idle)
        return True
    except Exception as e:
        logger.warning("[Playwright] 回收 Chromium 失败: %s", e)
        return False


async def browser_idle_loop(check_sec: float = 60.0) -> None:
    """后台任务：定期回收空闲浏览器（由 bot.py 启动）"""
    logger.info("Chromium 空闲回收已启动（空闲 > %.0fs 即释放，每 %.0fs 检查一次）",
                _BROWSER_IDLE_SEC, check_sec)
    while True:
        try:
            await asyncio.sleep(check_sec)
        except asyncio.CancelledError:
            break
        try:
            await reclaim_browser()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug("空闲回收检查异常(忽略): %s", e)
'''

R3 = (
    '    logger.info("[Playwright] Chromium 启动成功（极致加速已启用）")\n    return _browser\n',
    '    logger.info("[Playwright] Chromium 启动成功（极致加速已启用）")\n'
    '    touch_browser()\n'
    '    return _browser\n'
    + IDLE_BLOCK,
)

# ── 4. _screenshot_html 拆外壳 + 实现 ──
R4 = (
    "async def _screenshot_html(\n"
    "    html_content: str,\n"
    "    output_path: Path,\n"
    "    width: int = 800,\n"
    "    scale: float = 1.0,\n"
    ") -> bool:\n"
    '    """\n'
    "    Playwright 截图（v0.9.6 极致加速版 + 页面池）\n"
    '    """',
    "async def _screenshot_html(\n"
    "    html_content: str,\n"
    "    output_path: Path,\n"
    "    width: int = 800,\n"
    "    scale: float = 1.0,\n"
    ") -> bool:\n"
    '    """截图入口：统计在飞渲染数 + 刷新"最后使用时间"，再交给实现。\n'
    "\n"
    "    渲染期间 _browser_busy > 0，回收器会让路；渲染一结束就 touch，空闲计时从头开始。\n"
    '    """\n'
    "    global _browser_busy\n"
    "    _browser_busy += 1\n"
    "    try:\n"
    "        return await _screenshot_html_inner(html_content, output_path, width, scale)\n"
    "    finally:\n"
    "        _browser_busy -= 1\n"
    "        touch_browser()\n"
    "\n"
    "\n"
    "async def _screenshot_html_inner(\n"
    "    html_content: str,\n"
    "    output_path: Path,\n"
    "    width: int = 800,\n"
    "    scale: float = 1.0,\n"
    ") -> bool:\n"
    '    """\n'
    "    Playwright 截图（v0.9.6 极致加速版 + 页面池）\n"
    '    """',
)

REPLIES = [("import time", R1), ("模块级 logger", R2),
           ("_ensure_browser 尾部 + 回收模块", R3), ("_screenshot_html 拆分", R4)]


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    text = src.read_text(encoding="utf-8")
    crlf = "\r\n" in text
    text = text.replace("\r\n", "\n")
    print(f"输入换行: {'CRLF' if crlf else 'LF'}（统一按 LF 处理，避免整文件被改写）")
    for label, (old, new) in REPLIES:
        n = text.count(old)
        if n != 1:
            print(f"❌ [{label}] 期望命中 1 次，实际 {n} 次 —— 中止（不做任何替换）")
            sys.exit(1)
        text = text.replace(old, new, 1)
        print(f"✅ [{label}] 已替换")
    # ★ 必须 newline="\n"：Windows 上 write_text 默认会把 \n 翻成 \r\n，
    #   结果整个文件每行都变 → diff 爆炸、传到 Linux 还埋雷。
    with open(dst, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(f"\n已写出: {dst}（LF 换行）")


if __name__ == "__main__":
    main()

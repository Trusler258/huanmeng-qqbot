#!/usr/bin/env python3
"""Chromium 懒加载 + 空闲回收 端到端验证（v2.3.67）

验证四件事，并记录每步的内存/进程数：
  1. 初始无浏览器
  2. 渲染一张卡 → 浏览器启动、截图成功
  3. force 回收 → 进程消失、内存回落
  4. 再渲染一张 → 能重新懒加载并成功（关键的"回收不能把渲染搞坏"）
另外验证 touch / 空闲判断按预期工作。
只在本机（服务器）跑，不发送任何消息。

用法（服务器）:
  python3 scripts/_probe_browser_lifecycle.py
"""
import asyncio
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/root/bot")

import modules.changelog as cl  # noqa: E402


def meminfo():
    """返回 (可用内存MB, chromium进程数, chromium总RSS MB)"""
    avail = 0
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                avail = int(line.split()[1]) / 1024
                break
    except Exception:
        pass
    n, rss = 0, 0
    try:
        out = subprocess.run(["ps", "-eo", "rss,cmd"], capture_output=True, text=True).stdout
        for ln in out.splitlines():
            if "chrom" in ln.lower() and "grep" not in ln:
                n += 1
                try:
                    rss += int(ln.split(None, 1)[0])
                except Exception:
                    pass
    except Exception:
        pass
    return avail, n, rss / 1024


def snap(tag):
    a, n, r = meminfo()
    print("  [%-12s] 可用内存 %6.0fMB | chromium %2d 进程 %6.0fMB | 单例=%s"
          % (tag, a, n, r, "有" if cl._browser is not None else "无"))


HTML = """<!DOCTYPE html><html><head><meta charset="utf-8"><style>
body{margin:0;background:#1b1d22;color:#e8e8ee;font-family:sans-serif;width:420px}
.box{padding:24px}.t{font-size:22px;font-weight:700}.s{color:#9aa0aa;margin-top:8px}
</style></head><body><div class="box"><div class="t">生命周期探针 v2.3.67</div>
<div class="s">用于验证懒加载 / 空闲回收</div></div></body></html>"""


async def render(tag):
    out = Path("/tmp/_probe_lifecycle_%s.jpg" % tag)
    ok = await cl.render_card_to_image(HTML, output_filename=out.name, width=420)
    size = out.stat().st_size if out.exists() else 0
    print("  渲染[%s]: %s (%d bytes)" % (tag, "OK" if ok else "FAIL", size))
    return bool(ok), size


async def main():
    print("=== 1. 初始状态 ===")
    snap("初始")

    print("\n=== 2. 首次渲染（应触发懒加载）===")
    ok1, sz1 = await render("first")
    snap("渲染后")

    print("\n=== 3. 空闲判断 ===")
    print("  browser_idle_sec = %.1fs (阈值 %.0fs)" % (cl.browser_idle_sec(), cl._BROWSER_IDLE_SEC))
    r0 = await cl.reclaim_browser()
    print("  未到空闲阈值就回收？ → %s（应为 False）" % r0)

    print("\n=== 4. force 回收 ===")
    r1 = await cl.reclaim_browser(force=True)
    print("  force 回收 → %s（应为 True）" % r1)
    await asyncio.sleep(2)          # 等进程真正退出
    snap("回收后")

    print("\n=== 5. 回收后再渲染（关键：不能把渲染搞坏）===")
    ok2, sz2 = await render("second")
    snap("二次渲染后")

    print("\n=== 6. 空闲循环可被调用（不阻塞）===")
    try:
        task = asyncio.ensure_future(cl.browser_idle_loop(check_sec=0.5))
        await asyncio.sleep(1.2)
        task.cancel()
        print("  browser_idle_loop 正常运行并被取消 ✓")
    except Exception as e:
        print("  browser_idle_loop 异常:", e)

    print("\n读判：2 与 5 都应 OK 且字节数接近；3 应为 False；4 后进程数与内存应回落。")
    if not (ok1 and ok2):
        sys.exit(1)


asyncio.run(main())

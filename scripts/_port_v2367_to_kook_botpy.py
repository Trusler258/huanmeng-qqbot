#!/usr/bin/env python3
"""把 bot.py 里的「Chromium 预启动」换成「懒加载 + 空闲回收」（KOOK bot 版）。

用法:
  python scripts/_port_v2367_to_kook_botpy.py <输入 bot.py> <输出 bot.py>
"""
import sys
from pathlib import Path

OLD = '''        # ★ 预启动 Chromium 和渲染队列（不阻塞聊天）
        try:
            from core.queues import start_render_queue
            start_render_queue()
            from modules.changelog import _ensure_browser
            await _ensure_browser()
            info("Chromium 已预启动 + 渲染队列就绪")
        except Exception as e:
            warning("Chromium 预启动失败: %s (将在首次使用时懒加载)", e)
'''

NEW = '''        # ★ 不再预启动 Chromium —— 改「懒加载 + 空闲自动回收」。
        #   原先预启动后**永不释放**：实测本机常驻 10 个 chromium 进程 / 270MB，
        #   从 2026-09-14 起挂了 14 天没释放。更麻烦的是 snap 版 chromium 会跑到
        #   自己的 snap.chromium.*.scope，**不在本服务 cgroup 内** →
        #   `systemctl restart` 根本杀不掉它们，只能一直攒。
        #   现在首次真要渲染时才启动（~1s），空闲 10 分钟由 browser_idle_loop() 自动释放。
        try:
            from core.queues import start_render_queue
            start_render_queue()
            from modules.changelog import browser_idle_loop
            asyncio.ensure_future(browser_idle_loop())
            info("渲染队列就绪（Chromium 懒加载 + 空闲 10 分钟自动回收）")
        except Exception as e:
            warning("渲染队列启动失败: %s (仍会在首次使用时懒加载)", e)
'''


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    text = src.read_text(encoding="utf-8").replace("\r\n", "\n")
    n = text.count(OLD)
    if n != 1:
        print(f"❌ 预启动段命中 {n} 次（期望 1）—— 中止")
        sys.exit(1)
    if "import asyncio" not in text:
        print("❌ bot.py 里没有 import asyncio —— 中止（browser_idle_loop 需要 ensure_future）")
        sys.exit(1)
    text = text.replace(OLD, NEW, 1)
    with open(dst, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print("✅ bot.py 预启动段已替换为懒加载 + 空闲回收")


if __name__ == "__main__":
    main()

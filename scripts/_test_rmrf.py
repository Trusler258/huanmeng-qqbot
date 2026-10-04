# -*- coding: utf-8 -*-
"""chroot 沙箱 rm -rf /* 关键测试（安全：chroot 已验证）"""
import asyncio
import os
import sys

sys.path.insert(0, "/root/bot")
os.chdir("/root/bot")
from core import sandbox as sb  # noqa: E402


async def main():
    # 关键测试：chroot 里 rm -rf /*（此时 / 是沙箱目录）
    r = await sb.run_shell("rm -rf /* 2>/dev/null; echo SURVIVED; ls /")
    print("[rm -rf /* in chroot] ->", repr(r.get("stdout", "")[:150]))

    # 验证真实系统存活
    print("[real /etc/passwd exists]:", os.path.exists("/etc/passwd"))
    print("[real /usr/bin exists]:", os.path.isdir("/usr/bin"))
    print("[real /root/bot exists]:", os.path.isdir("/root/bot"))

    # 沙箱被砸后还能再跑一次吗（新沙箱）
    r2 = await sb.run_python("print('sandbox still works')")
    print("[py after] rc:", r2.get("returncode"), "| stdout:", repr(r2.get("stdout", "")[:40]))


asyncio.run(main())

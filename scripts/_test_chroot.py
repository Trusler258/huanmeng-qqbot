# -*- coding: utf-8 -*-
"""chroot 沙箱验证（安全测试，用完即删）"""
import asyncio
import os
import sys

sys.path.insert(0, "/root/bot")
os.chdir("/root/bot")
from core import sandbox as sb  # noqa: E402

print("isolation:", sb.isolation_mode())


async def main():
    # 1) py 基础
    r = await sb.run_python("print(1+1)\nprint('hello')")
    print("[py] rc:", r.get("returncode"), "| stdout:", repr(r.get("stdout", "")[:60]))

    # 2) chroot 里看 / ——应该是沙箱脚手架，不是真实根
    r2 = await sb.run_shell("ls /")
    print("[ls / in chroot] ->", repr(r2.get("stdout", "")[:120]))

    # 3) 尝试往 chroot 的 / 写标记（应该写进沙箱，不落真实根）
    r3 = await sb.run_shell("touch /MARKER_CHROOT && echo MARKER-WRITTEN && ls /MARKER_CHROOT")
    print("[marker] ->", repr(r3.get("stdout", "")[:80]))

    # 4) 验证真实根没有这个标记
    print("[real / has MARKER?]:", os.path.exists("/MARKER_CHROOT"))

    # 5) cpp 编译运行（/usr ro 绑定提供 g++）
    r5 = await sb.compile_and_run_cpp({"main.cpp": "#include <iostream>\nint main(){std::cout<<6*7;}"})
    print("[cpp] rc:", r5.get("returncode"), "| stdout:", repr(r5.get("stdout", "")[:40]))


asyncio.run(main())

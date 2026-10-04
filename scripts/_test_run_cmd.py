# -*- coding: utf-8 -*-
"""服务器端 /~run 指令端到端测试（用完即删）"""
import asyncio
import os
import sys

sys.path.insert(0, "/root/bot")
os.chdir("/root/bot")
from modules.commands import cmd_run  # noqa: E402


async def main():
    # 1) py 单行（raw_message 还原路径）
    r = await cmd_run(["py", "print(1+1)"], 3483585417, 0, "test", False, 0,
                      raw_message="/~run py print(1+1)")
    print("[py] ->", (r or "")[:150].replace("\n", " | "))

    # 2) py 多行（raw_message 保真）
    r2 = await cmd_run(["py"], 3483585417, 0, "test", False, 0,
                       raw_message="/~run py\nfor i in range(3):\n    print('line', i)")
    print("[py multi] ->", (r2 or "")[:150].replace("\n", " | "))

    # 3) cpp 编译运行
    r3 = await cmd_run(["cpp", "#include <iostream>\nint main(){std::cout << 7*6; }"],
                       3483585417, 0, "test", False, 0,
                       raw_message="/~run cpp #include <iostream>\nint main(){std::cout << 7*6; }")
    print("[cpp] ->", (r3 or "")[:150].replace("\n", " | "))

    # 4) sh 非管理员拒绝
    r4 = await cmd_run(["sh", "ls"], 123456, 0, "test", False, 0,
                       raw_message="/~run sh ls")
    print("[sh non-admin] ->", (r4 or "")[:80])


asyncio.run(main())

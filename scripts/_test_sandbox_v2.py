# -*- coding: utf-8 -*-
"""极简沙箱 v2 验证：持久工作区 + net 模式 + 预装包（用完即删）"""
import asyncio
import os
import sys

sys.path.insert(0, "/root/bot")
os.chdir("/root/bot")
from core.tools import _run_code, _ws_files  # noqa: E402


async def main():
    # 1) 工作区写入 → run_code 处理 → 跨次读取
    print("[ws write]:", await _ws_files("write", "data.csv", "name,score\ntrusler,99\nn1ghtch0rd,87"))
    r1 = await _run_code("python", "import pandas as pd\ndf=pd.read_csv('data.csv')\nprint(df.to_string(index=False))\nprint('avg =', df.score.mean())")
    print("[run pandas]:", (r1 or "")[:180].replace("\n", " | "))
    print("[ws read]:", (await _ws_files("read", "data.csv"))[:80].replace("\n", " | "))

    # 2) 第二次 run_code 读同一个文件（跨对话持久）
    r2 = await _run_code("python", "print(open('data.csv').read().splitlines()[0])")
    print("[persist]:", (r2 or "")[:100].replace("\n", " | "))

    # 3) net 模式抓网页
    r3 = await _run_code("python",
                         "import requests\nr=requests.get('https://api.anysearch.com/v1/search',timeout=10)\nprint('http', r.status_code)",
                         net=True)
    print("[net]:", (r3 or "")[:100].replace("\n", " | "))

    # 4) 默认隔离（net=False 无网络）
    r4 = await _run_code("python",
                         "import requests\ntry:\n    requests.get('https://example.com', timeout=5)\n    print('NET-LEAK')\nexcept Exception as e:\n    print('isolated OK:', type(e).__name__)")
    print("[isolated]:", (r4 or "")[:100].replace("\n", " | "))


asyncio.run(main())

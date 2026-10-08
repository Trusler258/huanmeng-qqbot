# -*- coding: utf-8 -*-
"""net 模式完整报错诊断（用完即删）"""
import asyncio
import os
import sys

sys.path.insert(0, "/root/bot")
os.chdir("/root/bot")
from core.tools import _run_code  # noqa: E402

CODE = (
    "import requests\n"
    "try:\n"
    "    r = requests.get('https://example.com', timeout=8)\n"
    "    print('http', r.status_code)\n"
    "except Exception as e:\n"
    "    print('ERR:', type(e).__name__, str(e)[:200])\n"
)


async def main():
    r = await _run_code("python", CODE, net=True)
    print((r or "")[:500])


asyncio.run(main())

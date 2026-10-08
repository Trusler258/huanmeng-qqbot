# -*- coding: utf-8 -*-
"""net 模式抓 GitHub 验证（用完即删）"""
import asyncio
import os
import sys

sys.path.insert(0, "/root/bot")
os.chdir("/root/bot")
from core.tools import _run_code  # noqa: E402

CODE = (
    "import requests, json\n"
    "r = requests.get('https://api.github.com/repos/Trusler258/huanmeng-qqbot', timeout=10)\n"
    "print('api:', r.status_code, json.loads(r.text).get('full_name'), 'stars:', json.loads(r.text).get('stargazers_count'))\n"
    "r2 = requests.get('https://raw.githubusercontent.com/Trusler258/huanmeng-qqbot/main/README.md', timeout=10)\n"
    "print('raw:', r2.status_code, len(r2.text), 'chars')\n"
    "r3 = requests.get('https://github.com/Trusler258/huanmeng-qqbot/commits/main.atom', timeout=10)\n"
    "print('atom:', r3.status_code, len(r3.text), 'chars')\n"
)


async def main():
    r = await _run_code("python", CODE, net=True)
    print((r or "")[:400])


asyncio.run(main())

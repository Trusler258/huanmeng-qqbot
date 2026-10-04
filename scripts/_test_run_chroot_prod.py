# -*- coding: utf-8 -*-
"""生产路径 /~run chroot 验证（用完即删）"""
import asyncio
import os
import sys

sys.path.insert(0, "/root/bot")
os.chdir("/root/bot")
from modules.commands import handle_command  # noqa: E402

MSG = """/~run py
import os, secrets
print('cwd files:', sorted(os.listdir('.')))
print('token:', secrets.token_hex(8))
with open('out.txt', 'w') as f:
    f.write('artifact ok')
print('DONE')
"""


async def main():
    r = await handle_command(MSG, 3483585417, 0, "test", False, 0, raw_message="")
    print("[prod chroot] ->", (r or "")[:400].replace("\n", " | "))


asyncio.run(main())

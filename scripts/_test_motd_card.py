# -*- coding: utf-8 -*-
"""服务器端 MOTD 卡端到端测试（用完即删）"""
import asyncio
import os
import sys

sys.path.insert(0, "/root/bot")
os.chdir("/root/bot")
from services.motd_card import make_card  # noqa: E402

png, status = asyncio.run(make_card("mc.hypixel.net"))
print("png:", png)
if status:
    pl = status.get("players") or {}
    print("online:", pl.get("online"), "/", pl.get("max"),
          "| latency:", status.get("_latency_ms"), "ms | source:", status.get("_source"))
if png:
    print("size:", os.path.getsize(png), "bytes")

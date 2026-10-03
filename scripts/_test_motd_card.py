# -*- coding: utf-8 -*-
"""服务器端 MOTD 卡端到端测试（用完即删）"""
import asyncio
import os
import sys
import time

sys.path.insert(0, "/root/bot")
os.chdir("/root/bot")
from services.motd_card import make_card  # noqa: E402


async def main():
    for target in ["wdsj.net", "mc233.cn"]:
        t0 = time.time()
        try:
            png, status, err = await asyncio.wait_for(make_card(target), timeout=120)
        except asyncio.TimeoutError:
            print(f"[{target}] 超时（120s）")
            continue
        dt = time.time() - t0
        if png:
            pl = status.get("players") or {}
            print(f"[{target}] OK {dt:.1f}s | {pl.get('online')}/{pl.get('max')} "
                  f"| {status.get('_latency_ms')}ms | {os.path.getsize(png)} bytes")
        else:
            print(f"[{target}] FAIL {dt:.1f}s | err: {err}")


asyncio.run(main())

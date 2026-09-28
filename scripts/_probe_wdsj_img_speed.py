#!/usr/bin/env python3
"""官方图下载 4s 是网络慢还是代码慢？—— 对同一 URL 做多次下载对照

对照项：
  A. 共享连接池（线上实际路径）连续下 3 次
  B. 每次新建 client 下 1 次（无 keepalive）
只读，不发消息。
"""
import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, "/root/bot")

_env = Path("/root/bot/config/.env")
if _env.exists():
    for _line in _env.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip())

import httpx  # noqa: E402
from services import wdsj_api as api  # noqa: E402


def ms(t0):
    return "%.0fms" % ((time.perf_counter() - t0) * 1000)


async def main():
    player = sys.argv[1] if len(sys.argv) > 1 else "trusler"
    tid = api.resolve_template("bw")
    d = await api.query_player_stats(player, tid, use_cache=False)
    if not d:
        print("查询失败:", getattr(api, "last_error", "?"))
        return
    snap = d.get("snapshotKey", "")
    url = api._api_url("/api/v1/images/%s" % snap)
    print("玩家=%s snapshot=%s" % (player, snap))
    print("URL=%s" % url)
    print("PROXY_BASE=%r" % (api.PROXY_BASE or "(空，直连)"))
    print()

    print("A. 共享连接池（线上路径）:")
    client = await api._get_client(timeout=15)
    for i in range(3):
        t = time.perf_counter()
        r = await client.get(url)
        n = len(r.content)
        print("   #%d %s  %d bytes  %.0f KB/s"
              % (i + 1, ms(t), n, (n / 1024) / max(0.001, time.perf_counter() - t)))

    print("B. 每次新建 client:")
    for i in range(1):
        t = time.perf_counter()
        async with httpx.AsyncClient(timeout=15) as c2:
            r = await c2.get(url)
        n = len(r.content)
        print("   #%d %s  %d bytes  %.0f KB/s"
              % (i + 1, ms(t), n, (n / 1024) / max(0.001, time.perf_counter() - t)))


asyncio.run(main())

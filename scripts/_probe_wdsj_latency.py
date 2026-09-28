#!/usr/bin/env python3
"""wdsj 查询分段计时：找出 /~wdsj 10.5s 到底耗在哪一步

对照线上日志：/~wdsj 7 天内 48 次，均 10.5s、最大 27.5s。
本脚本把单次查询拆成 [连接池建立 / 战绩 API / 官方图下载] 分别计时，并区分冷/热（缓存）。
只读，不发消息。

用法（服务器）:
  python3 scripts/_probe_wdsj_latency.py [玩家名]
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

from services import wdsj_api as api  # noqa: E402


def ms(t0: float) -> str:
    return "%.0fms" % ((time.perf_counter() - t0) * 1000)


async def main():
    player = sys.argv[1] if len(sys.argv) > 1 else "trusler"
    # ⚠️ 必须用 resolve_template 把别名("bw")换成真模板 id("bedwars-stats")，
    #    直接把别名丢给 API 会 404（踩过）
    tid = api.resolve_template("bw")
    print("玩家=%s 模板别名=bw → 真实模板=%s" % (player, tid))
    print("API host = %s" % getattr(api, "API_BASE", "?"))

    # 1) 连接池建立（首次）
    t = time.perf_counter()
    await api._get_client()
    print("1. 连接池创建(首次)      : %s" % ms(t))

    # 2) 冷查询（绕过缓存）
    t = time.perf_counter()
    d1 = await api.query_player_stats(player, tid, use_cache=False)
    print("2. 战绩API(冷,绕缓存)    : %s  -> %s%s"
          % (ms(t), "OK" if d1 else "FAIL",
             "" if d1 else ("  err=%s" % getattr(api, "last_error", "?"))))

    # 3) 热查询（命中 300s TTL 缓存）
    t = time.perf_counter()
    d2 = await api.query_player_stats(player, tid, use_cache=True)
    print("3. 战绩API(热,走缓存)    : %s  -> %s" % (ms(t), "OK" if d2 else "FAIL"))

    # 4) 再冷一次（看是否为首次建连成本）
    t = time.perf_counter()
    d3 = await api.query_player_stats(player, tid, use_cache=False)
    print("4. 战绩API(第二次冷)     : %s  -> %s" % (ms(t), "OK" if d3 else "FAIL"))

    # 5) 官网图下载
    snap = (d3 or {}).get("snapshotKey", "") or (d1 or {}).get("snapshotKey", "")
    if snap:
        out = "/tmp/_probe_wdsj_%s.png" % snap
        t = time.perf_counter()
        ok = await api.download_stats_image("/api/v1/images/%s" % snap, out)
        sz = os.path.getsize(out) if os.path.exists(out) else 0
        print("5. 官方图下载            : %s  -> %s (%d bytes)" % (ms(t), "OK" if ok else "FAIL", sz))
    else:
        print("5. 官方图下载            : 跳过（无 snapshotKey）")

    print("\n注意：线上 10.5s 是**整条消息**（提示语 + 两次请求 + 发送），")
    print("      若上面各项之和远小于 10.5s，瓶颈就不在 wdsj 本身。")


asyncio.run(main())

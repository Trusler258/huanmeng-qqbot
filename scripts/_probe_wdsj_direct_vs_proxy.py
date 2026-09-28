#!/usr/bin/env python3
"""wdsj 直连 vs 代理：同一批请求两条路各计时，量化「优先直连」的收益

背景：WDSJ_PROXY 配在 systemd Environment= 里（不在 .env），bot 进程一直看得到，
而旧 _api_url 的逻辑是「配了代理就永远走代理」→ 线上每次请求都绕 CF Worker。
本脚本手工构造两条 URL 对照计时（不依赖 bot 进程的环境）。
只读，不发消息。

用法（服务器）:
  python3 scripts/_probe_wdsj_direct_vs_proxy.py [玩家名]
"""
import asyncio
import os
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, "/root/bot")

# bot 运行时的真实代理地址（来自 systemctl show bot.service -p Environment）
PROXY = os.environ.get("WDSJ_PROXY", "https://wdsj1.truslerweb.dpdns.org/proxy").rstrip("/")
BASE = "https://www.wdsj.net/nexus"

import httpx  # noqa: E402


def ms(t0):
    return "%.0fms" % ((time.perf_counter() - t0) * 1000)


def kbs(n, dt):
    return "%.0f KB/s" % ((n / 1024) / max(0.001, dt))


async def timed(client, url, tag, n=1):
    out = []
    for i in range(n):
        t = time.perf_counter()
        try:
            r = await client.get(url, headers={"Referer": BASE + "/stats"}, timeout=30)
            dt = time.perf_counter() - t
            out.append((r.status_code, len(r.content), dt))
            print("   %-8s #%d  %s  HTTP %s  %d bytes  %s"
                  % (tag, i + 1, ms(t), r.status_code, len(r.content), kbs(len(r.content), dt)))
        except Exception as e:
            dt = time.perf_counter() - t
            out.append((0, 0, dt))
            print("   %-8s #%d  %s  失败: %s: %s" % (tag, i + 1, ms(t), type(e).__name__, e))
    return out


async def main():
    player = sys.argv[1] if len(sys.argv) > 1 else "trusler"
    from services import wdsj_api as _api
    # 用模块自己的身份编码（手搓会 400：name/nick 编码规则不同）
    enc = _api.build_identity(player, "name")
    stats_path = "/api/v1/players/%s/templates/bedwars-stats" % enc
    direct_stats = BASE + stats_path
    proxy_stats = "%s?url=%s" % (PROXY, urllib.parse.quote(direct_stats, safe=""))

    print("直连 = %s" % BASE)
    print("代理 = %s" % PROXY)
    print()

    async with httpx.AsyncClient(follow_redirects=True) as client:
        print("【1】玩家战绩 API")
        d = await timed(client, direct_stats, "直连", 3)
        p = await timed(client, proxy_stats, "代理", 3)

        # 取 snapshotKey 拼接图片 URL（直连拿）
        snap = ""
        try:
            j = (await client.get(direct_stats, timeout=20)).json()
            snap = ((j.get("data") or {}).get("snapshotKey") or "")
        except Exception as e:
            print("   取 snapshotKey 失败:", e)
        if snap:
            img_direct = "%s/api/v1/images/%s" % (BASE, snap)
            img_proxy = "%s?url=%s" % (PROXY, urllib.parse.quote(img_direct, safe=""))
            print("\n【2】官方图（%s）" % snap)
            di = await timed(client, img_direct, "直连", 3)
            pi = await timed(client, img_proxy, "代理", 3)

            def avg(xs):
                ok = [x[2] for x in xs if x[0] == 200]
                return (sum(ok) / len(ok)) if ok else 0
            print("\n均值: 图直连 %.0fms / 图代理 %.0fms" % (avg(di) * 1000, avg(pi) * 1000))
        else:
            print("\n(无 snapshotKey，跳过图片对照)")

        def avgs(xs):
            ok = [x[2] for x in xs if x[0] == 200]
            return "%.0fms" % (sum(ok) / len(ok) * 1000) if ok else "n/a"
        print("均值: API直连 %s / API代理 %s" % (avgs(d), avgs(p)))
        print("\n判读：直连明显更快 → 应该直连优先、代理只做兜底。")


asyncio.run(main())

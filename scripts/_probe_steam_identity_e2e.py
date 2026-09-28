#!/usr/bin/env python3
"""端到端验证：各种账号标识 → SteamID64 → 真实资料（生产条件，走真实 STEAM_KEY 与 Worker）

用法（服务器）: python3 scripts/_probe_steam_identity_e2e.py
"""
import asyncio
import sys

sys.path.insert(0, "/root/bot")

INPUTS = [
    "hkkhkghw",              # 字母好友代码
    "hkk-hkghw",             # 带横线
    "HKKHKGHW",              # 大写
    "76561199427581023",     # SteamID64
    "1467315295",            # account_id 数字
    "STEAM_1:1:733657647",   # SteamID2
    "[U:1:1467315295]",      # SteamID3
    "steamcommunity.com/profiles/76561199427581023",   # 资料链接
]


async def main():
    from services import steam_api as S
    print("输入 → 解析结果 → 真实资料")
    for inp in INPUTS:
        try:
            sid, err = await S.resolve_steamid(inp)
        except Exception as e:
            print("  %-46s → 解析异常: %s:%s" % (inp, type(e).__name__, e))
            continue
        if not sid:
            print("  %-46s → 解析失败: %s" % (inp, err))
            continue
        try:
            p = await S.player_summary(sid)
        except Exception as e:
            p = {}
        acc = S.steamid64_to_account_id(sid)
        print("  %-46s → %s  好友代码=%-10s 昵称=%s"
              % (inp, sid, S.account_id_to_friend_code(acc), p.get("name") or "(查不到)"))


asyncio.run(main())

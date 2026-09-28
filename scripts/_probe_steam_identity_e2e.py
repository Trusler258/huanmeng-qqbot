#!/usr/bin/env python3
"""端到端验证：各种账号标识 → **真实走 _do_who 分派** → 真实资料

⚠️ 为什么必须调 _do_who，而不是 resolve_steamid（教训）：
   v2.3.68 我写这脚本时直接 `await S.resolve_steamid(inp)`，结果"8 种输入全部通过"
   是**假阳性** —— 它绕过了 `_do_who` 开头的 `target = _qq_of(raw, group_id)`。
   真实命令路径上 `_parse_opponent` 会把任何 4~12 位数字抠出来当 QQ 号，
   于是 account_id / SteamID64 全被吃掉，用户只会看到「还没绑定 Steam 喵~」。

   所以本脚本只打桩 `_send_card`（不发真图），其余全走生产逻辑
   （STEAM_KEY / Worker 代理 / Steam API 都是真的）。

用法（本地或服务器）: python3 scripts/_probe_steam_identity_e2e.py
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

INPUTS = [
    "hkkhkghw",              # 字母好友代码
    "hkk-hkghw",             # 带横线
    "HKKHKGHW",              # 大写
    "76561199427581023",     # SteamID64
    "1467315295",            # account_id 数字（本次修的就是它）
    "STEAM_1:1:733657647",   # SteamID2
    "[U:1:1467315295]",      # SteamID3
    "steamcommunity.com/profiles/76561199427581023",   # 资料链接
]

sent = []


async def main():
    import modules.steam as M
    from services import steam_api as S

    async def fake_send_card(steamid, user_id, group_id, is_group):
        sent.append(steamid)
        return True

    async def fake_delayed(*a, **kw):
        return None

    # 只拦"发图"这一步；分派逻辑 / 解析 / API 全部真实
    M._send_card = fake_send_card
    M._delayed_notify = fake_delayed

    print("输入 → _do_who 分派出的 steamid → 真实资料")
    n_ok = 0
    for inp in INPUTS:
        sent.clear()
        try:
            reply = await M._do_who([inp], 10001, 20002, True, "probe")
        except Exception as e:
            print("  %-46s → 异常 %s: %s" % (inp, type(e).__name__, e))
            continue
        if not sent:
            print("  %-46s → ✗ 未出卡: %s"
                  % (inp, (reply or "").replace("\n", " ")[:66]))
            continue
        sid = sent[0]
        try:
            p = await S.player_summary(sid)
        except Exception:
            p = {}
        acc = S.steamid64_to_account_id(sid)
        code = S.account_id_to_friend_code(acc)
        print("  %-46s → %s  好友代码=%-10s 昵称=%s"
              % (inp, sid, code, p.get("name") or "(查不到)"))
        n_ok += 1

    print("\n%d/%d 走通真实分派" % (n_ok, len(INPUTS)))


asyncio.run(main())

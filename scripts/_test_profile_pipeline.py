# -*- coding: utf-8 -*-
"""验证 pipeline 的画像写入路径（_async_extract_profile）。

与 _test_profile_rules.py 的区别：那个测的是核心规则，
这个测的是**真实调用路径** —— 包括「提取为空也要计一次发言」这条改动。
"""
import asyncio
import json
import sys

sys.path.insert(0, '/root/bot')

from core.pipeline import _async_extract_profile  # noqa: E402

UID = 999999998
FILE = '/root/bot/data/user_profiles.json'
orig = open(FILE, encoding='utf-8').read()

MSG_REAL = "我叫小明。我今天作业太多了"      # 有真信息
MSG_Q = "我到底是谁啊这个"                   # 纯疑问
MSG_SHORT = "嗯嗯"                          # 太短，提取必然为空


async def main():
    for m in (MSG_REAL, MSG_Q, MSG_SHORT):
        await _async_extract_profile(UID, "测试用户", m)


try:
    asyncio.get_event_loop().run_until_complete(main())
    d = json.loads(open(FILE, encoding='utf-8').read())
    p = d.get(str(UID))
    if not p:
        print("[FAIL] 画像未写入")
    else:
        print("昵称        : %r" % (p.get("name") or ""))
        print("标签        : %s" % (p.get("tags") or []))
        print("事实        : %s" % (p.get("facts") or []))
        print("发言计数    : %s" % p.get("message_count"))
        print()
        ok = True
        if p.get("name") != "小明":
            print("[FAIL] 昵称应为「小明」"); ok = False
        else:
            print("[OK]   昵称 = 小明（疑问句没被当成名字）")
        if "学生" not in (p.get("tags") or []):
            print("[FAIL] 标签应含「学生」"); ok = False
        else:
            print("[OK]   标签含学生")
        if p.get("facts"):
            print("[FAIL] facts 应为空: %s" % p["facts"]); ok = False
        else:
            print("[OK]   facts 为空（quick 路径不再产出）")
        if (p.get("message_count") or 0) != 3:
            print("[FAIL] 发言计数应为 3（含短消息）"); ok = False
        else:
            print("[OK]   发言计数 = 3（提取为空的短消息也计了）")
        print("\n结论: %s" % ("全部通过" if ok else "存在失败项"))
finally:
    open(FILE, 'w', encoding='utf-8').write(orig)
    print("已还原原始数据")

# -*- coding: utf-8 -*-
"""试跑：用真实聊天记录测画像生成质量（写临时文件，不碰真实数据）。"""
import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, '/root/bot')

import core.user_profile as up  # noqa: E402

DATE = sys.argv[1] if len(sys.argv) > 1 else "2026-09-16"
N = int(sys.argv[2]) if len(sys.argv) > 2 else 3

agg = up.collect_day(DATE)
rows = sorted(agg.items(), key=lambda kv: -len(kv[1]["msgs"]))
# 挑一个有玩梗记录的用户（之前清洗时见过的）+ 两个正常的
picks = rows[:N]

tmp = Path(tempfile.mktemp(suffix=".json"))
up._DATA_FILE = tmp
tmp.write_text("{}", encoding="utf-8")


async def main():
    for key, v in picks:
        print("=" * 70)
        print("KEY %s  (%d 条, nick=%r)" % (key, len(v["msgs"]), v["nick"]))
        print("--- 送进去的消息（前 6 条 / 后 3 条）---")
        for m in v["msgs"][:6]:
            print("   ", m[:90])
        if len(v["msgs"]) > 9:
            print("    ...")
        for m in v["msgs"][-3:]:
            print("   ", m[:90])
        ok = await up.update_one(key, v["msgs"], DATE, v["nick"])
        p = up._load_all().get(key, {})
        print("--- 生成的画像 (ok=%s) ---" % ok)
        for f in up._FIELDS:
            val = p.get(f) or ""
            print("   %-11s %s" % (f, val if val else "(空)"))
        print("--- 注入效果 ---")
        print(up.build_profile_text(
            up.parse_scope(key)["group"], up.parse_scope(key)["qq"],
            up.parse_scope(key)["scope"] == "group", allow_cross_scope=False))


asyncio.get_event_loop().run_until_complete(main())

print("\n临时文件已生成:", tmp)
print(tmp.read_text(encoding="utf-8")[:200])

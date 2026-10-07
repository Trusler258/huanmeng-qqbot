# -*- coding: utf-8 -*-
"""AnySearch 集成验证（用完即删）"""
import asyncio
import os
import sys

sys.path.insert(0, "/root/bot")
os.chdir("/root/bot")

# 强制读取 .env（测试进程没有 systemd Environment）
for line in open("/root/bot/config/.env", encoding="utf-8"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

from modules.web_search import search_anysearch, agent_search  # noqa: E402


async def main():
    # 1) AnySearch 源单测
    r = search_anysearch("ADOFAI 节奏游戏", 3)
    print("[AnySearch source]:", len(r), "条")
    for e in r[:2]:
        print("  -", str(e.get("title", ""))[:50])

    # 2) Agent 搜索全链路（AnySearch 优先）
    text = agent_search("2026 大数据竞赛 赛项", 4)
    print("[agent_search]:", len(text or ""), "字符")
    print((text or "")[:200].replace("\n", " | "))


asyncio.run(main())

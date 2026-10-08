# -*- coding: utf-8 -*-
"""诊断 whois(.cn) + AnySearch（用完即删）"""
import os
import sys

sys.path.insert(0, "/root/bot")
os.chdir("/root/bot")
for line in open("/root/bot/config/.env", encoding="utf-8"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

import asyncio  # noqa: E402
import subprocess  # noqa: E402

from modules.web_search import search_anysearch  # noqa: E402

# 1) 系统 whois（.cn）
try:
    out = subprocess.run(["whois", "updream.cn"], capture_output=True, text=True, timeout=30)
    txt = (out.stdout or "")[:400]
    print("[system whois]:", "有输出" if txt.strip() else "空", "|", txt.replace("\n", " | ")[:250])
except Exception as e:
    print("[system whois] FAIL:", type(e).__name__, str(e)[:100])

# 2) bot 的 whois 工具
try:
    from modules.commands import cmd_whois
    r = asyncio.run(cmd_whois(["updream.cn"], 3483585417, 0, "test", False, 0))
    print("[bot whois]:", (r or "")[:250].replace("\n", " | "))
except Exception as e:
    print("[bot whois] ERR:", type(e).__name__, str(e)[:120])

# 3) AnySearch
for q in ("updream 我的世界", "updream.cn"):
    r = search_anysearch(q, 4)
    print(f"[anysearch '{q}']:", len(r), "条")
    for e in r[:2]:
        print("  -", str(e.get("title", ""))[:45], "|", str(e.get("url", ""))[:45])

# -*- coding: utf-8 -*-
"""服务器端 AnySearch 直连诊断（用完即删）"""
import os
import sys

sys.path.insert(0, "/root/bot")
os.chdir("/root/bot")
for line in open("/root/bot/config/.env", encoding="utf-8"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

import requests  # noqa: E402

print("ANYSEARCH_KEY set:", bool(os.getenv("ANYSEARCH_KEY")))
try:
    r = requests.post(
        "https://api.anysearch.com/v1/search",
        headers={"Authorization": "Bearer " + os.environ["ANYSEARCH_KEY"],
                 "Content-Type": "application/json"},
        json={"query": "test", "count": 2}, timeout=15)
    print("py http:", r.status_code, r.text[:200])
except Exception as e:
    print("py ERR:", type(e).__name__, str(e)[:300])

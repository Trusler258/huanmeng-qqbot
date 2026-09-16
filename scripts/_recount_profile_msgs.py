# -*- coding: utf-8 -*-
"""重算画像的 msg_count（累计已分析条数）。

v2.3.34 回填时 msg_count 是旧口径（由实时计数维护，回填期间 bot 没跑 → 全是 0）。
现在口径改为「每日任务累加的已分析条数」，用本脚本按 state 里记录过的日期重算，
不调 LLM，只扫一遍 msglog。
"""
import glob
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, '/root/bot')

import core.user_profile as up  # noqa: E402

st = up._load_state()
dates = st.get("done_dates") or []
if not dates:
    print("state 里没有已处理日期，无法重算")
    sys.exit(1)
dates = sorted(dates)[-30:]
print("按以下日期重算: %s" % ", ".join(dates))

cnt = defaultdict(int)
for d in dates:
    for key, v in up.collect_day(d).items():
        cnt[key] += len(v["msgs"])

data = up._load_all()
changed = 0
for key, n in cnt.items():
    p = data.get(key)
    if p is None:
        continue
    if p.get("msg_count") != n:
        p["msg_count"] = n
        changed += 1
up._save_all(data)

print("重算完成：涉及 %d 份画像，更新 %d 份" % (len(cnt), changed))
tot = sum((v.get("msg_count") or 0) for v in data.values())
print("全库已分析条数合计: %d" % tot)
top = sorted(data.items(), key=lambda kv: -(kv[1].get("msg_count") or 0))[:5]
for k, v in top:
    print("  %-28s %d 条  天数=%s" % (k, v.get("msg_count") or 0, v.get("days")))

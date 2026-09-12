# -*- coding: utf-8 -*-
"""缓存存活分析 —— 验证「会话间隔太久导致缓存失效」假设

用法: python3 tests/_test_cache_gap.py
输出: 按「与前一次调用的间隔」分组，统计命中率，验证缓存 TTL 影响
"""
import sys, os
from datetime import datetime

for p in (r"G:\py\qqbot", "/root/bot", os.getcwd()):
    if p not in sys.path and os.path.isdir(p):
        sys.path.insert(0, p)

from core.token_tracker import _load_range

recs = _load_range("2026-09-06", "2026-09-12")
recs = [r for r in recs if r.get("prompt_tokens")]
recs.sort(key=lambda r: r["time"])
print(f"总记录: {len(recs)} 条\n")

BUCKETS = [
    ("<1分钟", 0, 60),
    ("1-5分钟", 60, 300),
    ("5-30分钟", 300, 1800),
    ("30分-2小时", 1800, 7200),
    (">2小时", 7200, 10 ** 9),
]
stats = {name: [0, 0, 0, 0] for name, _, _ in BUCKETS}  # 次数, prompt合计, cached合计, prompt>2000的次数

prev_t = None
for r in recs:
    try:
        t = datetime.fromisoformat(r["time"])
    except Exception:
        continue
    if prev_t is not None:
        gap = (t - prev_t).total_seconds()
        for name, lo, hi in BUCKETS:
            if lo <= gap < hi:
                s = stats[name]
                s[0] += 1
                s[1] += r["prompt_tokens"]
                s[2] += r.get("cached_tokens", 0)
                if r["prompt_tokens"] > 2000:
                    s[3] += 1
                break
    prev_t = t

print(f"{'间隔分组':<12} {'调用数':>6} {'平均prompt':>10} {'平均命中率':>10} {'大prompt次数':>12}")
print("-" * 60)
for name, _, _ in BUCKETS:
    cnt, pr, ca, big = stats[name]
    if cnt == 0:
        print(f"{name:<12} {0:>6}")
        continue
    rate = (ca / pr * 100) if pr else 0
    print(f"{name:<12} {cnt:>6} {pr // cnt:>10} {rate:>9.1f}% {big:>12}")

print("\n【结论判读】")
for name, _, _ in BUCKETS:
    cnt, pr, ca, _ = stats[name]
    if cnt:
        print(f"  {name}: 命中率 {(ca / pr * 100) if pr else 0:.1f}% ({cnt} 次)")

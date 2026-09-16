"""token 消耗与缓存命中率深度分析

用户反馈：9-13 一天 689 万 token，命中率太低。
注意：记录字段是 time（不是 ts）。
"""
import json
from collections import defaultdict
from pathlib import Path

F = Path('/root/bot/data/token_2026-09.jsonl')

rows = []
bad = 0
for line in F.read_text(encoding='utf-8', errors='replace').splitlines():
    try:
        o = json.loads(line)
    except Exception:
        bad += 1
        continue
    t = str(o.get("time") or "")
    if not t:
        continue
    rows.append({
        "t": t,
        "day": t[:10],
        "h": t[11:13],
        "model": o.get("model") or "?",
        "scene": o.get("scene") or "(空)",
        "p": int(o.get("prompt_tokens") or 0),
        "c": int(o.get("completion_tokens") or 0),
        "hit": int(o.get("cached_tokens") or 0),
        "cw": int(o.get("cache_write_tokens") or 0),
    })

print(f"共载入 {len(rows)} 条（解析失败 {bad}）\n")

# ── 1. 逐日（核对用户给的 9-13 数字）──
print("=" * 74)
print(f"{'日期':11s} {'调用':>5s} {'输入':>11s} {'命中':>11s} {'未命中':>10s} {'输出':>8s} {'命中率':>7s}")
print("=" * 74)
byday = defaultdict(lambda: {"n": 0, "p": 0, "hit": 0, "c": 0})
for r in rows:
    d = byday[r["day"]]
    d["n"] += 1; d["p"] += r["p"]; d["hit"] += r["hit"]; d["c"] += r["c"]
for day in sorted(byday):
    d = byday[day]
    miss = d["p"] - d["hit"]
    rate = d["hit"] / d["p"] * 100 if d["p"] else 0
    mark = "  <<< 用户反馈" if day == "2026-09-13" else ""
    print(f"{day:11s} {d['n']:5d} {d['p']:11,} {d['hit']:11,} {miss:10,} {d['c']:8,} {rate:6.1f}%{mark}")

# ── 2. 9-13 命中率分布：多少调用是"几乎没命中" ──
print("\n" + "=" * 74)
print("9-13 单次调用的命中率分布")
print("=" * 74)
d13 = [r for r in rows if r["day"] == "2026-09-13"]
buckets = [(0, 1), (1, 10), (10, 30), (30, 60), (60, 90), (90, 100.1)]
print(f"{'命中率区间':>12s} {'调用数':>7s} {'占比':>7s} {'该组输入token':>14s} {'该组未命中':>12s}")
for lo, hi in buckets:
    grp = [r for r in d13 if r["p"] and lo <= r["hit"] / r["p"] * 100 < hi]
    if not grp:
        continue
    tp = sum(r["p"] for r in grp)
    tm = sum(r["p"] - r["hit"] for r in grp)
    print(f"{lo:>5d}-{hi:>5.0f}% {len(grp):7d} {len(grp)/len(d13)*100:6.1f}% {tp:14,} {tm:12,}")

# 完全没命中的
zero = [r for r in d13 if r["p"] >= 1000 and r["hit"] == 0]
print(f"\n输入≥1000 且命中为 0 的调用：{len(zero)} 次，"
      f"共浪费 {sum(r['p'] for r in zero):,} token")
for r in sorted(zero, key=lambda x: -x["p"])[:6]:
    print(f"   {r['t'][11:19]} {r['scene'][:14]:14s} 输入{r['p']:8,} 输出{r['c']:6,}")

# ── 3. 按 scene 汇总（9-13）──
print("\n" + "=" * 74)
print("9-13 按场景")
print("=" * 74)
bysc = defaultdict(lambda: [0, 0, 0, 0])
for r in d13:
    s = bysc[r["scene"]]
    s[0] += 1; s[1] += r["p"]; s[2] += r["hit"]; s[3] += r["c"]
print(f"{'场景':16s} {'调用':>5s} {'输入':>11s} {'命中率':>7s} {'未命中':>10s}")
for sc, (n, p, hit, c) in sorted(bysc.items(), key=lambda x: -x[1][1]):
    rate = hit / p * 100 if p else 0
    print(f"{sc:16s} {n:5d} {p:11,} {rate:6.1f}% {p-hit:10,}")

# ── 4. 按小时（9-13）──
print("\n" + "=" * 74)
print("9-13 按小时（找爆发时段 + 命中率变化）")
print("=" * 74)
byh = defaultdict(lambda: [0, 0, 0])
for r in d13:
    s = byh[r["h"]]
    s[0] += 1; s[1] += r["p"]; s[2] += r["hit"]
for h in sorted(byh):
    n, p, hit = byh[h]
    rate = hit / p * 100 if p else 0
    bar = "#" * min(50, p // 40000)
    print(f"  {h}时 {n:4d}次 {p:10,} 命中{rate:5.1f}% {bar}")

# ── 5. 单次输入 token 规模 ──
print("\n" + "=" * 74)
print("9-13 单次输入 token 分位（看上下文有多大）")
print("=" * 74)
ps = sorted(r["p"] for r in d13)
for p in (0.5, 0.75, 0.9, 0.95, 0.99, 1.0):
    i = min(len(ps) - 1, int(len(ps) * p))
    print(f"  P{int(p*100):3d}: {ps[i]:9,}")
print(f"  均值: {sum(ps)//len(ps):9,}")

"""回填历史 msglog 的用户画像。

按日期**从早到晚**逐天跑 run_daily，让画像逐日增量累积 ——
这正是增量更新的意义：后面几天的画像建立在前面几天的基础上。

用法：
    python3 scripts/backfill_profiles.py             # 默认近 7 天
    python3 scripts/backfill_profiles.py --days 14
    python3 scripts/backfill_profiles.py --dry-run    # 只看候选量，不调 LLM

注意：会调用 LLM。实测每天约 25 人达标（发言 >=5 条），近 7 天约 160 次调用。
"""
import argparse
import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.user_profile import collect_day, run_daily, _load_all  # noqa: E402


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7, help="回填最近多少天（默认 7）")
    ap.add_argument("--dry-run", action="store_true", help="只统计候选，不调用 LLM")
    ap.add_argument("--min-msgs", type=int, default=None, help="当天最少发言条数")
    args = ap.parse_args()

    today = datetime.now().date()
    dates = [(today - timedelta(days=i)).strftime("%Y-%m-%d")
             for i in range(args.days, 0, -1)]      # 从早到晚

    print("回填区间: %s → %s（%d 天）" % (dates[0], dates[-1], len(dates)))

    total_updated = 0
    for d in dates:
        agg = collect_day(d)
        if not agg:
            print("  %s  无消息" % d)
            continue
        r = await run_daily(d, force=True, min_msgs=args.min_msgs, dry_run=args.dry_run)
        if args.dry_run:
            print("  %s  活跃 %d 组合，候选 %d 个" % (d, len(agg), r.get("candidates", 0)))
        else:
            print("  %s  活跃 %d 组合，更新 %d/%d" % (d, len(agg), r.get("updated", 0), r.get("total", 0)))
            total_updated += r.get("updated", 0)
        sys.stdout.flush()

    if args.dry_run:
        print("\n（dry-run，未调用 LLM）")
        return

    data = _load_all()
    print("\n回填完成：本次更新 %d 次，当前画像库 %d 份" % (total_updated, len(data)))
    scopes = {}
    for k in data:
        s = "group" if k.startswith("g") else "private"
        scopes[s] = scopes.get(s, 0) + 1
    print("分布: 群 %d 份 / 私聊 %d 份" % (scopes.get("group", 0), scopes.get("private", 0)))


if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())

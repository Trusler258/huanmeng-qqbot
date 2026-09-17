# -*- coding: utf-8 -*-
"""msglog 重复记录检测（v2.3.36）。

背景：`_send_and_record` 里 `_log_bot_sent` 与 `record_incoming_message` 都写同一个
文件、同样的 entry 结构，群聊白名单里每条 bot 消息落盘两次（已修）。
另有一条 fallback 路径也是双重写入（`send_by_chat_type` 内部已写 + 显式再写）。

判定规则（key 的选择很关键，否则会误判）：
  - msg_id > 0：msg_id 唯一标识一条消息 → 用 msg_id 做 key，重复即为重复记录
  - msg_id = 0：发送失败/无 id 的兜底录制，无法区分「重复写」与
    「不同时间发了相同内容」→ 用 (内容, 秒级时间) 做 key，只有**同秒同内容**才算重复

跑法：python3 scripts/_check_msglog_dup.py            # 全部群
      python3 scripts/_check_msglog_dup.py 247478659  # 指定群
"""
import json
import sys
from collections import Counter
from pathlib import Path

MSGLOG = Path("/root/bot/data/msglog")


def key_of(d: dict) -> tuple:
    mid = d.get("msg_id") or 0
    try:
        mid = int(mid)
    except (TypeError, ValueError):
        mid = 0
    content = str(d.get("content"))[:100]
    if mid > 0:
        return ("id", mid)
    return ("nc", content, int(d.get("time") or 0))     # 无 id：内容 + 秒级时间


def scan(path: Path) -> dict:
    bot, user = Counter(), Counter()
    for line in path.open(encoding="utf-8", errors="ignore"):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        k = key_of(d)
        (bot if d.get("type") == "bot" else user)[k] += 1
    return {"bot": bot, "user": user}


def report(c: Counter, label: str):
    total, uniq = sum(c.values()), len(c)
    dup_keys = [k for k, v in c.items() if v > 1]
    dup_rows = sum(v - 1 for v in c.values() if v > 1)
    print("    %-4s 记录 %4d / 去重 %4d | 重复键 %3d | 多余 %3d  %s"
          % (label, total, uniq, len(dup_keys), dup_rows,
             "OK" if not dup_keys else "!!!"))
    return dup_keys, total - uniq


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    files = sorted(MSGLOG.glob("msglog_*.jsonl"))
    if only:
        files = [f for f in files if f.name == "msglog_%s.jsonl" % only]
    if not files:
        print("找不到 msglog 文件")
        return 1

    print("扫描 %d 个文件（key: msg_id>0 用 id；=0 用 内容+秒级时间）\n" % len(files))
    total_bad = 0
    worst = []
    for f in files:
        r = scan(f)
        if not r["bot"] and not r["user"]:
            continue
        print("  %s" % f.name)
        bk, bb = report(r["bot"], "bot")
        uk, ub = report(r["user"], "用户")
        total_bad += bb + ub
        if bb + ub:
            worst.append((bb + ub, f.name, list(r["bot"].items())[:2]))
        print()

    print("=" * 68)
    print("合计多余记录: %d 条" % total_bad)
    if worst:
        worst.sort(reverse=True)
        print("\n明细（最多 6 个群）:")
        for bad, name, samples in worst[:6]:
            print("  %-30s 多余 %d 条" % (name, bad))
            shown = 0
            for k, v in samples:
                if v > 1:
                    print("     x%d  %r" % (v, str(k)[:60]))
                    shown += 1
                    if shown >= 2:
                        break
    else:
        print("未发现重复记录 —— 修复生效")
    return 0


sys.exit(main())

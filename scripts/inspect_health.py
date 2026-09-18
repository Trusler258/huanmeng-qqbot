#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""无人值守巡检：扫 bot 的发言与事件，把可疑的地方写进一份 md 报告。

设计目标（用户要求"无人值守"）：不需要人盯着，跑一次就把「最近一段时间里
机器人哪里不对劲」汇总成一份能直接读的报告。检查项宁可多扫，报出来由人定夺。

检查项：
  1. 服务状态      —— 各 systemd 服务是否 active、近 24h 重启了几次
  2. 日志错误      —— journalctl 里 Traceback / ERROR 的数量与样例
  3. bot 发言扫描  —— msglog 里 bot 自己说的话：报错话术 / 超长回复 / 重复刷屏
  4. LLM 调用      —— 近 24h 次数、缓存命中率、空回复（completion=0）
  5. 画像任务      —— 最近一次画像更新是多久以前

用法：
    python3 scripts/inspect_health.py [天数]      # 默认 1 天
输出：
    data/inspection/inspect_YYYYMMDD_HHMM.md
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path("/root/bot") if Path("/root/bot").exists() else Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATA = ROOT / "data"
OUT_DIR = DATA / "inspection"

FINDINGS = []          # (级别, 检查项, 描述)   级别: 问题 / 注意 / 正常


def add(level, item, desc):
    FINDINGS.append((level, item, desc))


def fmt_ts(ts):
    return datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")


# ── 1. 服务状态 ────────────────────────────────────────────────

def check_services():
    services = ["bot.service", "panel.service", "power.service", "napcat.service"]
    rows = []
    for s in services:
        try:
            st = subprocess.run(["systemctl", "is-active", s],
                                capture_output=True, text=True, timeout=10).stdout.strip()
        except Exception:
            st = "?"
        # 近 24h 重启次数：从 journal 里数 "Started" 记录
        try:
            r = subprocess.run(["journalctl", "-u", s, "--since", "-24 hours",
                                "--no-pager", "-q"], capture_output=True, text=True, timeout=20)
            restarts = sum(1 for ln in r.stdout.splitlines() if "Started" in ln)
        except Exception:
            restarts = -1
        rows.append((s, st, restarts))
        if st != "active":
            add("问题", "服务状态", "%s 状态是 %s（应 active）" % (s, st))
        elif restarts > 8:
            # 阈值放宽到 8：部署日一天重启十几次是正常的（v2.3.40 上线当晚就重启了 20 次）。
            # 真正要警惕的是"没人动它也自己重启"，所以报告里注明这个前提。
            add("注意", "服务状态", "%s 近 24h 重启 %d 次 —— 若当天有部署属正常，否则要查原因" % (s, restarts))
    return rows


# ── 2. 日志错误 ────────────────────────────────────────────────

def check_journal(hours: int):
    try:
        r = subprocess.run(["journalctl", "-u", "bot.service", "--since",
                            "-%d hours" % hours, "--no-pager", "-q"],
                           capture_output=True, text=True, timeout=30)
        lines = r.stdout.splitlines()
    except Exception as e:
        add("注意", "日志错误", "journalctl 读取失败: %s" % e)
        return []
    tb, err = [], []
    for i, ln in enumerate(lines):
        if "Traceback" in ln:
            tb.append(ln)
        elif " ERROR " in ln or ln.count("ERROR"):
            err.append(ln)
    if len(tb) > 0:
        add("问题", "日志错误", "近 %dh 出现 %d 次 Traceback" % (hours, len(tb)))
    if len(err) > 20:
        add("注意", "日志错误", "近 %dh 出现 %d 条 ERROR（超过 20，看样例）" % (hours, len(err)))
    if not tb and len(err) <= 20:
        add("正常", "日志错误", "近 %dh 无 Traceback，ERROR %d 条" % (hours, len(err)))
    # 样例：取最后 3 条 Traceback 之后的 6 行
    samples = []
    if tb:
        idx = [i for i, ln in enumerate(lines) if "Traceback" in ln][-3:]
        for i in idx:
            samples.append("\n".join(lines[i:i + 7]))
    return samples


# ── 3. bot 发言扫描 ────────────────────────────────────────────

BOT_TROUBLE_WORDS = ("Traceback", "Error:", "Exception", "报错了", "出错了",
                     "解析失败", "调用失败", "稍后再试", "服务不可用", "请求失败")


def check_msgs(bot_qq: int, hours: int):
    cutoff = time.time() - hours * 3600
    trouble, long_msgs, dup_counter = [], [], Counter()
    total_bot = 0
    for f in DATA.glob("msglog/msglog_*.jsonl"):
        chat = f.stem.replace("msglog_", "")
        # 跳过 99* 测试会话（棋局自测用的群号段，里面的"重复发言"全是测试噪音）
        if chat.isdigit() and chat.startswith("99"):
            continue
        try:
            for ln in f.read_text(encoding="utf-8").splitlines():
                if not ln.strip():
                    continue
                try:
                    d = json.loads(ln)
                except Exception:
                    continue
                if d.get("recalled"):
                    continue
                if int(d.get("user_id") or 0) != bot_qq:
                    continue
                ts = int(d.get("time") or 0)
                if ts < cutoff:
                    continue
                c = str(d.get("content") or "")
                if not c:
                    continue
                total_bot += 1
                if any(w in c for w in BOT_TROUBLE_WORDS):
                    trouble.append((chat, ts, c[:160]))
                if len(c) > 1500:
                    long_msgs.append((chat, ts, len(c)))
                dup_counter[(chat, c)] += 1
        except Exception:
            continue

    if trouble:
        add("问题", "bot 发言", "近 %dh 有 %d 条回复像是在报错（含 Traceback/失败等话术）" % (hours, len(trouble)))
    else:
        add("正常", "bot 发言", "近 %dh 无报错话术（扫描 %d 条 bot 发言）" % (hours, total_bot))
    if long_msgs:
        add("注意", "bot 发言", "%d 条超长回复（>1500 字）" % len(long_msgs))
    dups = [(k, v) for k, v in dup_counter.items() if v >= 3]
    if dups:
        dups.sort(key=lambda x: -x[1])
        add("注意", "bot 发言", "%d 组重复发言（同群同内容 ≥3 次），可能有循环触发" % len(dups))
    dups.sort(key=lambda x: -x[1])
    return trouble[:5], long_msgs[:5], dups[:5], total_bot


# ── 4. LLM 调用 ────────────────────────────────────────────────

def check_llm(hours: int):
    cutoff = time.time() - hours * 3600
    month = datetime.now().strftime("%Y-%m")
    f = DATA / ("token_%s.jsonl" % month)
    if not f.exists():
        # 跨月时回退上个月
        prev = (datetime.now() - timedelta(days=1)).strftime("%Y-%m")
        f = DATA / ("token_%s.jsonl" % prev)
        if not f.exists():
            add("注意", "LLM 调用", "找不到 token 用量文件")
            return None
    p = c = comp0 = 0
    n = 0
    bym = defaultdict(lambda: [0, 0, 0])   # model -> [n, prompt, cached]
    try:
        for ln in f.read_text(encoding="utf-8").splitlines():
            if not ln.strip():
                continue
            try:
                d = json.loads(ln)
            except Exception:
                continue
            ts = d.get("time") or 0
            if isinstance(ts, str):
                # ★ token 文件的 time 是 ISO 格式且带 T 和微秒（实测
                #   "2026-09-18T00:05:22.453315"）—— 用 fromisoformat 才解析得了。
                #   第一版用 strptime("%Y-%m-%d %H:%M:%S") 全部失败，
                #   结果误报"近 24h 零调用"。
                try:
                    ts = datetime.fromisoformat(ts)
                    ts = ts.timestamp()
                except Exception:
                    continue
            if ts < cutoff:
                continue
            m = str(d.get("model") or "?")
            pt = int(d.get("prompt_tokens") or 0)
            ct = int(d.get("cached_tokens") or 0)
            cp = int(d.get("completion_tokens") or 0)
            n += 1
            p += pt
            c += ct
            bym[m][0] += 1
            bym[m][1] += pt
            bym[m][2] += ct
            if cp == 0 and "deepseek" in m:
                comp0 += 1
    except Exception as e:
        add("注意", "LLM 调用", "token 文件读取失败: %s" % e)
        return None
    hit = (c / p * 100) if p else 0
    if n == 0:
        add("问题", "LLM 调用", "近 %dh 一次 LLM 调用都没有 —— bot 可能没在工作" % hours)
    elif hit < 50:
        add("注意", "LLM 调用", "近 %dh 缓存命中率 %.1f%%（低于 50%%，看看是不是上下文经常变）" % (hours, hit))
    else:
        add("正常", "LLM 调用", "近 %dh %d 次调用，命中率 %.1f%%" % (hours, n, hit))
    if comp0 > 3:
        add("注意", "LLM 调用", "%d 次回复 completion=0（空回复）" % comp0)
    rows = []
    for m, (cnt, pt_, ct_) in sorted(bym.items(), key=lambda x: -x[1][0]):
        rows.append((m, cnt, pt_, ct_, (ct_ / pt_ * 100) if pt_ else 0))
    return {"n": n, "hit": hit, "comp0": comp0, "rows": rows}


# ── 5. 画像任务 ────────────────────────────────────────────────

def check_profile():
    try:
        r = subprocess.run(["journalctl", "-u", "bot.service", "--since", "-3 days",
                            "--no-pager", "-q"], capture_output=True, text=True, timeout=30)
        lines = [ln for ln in r.stdout.splitlines() if "画像" in ln]
    except Exception:
        lines = []
    if not lines:
        add("注意", "画像任务", "近 3 天没有任何画像日志 —— 任务可能没跑")
        return None
    last = lines[-1]
    if "失败" in last or "解析失败" in last:
        add("问题", "画像任务", "最近一条画像日志是失败: %s" % last[-120:])
    else:
        add("正常", "画像任务", "最近一条: %s" % last[-120:])
    return lines[-3:]


# ── 报告 ───────────────────────────────────────────────────────

def write_report(hours, svc, samples, msg, llm, prof):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    out = OUT_DIR / ("inspect_%s.md" % now.strftime("%Y%m%d_%H%M"))
    probs = [x for x in FINDINGS if x[0] == "问题"]
    warns = [x for x in FINDINGS if x[0] == "注意"]
    oks = [x for x in FINDINGS if x[0] == "正常"]

    L = []
    L.append("# 机器人巡检报告")
    L.append("")
    L.append("> 时间：%s ｜ 范围：近 %d 小时 ｜ 结论：**问题 %d / 注意 %d / 正常 %d**"
             % (now.strftime("%Y-%m-%d %H:%M"), hours, len(probs), len(warns), len(oks)))
    L.append("")
    if probs:
        L.append("## 需要处理")
        L.append("")
        for lv, item, desc in probs:
            L.append("- **[%s]** %s： %s" % (lv, item, desc))
        L.append("")
    if warns:
        L.append("## 留意")
        L.append("")
        for lv, item, desc in warns:
            L.append("- [%s] %s： %s" % (lv, item, desc))
        L.append("")

    L.append("## 明细")
    L.append("")
    L.append("### 服务状态")
    L.append("")
    L.append("| 服务 | 状态 | 近 24h 重启 |")
    L.append("|---|---|---|")
    for s, st, rn in svc:
        L.append("| %s | %s | %s |" % (s, st, rn))
    L.append("")

    L.append("### LLM 调用（近 %dh）" % hours)
    L.append("")
    if llm:
        L.append("- 调用 %d 次 ｜ 缓存命中 %.1f%% ｜ 空回复 %d 次" % (llm["n"], llm["hit"], llm["comp0"]))
        L.append("")
        L.append("| 模型 | 次数 | prompt | cached | 命中 |")
        L.append("|---|---|---|---|---|")
        for m, cnt, pt_, ct_, hr in llm["rows"]:
            L.append("| %s | %d | %d | %d | %.1f%% |" % (m, cnt, pt_, ct_, hr))
    else:
        L.append("- 无数据")
    L.append("")

    L.append("### bot 发言扫描")
    L.append("")
    trouble, long_msgs, dups, total = msg
    L.append("- bot 发言 %d 条" % total)
    if trouble:
        L.append("- 报错话术样例：")
        for chat, ts, c in trouble:
            L.append("  - `%s` %s：%s" % (chat, fmt_ts(ts), c.replace("\n", " ")))
    if long_msgs:
        L.append("- 超长回复：")
        for chat, ts, n_ in long_msgs:
            L.append("  - `%s` %s：%d 字" % (chat, fmt_ts(ts), n_))
    if dups:
        L.append("- 重复发言：")
        for (chat, c), v in dups:
            L.append("  - `%s` × %d：%s" % (chat, v, c[:80].replace("\n", " ")))
    L.append("")

    if samples:
        L.append("### Traceback 样例")
        L.append("")
        L.append("```")
        for s in samples:
            L.append(s)
            L.append("")
        L.append("```")
    if prof:
        L.append("### 画像任务最近日志")
        L.append("")
        for ln in prof:
            L.append("- %s" % ln[-140:])
        L.append("")

    L.append("---")
    L.append("")
    L.append("> 生成：`python3 scripts/inspect_health.py %d` ｜ 下次巡检建议：每月 1 号随月报一起看"
             % hours)
    out.write_text("\n".join(L), encoding="utf-8")
    return out


def main():
    hours = int(sys.argv[1]) if len(sys.argv) > 1 else 24
    print("巡检范围：近 %d 小时" % hours)
    svc = check_services()
    samples = check_journal(hours)
    from core.config import get_config
    bot_qq = int(getattr(get_config(), "bot_qq", 0) or 0)
    msg = check_msgs(bot_qq, hours)
    llm = check_llm(hours)
    prof = check_profile()
    out = write_report(hours, svc, samples, msg, llm, prof)

    probs = [x for x in FINDINGS if x[0] == "问题"]
    warns = [x for x in FINDINGS if x[0] == "注意"]
    print()
    for lv, item, desc in FINDINGS:
        mark = "!" if lv == "问题" else ("~" if lv == "注意" else " ")
        print("  [%s] %-8s %s" % (mark, item, desc[:90]))
    print()
    print("报告已写入: %s" % out)
    return 1 if probs else 0


if __name__ == "__main__":
    sys.exit(main())

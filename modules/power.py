# -*- coding: utf-8 -*-
"""服务器功耗 / 电费查询指令。

指令：
    /~power              当前功耗 + 今日电费（简洁）
    /~power 明细         各部件功耗明细（CPU 实测 + 其余估算）
    /~power <天数>       近 N 天汇总（如 /~power 7）
    /~power 曲线         最近 30 分钟采样点（简图）

仅管理员可用（属于服务器运维信息）。

数据来源有两条，优先走服务、失败再读文件：
  1. 常驻服务 `power.service`（127.0.0.1:58893）—— 有实时值和内存里的采样曲线
  2. 直接读 `data/power/power_YYYYMM.jsonl` —— 服务挂了也能看（只是没有"此刻"的实时值）
两个来源共用同一份数据文件，所以数值一致。
"""
from __future__ import annotations

import json
from typing import Optional

from core.logger import get_logger

logger = get_logger("power")

SERVICE = "http://127.0.0.1:58893"
TIMEOUT = 2.0


# ── 数据来源 ───────────────────────────────────────────────────

async def _get(path: str) -> Optional[dict]:
    """请求常驻服务；失败返回 None（调用方回退到读文件）"""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=TIMEOUT, trust_env=False) as c:
            r = await c.get(SERVICE + path)
            if r.status_code == 200:
                return r.json()
    except Exception as e:
        logger.debug("功耗服务不可达 %s: %s", path, e)
    return None


def _fallback_now() -> Optional[dict]:
    """服务不可达时的兜底：直接读数据文件（最后一条采样 + 硬件测算）"""
    try:
        import sys
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent
        if str(root / "scripts") not in sys.path:
            sys.path.insert(0, str(root / "scripts"))
        import power_meter as PM

        info = PM.detect()
        recs = list(PM.iter_records(0))
        last = recs[-1] if recs else {}
        cpu_w = float(last.get("cpu_w") or 0)
        other = info["other_dc_w"]
        eff = info["psu_efficiency"]
        return {
            "cpu_w": round(cpu_w, 2),
            "other_dc_w": other,
            "breakdown": info["breakdown"],
            "dc_w": round(cpu_w + other, 1),
            "wall_w": round((cpu_w + other) / eff, 1),
            "psu_efficiency": eff,
            "cpu_temp_c": None,
            "price": PM.DEFAULT_PRICE,
            "stale": True,
        }
    except Exception as e:
        logger.warning("功耗兜底读取失败: %s", e)
        return None


def _fallback_report(days: int = 0) -> Optional[dict]:
    try:
        import sys
        from datetime import date, datetime
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent
        if str(root / "scripts") not in sys.path:
            sys.path.insert(0, str(root / "scripts"))
        import power_meter as PM

        price = PM.DEFAULT_PRICE
        recs = list(PM.iter_records(0))
        today = date.today().strftime("%Y-%m-%d")
        month = date.today().strftime("%Y-%m")
        t = PM.agg([r for r in recs
                    if datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d") == today])
        m = PM.agg([r for r in recs
                    if datetime.fromtimestamp(r["ts"]).strftime("%Y-%m") == month])
        a = PM.agg(recs)
        return {
            "price": price,
            "today": {"kwh": round(t["kwh"], 4), "cny": round(t["kwh"] * price, 2),
                      "avg_w": round(t["avg_w"], 1)},
            "month": {"kwh": round(m["kwh"], 4), "cny": round(m["kwh"] * price, 2),
                      "avg_w": round(m["avg_w"], 1)},
            "total": {"kwh": round(a["kwh"], 4), "cny": round(a["kwh"] * price, 2),
                      "avg_w": round(a["avg_w"], 1)},
        }
    except Exception as e:
        logger.warning("功耗兜底汇总失败: %s", e)
        return None


# ── 渲染 ───────────────────────────────────────────────────────

def _fmt_main(now: dict, rep: dict) -> str:
    price = rep.get("price", 0.55)
    wall = now.get("wall_w", 0)
    t = rep.get("today", {})
    m = rep.get("month", {})
    lines = ["【服务器功耗】"]
    temp = now.get("cpu_temp_c")
    lines.append("  当前: CPU %.1fW + 其余 %.1fW = 墙面 %.1fW%s"
                 % (now.get("cpu_w", 0), now.get("other_dc_w", 0), wall,
                    ("  %.0f°C" % temp) if temp else ""))
    lines.append("  今日: %.3f 度 = %.2f 元" % (t.get("kwh", 0), t.get("cny", 0)))
    lines.append("  本月: %.3f 度 = %.2f 元" % (m.get("kwh", 0), m.get("cny", 0)))
    avg = t.get("avg_w") or wall
    if avg:
        d = avg * 24 / 1000.0
        lines.append("  按当前功率推算:")
        lines.append("    一天 %.1f 度 = %.2f 元" % (d, d * price))
        lines.append("    一月 %.1f 度 = %.2f 元" % (d * 30, d * 30 * price))
        lines.append("    一年 %.0f 度 = %.2f 元" % (d * 365, d * 365 * price))
    if now.get("stale"):
        lines.append("  （功耗服务未运行，显示的是最近一次采样的值）")
    lines.append("  电价 %.2f 元/度 · /~power 明细 ｜ /~power 7" % price)
    return "\n".join(lines)


def _fmt_detail(now: dict) -> str:
    lines = ["【功耗明细】"]
    lines.append("  CPU（RAPL 实测）: %.2f W" % now.get("cpu_w", 0))
    lines.append("  其余部件（估算）: %.1f W" % now.get("other_dc_w", 0))
    for k, v in (now.get("breakdown") or {}).items():
        lines.append("      %-12s %.1f W" % (k, v))
    lines.append("  直流合计: %.1f W" % now.get("dc_w", 0))
    lines.append("  墙面市电: %.1f W  (按电源效率 %.0f%% 折算)"
                 % (now.get("wall_w", 0), (now.get("psu_efficiency") or 0.78) * 100))
    temp = now.get("cpu_temp_c")
    if temp:
        lines.append("  CPU 温度: %.0f°C" % temp)
    lines.append("")
    lines.append("  注: 只有 CPU 是实测（Intel RAPL），磁盘/主板/内存/风扇/网卡")
    lines.append("      按本机实际硬件清单估算，电源转换损耗按固定系数假设。")
    return "\n".join(lines)


def _fmt_days(rep: dict, days: int) -> str:
    ld = rep.get("last_days") or {}
    if not ld:
        return "没有近 %d 天的数据喵～" % days
    price = rep.get("price", 0.55)
    lines = ["【近 %d 天功耗】" % days]
    lines.append("  用电 %.3f 度 = %.2f 元  (平均 %.1f W)"
                 % (ld.get("kwh", 0), ld.get("cny", 0), ld.get("avg_w", 0)))
    lines.append("  累计: %.3f 度 = %.2f 元" % (rep.get("total", {}).get("kwh", 0),
                                              rep.get("total", {}).get("cny", 0)))
    lines.append("  电价 %.2f 元/度" % price)
    return "\n".join(lines)


def _fmt_curve(hist: dict) -> str:
    pts = hist.get("points") or []
    if len(pts) < 2:
        return "采样点还不够画曲线喵～（服务刚启动？）"
    ws = [p.get("wall_w", 0) for p in pts]
    lo, hi = min(ws), max(ws)
    span = max(0.1, hi - lo)
    blocks = "▁▂▃▄▅▆▇█"
    # 取最多 40 个点压成一行
    step = max(1, len(ws) // 40)
    seq = ws[::step][-40:]
    line = "".join(blocks[min(7, int((w - lo) / span * 7))] for w in seq)
    lines = ["【最近 %d 分钟功耗曲线】" % hist.get("minutes", 0)]
    lines.append("  " + line)
    lines.append("  最低 %.1fW / 最高 %.1fW / 当前 %.1fW"
                 % (lo, hi, ws[-1]))
    return "\n".join(lines)


# ── 入口 ───────────────────────────────────────────────────────

async def cmd_power(args, user_id, group_id, sender_name, is_group, bot_qq) -> str:
    """/~power —— 服务器功耗与电费（仅管理员）"""
    from core.config import get_config
    cfg = get_config()
    if not cfg.is_admin(user_id, group_id):
        return "只有管理员能看服务器功耗喵～"

    sub = (args[0].lower() if args else "")

    # 近 N 天
    if sub.isdigit():
        days = max(1, min(int(sub), 3650))
        rep = await _get("/report?days=%d" % days) or _fallback_report(days)
        if not rep:
            return "读不到功耗数据喵～服务没跑？"
        return _fmt_days(rep, days)

    # 曲线
    if sub in ("曲线", "curve", "历史", "history"):
        hist = await _get("/history?minutes=30")
        if not hist:
            return "功耗服务未运行，取不到曲线喵～"
        return _fmt_curve(hist)

    # 明细
    if sub in ("明细", "detail", "详情", "now"):
        now = await _get("/now") or _fallback_now()
        if not now:
            return "读不到功耗数据喵～"
        return _fmt_detail(now)

    # 默认：当前 + 今日
    now = await _get("/now") or _fallback_now()
    rep = await _get("/report") or _fallback_report()
    if not now or not rep:
        return "读不到功耗数据喵～"
    return _fmt_main(now, rep)

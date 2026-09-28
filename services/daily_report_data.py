"""群聊日报的数据载荷 + HTML 填充 —— HTML 与 Pillow 两条渲染路**共用同一份数据**。

为什么要单独抽这一层（v2.3.68）：
  日报卡原来把「算数据」和「拼 HTML」耦合在 `modules/stats.py` 里。要迁 Pillow 且做到
  1:1，就必须保证两条路**输入完全一致**，否则像素差里混进数据差异，根本没法判。
  所以：build_payload() 只负责算，payload_to_html() / daily_report_pillow 只负责画。

⚠️ 这里的取数口径是**逐行照搬原实现**的，包括两处"看起来不一致但不要顺手改"的地方：
  1. 排行条用 `cfg.get_display_name()`（分群昵称），锐评里用的是记录里的 `name` 字段
     —— 两者可能不同，但原实现如此，改了就与既有历史卡片不一致；
  2. 锐评第一条/第二条取 `user_entries[0] / user_entries[-1]`，即**上榜与未上榜一起算的**
     完整排序（[-1] 是最后一名，不是第 8 名）。
"""
from __future__ import annotations

from datetime import datetime

# 锐评用的 emoji：HTML 直接写字符，Pillow 走 data/web_assets/daily_icons/ 贴图
# （同源字体：Chromium 用 snap 的 NotoColorEmoji；Pillow 侧由 scripts/_gen_daily_emoji_assets.py
#   从同一个字体抽成 PNG）
FACT_ICONS = {
    "mic": "🗣️",
    "diver": "🤿",
    "sleep": "😴",
    "moon": "🌙",
    "sun": "☀️",
}


def build_payload(stats: dict, group_id: int, date_str: str,
                  group_name: str = "", report_time: str | None = None) -> dict | None:
    """把 stats.json 的原始统计算成日报卡载荷；无数据返回 None。

    stats 形如 {"_meta": {"total": N}, "<uid>": {"count": n, "hours": {"8": 3, ...}, "name": "x"}}
    """
    meta = stats.get("_meta", {})
    total = meta.get("total", 0)
    if total == 0:
        return None

    user_entries = [(uid, v) for uid, v in stats.items() if uid != "_meta"]
    user_entries.sort(key=lambda x: x[1]["count"], reverse=True)
    participants = len(user_entries)

    # ── 24h 汇总 ──
    all_hours: dict[str, int] = {}
    for _uid, v in user_entries:
        for h, c in v.get("hours", {}).items():
            all_hours[h] = all_hours.get(h, 0) + c
    peak_hour = max(all_hours, key=all_hours.get) if all_hours else "?"
    peak_label = f"{peak_hour}:00"

    # ── 排行条 ──
    from core.config import get_config
    cfg = get_config()
    max_count = user_entries[0][1]["count"] if user_entries else 1
    ranking = []
    for i, (uid, v) in enumerate(user_entries[:8]):
        name = cfg.get_display_name(uid, group_id=group_id)
        if name == str(uid):
            name = v.get("name", str(uid))
        count = v["count"]
        ranking.append({
            "rank": i + 1,
            "name": name,
            "count": count,
            "pct": int(count / max_count * 100),
        })

    # ── 24h 热力（0→l0, 1-20→l1, 21-40→l2, 41-60→l3, 61-80→l4, 81-100→l5）──
    max_hour = max(all_hours.values()) if all_hours else 1
    hours = []
    for h in range(24):
        h_str = str(h)
        count = all_hours.get(h_str, 0)
        intensity = int(count / max_hour * 100) if max_hour > 0 else 0
        if intensity == 0:
            lv = 0
        elif intensity <= 20:
            lv = 1
        elif intensity <= 40:
            lv = 2
        elif intensity <= 60:
            lv = 3
        elif intensity <= 80:
            lv = 4
        else:
            lv = 5
        hours.append({"h": h, "count": count, "level": lv})

    # ── 锐评：每条 = 图标 key + 文本片段列表 [(文本, 是否加粗)] ──
    facts: list[dict] = []
    if user_entries:
        top_name = user_entries[0][1].get("name", str(user_entries[0][0]))
        top_n = user_entries[0][1]["count"]
        facts.append({
            "icon": "mic",
            "runs": [
                ("今日金话筒：", False),
                (top_name, True),
                (f"，贡献了 {top_n} 条消息，占全群 {int(top_n / total * 100)}%，话痨认证喵~", False),
            ],
        })
    if len(user_entries) >= 3:
        last_name = user_entries[-1][1].get("name", str(user_entries[-1][0]))
        last_n = user_entries[-1][1]["count"]
        facts.append({
            "icon": "diver",
            "runs": [
                ("深海潜水员：", False),
                (last_name, True),
                (f"，仅冒泡 {last_n} 次，需要氧气瓶吗？", False),
            ],
        })
    if all_hours:
        dead_hours = sorted(all_hours.items(), key=lambda x: x[1])[:2]
        dead_str = "、".join(f"{h}点" for h, _ in dead_hours)
        facts.append({
            "icon": "sleep",
            "runs": [(f"全员休眠期：{dead_str}，群聊变鬼城(。-ω-)zzz", False)],
        })
        morning = all_hours.get("8", 0) + all_hours.get("9", 0) + all_hours.get("10", 0)
        night = all_hours.get("21", 0) + all_hours.get("22", 0) + all_hours.get("23", 0)
        if night > morning * 1.5:
            facts.append({
                "icon": "moon",
                "runs": [(f"夜猫子聚集地！晚上比早上活跃 {int(night / max(1, morning))} 倍，"
                          f"熬夜冠军预备中~", False)],
            })
        elif morning > night * 1.5:
            facts.append({
                "icon": "sun",
                "runs": [(f"早鸟群！早上比晚上活跃 {int(morning / max(1, night))} 倍，"
                          f"打工人的觉悟喵~", False)],
            })

    return {
        "group_id": group_id,
        "group_name": group_name or f"群{group_id}",
        "date_str": date_str,
        "total": total,
        "participants": participants,
        "peak_label": peak_label,
        "avg": str(round(total / max(1, participants), 1)),
        "report_time": report_time or datetime.now().strftime("%H:%M"),
        "ranking": ranking,
        "hours": hours,
        "facts": facts,
        "brand": "幻梦 Project",
    }


# ══════════════════════════════════════════════════════════
#  HTML 填充（与迁移前 modules/stats.py 的输出保持一致）
# ══════════════════════════════════════════════════════════
_RANK_CLS = {0: "r1", 1: "r2", 2: "r3"}
_BAR_CLS = {0: "rb1", 1: "rb2", 2: "rb3"}
_RANK_ICON = {0: "🥇", 1: "🥈", 2: "🥉"}
_HOUR_LV = {0: "l0", 1: "l1", 2: "l2", 3: "l3", 4: "l4", 5: "l5"}


def payload_to_html(payload: dict, template: str) -> str:
    """把载荷填进 daily_report.html（占位符与迁移前一致）"""
    ranking_rows = []
    for i, r in enumerate(payload["ranking"]):
        rc = _RANK_CLS.get(i, "")
        bc = _BAR_CLS.get(i, "")
        icon = _RANK_ICON.get(i, f"{i + 1}")
        ranking_rows.append(
            f'<div class="rrow">'
            f'<div class="rnum {rc}">{icon}</div>'
            f'<div class="rname">{r["name"]}</div>'
            f'<div class="rbar-w"><div class="rbar {bc}" style="width:{r["pct"]}%"></div></div>'
            f'<div class="rcnt">{r["count"]}</div>'
            f'</div>'
        )

    hours_cells = [
        f'<div class="hr-c {_HOUR_LV[h["level"]]}" title="{h["h"]}h:{h["count"]}条"></div>'
        for h in payload["hours"]
    ]

    fun = []
    for f in payload["facts"]:
        parts = []
        for text, bold in f["runs"]:
            parts.append(f"<strong>{text}</strong>" if bold else text)
        fun.append(f'<div class="fn-it">{FACT_ICONS[f["icon"]]} {"".join(parts)}</div>')

    html = template
    for key, token in (
        (str(payload["group_id"]), "{{GROUP_ID}}"),
        (payload["group_name"], "{{GROUP_NAME}}"),
        (payload["date_str"], "{{REPORT_DATE}}"),
        (str(payload["total"]), "{{TOTAL_MSGS}}"),
        (str(payload["participants"]), "{{PARTICIPANTS}}"),
        (payload["peak_label"], "{{PEAK_HOUR}}"),
        (payload["avg"], "{{AVG_MSG}}"),
        ("\n".join(ranking_rows), "{{RANKING_ROWS}}"),
        ("\n".join(hours_cells), "{{HOURS_CELLS}}"),
        ("\n".join(fun), "{{FUN_FACTS}}"),
        (payload["report_time"], "{{REPORT_TIME}}"),
        (payload["brand"], "{{BRAND}}"),
    ):
        html = html.replace(token, key)
    return html

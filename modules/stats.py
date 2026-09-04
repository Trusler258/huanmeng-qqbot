"""
群聊数据统计模块
- 实时追踪每条消息（用户发言次数、时段分布）
- /~stats 查看今日统计
- 每日 0 点自动发送群聊日报（在 bot.py 中启动后台任务）
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

from core.logger import get_logger

logger = get_logger("stats")

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_TODAY_FILE_PREFIX = "stats_"

# ── 统计开关（按群，内存缓存） ──
_stats_disabled: set[int] = set()

def is_stats_enabled(group_id: int) -> bool:
    """检查某群统计是否开启（默认开启）"""
    return group_id not in _stats_disabled

def set_stats_state(group_id: int, enabled: bool):
    """设置某群统计开关状态"""
    if enabled:
        _stats_disabled.discard(group_id)
    else:
        _stats_disabled.add(group_id)


def _stats_file(group_id: int) -> Path:
    """获取某群今日统计文件路径"""
    today = datetime.now().strftime("%Y%m%d")
    return _DATA_DIR / f"{_TODAY_FILE_PREFIX}{group_id}_{today}.json"


def _archive_dir() -> Path:
    """统计归档目录"""
    d = _DATA_DIR / "stats_archive"
    d.mkdir(parents=True, exist_ok=True)
    return d


def record_message(group_id: int, user_id: int, msg_content: str, sender_name: str = ""):
    """
    记录一条群消息到今日统计。
    在 dispatcher._handle_message 中调用。
    """
    if not is_stats_enabled(group_id):
        return

    today_file = _stats_file(group_id)
    data: dict = {}

    if today_file.exists():
        try:
            data = json.loads(today_file.read_text(encoding="utf-8"))
        except Exception:
            data = {}

    uid = str(user_id)
    if uid not in data:
        data[uid] = {"count": 0, "hours": {}, "name": sender_name}
    elif sender_name and not data[uid].get("name"):
        # ★ 补全名字（首条消息时记录，后续覆盖取最新）
        data[uid]["name"] = sender_name
    elif sender_name:
        data[uid]["name"] = sender_name

    data[uid]["count"] += 1

    # 时段统计
    hour = datetime.now().hour
    hour_key = str(hour)
    data[uid]["hours"][hour_key] = data[uid]["hours"].get(hour_key, 0) + 1

    # 全局元数据
    if "_meta" not in data:
        data["_meta"] = {"total": 0, "last_update": 0}
    data["_meta"]["total"] += 1
    data["_meta"]["last_update"] = int(time.time())

    today_file.parent.mkdir(parents=True, exist_ok=True)
    today_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_today_stats(group_id: int) -> dict | None:
    """获取某群今日统计数据，无数据返回 None"""
    f = _stats_file(group_id)
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None


def get_yesterday_stats(group_id: int) -> dict | None:
    """获取某群昨日统计数据（从归档中读取）"""
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    archive = _archive_dir() / f"{_TODAY_FILE_PREFIX}{group_id}_{yesterday}.json"
    if not archive.exists():
        return None
    try:
        return json.loads(archive.read_text(encoding="utf-8"))
    except Exception:
        return None


def _active_hours_desc(hours_data: dict[str, int]) -> str:
    """描述活跃时段"""
    if not hours_data:
        return "暂无数据"
    sorted_hours = sorted(hours_data.items(), key=lambda x: x[1], reverse=True)
    top = sorted_hours[:3]
    parts = [f"{h}点({c}条)" for h, c in top]
    return "、".join(parts)


def format_stats_report(stats: dict, cfg, group_id: int, title: str = "今日群聊统计") -> str:
    """将统计数据格式化为猫娘口吻的文本报告"""
    if not stats:
        return "今天还没有人说话喵…好安静 (´;ω;`)"

    meta = stats.get("_meta", {})
    total = meta.get("total", 0)
    if total == 0:
        return "今天还没有人说话喵…好安静 (´;ω;`)"

    # 按发言数排序
    user_entries = [(uid, v) for uid, v in stats.items() if uid != "_meta"]
    user_entries.sort(key=lambda x: x[1]["count"], reverse=True)

    lines = [f"【{title}】"]
    lines.append(f"今日总消息: {total} 条 | 参与人数: {len(user_entries)} 人")
    lines.append("")

    # Top 5 发言榜
    lines.append("📊 发言排行 (前5):")
    for i, (uid, v) in enumerate(user_entries[:5], 1):
        name = cfg.get_display_name(uid, group_id=group_id)
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        medal = medals.get(i, f"{i}.")
        active_hours = _active_hours_desc(v.get("hours", {}))
        lines.append(f"  {medal} {name}: {v['count']} 条 (活跃于 {active_hours})")

    # 水群帝 & 潜水王
    if len(user_entries) >= 2:
        most = user_entries[0]
        least = user_entries[-1]
        most_name = cfg.get_display_name(most[0], group_id=group_id)
        least_name = cfg.get_display_name(least[0], group_id=group_id)
        lines.append("")
        lines.append(f"💬 今日水群王: {most_name} ({most[1]['count']}条) — 话痨认证喵~")
        lines.append(f"🤿 今日潜水王: {least_name} ({least[1]['count']}条) — 需要氧气瓶吗？")

    # 全群活跃时段
    all_hours: dict[str, int] = {}
    for uid, v in user_entries:
        for h, c in v.get("hours", {}).items():
            all_hours[h] = all_hours.get(h, 0) + c
    if all_hours:
        peak_hour = max(all_hours, key=all_hours.get)
        lines.append(f"⏰ 最热闹时段: {peak_hour}点 (共{all_hours[peak_hour]}条消息)")
        dead_hours = sorted(all_hours.items(), key=lambda x: x[1])[:3]
        dead_str = "、".join(f"{h}点" for h, _ in dead_hours)
        lines.append(f"😴 最冷清时段: {dead_str}")

    return "\n".join(lines)


# ════════════════════════════════════════════════════════════
#  HTML 日报卡片渲染
# ════════════════════════════════════════════════════════════

_DAILY_TPL_PATH = Path(__file__).resolve().parent.parent / "data" / "templates" / "daily_report.html"
_DAILY_TPL = None  # 缓存


def _read_daily_template() -> str:
    global _DAILY_TPL
    if _DAILY_TPL is None:
        _DAILY_TPL = _DAILY_TPL_PATH.read_text(encoding="utf-8")
    return _DAILY_TPL


async def generate_daily_report_image(stats: dict, group_id: int, date_str: str, group_name: str = "") -> str | None:
    """生成日报 HTML 卡片 → Playwright 截图 → 返回图片路径"""
    meta = stats.get("_meta", {})
    total = meta.get("total", 0)
    if total == 0:
        return None

    user_entries = [(uid, v) for uid, v in stats.items() if uid != "_meta"]
    user_entries.sort(key=lambda x: x[1]["count"], reverse=True)
    participants = len(user_entries)

    # ── 摘要 ──
    all_hours: dict[str, int] = {}
    for uid, v in user_entries:
        for h, c in v.get("hours", {}).items():
            all_hours[h] = all_hours.get(h, 0) + c
    peak_hour = max(all_hours, key=all_hours.get) if all_hours else "?"
    peak_label = f"{peak_hour}:00"

    # ── 排行条 ──
    from core.config import get_config
    cfg = get_config()
    max_count = user_entries[0][1]["count"] if user_entries else 1
    bar_classes = {0: "rb1", 1: "rb2", 2: "rb3"}
    rank_classes = {0: "r1", 1: "r2", 2: "r3"}
    rank_icons = {0: "🥇", 1: "🥈", 2: "🥉"}
    ranking_rows = []
    for i, (uid, v) in enumerate(user_entries[:8]):
        name = cfg.get_display_name(uid, group_id=group_id)
        if name == str(uid):
            name = v.get("name", str(uid))
        count = v["count"]
        pct = int(count / max_count * 100)
        rc = rank_classes.get(i, "")
        bc = bar_classes.get(i, "")
        icon = rank_icons.get(i, f"{i+1}")
        ranking_rows.append(
            f'<div class="rrow">'
            f'<div class="rnum {rc}">{icon}</div>'
            f'<div class="rname">{name}</div>'
            f'<div class="rbar-w"><div class="rbar {bc}" style="width:{pct}%"></div></div>'
            f'<div class="rcnt">{count}</div>'
            f'</div>'
        )

    # ── 24h 热力 ──
    max_hour = max(all_hours.values()) if all_hours else 1
    hours_cells = []
    for h in range(24):
        h_str = str(h)
        count = all_hours.get(h_str, 0)
        intensity = int(count / max_hour * 100) if max_hour > 0 else 0
        # 0→l0, 1-20→l1, 21-40→l2, 41-60→l3, 61-80→l4, 81-100→l5
        if intensity == 0:     lv = "l0"
        elif intensity <= 20:  lv = "l1"
        elif intensity <= 40:  lv = "l2"
        elif intensity <= 60:  lv = "l3"
        elif intensity <= 80:  lv = "l4"
        else:                  lv = "l5"
        hours_cells.append(f'<div class="hr-c {lv}" title="{h}h:{count}条"></div>')

    # ── 锐评 ──
    fun = []
    if user_entries:
        top_name = user_entries[0][1].get("name", str(user_entries[0][0]))
        fun.append(
            f'<div class="fn-it">🗣️ 今日金话筒：<strong>{top_name}</strong>，'
            f'贡献了 {user_entries[0][1]["count"]} 条消息，占全群 {int(user_entries[0][1]["count"]/total*100)}%，话痨认证喵~</div>'
        )
    if len(user_entries) >= 3:
        last_name = user_entries[-1][1].get("name", str(user_entries[-1][0]))
        fun.append(
            f'<div class="fn-it">🤿 深海潜水员：<strong>{last_name}</strong>，'
            f'仅冒泡 {user_entries[-1][1]["count"]} 次，需要氧气瓶吗？</div>'
        )
    if all_hours:
        dead_hours = sorted(all_hours.items(), key=lambda x: x[1])[:2]
        dead_str = "、".join(f"{h}点" for h, _ in dead_hours)
        fun.append(
            f'<div class="fn-it">😴 全员休眠期：{dead_str}，'
            f'群聊变鬼城(。-ω-)zzz</div>'
        )
        morning = all_hours.get("8", 0) + all_hours.get("9", 0) + all_hours.get("10", 0)
        night = all_hours.get("21", 0) + all_hours.get("22", 0) + all_hours.get("23", 0)
        if night > morning * 1.5:
            fun.append(
                f'<div class="fn-it">🌙 夜猫子聚集地！晚上比早上活跃 {int(night/max(1,morning))} 倍，'
                f'熬夜冠军预备中~</div>'
            )
        elif morning > night * 1.5:
            fun.append(
                f'<div class="fn-it">☀️ 早鸟群！早上比晚上活跃 {int(morning/max(1,night))} 倍，'
                f'打工人的觉悟喵~</div>'
            )

    # ── 填充模板 ──
    html = _read_daily_template()
    now = datetime.now()
    html = html.replace("{{GROUP_ID}}", str(group_id))
    html = html.replace("{{GROUP_NAME}}", group_name or f"群{group_id}")
    html = html.replace("{{REPORT_DATE}}", date_str)
    html = html.replace("{{TOTAL_MSGS}}", str(total))
    html = html.replace("{{PARTICIPANTS}}", str(participants))
    html = html.replace("{{PEAK_HOUR}}", peak_label)
    html = html.replace("{{AVG_MSG}}", str(round(total / max(1, participants), 1)))
    html = html.replace("{{RANKING_ROWS}}", "\n".join(ranking_rows))
    html = html.replace("{{HOURS_CELLS}}", "\n".join(hours_cells))
    html = html.replace("{{FUN_FACTS}}", "\n".join(fun))
    html = html.replace("{{REPORT_TIME}}", now.strftime("%H:%M"))
    html = html.replace("{{BRAND}}", "幻梦 Project")

    # ── 截图 ──
    from modules.changelog import render_card_to_image, _ensure_browser
    ts = now.strftime("%Y%m%d_%H%M%S")
    filename = f"daily_{group_id}_{date_str.replace('-','')}_{ts}.jpg"
    return await render_card_to_image(html, filename, width=720)




async def midnight_report_loop(cfg):
    """
    后台任务：每日 0 点自动发送群聊日报。
    对昨天有消息的群，发送日报并归档统计文件。
    """
    from services.sender import send_group_msg

    logger.info("每日统计推送已启动（等待凌晨0点...）")

    last_date = None
    wdsj_sent_today = False

    while True:
        now = datetime.now()
        today_str = now.strftime("%Y%m%d")

        # 检测日期变更（跨天）或 重启后补发（00:xx 未发）
        if last_date is None:
            last_date = today_str
        elif today_str != last_date:
            # 真正的跨天 — 重置所有状态
            wdsj_sent_today = False

        if today_str != last_date or (now.hour == 0 and not wdsj_sent_today):
            logger.info("🌅 新的一天 %s — 发送昨日群聊日报", today_str)
            # wdsj 采集 + 推送由 _bg_wdsj_collector 统一负责，这里不需要重复
            wdsj_sent_today = True

            # 昨日日期
            yesterday = now - timedelta(days=1)
            yesterday_str = yesterday.strftime("%Y%m%d")

            # 扫描所有 stats 文件，找到昨天的统计
            for f in _DATA_DIR.glob(f"{_TODAY_FILE_PREFIX}*_{yesterday_str}.json"):
                try:
                    # 从文件名提取群号: stats_1234567_20260601.json → 1234567
                    stem = f.stem  # stats_1234567_20260601
                    group_id_str = stem.replace(_TODAY_FILE_PREFIX, "").replace(f"_{yesterday_str}", "")
                    group_id = int(group_id_str)

                    stats = json.loads(f.read_text(encoding="utf-8"))
                    meta = stats.get("_meta", {})
                    if meta.get("total", 0) == 0:
                        continue

                    date_str = f"{yesterday.strftime('%Y.%m.%d')}"

                    # ★ 生成 HTML 日报卡片
                    card_path = await generate_daily_report_image(
                        stats, group_id, date_str,
                        group_name=f"群{group_id}"
                    )

                    # 归档
                    archive_path = _archive_dir() / f"{_TODAY_FILE_PREFIX}{group_id}_{yesterday_str}.json"
                    archive_path.write_text(f.read_text(encoding="utf-8"))
                    f.unlink(missing_ok=True)

                    if card_path:
                        from services.sender import send_group_msg
                        normalized = card_path.replace("\\", "/")
                        cq = f"[CQ:image,file=file:///{normalized}]"
                        await send_group_msg(cq, group_id)
                        logger.info("日报卡片已发送: 群=%d 昨日%d条消息", group_id, meta["total"])
                    else:
                        logger.warning("日报卡片生成失败: 群=%d", group_id)

                    # 避免瞬间大量发送
                    await asyncio.sleep(2)

                except Exception as e:
                    logger.error("日报发送失败 (文件=%s): %s", f.name, e)

            last_date = today_str

        # 每 30 秒检查一次
        await asyncio.sleep(30)

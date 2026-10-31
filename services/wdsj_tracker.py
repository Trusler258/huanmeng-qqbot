import asyncio
import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from core.logger import get_logger

logger = get_logger("wdsj_tracker")

_HISTORY_FILE = Path(__file__).resolve().parent.parent / "data" / "wdsj_history.json"

# ── 采集节奏（wdsj.collect_interval：4h / 12h）────────────────────────
# ★ 单一真相源：bg_tasks 的调度循环、commands.py 的日榜/竞技卡片、断流公告卡
#   全部从这里取节奏，避免"调度已切 12h、卡片上还写着下一轮 4:01"这种口径漂移。
_WDSJ_INTERVALS = (4, 12)
_WDSJ_INTERVAL_DEFAULT = 12
_WDSJ_CFG_PATH = Path(__file__).resolve().parent.parent / "config" / "bot_config.toml"


def collect_interval() -> int:
    """当前采集节奏（小时）：4 或 12。读不到 / 非法值一律回退默认 12。"""
    try:
        import toml
        v = int(toml.load(_WDSJ_CFG_PATH).get("wdsj", {}).get("collect_interval")
                or _WDSJ_INTERVAL_DEFAULT)
    except Exception:
        v = _WDSJ_INTERVAL_DEFAULT
    return v if v in _WDSJ_INTERVALS else _WDSJ_INTERVAL_DEFAULT


def collect_slots(interval: int | None = None) -> tuple:
    """采集时隙的小时数（统一取该小时的**第 1 分钟**，避开整点拥挤）。"""
    iv = collect_interval() if interval is None else interval
    return (0, 4, 8, 12, 16, 20) if iv == 4 else (0, 12)


def next_slot(now=None, interval: int | None = None):
    """离 now 最近的**未来**采集时刻。"""
    now = now or datetime.now()
    cands = [now.replace(hour=h, minute=1, second=0, microsecond=0)
             for h in collect_slots(interval)]
    cands += [c + timedelta(days=1) for c in cands]
    return min(c for c in cands if c > now)


def last_passed_slot(now=None, interval: int | None = None):
    """最近一个**已经过去**的采集时隙（用来判断"是否漏采了一轮"）。"""
    now = now or datetime.now()
    cands = [now.replace(hour=h, minute=1, second=0, microsecond=0)
             for h in collect_slots(interval)]
    cands += [c - timedelta(days=1) for c in cands]
    passed = [c for c in cands if c <= now]
    return max(passed) if passed else None


def slots_hint() -> str:
    """时隙列表文案，如「0:01 / 12:01」。"""
    return " / ".join(f"{h}:01" for h in collect_slots())


# 全量采集模板 — 每个模板存整个 values dict
_TEMPLATES = {
    "bedwars-stats": "起床战争",
    "arena-stats": "竞技场",
}

# 日报卡片用的字段映射（起床战争）
_DAILY_FIELDS = {
    "kills": ("击杀", "num"),
    "finalKills": ("终杀", "num"),
    "wins": ("胜场", "num"),
    "deaths": ("死亡", "death"),
}

# 竞技场日报字段
_ARENA_DAILY_FIELDS = {
    "kills": ("击杀", "num"),
    "wins": ("胜场", "num"),
    "losses": ("败场", "loss"),
    "deaths": ("死亡", "death"),
}

# 趋势图指标列表
_TREND_METRICS = [
    ("bw_kills", "bedwars-stats", "kills", "起床-击杀"),
    ("bw_wins", "bedwars-stats", "wins", "起床-胜场"),
    ("bw_finals", "bedwars-stats", "finalKills", "起床-终杀"),
    ("bw_deaths", "bedwars-stats", "deaths", "起床-死亡"),
    ("arena_kills", "arena-stats", "kills", "竞技场-击杀"),
]


def _load_history():
    if _HISTORY_FILE.exists():
        try:
            return json.loads(_HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_history(data):
    _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    _HISTORY_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))


# ── 防双跑锁：bot.py 遗留循环 与 bg_tasks 插件循环 并存时只允许一轮采集执行 ──
# v2.0.4s: 2026-09-03 事故，两条"每日采集完成"日志 = 同进程双循环重复采集，
# 上游并发翻倍 → ConnectTimeout 批量失败（80 成功 / 20 失败）
_collect_lock = asyncio.Lock()


# ------每日采集------
async def daily_stats_collect(max_retry_rounds: int = 3, retry_delay: float = 8.0):
    """
    每日战绩采集（★ v2.0.4s 升级）
    - 首轮全量采集 → 统计失败项 → 对失败名单整体统一重试（最多 max_retry_rounds 轮）
    - 防双跑：模块级锁，已有采集在跑则直接跳过
    - 返回最终仍失败的项目 [(player, template_id), ...]，由调用方决定是否通知
      （如定时采集后把 3 轮仍失败的人发 QQ 空间说明）
    """
    if _collect_lock.locked():
        logger.warning("已有采集任务在运行，本次跳过（防双跑）")
        return []

    async with _collect_lock:
        from services import wdsj_api as api
        from modules.commands import _load_wdsj_bindings

        bindings = _load_wdsj_bindings()
        if not bindings:
            return []

        from datetime import datetime as dt
        ts_str = dt.now().strftime("%Y-%m-%dT%H:%M:%S")
        history = _load_history()
        players = list(set(bindings.values()))

        logger.info("每日战绩采集: %d 个玩家 x %d 模板 (重试上限 %d 轮)",
                    len(players), len(_TEMPLATES), max_retry_rounds)

        all_targets = [(p, t) for p in players for t in _TEMPLATES]
        # ★ v2.3.24 并发度 3 → 8：实测（20 目标，服务器）并发 3=5.40s / 6=2.35s /
        #   10=2.02s / 16=1.63s，全程 20/20 成功、无 403/429 风控。
        #   取 8 是"接近最优 + 留余量"的折中（风控策略可能变化，不打满 16）。
        #   另：wdsj_api 已改共享连接池，每个请求省掉 TCP/TLS 握手。
        sem = asyncio.Semaphore(8)
        collected = 0
        failed: dict = {}  # (player, tid) -> 失败原因

        async def _collect_target(player, tid):
            nonlocal collected
            async with sem:
                try:
                    data = await api.query_player_stats(player, tid, timeout=5.0)
                except Exception as e:
                    failed[(player, tid)] = f"{type(e).__name__}: {e}"
                    return
                if data and data.get("values"):
                    entry = history.setdefault(player, {})
                    series = entry.setdefault(tid, [])
                    # ★ 只追加，不做日级覆盖（多时间点才能算增量）
                    series.append({"ts": ts_str, "values": dict(data["values"])})
                    collected += 1
                    failed.pop((player, tid), None)
                else:
                    failed[(player, tid)] = api.last_error or "empty data"

        # 首轮全量 + 对失败名单统一重试（最多 max_retry_rounds 轮）
        for rnd in range(1 + max_retry_rounds):
            targets = all_targets if rnd == 0 else list(failed.keys())
            if not targets:
                break
            if rnd > 0:
                logger.warning("失败重试第 %d/%d 轮: %d 项 → %s",
                               rnd, max_retry_rounds, len(targets),
                               "、".join(f"{p}[{t}]" for p, t in targets))
            await asyncio.gather(*[_collect_target(p, t) for p, t in targets])
            if rnd == 0:
                logger.info("首轮采集完成: 成功 %d, 失败 %d 项", collected, len(failed))
            if failed and rnd < max_retry_rounds:
                await asyncio.sleep(retry_delay)  # 统一重试前稍等，给上游恢复时间

        _save_history(history)
        logger.info("每日战绩采集完成: %d 条新记录 (最终失败 %d)", collected, len(failed))
        if failed:
            logger.warning("最终失败名单(%d轮后仍失败): %s", 1 + max_retry_rounds,
                           "、".join(f"{p}[{t}]({e})" for (p, t), e in failed.items()))
        return list(failed.keys())


# ------趋势数据------
def get_player_trend(player, metric, days=None):
    """取趋势序列；每天只保留最后一条记录（按日期去重取最新）
    days=None 表示从首次记录到最新一条"""
    history = _load_history()
    entry = history.get(player, {})
    for mid, tid, fn, lb in _TREND_METRICS:
        if mid == metric:
            raw = entry.get(tid, [])
            if days is not None and days > 0:
                raw = raw[-days:]
            # 按日期分组，每天只取最后一条
            by_day = {}
            for s in raw:
                day = s["ts"][:10]  # YYYY-MM-DD
                by_day[day] = {"ts": s["ts"], "val": int(s["values"].get(fn, 0))}
            return [by_day[k] for k in sorted(by_day)]
    return []


# ------趋势图------
def generate_trend_chart(player, metric, days=None):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
    except ImportError:
        return None

    series = get_player_trend(player, metric, days)
    if not series:
        return None

    label = metric
    for mid, tid, fn, lb in _TREND_METRICS:
        if mid == metric:
            label = lb
            break

    dates = [datetime.strptime(s["ts"], "%Y-%m-%dT%H:%M:%S") for s in series]
    vals = [s["val"] for s in series]

    try:
        import matplotlib.font_manager as fm
        import os
        cache_dir = matplotlib.get_cachedir()
        for f in Path(cache_dir).glob("fontlist-v*.json"):
            f.unlink()
        fm._load_fontmanager(try_read_cache=False)
        plt.rcParams["font.sans-serif"] = ["WenQuanYi Zen Hei", "Noto Sans CJK SC", "SimHei"]
        plt.rcParams["axes.unicode_minus"] = False
    except Exception:
        pass

    fig, ax = plt.subplots(figsize=(10, 4))
    line_color = "#7c3aed"
    ax.plot(dates, vals, marker="o", linewidth=2, markersize=5, color=line_color)
    ax.fill_between(dates, vals, alpha=0.08, color=line_color)

    vmin, vmax = min(vals), max(vals)
    margin = max((vmax - vmin) * 0.15, 1)
    ax.set_ylim(vmin - margin, vmax + margin)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=max(1, len(dates) // 10)))

    # 每天一个点，全部标注
    for d, v in zip(dates, vals):
        ax.annotate(str(v), (d, v), textcoords="offset points", xytext=(0, 10),
                    ha="center", fontsize=8, color=line_color)

    span = f"{dates[0]:%m/%d} ~ {dates[-1]:%m/%d}"
    ax.set_title(f"{player} - {label} 趋势 ({len(dates)}天, {span})", fontsize=13, fontweight="bold", color="#e0e0e0")
    ax.tick_params(colors="#999")
    ax.grid(True, alpha=0.2, color="#555")
    fig.patch.set_facecolor("#1a1a2e")
    ax.set_facecolor("#1a1a2e")
    for spine in ax.spines.values():
        spine.set_color("#333")

    fig.tight_layout()
    out_dir = Path(__file__).resolve().parent.parent / "data" / "img_temp"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = str(out_dir / f"trend_{player}_{metric}_{int(time.time())}.png")
    fig.savefig(out_path, dpi=120, bbox_inches="tight", facecolor="#1a1a2e")
    plt.close(fig)
    return out_path


# ------群内总排名------
async def build_group_rank(group_id, mode_key="bw_kills"):
    """取所有绑定玩家的最新指标值，排序生成排名卡片图片"""
    import time as _time
    t0 = _time.time() * 1000               # v2.3.32 页脚「渲染时间」起点
    from modules.commands import _load_wdsj_bindings
    bindings = _load_wdsj_bindings()

    # 指标中文名
    label = mode_key
    for mid, tid, fn, lb in _TREND_METRICS:
        if mid == mode_key:
            label = lb
            break

    # 取每个玩家最新一条记录的值
    rows = []
    for qq, name in bindings.items():
        series = get_player_trend(name, mode_key)
        if series:
            rows.append((name, series[-1]["val"]))
    rows.sort(key=lambda x: -x[1])

    if not rows:
        return "暂无排名数据喵~ 需要先采集几天数据", None

    # 生成 HTML 卡片
    medal = ["🥇", "🥈", "🥉"]
    body_rows = []
    for i, (name, val) in enumerate(rows):
        rank_icon = medal[i] if i < 3 else f"#{i+1}"
        body_rows.append(
            f'<tr><td class="rank">{rank_icon}</td>'
            f'<td class="name">{name}</td>'
            f'<td class="val">{val}</td></tr>'
        )

    html = f"""<div class="rank-card">
      <div class="rank-header">
        <h2>群内排名 - {label}</h2>
        <div class="sub">{len(rows)} 名玩家</div>
      </div>
      <table>
        <thead><tr><th>#</th><th>玩家</th><th>{label}</th></tr></thead>
        <tbody>
          {''.join(body_rows)}
        </tbody>
      </table>
      <div class="footer">Powered by wdsj.net Nexus | 幻梦 QQ Bot</div>
    </div>"""

    tmpl_path = Path(__file__).resolve().parent.parent / "data" / "templates" / "wdsj_card.html"
    tmpl = tmpl_path.read_text(encoding="utf-8")
    from services.card_base import inject_stamp
    full_html = inject_stamp(tmpl.replace("${CARD_CONTENT}", html), t0)

    from modules.changelog import render_card_to_image
    import uuid
    filename = f"wdsj_rank_{mode_key}_{uuid.uuid4().hex[:8]}.jpg"
    img_path = await render_card_to_image(full_html, filename, width=680)
    return None, img_path


# ------每日排名数据------
# v2.3.22: 统计区间信息返回给调用方，让卡片副标题显示真实日期范围
_DAILY_RET = ("rows", "label_date", "range_start", "range_end", "new_players", "fallback")

def build_daily_rankings(label_date=None, cross_day=False, auto_fallback=True):
    """
    返回 (rows, label_date, time_start, time_end, new_players, fallback)
      fallback=False: 正常模式（cross_day 时 label_date 为统计起始日）
      fallback=True:  今日(00:01一轮)全员零增量 → 自动回退为 昨日0:01→今日0:01 完整跨天榜
                     （label_date 已替换为昨日日期，time_start/time_end 显示真实区间）
    cross_day=False: 当日最早 → 当日最新（手动查询）
    cross_day=True:  当日最早 → 次日最早（凌晨自动发送）
    """
    history = _load_history()
    today_str = date.today().isoformat()
    if label_date is None:
        label_date = today_str
    from modules.commands import _load_wdsj_bindings
    bindings = _load_wdsj_bindings()

    fallback = False
    # ★ v2.3.22: 手动查询"今日"且尚未跨天时采用跨天口径（今日0:01→今日0:01 不合理）
    #   —— 但用户手动查今天仍保留当日0:01→当前最新（否则0-4点查今天会被误判）
    #   判断"今日全员零增量"在下方 rows 构建后统一进行
    # 计算次日日期（用于 cross_day 模式）
    tomorrow = (datetime.strptime(label_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d") if cross_day else None

    rows = []
    new_players = []
    _seen_names = set()  # v2.0.4ao: 按游戏名去重, 防同名绑定(如 shuangzi_1228 绑到两个QQ)重复上榜
    for uid, name in bindings.items():
        if name in _seen_names:
            continue
        _seen_names.add(name)
        entry = history.get(name, {})
        bw = entry.get("bedwars-stats", [])
        if not bw:
            continue
        day_entries = [s for s in bw if s.get("ts", "").startswith(label_date)]
        if not day_entries:
            new_players.append(name)
            continue

        # baseline = 当日最早
        prev = day_entries[0].get("values", {})

        if cross_day:
            # 次日最早作为 end
            next_day = [s for s in bw if s.get("ts", "").startswith(tomorrow)]
            curr = next_day[0].get("values", {}) if next_day else day_entries[-1].get("values", {})
        else:
            # 当日最新
            curr = day_entries[-1].get("values", {})
        diffs = {}
        for k in _DAILY_FIELDS:
            pv = int(prev.get(k, 0))
            cv = int(curr.get(k, 0))
            diffs[k] = cv - pv
        # 增量全零标记（稍后判断是否全滤）
        is_zero = all(v == 0 for v in diffs.values())
        # KD 按今日增量计算: (击杀+终杀) / 死亡
        today_kills = diffs.get("kills", 0) + diffs.get("finalKills", 0)
        today_deaths = max(diffs.get("deaths", 1), 1)
        kd = today_kills / today_deaths
        rows.append((name, diffs, kd, is_zero))
    rows.sort(key=lambda x: -x[1].get("kills", 0))

    # ★ v2.3.22: 今日全员零增量回退
    #   场景: 0点~4点之间手动查 /wdsj daily（或今日刚采集完一轮00:01），
    #         此时"今日"只有一轮数据、增量必然全 0；直接展示"今日 00:01→今天"毫无意义。
    #   处理: 自动把统计区间改为 昨日 0:01 → 今日 0:01 的完整跨天数据。
    if auto_fallback and label_date == today_str and not cross_day and rows and all(r[-1] for r in rows):
        logger.info("今日全员零增量(可能仅采集一轮)，自动回退为昨日跨天榜")
        yesterday = (datetime.strptime(today_str, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        _ret = build_daily_rankings(label_date=yesterday, cross_day=True, auto_fallback=False)
        return _ret[:5] + (True,)  # 保持 6 元组: (rows, label, start, end, new_players, fallback)

    # 如果并非全员零增量，则过滤零增量玩家
    # ★ v2.3.21: 全员零增量时【不要】把 rows 滤空——凌晨刚采完第一轮(00:01)时
    #   所有玩家增量都是 0，旧逻辑会滤到只剩 1 人，看起来像"数据丢了"。
    #   此时保留全部并显示 0 增量，至少把"今日已采集 N 人"展示出来。
    if rows and not all(r[-1] for r in rows):
        rows = [r for r in rows if not r[-1]]
    # 收集所有时间戳 → 计算时间段
    all_times = []
    cross_times = []
    for uid, name in bindings.items():
        bw = history.get(name, {}).get("bedwars-stats", [])
        day_entries = [s for s in bw if s.get("ts", "").startswith(label_date)]
        for e in day_entries:
            all_times.append(e.get("ts", ""))
        if cross_day:
            next_day = [s for s in bw if s.get("ts", "").startswith(tomorrow)]
            for e in next_day:
                cross_times.append(e.get("ts", ""))
    # 直接使用最早/最晚的真实采集时间
    time_start = min(all_times)[11:16] if all_times else "??:??"
    if cross_day and cross_times:
        end_ts = min(cross_times) if cross_times else max(all_times)
        # ★ 跨天模式：end 加日期前缀避免"00:01 → 00:01"的误解
        time_end = f"{end_ts[5:10]} {end_ts[11:16]}"
    else:
        time_end = max(all_times)[11:16] if all_times else "??:??"
    return rows, label_date, time_start, time_end, new_players, fallback


# ── 竞技场日报 ──

def build_arena_daily_rankings(label_date=None, cross_day=False, auto_fallback=True):
    """竞技场日榜: 击杀 / 胜场 / 败场 / 死亡 / KD
    返回 (rows, label_date, time_start, time_end, fallback)
      fallback=False: 正常模式（cross_day 时 label_date 为统计起始日）
      fallback=True:  今日(00:01一轮)全员零增量 → 自动回退为 昨日0:01→今日0:01 完整跨天榜
    cross_day=False: 当日最早 -> 当日最新（手动查询）
    cross_day=True:  当日最早 -> 次日最早（凌晨自动发送，与起床对齐）
    """
    history = _load_history()
    today_str = date.today().isoformat()
    if label_date is None:
        label_date = today_str
    from modules.commands import _load_wdsj_bindings
    bindings = _load_wdsj_bindings()

    fallback = False
    tomorrow = (datetime.strptime(label_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d") if cross_day else None

    rows = []
    _seen_names = set()  # v2.0.4ao: 按游戏名去重, 防同名绑定重复上榜
    for uid, name in bindings.items():
        if name in _seen_names:
            continue
        _seen_names.add(name)
        entry = history.get(name, {})
        arena = entry.get("arena-stats", [])
        if not arena:
            continue
        day_entries = [s for s in arena if s.get("ts", "").startswith(label_date)]
        if not day_entries:
            continue
        prev = day_entries[0].get("values", {})
        if cross_day:
            next_day = [s for s in arena if s.get("ts", "").startswith(tomorrow)]
            curr = next_day[0].get("values", {}) if next_day else day_entries[-1].get("values", {})
        else:
            curr = day_entries[-1].get("values", {})
        diffs = {}
        for k in _ARENA_DAILY_FIELDS:
            pv = int(prev.get(k, 0))
            cv = int(curr.get(k, 0))
            diffs[k] = cv - pv
        kills = diffs.get("kills", 0)
        deaths = max(diffs.get("deaths", 1), 1)
        kd = kills / deaths
        rows.append((name, diffs, kd, curr.get("division", "?").replace("§c","").replace("§","")))
    rows.sort(key=lambda x: -x[1].get("kills", 0))

    # ★ v2.3.22: 今日全员零增量回退（同起床版，见 build_daily_rankings 注释）
    if auto_fallback and label_date == today_str and not cross_day and rows and all(int(r[1].get("kills", 0)) == 0 and int(r[1].get("wins", 0)) == 0 for r in rows):
        logger.info("竞技场今日全员零增量(可能仅采集一轮)，自动回退为昨日跨天榜")
        yesterday = (datetime.strptime(today_str, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        _ret = build_arena_daily_rankings(label_date=yesterday, cross_day=True, auto_fallback=False)
        return _ret[:4] + (True,)  # 保持 5 元组: (rows, label, start, end, fallback)

    # ★ v2.3.21: 竞技场版同样避免"全零被滤光"——不再在循环里 continue 滤零，
    #   改为先全部收集，再按"是否全员零"决定滤不滤（凌晨刚采完 00:01 一轮时
    #   全员零增量，旧逻辑会把榜滤空/只剩 1 人，看起来像数据丢了）。
    if any(int(r[1].get("kills", 0)) != 0 or int(r[1].get("wins", 0)) != 0 for r in rows):
        rows = [r for r in rows if not (int(r[1].get("kills", 0)) == 0 and int(r[1].get("wins", 0)) == 0)]

    all_times = []
    cross_times = []
    for uid, name in bindings.items():
        arena = history.get(name, {}).get("arena-stats", [])
        for e in arena:
            if e.get("ts", "").startswith(label_date):
                all_times.append(e.get("ts", ""))
            if cross_day and e.get("ts", "").startswith(tomorrow):
                cross_times.append(e.get("ts", ""))
    time_start = min(all_times)[11:16] if all_times else "??:??"
    if cross_day and cross_times:
        end_ts = min(cross_times) if cross_times else max(all_times)
        time_end = f"{end_ts[5:10]} {end_ts[11:16]}"
    else:
        time_end = max(all_times)[11:16] if all_times else "??:??"
    return rows, label_date, time_start, time_end, fallback


# ══════════════════════════════════════════════════════════════
#  趋势卡（HTML 版，2026-10-27 重做）
#  主题对齐 data/templates/wdsj_dual_card.html：
#  米白底 + 橙/蓝双光晕 + 白色半透明玻璃面板 + Monocraft 数字字体
#  matplotlib 版（generate_trend_chart）保留为 Playwright 不可用时的兜底
# ══════════════════════════════════════════════════════════════
_TREND_CARD_W = 1800
_TREND_TPL_PATH = Path(__file__).resolve().parent.parent / "data" / "templates" / "wdsj_trend.html"
_trend_tpl_cache: str | None = None
_monocraft_cache: str | None = None

# 指标 → 视觉（起床用暖橙，竞技场用冷蓝，对应双模式卡的 --red-hue / --blue-hue）
_TREND_THEME = {
    "bw": {"accent": "#ee5a3a", "accent2": "#ff9e7a", "glow_a": "rgba(255,183,154,.34)",
           "glow_b": "rgba(158,188,255,.30)", "shadow": "rgba(230,90,58,.26)"},
    "ar": {"accent": "#4a6cf7", "accent2": "#8fa9ff", "glow_a": "rgba(255,183,154,.26)",
           "glow_b": "rgba(158,188,255,.38)", "shadow": "rgba(74,108,247,.24)"},
}


def _trend_font_face() -> str:
    """从双模式卡里抽 @font-face，避免同一份 380KB base64 维护两遍。"""
    global _monocraft_cache
    if _monocraft_cache is not None:
        return _monocraft_cache
    _monocraft_cache = ""
    try:
        import re as _re
        dual = (_TREND_TPL_PATH.parent / "wdsj_dual_card.html").read_text(encoding="utf-8")
        m = _re.search(r"@font-face\s*\{[^}]*?data:font/ttf;base64,[^}]*\}", dual, _re.S)
        if m:
            _monocraft_cache = m.group(0)
    except Exception as e:
        logger.warning("趋势卡字体提取失败（回退系统字体）: %s", e)
    return _monocraft_cache


def _fmt_num(v: int) -> str:
    return f"{v:,}" if abs(v) >= 10000 else str(v)


# ── 多指标配色（最多 3 条线，色相拉开，避免同色系分不清）──
_TREND_PALETTE = [
    {"accent": "#ee5a3a", "accent2": "#ff9e7a"},   # 暖橙
    {"accent": "#4a6cf7", "accent2": "#8fa9ff"},   # 冷蓝
    {"accent": "#0f9d58", "accent2": "#6cd39b"},   # 青绿
]

# 指标 key → 中文名（命令解析与图例共用）
_TREND_LABELS = {
    "bw_kills": "击杀", "bw_wins": "胜场", "bw_finals": "最终击杀",
    "bw_deaths": "死亡", "arena_kills": "竞技场击杀",
}


def _axis_scale(vmin: float, vmax: float, target: int = 4):
    """整数友好刻度：步长取 1/2/2.5/5×10ⁿ，网格最多 6 条。"""
    import math as _m
    if vmax <= vmin:
        d = max(1, int(round(abs(vmax) * 0.05)))
        vmin, vmax = vmax - d, vmax + d
    span = max(1.0, vmax - vmin)
    raw = span / target
    mag = 10 ** _m.floor(_m.log10(raw)) if raw > 0 else 1
    step = mag
    for mul in (1, 2, 2.5, 5, 10):
        if raw <= mul * mag:
            step = mul * mag
            break
    step = max(1, round(step))
    y0 = _m.floor(vmin / step) * step
    y1 = _m.ceil(vmax / step) * step
    if y1 - y0 < step:
        y1 = y0 + step
    while (y1 - y0) / step > 6:
        step *= 2
        y0 = _m.floor(vmin / step) * step
        y1 = _m.ceil(vmax / step) * step
    return y0, y1, step


def _trend_svg(datasets: list) -> str:
    """多序列折线图（纯 SVG）。

    datasets: [{"key","label","series":[{"ts","val"}],"accent","accent2","axis"}]
      axis="l" 挂左轴 / "r" 挂右轴——量级差太大时拆双轴，
      否则「胜场 300」和「击杀 7000」画在一起，胜场会被压成一条贴底的直线。
    """
    W, H = 1640, 520
    has_right = any(d["axis"] == "r" for d in datasets)
    L, R, T, B = 64, (92 if has_right else 30), 22, 54
    pw, ph = W - L - R, H - T - B

    dates = sorted({s["ts"][:10] for d in datasets for s in d["series"]})
    n = len(dates)
    idx = {d: i for i, d in enumerate(dates)}

    def X(i: int) -> float:
        return L + (pw / 2 if n == 1 else pw * i / (n - 1))

    left = [d for d in datasets if d["axis"] == "l"] or datasets
    right = [d for d in datasets if d["axis"] == "r"]

    lv = [s["val"] for d in left for s in d["series"]]
    ly0, ly1, lstep = _axis_scale(min(lv), max(lv))
    if right:
        rv = [s["val"] for d in right for s in d["series"]]
        ry0, ry1, rstep = _axis_scale(min(rv), max(rv))

    def YL(v: float) -> float:
        return T + ph - (v - ly0) / max(1e-9, (ly1 - ly0)) * ph

    def YR(v: float) -> float:
        return T + ph - (v - ry0) / max(1e-9, (ry1 - ry0)) * ph

    # ── 网格与左轴刻度 ──
    grid, ltick = [], []
    k = 0
    while ly0 + k * lstep <= ly1 + 1e-6:
        v = ly0 + k * lstep
        y = YL(v)
        grid.append(f'<line x1="{L}" y1="{y:.1f}" x2="{W-R}" y2="{y:.1f}" '
                    f'stroke="rgba(120,100,80,.13)" stroke-width="1" stroke-dasharray="4 6"/>')
        ltick.append(f'<text x="{L-14}" y="{y+4:.1f}" text-anchor="end" font-size="15" '
                     f'fill="#a89e96" font-family="Monocraft,PingFang SC,sans-serif">{_fmt_num(int(round(v)))}</text>')
        k += 1

    # ── 右轴刻度（用该序列的颜色，避免和左轴混淆）──
    rtick = []
    if right:
        rc = right[0]["accent"]
        k = 0
        while ry0 + k * rstep <= ry1 + 1e-6:
            v = ry0 + k * rstep
            y = YR(v)
            rtick.append(f'<text x="{W-R+14}" y="{y+4:.1f}" text-anchor="start" font-size="15" '
                         f'fill="{rc}" opacity=".85" '
                         f'font-family="Monocraft,PingFang SC,sans-serif">{_fmt_num(int(round(v)))}</text>')
            k += 1

    # ── 折线 / 采样点 ──
    paths, dots, labels = [], [], []
    end_pts = []                 # 多序列的末点标注，画完统一定位（防重叠）
    multi = len(datasets) > 1
    for d in datasets:
        ys = YL if d["axis"] == "l" else YR
        pts = [(X(idx[s["ts"][:10]]), ys(s["val"]), s["val"]) for s in d["series"]]
        line = "M " + " L ".join(f"{x:.1f} {y:.1f}" for x, y, _ in pts)
        paths.append(
            f'<path d="{line}" fill="none" stroke="{d["accent"]}" stroke-width="4" '
            f'stroke-linecap="round" stroke-linejoin="round"/>'
        )
        r = 5.0 if len(pts) <= 30 else (3.6 if len(pts) <= 60 else 2.6)
        for x, y, _v in pts:
            dots.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="#fff" '
                        f'stroke="{d["accent"]}" stroke-width="2.2"/>')

        # 数值标注：单序列走自适应密度（最多 15 个）；多序列只标末点，避免互相压
        if not multi:
            picks = _label_picks([p[2] for p in pts], pw, len(str(max(p[2] for p in pts))))
            for i in picks:
                x, y, v = pts[i]
                labels.append(_value_label(x, y, v, d["accent"], i == 0, i == len(pts) - 1))
        else:
            x, y, v = pts[-1]
            end_pts.append((x, y, v, d["accent"]))

    # 多序列末点标注：按 y 排序，挨太近的逐个下移，避免「475 / 7356」叠在一起
    end_pts.sort(key=lambda tp: tp[1])
    used_y = []
    for x, y, v, c in end_pts:
        ny = y
        while any(abs(ny - uy) < 26 for uy in used_y):
            ny += 30
        used_y.append(ny)
        labels.append(_value_label(x, ny, v, c, False, True))

    # 单序列保留面积渐变；多序列叠加面积会糊成一团
    area = ""
    if not multi:
        d0 = datasets[0]
        ys = YL if d0["axis"] == "l" else YR
        pts = [(X(idx[s["ts"][:10]]), ys(s["val"])) for s in d0["series"]]
        path = "M " + " L ".join(f"{x:.1f} {y:.1f}" for x, y in pts)
        area = (f'<path d="M {pts[0][0]:.1f} {T+ph:.1f} L ' +
                " L ".join(f"{x:.1f} {y:.1f}" for x, y in pts) +
                f' L {pts[-1][0]:.1f} {T+ph:.1f} Z" fill="url(#ag)"/>')

    # ── x 轴日期（最多 10 个，首尾向内对齐）──
    xlab = []
    step_x = max(1, (n - 1) // 9) if n > 1 else 1
    xs_pick = list(range(0, n, step_x))
    if n > 1:
        # 末位日期一定要有；但它和上一格的像素距离不足一个字宽+18 时，把上一格挤掉
        gap_px = pw * (n - 1 - xs_pick[-1]) / max(1, n - 1)
        if gap_px >= 46 + 34:   # 日期 5 字符约 46px，再留 34px 才不至于糊在一起
            xs_pick.append(n - 1)
        elif len(xs_pick) > 1:
            xs_pick[-1] = n - 1
    for i in xs_pick:
        anchor = "start" if i == 0 else ("end" if i == n - 1 else "middle")
        xlab.append(f'<text x="{X(i):.1f}" y="{H-18}" text-anchor="{anchor}" font-size="15" '
                    f'fill="#a89e96" font-family="Monocraft,PingFang SC,sans-serif">'
                    f'{dates[i][5:].replace("-", "/")}</text>')

    grad = ""
    if not multi:
        c = datasets[0]["accent"]
        grad = (f'<linearGradient id="ag" x1="0" y1="0" x2="0" y2="1">'
                f'<stop offset="0%" stop-color="{c}" stop-opacity=".26"/>'
                f'<stop offset="72%" stop-color="{c}" stop-opacity=".05"/>'
                f'<stop offset="100%" stop-color="{c}" stop-opacity="0"/></linearGradient>')

    return f'''<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">
  <defs>{grad}</defs>
  {''.join(grid)}
  {area}
  {''.join(paths)}
  {''.join(dots)}
  {''.join(labels)}
  {''.join(xlab)}
  {''.join(ltick)}
  {''.join(rtick)}
</svg>'''


def _label_picks(vals: list, plot_w: float, max_chars: int) -> list:
    """按「数字宽度 + 间隔」决定标几个数值，整条线最多 15 个。"""
    n = len(vals)
    if n <= 1:
        return [0]
    fs, gap, cap = 16, 10, 15
    label_w = max_chars * fs * 0.62
    fit = int(plot_w // (label_w + gap)) + 1
    k = max(2, min(cap, fit, n))
    if k >= n:
        return list(range(n))
    picks = sorted({round(i * (n - 1) / (k - 1)) for i in range(k)})
    kept = []
    for i in picks:
        if not kept:
            kept.append(i)
            continue
        need = label_w + gap + (label_w * 0.5 if (i in (0, n - 1) or kept[-1] in (0, n - 1)) else 0)
        if (i - kept[-1]) / max(1, n - 1) * plot_w >= need:
            kept.append(i)
    return kept


def _value_label(x: float, y: float, v: int, color: str, first: bool, last: bool) -> str:
    anchor = "start" if first else ("end" if last else "middle")
    dx = 2 if first else (-2 if last else 0)
    return (f'<text x="{x+dx:.1f}" y="{y-16:.1f}" text-anchor="{anchor}" font-size="16" '
            f'font-weight="700" fill="{color}" '
            f'font-family="Monocraft,PingFang SC,sans-serif">{_fmt_num(v)}</text>')


def build_trend_card_html(player: str, datasets: list, days: int | None = None,
                          head_uri: str = "") -> str:
    """趋势卡 HTML（主题同双模式战绩卡）。datasets 见 _trend_svg。"""
    global _trend_tpl_cache
    if _trend_tpl_cache is None:
        _trend_tpl_cache = _TREND_TPL_PATH.read_text(encoding="utf-8")

    first_key = datasets[0]["key"]
    th = _TREND_THEME["ar"] if first_key.startswith("arena") else _TREND_THEME["bw"]
    accent, accent2 = datasets[0]["accent"], datasets[0]["accent2"]

    n = len(datasets[0]["series"])
    d0 = datasets[0]["series"][0]["ts"][:10].replace("-", ".")
    d1 = datasets[0]["series"][-1]["ts"][:10].replace("-", ".")
    multi = len(datasets) > 1

    # 图例：多序列才需要，单序列标题已经写明指标
    if multi:
        legend = "".join(
            f'<span class="lg"><i style="background:{d["accent"]}"></i>{_esc_html(d["label"])}</span>'
            for d in datasets
        )
    else:
        legend = ""

    # 统计条：单序列 5 格；多序列每指标一格（当前 + 增长）
    if multi:
        stats = ""
        for d in datasets:
            vals = [s["val"] for s in d["series"]]
            delta = vals[-1] - vals[0]
            stats += (
                f'<div class="stat"><div class="k">{_esc_html(d["label"])} 当前</div>'
                f'<div class="v" style="color:{d["accent"]}">{_fmt_num(vals[-1])}</div>'
                f'<div class="k" style="margin-top:3px">增长 {"+" if delta > 0 else ""}{_fmt_num(delta)}</div></div>'
            )
    else:
        vals = [s["val"] for s in datasets[0]["series"]]
        first, last = vals[0], vals[-1]
        delta = last - first
        per_day = delta / max(1, len(vals) - 1)
        diffs = [vals[i] - vals[i - 1] for i in range(1, len(vals))]
        max_day = max(diffs) if diffs else 0
        stats = (
            f'<div class="stat"><div class="k">起始</div><div class="v flat">{_fmt_num(first)}</div></div>'
            f'<div class="stat"><div class="k">当前</div><div class="v">{_fmt_num(last)}</div></div>'
            f'<div class="stat"><div class="k">{"增长" if delta >= 0 else "减少"}</div>'
            f'<div class="v {"up" if delta > 0 else "flat"}">{"+" if delta > 0 else ""}{_fmt_num(delta)}</div></div>'
            f'<div class="stat"><div class="k">日均增长</div><div class="v flat">{"+" if per_day > 0 else ""}{per_day:.1f}</div></div>'
            f'<div class="stat"><div class="k">单日最高</div>'
            f'<div class="v flat">{"+" if max_day > 0 else ""}{_fmt_num(int(max_day))}</div></div>'
        )

    title = datasets[0]["label"] if not multi else " / ".join(d["label"] for d in datasets)
    note = f"每日一个采样点 · 共 {n} 个点"
    if any(d["axis"] == "r" for d in datasets):
        note += " · 两条量级差太多，右侧另起一轴"

    html = (_trend_tpl_cache
            .replace("{{WIDTH}}", str(_TREND_CARD_W))
            .replace("{{FONT_FACE}}", _trend_font_face())
            .replace("{{ACCENT2}}", accent2)
            .replace("{{ACCENT}}", accent)
            .replace("{{SHADOW}}", th["shadow"])
            .replace("{{GLOW_A}}", th["glow_a"])
            .replace("{{GLOW_B}}", th["glow_b"])
            .replace("{{CHIP_BG}}", "rgba(255,255,255,.75)")
            .replace("{{CHIP_BORDER}}", "rgba(255,255,255,.95)")
            .replace("{{PLAYER}}", _esc_html(player))
            .replace("{{HEAD}}", (
                f'<img src="{head_uri}" alt="">' if head_uri
                else _esc_html((player or "?")[0].upper())
            ))
            .replace("{{MODE_LABEL}}", "竞技场" if first_key.startswith("arena") else "起床战争")
            .replace("{{METRIC_LABEL}}", _esc_html(title))
            .replace("{{DAYS}}", str(n))
            .replace("{{RANGE_LABEL}}", f"近 {days} 天" if days else "全量")
            .replace("{{SPAN}}", f"{d0} → {d1}")
            .replace("{{NOTE}}", note)
            .replace("{{LEGEND}}", legend)
            .replace("{{STATS}}", stats)
            .replace("{{SVG}}", _trend_svg(datasets)))

    from services.card_base import inject_stamp
    import time as _t
    return inject_stamp(html, _t.time() * 1000)


def _esc_html(s) -> str:
    import html as _h
    return _h.escape(str(s), quote=True)


def _assign_axes(datasets: list) -> None:
    """按「量级」分簇决定左右轴。就地改 axis 字段。

    只看最大最小比值不够：三条线里两条同量级、一条小一大截时，
    若把「最大的那条」单独放左轴，另一条同级的会被推去右轴，
    结果两条大值挤在一条轴上、小值那侧轴被撑到 0~8000，压成贴底的直线
    （2026-10-31 实测：死亡 6300 / 击杀 4267 / 胜场 280 三条线）。

    正确做法：先把量级相近（最大值差 10 倍以内）的指标并成一簇，
    簇内共用一条轴；最大那簇放左轴，其余各簇放右轴。
    """
    for d in datasets:
        d["axis"] = "l"
    if len(datasets) < 2:
        return

    highs = [max(s["val"] for s in d["series"]) for d in datasets]
    order = sorted(range(len(datasets)), key=lambda i: -highs[i])

    clusters: list[list[int]] = []
    for i in order:
        for c in clusters:
            if highs[i] * 10 >= max(highs[j] for j in c):   # 同量级（差不到一个数量级）
                c.append(i)
                break
        else:
            clusters.append([i])

    clusters.sort(key=lambda c: -max(highs[j] for j in c))
    for c in clusters[1:]:
        for i in c:
            datasets[i]["axis"] = "r"


async def generate_trend_card(player: str, metrics, days: int | None = None) -> str | None:
    """趋势卡主入口。metrics 传字符串或列表（最多 3 条线）。

    数据不足 2 天的指标会被静默丢掉；一条都画不了时返回 None，由调用方回文案。
    """
    if isinstance(metrics, str):
        metrics = [metrics]
    metrics = [m for m in metrics if m][:3]

    datasets = []
    for i, m in enumerate(metrics):
        series = get_player_trend(player, m, days)
        if len(series) < 2:
            continue
        datasets.append({
            "key": m,
            "label": _TREND_LABELS.get(m, m),
            "series": series,
            **_TREND_PALETTE[i % len(_TREND_PALETTE)],
            "axis": "l",
        })
    if not datasets:
        return None
    _assign_axes(datasets)

    try:
        import time as _t
        from modules.changelog import render_card_to_image
        head_uri = ""
        try:
            from services.wdsj_api import fetch_player_head_data_uri
            head_uri = await fetch_player_head_data_uri(player)
        except Exception as e:
            logger.warning("趋势卡头像获取失败（回退首字母）: %s", e)
        html = build_trend_card_html(player, datasets, days, head_uri=head_uri)
        out = await render_card_to_image(
            html,
            output_filename=f"trend_{player}_{'_'.join(d['key'] for d in datasets)}_{int(_t.time())}.png",
            width=_TREND_CARD_W + 40,
        )
        if out:
            return out
        logger.warning("趋势卡截图返回空，回退 matplotlib")
    except Exception as e:
        logger.error("趋势卡 HTML 渲染失败，回退 matplotlib: %s", e, exc_info=True)
    return generate_trend_chart(player, datasets[0]["key"], days)

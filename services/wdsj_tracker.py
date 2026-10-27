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


def _trend_svg(series: list, accent: str, accent2: str) -> str:
    """把每日采样画成 SVG：面积渐变 + 折线 + 采样点 + 网格。

    点少的日期全部标注数值；点多时只标首尾与极值，避免糊成一片。
    """
    W, H = 1640, 520
    L, R, T, B = 70, 32, 24, 54
    pw, ph = W - L - R, H - T - B

    n = len(series)
    vals = [s["val"] for s in series]
    labels = [s["ts"][5:10].replace("-", "/") for s in series]   # MM/DD

    vmin, vmax = min(vals), max(vals)
    if vmax == vmin:
        # 5 天都卡在同一个值时，折线会贴在底边上、刻度还会出现 48/48 这种重复
        # （2026-10-27 实测 sb91786713lll 类玩家）。上下各留 5%（至少 1）居中。
        _d = max(1, int(round(abs(vmax) * 0.05)))
        vmin, vmax = vmax - _d, vmax + _d
    pad = (vmax - vmin) * 0.10
    y0, y1 = vmin - pad, vmax + pad

    # y 轴取「好看的整数刻度」：步长落在 1/2/2.5/5 × 10^n，避免 7412/6529 这种碎值
    import math as _m
    span = max(1.0, y1 - y0)
    raw_step = span / 4
    mag = 10 ** _m.floor(_m.log10(raw_step)) if raw_step > 0 else 1
    step_y = mag
    for _mul in (1, 2, 2.5, 5, 10):
        if raw_step <= _mul * mag:
            step_y = _mul * mag
            break
    # 直接用「数据极值」对齐刻度，不再额外留白——否则 4267 会被拉到 3000 起，
    # 图下方空出一大块。留白由刻度本身给（最多一个 step）。
    step_y = max(1, round(step_y))        # 计数指标只用整数刻度
    y0 = _m.floor(vmin / step_y) * step_y
    y1 = _m.ceil(vmax / step_y) * step_y
    if y1 - y0 < step_y:
        y1 = y0 + step_y
    while (y1 - y0) / step_y > 6:          # 最多 6 条网格线，避免刻度刷屏
        step_y *= 2
        y0 = _m.floor(vmin / step_y) * step_y
        y1 = _m.ceil(vmax / step_y) * step_y
    n_ticks = max(2, int(round((y1 - y0) / step_y)) + 1)

    def X(i: int) -> float:
        return L + (pw / 2 if n == 1 else pw * i / (n - 1))

    def Y(v: float) -> float:
        return T + ph - (v - y0) / (y1 - y0) * ph

    # ── 网格与 y 轴刻度（5 条）──
    grid, ticks = [], []
    for k in range(n_ticks):
        v = y0 + step_y * k
        y = Y(v)
        grid.append(f'<line x1="{L}" y1="{y:.1f}" x2="{W-R}" y2="{y:.1f}" '
                    f'stroke="rgba(120,100,80,.13)" stroke-width="1" stroke-dasharray="4 6"/>')
        ticks.append(f'<text x="{L-14}" y="{y+4:.1f}" text-anchor="end" '
                     f'font-size="15" fill="#a89e96" font-family="Monocraft,PingFang SC,sans-serif">'
                     f'{_fmt_num(int(round(v)))}</text>')

    # ── 折线与面积 ──
    pts = [(X(i), Y(v)) for i, v in enumerate(vals)]
    line = "M " + " L ".join(f"{x:.1f} {y:.1f}" for x, y in pts)
    area = (f'M {pts[0][0]:.1f} {T+ph:.1f} L ' +
            " L ".join(f"{x:.1f} {y:.1f}" for x, y in pts) +
            f' L {pts[-1][0]:.1f} {T+ph:.1f} Z')

    # ── 采样点 ──
    dots = []
    dr, dw = (5.6, 3.0) if n <= 30 else ((4.0, 2.4) if n <= 60 else (2.8, 1.8))
    for i, (x, y) in enumerate(pts):
        dots.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{dr}" fill="#fff" '
                    f'stroke="{accent}" stroke-width="{dw}"/>')

    # ── 数值标注：按「标注宽度 + 间距」自适应，整条线最多约 15 个 ──
    #   Monocraft 的数字近似等宽，按 0.62em 估字宽；每格至少留 (字宽 + 10px)，
    #   否则 101 个点的数字会连成一串（2026-10-27 用户反馈）。
    _FS, _GAP, _CAP = 16, 10, 15
    _max_chars = max(len(_fmt_num(v)) for v in vals)
    _label_w = _max_chars * _FS * 0.62
    _fit = int(pw // (_label_w + _GAP)) + 1          # 宽度允许的最多个数
    _k = max(2, min(_CAP, _fit, n))
    if _k >= n:
        ann_idx = list(range(n))
    else:
        ann_idx = sorted({round(i * (n - 1) / (_k - 1)) for i in range(_k)})
    # 首尾标注是向内对齐的（anchor start/end），实际占位比居中的宽半格，
    # 等距取样后会出现「4267 和 4625 贴在一起」——按真实横向距离再筛一遍
    _kept: list[int] = []
    for _i in ann_idx:
        if not _kept:
            _kept.append(_i)
            continue
        _need = _label_w + _GAP
        if _i in (0, n - 1) or _kept[-1] in (0, n - 1):
            _need += _label_w * 0.5
        if X(_i) - X(_kept[-1]) >= _need:
            _kept.append(_i)
    ann_idx = _kept
    anns = []
    for i in ann_idx:
        x, y = pts[i]
        # 首尾贴边：顶到画布边缘会被裁或压住 y 轴刻度，改成向内对齐
        # ⚠️ anchor 必须在下面算 dy 之前定，否则引用了还没赋值的名字（会静默回退兜底）
        anchor, dx = "middle", 0
        if i == 0:
            anchor, dx = "start", 2
        elif i == n - 1:
            anchor, dx = "end", -2
        # 标注高度按「自身横向覆盖范围内折线的最高点」动态抬升，
        # 否则上升段会把文字穿过（2026-10-27：首点 4267 的末位被数据点盖住）。
        _x_lo, _x_hi = x - _label_w / 2, x + _label_w / 2
        if anchor == "start":
            _x_lo, _x_hi = x - 2, x + _label_w
        elif anchor == "end":
            _x_lo, _x_hi = x - _label_w, x + 2
        _ys = [py for (px, py) in pts if _x_lo <= px <= _x_hi]
        _top = min(_ys) if _ys else y             # 像素 y 越小 = 位置越高
        dy = min(-16, _top - y - 14)              # 至少高出该段最高点 14px
        dy = max(dy, -62)                         # 别飘得太远
        if (y - T) < 30:
            dy = 22                               # 贴画布顶端时改放下方，避免被裁
        anns.append(f'<text x="{x+dx:.1f}" y="{y+dy:.1f}" text-anchor="{anchor}" font-size="16" '
                    f'font-weight="700" fill="{accent}" '
                    f'font-family="Monocraft,PingFang SC,sans-serif">{_fmt_num(vals[i])}</text>')

    # ── x 轴日期：同款自适应（日期固定 5 字符 MM/DD，比数值标注宽，上限 10 个）──
    _DFS, _DGAP, _DCAP = 15, 12, 10
    _dw = 5 * _DFS * 0.62
    _dfit = int(pw // (_dw + _DGAP)) + 1
    _dk = max(2, min(_DCAP, _dfit, n))
    if _dk >= n:
        x_idx = list(range(n))
    else:
        x_idx = sorted({round(i * (n - 1) / (_dk - 1)) for i in range(_dk)})
    xlab = []
    for i in x_idx:
        # 首尾日期同样向内对齐，否则贴边那颗会被面板内边切掉（2026-10-27 实测 10/23 被裁）
        _a = "start" if i == 0 else ("end" if i == n - 1 else "middle")
        xlab.append(f'<text x="{X(i):.1f}" y="{H-18}" text-anchor="{_a}" font-size="15" '
                    f'fill="#a89e96" font-family="Monocraft,PingFang SC,sans-serif">{labels[i]}</text>')

    return f'''<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="ag" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="{accent}" stop-opacity=".26"/>
      <stop offset="72%" stop-color="{accent}" stop-opacity=".05"/>
      <stop offset="100%" stop-color="{accent}" stop-opacity="0"/>
    </linearGradient>
    <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
      <feGaussianBlur stdDeviation="7" result="b"/>
      <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
  </defs>
  {''.join(grid)}
  <path d="{area}" fill="url(#ag)"/>
  <path d="{line}" fill="none" stroke="{accent2}" stroke-width="9" stroke-opacity=".35"
        stroke-linecap="round" stroke-linejoin="round" filter="url(#glow)"/>
  <path d="{line}" fill="none" stroke="{accent}" stroke-width="4.2"
        stroke-linecap="round" stroke-linejoin="round"/>
  {''.join(dots)}
  {''.join(anns)}
  {''.join(xlab)}
  {''.join(ticks)}
</svg>'''


def build_trend_card_html(player: str, metric: str, metric_label: str,
                          series: list, days: int | None = None) -> str:
    """趋势卡 HTML（主题同双模式战绩卡）。"""
    global _trend_tpl_cache
    if _trend_tpl_cache is None:
        _trend_tpl_cache = _TREND_TPL_PATH.read_text(encoding="utf-8")

    th = _TREND_THEME["ar"] if metric.startswith("arena") else _TREND_THEME["bw"]
    accent, accent2 = th["accent"], th["accent2"]
    mode_label = "竞技场" if metric.startswith("arena") else "起床战争"

    vals = [s["val"] for s in series]
    n = len(vals)
    first, last = vals[0], vals[-1]
    delta = last - first
    span_days = max(1, n - 1)
    per_day = delta / span_days
    diffs = [vals[i] - vals[i - 1] for i in range(1, n)]
    max_day = max(diffs) if diffs else 0

    d0 = series[0]["ts"][:10].replace("-", ".")
    d1 = series[-1]["ts"][:10].replace("-", ".")

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
            .replace("{{INITIAL}}", _esc_html((player or "?")[0].upper()))
            .replace("{{MODE_LABEL}}", mode_label)
            .replace("{{METRIC_LABEL}}", _esc_html(metric_label))
            .replace("{{DAYS}}", str(n))
            .replace("{{RANGE_LABEL}}", f"近 {days} 天" if days else "全量")
            .replace("{{SPAN}}", f"{d0} → {d1}")
            .replace("{{NOTE}}", f"每日一个采样点 · 共 {n} 个点 · 累计值走势")
            .replace("{{SVG}}", _trend_svg(series, accent, accent2))
            .replace("{{V_FIRST}}", _fmt_num(first))
            .replace("{{V_LAST}}", _fmt_num(last))
            .replace("{{DELTA_LABEL}}", "增长" if delta >= 0 else "减少")
            .replace("{{DELTA_CLASS}}", "up" if delta > 0 else "flat")
            .replace("{{V_DELTA}}", ("+" if delta > 0 else "") + _fmt_num(delta))
            .replace("{{V_PERDAY}}", ("+" if per_day > 0 else "") + f"{per_day:.1f}")
            .replace("{{V_MAXDAY}}", ("+" if max_day > 0 else "") + _fmt_num(int(max_day))))

    from services.card_base import inject_stamp
    import time as _t
    return inject_stamp(html, _t.time() * 1000)


def _esc_html(s) -> str:
    import html as _h
    return _h.escape(str(s), quote=True)


async def generate_trend_card(player: str, metric: str, days: int | None = None) -> str | None:
    """趋势卡主入口：HTML → Playwright 截图。失败时回退 matplotlib 版。"""
    series = get_player_trend(player, metric, days)
    if not series:
        return None
    label = metric
    for mid, tid, fn, lb in _TREND_METRICS:
        if mid == metric:
            label = lb
            break
    try:
        import time as _t
        from modules.changelog import render_card_to_image
        html = build_trend_card_html(player, metric, label, series, days)
        out = await render_card_to_image(
            html,
            output_filename=f"trend_{player}_{metric}_{int(_t.time())}.png",
            width=_TREND_CARD_W + 40,
        )
        if out:
            return out
        logger.warning("趋势卡截图返回空，回退 matplotlib")
    except Exception as e:
        logger.error("趋势卡 HTML 渲染失败，回退 matplotlib: %s", e, exc_info=True)
    return generate_trend_chart(player, metric, days)

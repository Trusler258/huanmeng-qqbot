"""
Token 消耗追踪
- 每次 LLM 调用记录 prompt/completion tokens + 缓存命中
- 每日汇总消耗金额（DeepSeek 价格）
- /~cost 查看每日/累计消耗
- /~tokens <文本> 计算 token 数和预估费用
- /~cache 查看缓存命中率趋势 + 排查命中率低的原因
"""

from __future__ import annotations

import json
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from core.logger import get_logger

logger = get_logger("token")

# ══════════════════════════════════════════════════════════════
#  DeepSeek 计价（v2.3.35 重做：按时段 × 按模型 × 按生效日期）
#
#  为什么原来不准：
#    1. 只写了「空闲时段」一档价，没管高峰 —— 高峰是空闲的 2 倍
#    2. PRICE_OUTPUT 还是涨价前的 2 元，实际 4 元（少算一半）
#    3. 所有模型一律按 flash 计价，实际库里混着 4 个模型
#    4. 价格是 2026-09-10 才改的，历史记录用新价算会虚高
#
#  峰谷时段（北京时间）：周一至周五 9:00-12:00、14:00-18:00 为高峰，
#  其余（含周末全天、午休、夜间、清晨）为空闲，空闲价 = 高峰价的一半。
# ══════════════════════════════════════════════════════════════

# 高峰时段（起, 止，左闭右开）；不在其中即空闲
_PEAK_WINDOWS = (((9, 0), (12, 0)), ((14, 0), (18, 0)))
_PEAK_WEEKDAYS = {0, 1, 2, 3, 4}        # 周一=0，周末全天算空闲

# 价格表：[ (生效起始时间, 命中价(峰,闲), 未命中价(峰,闲), 输出价(峰,闲)) ]
# 单位：元/百万 tokens。倒序匹配第一个 <= 记录时间的条目。
# 平价时段把「峰」「闲」写同一个值即可。
_FLASH_TABLE = [
    # 2026-09-10 12:00 起（当前）：降价至 0.02/1/4
    (datetime(2026, 9, 10, 12, 0), (0.04, 0.02), (2.0, 1.0), (8.0, 4.0)),
    # 2026-08-17 00:00 起：全面上调 + 启用峰谷定价
    (datetime(2026, 8, 17, 0, 0), (0.10, 0.05), (3.0, 1.5), (9.0, 4.5)),
    # 更早：平价（无峰谷）
    (datetime(2000, 1, 1), (0.02, 0.02), (1.0, 1.0), (2.0, 2.0)),
]

_PRO_TABLE = [
    # 2026-09-14 12:00 起 V4-Pro 请求被路由到 Flash 并按 Flash 计费
    (datetime(2026, 9, 14, 12, 0), (0.04, 0.02), (2.0, 1.0), (8.0, 4.0)),
    (datetime(2026, 8, 17, 0, 0), (0.30, 0.15), (9.0, 4.5), (27.0, 13.5)),
    (datetime(2000, 1, 1), (0.025, 0.025), (3.0, 3.0), (6.0, 6.0)),
]

_TABLES = {"flash": _FLASH_TABLE, "pro": _PRO_TABLE}

# 模型名 → 计价族。
# 官方说明：deepseek-chat / deepseek-reasoner 两个名字已弃用，
# 「分别对应 DeepSeek-V4-Flash 的非思考与思考模式」→ 都按 flash 族计价。
_MODEL_FAMILY = {
    "deepseek-flash": "flash",
    "deepseek-v4-flash": "flash",
    "deepseek-v4-flash-vision-exp": "flash",
    "deepseek-v4.1-flash": "flash",
    "deepseek-chat": "flash",
    "deepseek-reasoner": "flash",
    "deepseek-v4-pro": "pro",
    "deepseek-pro": "pro",
}


def is_peak(dt: datetime) -> bool:
    """判断某时刻是否处于高峰时段（北京时间，周一至周五 9-12、14-18）"""
    if dt.weekday() not in _PEAK_WEEKDAYS:
        return False
    hm = dt.hour * 60 + dt.minute
    for (sh, sm), (eh, em) in _PEAK_WINDOWS:
        if sh * 60 + sm <= hm < eh * 60 + em:
            return True
    return False


def price_for(model: str, dt: datetime):
    """取某模型在某时刻的单价，返回 (命中, 未命中, 输出) 或 None（无法计价）。

    无法计价的情形：非 DeepSeek 模型（如 SiliconFlow 上的 Qwen）——
    返回 None 而不是硬套 flash 价格，避免算出一个看起来精确但错误的值。
    """
    fam = _MODEL_FAMILY.get(str(model or "").strip().lower())
    if not fam:
        return None
    peak = is_peak(dt)
    for since, hit, miss, out in _TABLES[fam]:      # 表内已按时间倒序
        if dt >= since:
            return (hit[0] if peak else hit[1],
                    miss[0] if peak else miss[1],
                    out[0] if peak else out[1])
    return None


def cost_of_record(rec: dict):
    """单条记录的（费用, 是否高峰）。无法计价时费用为 None。"""
    try:
        dt = datetime.fromisoformat(str(rec.get("time") or ""))
    except ValueError:
        return None, False
    p = price_for(rec.get("model"), dt)
    if p is None:
        return None, False
    hit_price, miss_price, out_price = p
    prompt = int(rec.get("prompt_tokens") or 0)
    cached = int(rec.get("cached_tokens") or 0)
    cached = min(cached, prompt)          # 防脏数据：命中数不可能超过输入
    out = int(rec.get("completion_tokens") or 0)
    cost = (cached * hit_price + (prompt - cached) * miss_price + out * out_price) / 1_000_000
    return cost, is_peak(dt)


# 兼容旧引用（/~tokens 等）：给的是当前空闲时段单价
PRICE_CACHE_HIT = _FLASH_TABLE[0][1][1]      # 0.02
PRICE_CACHE_MISS = _FLASH_TABLE[0][2][1]     # 1.0
PRICE_OUTPUT = _FLASH_TABLE[0][3][1]         # 4.0

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
TRACKER_FILE = "token_usage.jsonl"

# Tokenizer（DeepSeek V3，懒加载）
_TOKENIZER = None


def _get_tokenizer():
    global _TOKENIZER
    if _TOKENIZER is None:
        # 优先 bot 内置目录，回退桌面
        for d in [
            DATA_DIR / "tokenizer",
            Path(os.path.expanduser("~/Desktop/deepseek_v3_tokenizer")),
        ]:
            if (d / "tokenizer.json").exists():
                try:
                    import transformers
                    _TOKENIZER = transformers.AutoTokenizer.from_pretrained(
                        str(d), trust_remote_code=True
                    )
                    logger.info("Tokenizer 已加载: %s", d)
                    return _TOKENIZER
                except Exception as e:
                    logger.warning("Tokenizer 加载失败 (%s): %s", d, e)
        return None
    return _TOKENIZER


def _token_count(text: str) -> int | None:
    tok = _get_tokenizer()
    if tok is None:
        return None
    try:
        return len(tok.encode(text))
    except Exception:
        return None


def _today_file() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR / f"token_{date.today().strftime('%Y-%m')}.jsonl"


def record_usage(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int = 0,
    cache_write_tokens: int = 0,
    scene: str = "",
):
    """记录一次 LLM 调用的 token 消耗

    cached_tokens: DeepSeek 返回的 prompt_tokens_details.cached_tokens（缓存命中）
    cache_write_tokens: prompt_tokens_details.cache_write_tokens（写入缓存）
    scene: 调用场景标识（reply/judge/search/tools 等），用于命中率归因
    """
    entry = {
        "time": datetime.now().isoformat(),
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cached_tokens": cached_tokens,
        "cache_write_tokens": cache_write_tokens,
        "scene": scene,
    }
    try:
        with open(_today_file(), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _load_range(from_date: str, to_date: str | None = None) -> list[dict]:
    """加载指定日期范围的记录。

    v2.3.35: 改成按文件 glob + 过滤。
    原来是「从 start 逐月 +1 直到 end」——累计查询时从 2000-01 开始空转
    三百多次文件判断。月份文件名本就是天然分片，直接筛出来更清楚也更快。
    """
    lo, hi = from_date, (to_date or from_date)
    out = []
    for f in sorted(DATA_DIR.glob("token_*.jsonl")):
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                d = str(r.get("time") or "")[:10]
                if lo <= d <= hi:
                    out.append(r)
        except Exception:
            continue
    return out


def _agg(records: list[dict]) -> dict:
    """汇总一组记录。

    ★ token 与费用必须**同口径**：只累计能计价的记录。
      第一版把未计价模型（Qwen）的 token 也加进 prompt/completion，
      于是「67.4M tokens = ¥29.4」这句里的 token 与钱对应不上
      （差了 76 万 token）。它们单独报，不混进主数字。
    """
    acc = {
        "prompt": 0, "completion": 0, "cached": 0, "calls": 0,
        "cost": 0.0, "peak_cost": 0.0, "idle_cost": 0.0,
        "peak_calls": 0,
        "unpriced_calls": 0, "unpriced_tokens": 0,
    }
    for r in records:
        prompt = int(r.get("prompt_tokens") or 0)
        out = int(r.get("completion_tokens") or 0)
        c, peak = cost_of_record(r)
        if c is None:
            acc["unpriced_calls"] += 1
            acc["unpriced_tokens"] += prompt + out
            continue
        acc["calls"] += 1
        acc["prompt"] += prompt
        acc["completion"] += out
        acc["cached"] += min(int(r.get("cached_tokens") or 0), prompt)
        acc["cost"] += c
        if peak:
            acc["peak_cost"] += c
            acc["peak_calls"] += 1
        else:
            acc["idle_cost"] += c
    acc["total_calls"] = acc["calls"] + acc["unpriced_calls"]
    return acc


def _by_model(records: list[dict]) -> list[dict]:
    """按模型分组统计（库里混着 flash / chat / Qwen 等多个模型，分开看才有意义）"""
    groups: dict[str, list] = {}
    for r in records:
        groups.setdefault(str(r.get("model") or "(未知)"), []).append(r)
    rows = []
    for m, rs in groups.items():
        a = _agg(rs)
        rows.append({
            "model": m,
            "calls": a["total_calls"],
            "tokens": a["prompt"] + a["completion"] + a["unpriced_tokens"],
            "cost": a["cost"],
            "priced": a["calls"] > 0,          # 有可计价记录
            "unpriced_calls": a["unpriced_calls"],
        })
    rows.sort(key=lambda x: -x["tokens"])
    return rows


def calc_cost(today_only: bool = False) -> dict:
    """计算消耗概览。

    返回 today / total 两组，字段含义：
      prompt/completion/cached/calls/cost  —— 保持旧字段，供 /~status 复用
      peak_cost / idle_cost / peak_calls   —— 峰谷拆分（v2.3.35）
      unpriced_calls / unpriced_tokens     —— 无法计价的调用（非 DeepSeek 模型）
      by_model                             —— 按模型分组
    """
    today_str = date.today().strftime("%Y-%m-%d")
    records = _load_range(today_str) if today_only else _load_range("2000-01-01", today_str)
    today_records = [r for r in records if str(r.get("time") or "")[:10] == today_str]

    res = {"today": _agg(today_records), "total": _agg(records)}
    res["total"]["by_model"] = _by_model(records)
    res["peak_now"] = is_peak(datetime.now())
    return res


def _fmt_block(label: str, a: dict, money_digits: int = 4) -> list[str]:
    """渲染一组统计（今日 / 累计共用）"""
    prompt = a["prompt"]
    if not a["calls"]:
        out = [f"  {label}: 无可计价的调用"]
        if a["unpriced_calls"]:
            out.append(f"    ⚠ 另有 {a['unpriced_calls']} 次未计价（非 DeepSeek 模型）")
        return out
    hit_rate = (a["cached"] / prompt * 100) if prompt else 0.0
    lines = [f"  {label}: {a['calls']}次 {prompt + a['completion']:,} tokens = ¥{a['cost']:.{money_digits}f}"]
    lines.append(f"    输入 {prompt:,}(缓存{a['cached']:,} 命中率{hit_rate:.0f}%) + 输出 {a['completion']:,}")
    if a["cost"] > 0:
        idle_calls = a["calls"] - a["peak_calls"]
        fmt = f"{{:.{money_digits}f}}"
        lines.append(
            "    峰谷: 高峰 %d次 %s / 空闲 %d次 %s"
            % (a["peak_calls"], fmt.format(a["peak_cost"]),
               idle_calls, fmt.format(a["idle_cost"])))
    if a["unpriced_calls"]:
        lines.append(f"    ⚠ 另有 {a['unpriced_calls']} 次未计价（{a['unpriced_tokens']:,} tokens，非 DeepSeek 模型）")
    return lines


async def cmd_cost(args, user_id, group_id, sender_name, is_group, bot_qq):
    """查看 Token 消耗 /~cost"""
    data = calc_cost(today_only=False)
    t, total = data["today"], data["total"]

    lines = ["【Token 消耗统计】"]
    lines += _fmt_block("今日", t)
    lines += _fmt_block("累计", total, money_digits=2)

    # 按模型拆分：库里混着多个模型，只给一个总数看不出钱花在哪
    rows = [r for r in (total.get("by_model") or []) if r["tokens"] > 0]
    if len(rows) > 1:
        lines.append("  ── 按模型 ──")
        for r in rows[:6]:
            money = f"¥{r['cost']:.2f}" if r["priced"] else "未计价"
            lines.append(f"    {r['model'][:26]:<26} {r['calls']:>6}次 {r['tokens']:>10,} tk  {money}")

    lines.append("  计费: %s · flash 空闲 0.02/1/4 高峰 0.04/2/8 元每百万"
                 % ("高峰" if data.get("peak_now") else "空闲"))
    return "\n".join(lines)


async def cmd_tokens(args, user_id, group_id, sender_name, is_group, bot_qq):
    """计算 token 数 /~tokens <文本>"""
    if not args:
        return "用法: /~tokens <文本>\n计算文本的 token 数和预估费用喵~"

    text = " ".join(args)
    count = _token_count(text)
    if count is None:
        return "Tokenizer 加载失败，请检查 ~/Desktop/deepseek_v3_tokenizer/ 目录喵~"

    # v2.3.35: 峰谷两档都列出来（当前时段标注），输入与输出分开算 ——
    #          实际计费里输出比输入贵得多，只报一个数会低估
    now = datetime.now()
    peak_now = is_peak(now)
    rows = []
    for label, peak in (("空闲", False), ("高峰", True)):
        probe = now if peak == peak_now else _shift_to_other_band(now, peak)
        p = price_for("deepseek-flash", probe)
        if p is None:
            continue
        _, miss_price, out_price = p
        in_cost = count / 1_000_000 * miss_price
        out_cost = count / 1_000_000 * out_price
        mark = "  ← 当前时段" if peak == peak_now else ""
        rows.append(f"    {label}: 输入 ¥{in_cost:.6f} / 输出(同量) ¥{out_cost:.6f}{mark}")

    lines = [
        f"文本: {text[:60]}{'...' if len(text) > 60 else ''}",
        f"Token: {count}（按 deepseek-flash 计）",
        "  预估费用:",
        *rows,
        "  注: 缓存命中的输入只要 0.02 元/百万，实际通常远低于上面的未命中价",
    ]
    return "\n".join(lines)


def _shift_to_other_band(dt: datetime, want_peak: bool) -> datetime:
    """把一个时间挪到目标峰谷档，用于报价对照（不改真实时间语义）"""
    if want_peak:
        # 找一个工作日的高峰时刻
        d = dt.date()
        for i in range(7):
            cand = datetime.combine(d + timedelta(days=i), datetime.min.time()).replace(hour=10)
            if is_peak(cand):
                return cand
    else:
        d = dt.date()
        for i in range(7):
            cand = datetime.combine(d + timedelta(days=i), datetime.min.time()).replace(hour=3)
            if not is_peak(cand):
                return cand
    return dt


# ── 缓存命中率分析（/~cache）────────────────────────────

def _hit_rate(prompt: int, cached: int) -> float:
    """缓存命中率 = cached / prompt"""
    return cached / prompt * 100 if prompt else 0.0


def analyze_cache(days: int = 7) -> dict:
    """分析缓存命中率，返回按天/按小时/按场景/按大小的统计数据"""
    today = date.today()
    start = (today - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    records = _load_range(start, today.strftime("%Y-%m-%d"))

    # 按天聚合
    daily: dict[str, dict] = {}
    # 按小时聚合（最近一天）
    hourly: dict[str, dict] = {}
    # 按场景聚合
    by_scene: dict[str, dict] = {}
    # 按 prompt 大小分桶（区分主回复 vs judge/cheap 小调用）
    by_size: dict[str, dict] = {
        "主回复(>2000)": {"calls": 0, "prompt": 0, "cached": 0},
        "中(500-2000)": {"calls": 0, "prompt": 0, "cached": 0},
        "小(<500)": {"calls": 0, "prompt": 0, "cached": 0},
    }
    today_str = today.strftime("%Y-%m-%d")

    for r in records:
        d = r["time"][:10]
        h = r["time"][11:13]
        scene = r.get("scene", "") or "unknown"
        pt = r.get("prompt_tokens", 0)
        ct = r.get("cached_tokens", 0)
        daily.setdefault(d, {"calls": 0, "prompt": 0, "cached": 0, "cwrite": 0})
        daily[d]["calls"] += 1
        daily[d]["prompt"] += pt
        daily[d]["cached"] += ct
        daily[d]["cwrite"] += r.get("cache_write_tokens", 0)

        hourly.setdefault(h, {"calls": 0, "prompt": 0, "cached": 0})
        hourly[h]["calls"] += 1
        hourly[h]["prompt"] += pt
        hourly[h]["cached"] += ct

        by_scene.setdefault(scene, {"calls": 0, "prompt": 0, "cached": 0})
        by_scene[scene]["calls"] += 1
        by_scene[scene]["prompt"] += pt
        by_scene[scene]["cached"] += ct

        bucket = "主回复(>2000)" if pt > 2000 else ("中(500-2000)" if pt >= 500 else "小(<500)")
        by_size[bucket]["calls"] += 1
        by_size[bucket]["prompt"] += pt
        by_size[bucket]["cached"] += ct

    # 组装结果
    daily_out = [
        {
            "date": d,
            "calls": v["calls"],
            "prompt": v["prompt"],
            "cached": v["cached"],
            "cwrite": v["cwrite"],
            "rate": _hit_rate(v["prompt"], v["cached"]),
        }
        for d, v in sorted(daily.items())
    ]
    hourly_out = [
        {"hour": h, "calls": v["calls"], "prompt": v["prompt"], "cached": v["cached"],
         "rate": _hit_rate(v["prompt"], v["cached"])}
        for h, v in sorted(hourly.items())
    ]
    scene_out = [
        {"scene": s, "calls": v["calls"], "prompt": v["prompt"], "cached": v["cached"],
         "rate": _hit_rate(v["prompt"], v["cached"])}
        for s, v in sorted(by_scene.items(), key=lambda x: -x[1]["prompt"])
    ]
    size_out = [
        {"bucket": s, "calls": v["calls"], "prompt": v["prompt"], "cached": v["cached"],
         "rate": _hit_rate(v["prompt"], v["cached"])}
        for s, v in by_size.items()
    ]

    return {"daily": daily_out, "hourly": hourly_out, "by_scene": scene_out, "by_size": size_out, "today": today_str}


def _fmt_rate(rate: float) -> str:
    """命中率着色提示（纯文本用符号标记）"""
    if rate >= 70:
        return f"{rate:.1f}% ✓"
    if rate >= 40:
        return f"{rate:.1f}% ~"
    return f"{rate:.1f}% ✗"


async def cmd_cache(args, user_id, group_id, sender_name, is_group, bot_qq):
    """查看 Token 缓存命中率趋势（默认近 7 天）"""
    try:
        days = int(args[0]) if args else 7
        days = max(1, min(days, 30))
    except ValueError:
        days = 7

    data = analyze_cache(days)
    lines = [f"【缓存命中率分析】近{days}天"]

    # 按天
    lines.append("按天:")
    for d in data["daily"]:
        flag = " ← 今天" if d["date"] == data["today"] else ""
        lines.append(
            f"  {d['date']}: {d['calls']:>3}次 输入{d['prompt']:>7,} 缓存{d['cached']:>7,} "
            f"命中率{_fmt_rate(d['rate'])}{flag}"
        )

    # 按 prompt 大小（关键：区分主回复 vs judge）
    lines.append("按输入大小(关键):")
    for s in data["by_size"]:
        if s["calls"]:
            lines.append(
                f"  {s['bucket']:<12}: {s['calls']:>3}次 输入{s['prompt']:>7,} "
                f"命中率{_fmt_rate(s['rate'])}"
            )

    # 按场景（如果有多场景）
    scenes = data["by_scene"]
    if len(scenes) > 1:
        lines.append("按场景:")
        for s in scenes[:6]:
            lines.append(
                f"  {s['scene'][:12]:<12}: {s['calls']:>3}次 输入{s['prompt']:>7,} "
                f"命中率{_fmt_rate(s['rate'])}"
            )

    # 最近一天按小时（帮助定位低谷时段）
    hourly = data["hourly"]
    if hourly:
        lines.append("最近一天按小时(低谷段):")
        low = [h for h in hourly if h["rate"] < 40 and h["calls"] >= 1]
        if low:
            for h in low[:8]:
                lines.append(f"  {h['hour']}:00  {h['calls']}次 命中率{_fmt_rate(h['rate'])}")
        else:
            lines.append("  无明显低谷时段")

    # 结论：用主回复命中率（>2000 token）判断真实缓存健康度
    main_bucket = next((s for s in data["by_size"] if s["bucket"] == "主回复(>2000)"), None)
    main_rate = main_bucket["rate"] if main_bucket and main_bucket["calls"] else None
    today_rate = next((d["rate"] for d in data["daily"] if d["date"] == data["today"]), 0)
    lines.append("结论:")
    if main_rate is not None:
        if main_rate >= 70:
            lines.append(f"  主回复命中率 {main_rate:.1f}% — 缓存健康 ✓")
        elif main_rate >= 40:
            lines.append(f"  主回复命中率 {main_rate:.1f}% — 偏低，注意 system 是否频繁变化")
        else:
            lines.append(f"  主回复命中率 {main_rate:.1f}% — 低，system prompt 变化或调用不连续")
        lines.append(f"  总体命中率 {today_rate:.1f}%（受 judge 小调用稀释，仅供参考）")
    else:
        if today_rate >= 70:
            lines.append(f"  今日命中率 {today_rate:.1f}% 正常 ✓")
        else:
            lines.append(f"  今日命中率 {today_rate:.1f}% — 主回复调用少，多为 judge 小调用拉低")
            lines.append("  建议: ①保持 system prompt 稳定 ②群活跃时前缀缓存自然累积")

    return "\n".join(lines)

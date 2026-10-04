"""
洛花星雨 Nexus 战绩查询 API 封装 v2
- 2026-08-27 爬取更新: 模板 13->16, 玩家标识支持 name/nick/uid/uuid, 新增 player-heads 头像端点
"""
from __future__ import annotations

import asyncio
import httpx
import urllib.parse
from typing import Optional

from core.logger import get_logger

logger = get_logger("wdsj")

# 最近一次查询失败的详细原因（供 cmd 层区分 风控/玩家不存在/网络错误）
last_error = ""

# ── v2.3.23: 模块级复用 AsyncClient 连接池 ──
# 每次查询新建 AsyncClient(timeout=15) 会重复 TCP+TLS 握手（实测单次建连 ~0.2-1s），
# 高频率查询时差距被放大。这里全局持有一个共用 client，连接复用后查询耗时显著下降。
# 生命周期：模块导入时惰性创建，进程退出时由 atexit 关闭（连接是 keep-alive 的，无需主动关）。
_shared_client: httpx.AsyncClient | None = None
_shared_lock = asyncio.Lock()

# ── v2.3.24 查询结果 TTL 缓存 ──
# 战绩每 4 小时才采集一轮，5 分钟内的重复查询结果必然相同 → 缓存省一次网络往返。
# key=(player, template, id_type) → (记录时间戳, data)
import time as _time_mod
_STATS_TTL = 300.0
_STATS_CACHE_MAX = 200
_stats_cache: dict[tuple[str, str, str], tuple[float, dict]] = {}


async def _get_client(timeout: float = 15.0) -> httpx.AsyncClient:
    """获取全局共享 AsyncClient（惰性创建 + 线程安全锁）"""
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        async with _shared_lock:
            if _shared_client is None or _shared_client.is_closed:
                # limits: 默认 100 连接池；keepalive 30s——高频查询保持连接不重建
                _shared_client = httpx.AsyncClient(
                    timeout=timeout,
                    headers=HEADERS,
                    limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
                )
    return _shared_client


def _close_shared_client():
    """进程退出时兜底关闭共享连接池"""
    global _shared_client
    if _shared_client is not None:
        try:
            import asyncio as _a
            try:
                _a.get_running_loop()
            except RuntimeError:
                _a.run(_shared_client.aclose())
            else:
                asyncio.create_task(_shared_client.aclose())
        except Exception:
            pass
        _shared_client = None


import atexit as _atexit
_atexit.register(_close_shared_client)

BASE_URL = "https://www.wdsj.net/nexus"
HEADERS = {"Referer": "https://www.wdsj.net/nexus/stats"}

# ── v2.3.66: 直连优先 + 代理兜底 ───────────────────────────────
# 旧逻辑是「配了 WDSJ_PROXY 就**永远**走代理」——等于默认每次请求都多绕一跳，
# 而代理的本意只是「服务器 IP 被 CrowdSec 风控时」的备胎，不该是常态路径。
# 现在：先直连 wdsj.net；只有直连真的不行（网络异常 / HTTP 403 风控 / 5xx）
# 才走代理兜底。404 属业务结果（玩家/资源不存在），不算"直连不可用"，不回退。
# 环境变量 WDSJ_PROXY=https://xxx.workers.dev/proxy?url=  （不配 = 没有兜底，纯直连）
import os as _os
PROXY_BASE = _os.environ.get("WDSJ_PROXY", "").strip().rstrip("/")


def _direct_url(path: str) -> str:
    """直连 URL。path 可以是 '/api/...'，也可以已是完整 http(s) URL。"""
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return f"{BASE_URL}{path}"


def _proxy_url(full: str) -> Optional[str]:
    """把完整 URL 包进代理；未配置代理时返回 None"""
    if not PROXY_BASE:
        return None
    return f"{PROXY_BASE}?url={urllib.parse.quote(full, safe='')}"


def _api_url(path: str) -> str:
    """构造**直连** URL（保留旧名，兼容探针/外部调用）。

    ⚠️ v2.3.66 起语义有变：以前"配了代理就返回代理 URL"，现在恒返回直连 URL。
    需要自动兜底的请求请用 _request()。
    """
    return _direct_url(path)


async def _request(path: str, *, timeout: float = 15.0) -> Optional[httpx.Response]:
    """请求 wdsj：**优先直连，失败兜底代理**。

    回退条件：网络异常（超时/连不上）、HTTP 403（CrowdSec 风控）、HTTP 5xx。
    不回退：404 及其他 4xx（业务结果，直连是好的）。
    两边都失败时返回直连的响应（或 None）。
    """
    full = _direct_url(path)
    client = await _get_client(timeout)
    resp: Optional[httpx.Response] = None
    try:
        resp = await client.get(full, timeout=timeout)
        if resp.status_code != 403 and resp.status_code < 500:
            return resp                     # 200 / 404 / 其他 4xx → 直连可用
        logger.info("wdsj 直连 HTTP %s，尝试代理兜底: %s", resp.status_code, full)
    except Exception as e:
        logger.info("wdsj 直连失败(%s)，尝试代理兜底: %s", type(e).__name__, full)

    purl = _proxy_url(full)
    if not purl:
        return resp                          # 没配代理 → 直连结果（可能 None）就是结果
    try:
        presp = await client.get(purl, timeout=timeout)
        logger.info("wdsj 代理兜底成功 (HTTP %s): %s", presp.status_code, full)
        return presp
    except Exception as e:
        logger.warning("wdsj 代理兜底也失败(%s): %s", type(e).__name__, full)
        return resp

# v2: 16 个模板（新增 villagedefense/naturaldisasters/csgo）
TEMPLATES = {
    "bedwars-stats": "起床战争", "knockbackwars-stats": "击退战场",
    "arena-stats": "竞技场", "kitpvp-stats": "职业战争",
    "skywars-stats": "空岛战争", "thepit-stats": "天坑乱斗",
    "colorwars-stats": "色盲战争", "drawguess-stats": "你画我猜",
    "hideandseek-stats": "躲猫猫", "murdermystery-stats": "神秘谋杀",
    "uhc-stats": "极限生存", "watercube-stats": "星跃水立方",
    "buildbattle-stats": "建筑战争", "villagedefense-stats": "村庄保卫战",
    "naturaldisasters-stats": "天灾逃生", "csgo-stats": "反恐精英",
    "arena-modern-stats": "高版本竞技场", "luckypillars-stats": "幸运之柱",
}

ALIASES = {
    "bw": "bedwars-stats", "kbw": "knockbackwars-stats",
    "are": "arena-stats", "jjc": "arena-stats", "kp": "kitpvp-stats", "sw": "skywars-stats",
    "pit": "thepit-stats", "cw": "colorwars-stats", "dg": "drawguess-stats",
    "has": "hideandseek-stats", "mm": "murdermystery-stats",
    "uhc": "uhc-stats", "wc": "watercube-stats", "bb": "buildbattle-stats",
    "vd": "villagedefense-stats", "nd": "naturaldisasters-stats",
    "cs": "csgo-stats", "csgo": "csgo-stats",
    "am": "arena-modern-stats", "modern": "arena-modern-stats",
    "lp": "luckypillars-stats", "pillar": "luckypillars-stats",
}

# 模板中文名 -> id（支持 /~wdsj 幸运之柱 xxx 这种自然说法）
TEMPLATE_CN = {v: k for k, v in TEMPLATES.items()}

# v2: 玩家标识类型（templates API 返回 allowedIdentityTypes）
# 注意: 线上仅允许 name/nick，uid 与 uuid 均已被服务端禁用
# （templates 返回 allowedIdentityTypes=["name","nick"]；uid 实测 400 "不允许使用 uid 查询玩家"）
IDENTITY_TYPES = ("name", "nick", "uid", "uuid")

BOARD_ALIASES = {
    "bwk": "起床战争-击杀", "bww": "bedwars-wins", "bwb": "bedwars-beds",
    "bwfk": "起床战争-最终击杀", "bw1k": "起床战争-首杀",
    "kbwk": "knockbackwars-kills", "kbwt": "击退战场-TNT击杀",
    "swk": "skywars-kills", "sww": "空岛战争-胜利",
    "pt": "playtime-minutes", "cp": "情侣-亲密值", "title": "全服-称号数量",
    # 单词直接别名 (兼容 /~wdsj lb beds month 等简化写法)
    "beds": "bedwars-beds", "wins": "bedwars-wins",
    "tnt": "击退战场-TNT击杀",
    # v2.3.15 第三轮反扒新增: 起床战争
    "bwde": "bedwars-deaths", "bwscore": "bedwars-overall", "overall": "bedwars-overall",
    "bwns": "bedwars-normal-win-streak", "bwms": "bedwars-moe-win-streak",
    "bwos": "bedwars-oneblock-win-streak", "bwss": "bedwars-solo-win-streak",
    # 幸运之柱
    "lpw": "luckypillars-wins", "lpk": "luckypillars-kills",
    "lpt": "luckypillars-total-survival-seconds",
    # 高版本竞技场
    "amw": "arena-modern-wins", "amk": "arena-modern-kills",
    "ams": "arena-modern-best-streak", "amelo": "arena-modern-global-elo",
    # 空岛战争
    "sws": "skywars-best-win-streak", "swl": "skywars-highest-loss-streak",
    # 凌空乱斗
    "abk": "aerial-battle-kills", "abl": "aerial-battle-loot-chests",
    # 其他英文 id
    "pitk": "thepit-kills", "bbw": "buildbattle-wins",
    "mmk": "murdermystery-kills", "sheep": "sheepwars-kills",
    "sbw": "speedbuilders-wins", "coins": "wealth-coins",
    "kpd": "kitpvp-deaths",
}

BOARD_SHORTHAND = {
    ("bw", "kill"): "起床战争-击杀", ("bw", "win"): "bedwars-wins",
    ("bw", "beds"): "bedwars-beds", ("bw", "fk"): "起床战争-最终击杀",
    ("bw", "1k"): "起床战争-首杀", ("bw", "void"): "起床战争-自走虚空",
    ("bw", "egg"): "起床战争-鸡蛋击杀", ("bw", "fb"): "起床战争-火球击杀",
    ("kbw", "kill"): "knockbackwars-kills", ("kbw", "dead"): "击退战场-死亡",
    ("kbw", "tnt"): "击退战场-TNT击杀", ("kbw", "arrow"): "击退战场-弓箭击杀",
    ("kbw", "rod"): "击退战场-鱼竿击杀", ("kbw", "jp"): "击退战场-跳板击杀",
    ("sw", "kill"): "skywars-kills", ("sw", "win"): "空岛战争-胜利",
    ("sw", "dead"): "空岛战争-死亡", ("sw", "1k"): "空岛战争-首杀",
    ("kp", "kill"): "职业战争-击杀", ("kp", "xp"): "职业战争-经验",
    ("pt", ""): "playtime-minutes", ("cp", ""): "情侣-亲密值",
    ("title", ""): "全服-称号数量", ("guild", ""): "公会-总贡献",
    ("dg", "win"): "你画我猜-获胜", ("cw", "win"): "色盲战争-获胜",
    ("cw", "kill"): "色盲战争-杀敌", ("has", "win"): "躲猫猫-获胜",
    # v2.3.15 第三轮反扒新增
    ("bw", "dead"): "bedwars-deaths", ("bw", "score"): "bedwars-overall",
    ("bw", "ns"): "bedwars-normal-win-streak", ("bw", "ms"): "bedwars-moe-win-streak",
    ("bw", "os"): "bedwars-oneblock-win-streak", ("bw", "ss"): "bedwars-solo-win-streak",
    ("lp", "win"): "luckypillars-wins", ("lp", "kill"): "luckypillars-kills",
    ("lp", "time"): "luckypillars-total-survival-seconds",
    ("am", "win"): "arena-modern-wins", ("am", "kill"): "arena-modern-kills",
    ("am", "streak"): "arena-modern-best-streak", ("am", "elo"): "arena-modern-global-elo",
    ("sw", "streak"): "skywars-best-win-streak", ("sw", "loss"): "skywars-highest-loss-streak",
    ("ab", "kill"): "aerial-battle-kills", ("ab", "chest"): "aerial-battle-loot-chests",
    ("pit", "kill"): "thepit-kills", ("bb", "win"): "buildbattle-wins",
    ("mm", "kill"): "murdermystery-kills", ("sheep", "kill"): "sheepwars-kills",
    ("sb", "win"): "speedbuilders-wins", ("wealth", "coin"): "wealth-coins",
    ("kp", "dead"): "kitpvp-deaths",
}

PERIOD_LABELS = {"ALLTIME": "总榜", "MONTHLY": "月榜", "WEEKLY": "周榜", "DAILY": "日榜", "SEASON": "赛季"}


def resolve_template(raw: str) -> str | None:
    raw = raw.lower()
    if raw in TEMPLATES: return raw
    if raw in TEMPLATE_CN: return TEMPLATE_CN[raw]
    return ALIASES.get(raw)


def resolve_board(raw: str) -> str | None:
    raw_lower = raw.lower()
    return BOARD_ALIASES.get(raw_lower, raw)


def resolve_board_shorthand(game: str, metric: str = "") -> str | None:
    return BOARD_SHORTHAND.get((game.lower(), metric.lower()))


def build_identity(player: str, id_type: str = "name") -> str:
    """构造带类型前缀的玩家标识，自动检测已带前缀的情况"""
    p = player.strip()
    if ":" in p and p.split(":", 1)[0].lower() in IDENTITY_TYPES:
        return urllib.parse.quote(p, safe=":")
    if id_type not in IDENTITY_TYPES:
        id_type = "name"
    return f"{id_type}:{urllib.parse.quote(p)}"


async def query_player_stats(player: str, template_id: str,
                             id_type: str = "name", timeout: float = 15.0,
                             use_cache: bool = True) -> Optional[dict]:
    """查询玩家某模板战绩。

    ★ v2.3.24 新增 TTL 缓存（默认 300s）：战绩每 4 小时才采集一轮，5 分钟内的
    重复查询结果必然相同。多人问同一玩家 / 同一人反复查时直接命中缓存，
    省掉一次完整网络往返。传 use_cache=False 可强制走网络（如需最新快照）。
    """
    global last_error
    _ck = (str(player), str(template_id), str(id_type))
    if use_cache:
        _hit = _stats_cache.get(_ck)
        if _hit and (_time_mod.time() - _hit[0]) < _STATS_TTL:
            logger.info("wdsj 命中查询缓存: player=%s template=%s", player, template_id)
            last_error = ""
            return _hit[1]
    encoded = build_identity(player, id_type)
    path = f"/api/v1/players/{encoded}/templates/{urllib.parse.quote(template_id)}"
    try:
        resp = await _request(path, timeout=timeout)
        if resp is None:
            last_error = "直连与代理均不可用"
            return None
        if resp.status_code != 200:
            last_error = f"HTTP {resp.status_code}"
            if resp.status_code == 403:
                logger.warning("wdsj API 被风控/拒绝 (HTTP 403): %s", path)
            elif resp.status_code == 404:
                logger.info("wdsj 玩家不存在 (HTTP 404): %s", path)
            else:
                logger.warning("wdsj API HTTP %s: %s", resp.status_code, path)
            return None
        data = resp.json()
        if data.get("code") != 0:
            last_error = f"API error {data.get('code')}: {data.get('message')}"
            logger.warning("wdsj API 业务错误: %s", last_error)
            return None
        last_error = ""
        if use_cache:
            _stats_cache[_ck] = (_time_mod.time(), data["data"])
            if len(_stats_cache) > _STATS_CACHE_MAX:
                # 简单清理：丢掉最旧的一半，防无限增长
                for _k in sorted(_stats_cache, key=lambda k: _stats_cache[k][0])[: len(_stats_cache) // 2]:
                    _stats_cache.pop(_k, None)
        return data["data"]
    except Exception as e:
        last_error = f"{type(e).__name__}: {e}"
        logger.error("wdsj 查询异常: player=%r template=%s err=%s:%r path=%s",
                     player, template_id, type(e).__name__, e, path)
        return None


async def download_stats_image(image_url: str, save_path: str, timeout: float = 15.0) -> bool:
    try:
        resp = await _request(image_url, timeout=timeout)   # image_url 可 "/..." 也可完整 URL
        if resp is None:
            logger.error("下载战绩图片失败: 直连与代理均不可用 %s", image_url)
            return False
        resp.raise_for_status()
        with open(save_path, "wb") as f:
            f.write(resp.content)
        return True
    except Exception as e:
        logger.error("下载战绩图片失败: %s", e)
        return False


async def download_player_head(name: str, save_path: str, timeout: float = 15.0) -> bool:
    """v2 新增: 玩家头像 /api/v1/player-heads/{name}/head.png"""
    path = f"/api/v1/player-heads/{urllib.parse.quote(name)}/head.png"
    try:
        resp = await _request(path, timeout=timeout)
        if resp is None:
            logger.error("下载玩家头像失败: 直连与代理均不可用 %s", path)
            return False
        resp.raise_for_status()
        with open(save_path, "wb") as f:
            f.write(resp.content)
        return True
    except Exception as e:
        logger.error("下载玩家头像失败: %s", e)
        return False


async def fetch_player_head_data_uri(name: str, timeout: float = 5.0) -> str:
    """下载玩家皮肤头像并转成 data URI；失败返回空串（前端会回退默认头）。

    目的：卡片渲染时不再依赖外部网络（原方案由浏览器加载 wdsj 头像，
    networkidle 会一直等，网络抖动时渲染从 3s 涨到 9s）。
    """
    import base64 as _b64
    path = f"/api/v1/player-heads/{urllib.parse.quote(name)}/head.png"
    try:
        resp = await _request(path, timeout=timeout)
        if resp is not None and resp.status_code == 200 and resp.content:
            return "data:image/png;base64," + _b64.b64encode(resp.content).decode()
        logger.info("头像下载异常状态: %s %s",
                    resp.status_code if resp is not None else "无响应", path)
    except Exception as e:
        logger.warning("头像下载失败: %s: %r", type(e).__name__, e)
    return ""


async def query_leaderboards() -> Optional[list]:
    try:
        resp = await _request("/api/v1/leaderboards", timeout=15)
        if resp is None:
            last_error = "直连与代理均不可用"
            return None
        if resp.status_code != 200:
            last_error = f"HTTP {resp.status_code}"
            return None
        data = resp.json()
        if data.get("code") != 0:
            last_error = f"API error {data.get('code')}: {data.get('message')}"
            logger.warning("wdsj 榜单业务错误: %s", last_error)
            return None
        return data["data"]["boards"]
    except Exception as e:
        logger.error("获取排行榜列表失败: %s", e)
        return None


async def query_leaderboard(board_id: str, period: str = "ALLTIME") -> Optional[dict]:
    encoded = urllib.parse.quote(board_id)
    try:
        resp = await _request(f"/api/v1/leaderboards/{encoded}?type={period}", timeout=15)
        if resp is None or resp.status_code != 200:
            return None
        data = resp.json()
        if data.get("code") != 0:
            return None
        return data["data"]
    except Exception as e:
        logger.error("查询排行榜失败: %s %s", type(e).__name__, e)
        return None


LEADERBOARD_ENTRY_HTML = """<div class="entry">
  <span class="rank">#{rank}</span>
  <div class="head"><img src="https://www.wdsj.net/nexus{head_url}"></div>
  <span class="name">{name}</span>
  <span class="value">{value} {unit}</span>
</div>"""


def build_leaderboard_html(data: dict, bot_name: str) -> str:
    from pathlib import Path
    _t0 = _time_mod.time() * 1000          # v2.3.32 页脚「渲染时间」起点
    board = data["board"]
    entries = data.get("entries", [])
    period_label = PERIOD_LABELS.get(data.get("type", "ALLTIME"), "总榜")
    entry_htmls = []
    for e in entries:
        entry_htmls.append(LEADERBOARD_ENTRY_HTML.format(
            rank=e["rank"], name=e["owner"], value=e["value"],
            head_url=urllib.parse.quote(e.get("headImageUrl", ""), safe="/"),
            unit=board.get("unit", "")))
    template_path = Path(__file__).resolve().parent.parent / "data" / "templates" / "leaderboard_card.html"
    html = template_path.read_text(encoding="utf-8")
    html = html.replace("{{TITLE}}", f"{board.get('group','')} {board.get('displayName','')}")
    html = html.replace("{{PERIOD}}", period_label)
    html = html.replace("{{ENTRIES}}", "\n".join(entry_htmls))
    html = html.replace("{{BRAND}}", f"Generated by {bot_name}")
    from services.card_base import inject_stamp
    return inject_stamp(html, _t0)


_MC_COLOR_RE = None


def _strip_mc_color(s) -> str:
    """剥离 Minecraft 颜色码（备用；卡片渲染已改为保留颜色码由前端着色）"""
    import re as _re
    global _MC_COLOR_RE
    if _MC_COLOR_RE is None:
        _MC_COLOR_RE = _re.compile(r"[\u00a7&].")
    return _MC_COLOR_RE.sub("", str(s))


def build_dual_card_html(bw_data: Optional[dict], ar_data: Optional[dict],
                         head_data_uri: str = "") -> str:
    """双模式横屏战绩卡（起床战争 + 竞技场，一图双段）

    从 API 返回的 labels/values 全量提取字段，注入 data/templates/wdsj_dual_card.html。
    """
    import html as _html
    import json as _json
    from pathlib import Path as _P

    _t0 = _time_mod.time() * 1000          # v2.3.32 页脚「渲染时间」起点

    def esc(s) -> str:
        return _html.escape(str(s), quote=True)

    def fields_of(data) -> list:
        """按 API 返回顺序全量提取 [key, label, value]

        注意：值保留 Minecraft 颜色码（如 §3铂金 III），由前端渲染成真实颜色。
        """
        data = data or {}
        labels = data.get("labels") or {}
        values = data.get("values") or {}
        out = []
        for k, v in values.items():
            out.append([k, esc(labels.get(k, k)), esc(v)])
        return out

    def display_of(data, default: str) -> str:
        return (data or {}).get("displayName") or default

    pi = ((ar_data or bw_data) or {}).get("player") or {}

    # 注册时间（起床战争的 headerCards 里有）
    reg = ""
    for c in (bw_data or {}).get("headerCards") or []:
        if c.get("key") == "registerTime":
            reg = c.get("value", "")
            break

    ar_values = (ar_data or {}).get("values") or {}
    payload = {
        "player": {
            "uid": pi.get("uid", ""),
            "name": pi.get("name", ""),
            "uuid": pi.get("uuid", ""),
            "head": head_data_uri or "",     # 已内联的皮肤头像（空则由前端回退）
        },
        "registerTime": reg,
        "bw": {
            "displayName": display_of(bw_data, "起床战争"),
            "fields": fields_of(bw_data),
        },
        "ar": {
            "displayName": display_of(ar_data, "竞技场"),
            "division": ar_values.get("division", ""),
            "fields": fields_of(ar_data),
        },
    }

    tpl_path = _P(__file__).resolve().parent.parent / "data" / "templates" / "wdsj_dual_card.html"
    tpl = tpl_path.read_text(encoding="utf-8")
    # 防 </script> 提前闭合
    js = _json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    html = tpl.replace("/*__WDSJ_DATA__*/null", js, 1)
    from services.card_base import inject_stamp
    return inject_stamp(html, _t0)


def build_wdsj_help_card_html(bot_name: str) -> str:
    """构建 /~wdsj help 卡片（v2.3.81 双模式卡主题：浅底暖色玻璃，内容加详）。

    主题与 data/templates/wdsj_dual_card.html 一致：米白底 + 橙/蓝双光晕 +
    白色半透明面板 + 橙色点缀。一张卡列全子指令/模式/排行榜/周期/示例。
    """
    modes = [
        ("bw", "起床战争"), ("kbw", "击退战场"), ("sw", "空岛战争"),
        ("kp", "职业战争"), ("are", "竞技场"), ("pit", "天坑乱斗"),
        ("cw", "色盲战争"), ("dg", "你画我猜"), ("has", "躲猫猫"),
        ("mm", "神秘谋杀"), ("uhc", "极限生存"), ("wc", "星跃水立方"),
        ("bb", "建筑战争"), ("vd", "村庄保卫战"), ("nd", "天灾逃生"),
        ("cs", "反恐精英"), ("am", "高版本竞技场"), ("lp", "幸运之柱"),
    ]
    mode_chips = "".join(
        f'<span class="chip"><b>{a}</b> {n}</span>' for a, n in modes
    )
    subcmds = [
        ("me", "双模式横屏卡", "起床+竞技场全字段+18 项比率", "/~wdsj me ｜ /~wdsj <玩家> me"),
        ("bd", "绑定玩家名", "绑定后可省略名字；bd list 列全部", "/~wdsj bd <玩家名>"),
        ("lb", "排行榜", "双词/单词榜单，周期可省，末尾 img 发图卡", "/~wdsj lb <榜> [周期] [img]"),
        ("daily", "日榜", "每天 0:01-20:01 六轮产出；are=竞技场", "/~wdsj daily [are] [日期] [send]"),
        ("rank", "群内排行", "统计群内已绑定玩家", "/~wdsj rank [指标]"),
        ("trend", "趋势图", "折线图，需 ≥2 天采集记录", "/~wdsj trend <玩家> [指标]"),
        ("boards", "榜单速查", "可带游戏过滤（boards bw 只看起床）", "/~wdsj boards [游戏]"),
        ("list", "别名全表", "模式与榜单别名", "/~wdsj list"),
        ("collect", "手动采集", "仅管理员；返回失败名单", "/~wdsj collect"),
    ]
    sub_rows = "".join(
        f'<div class="row"><code>/{a}</code><span class="d">{d}</span>'
        f'<span class="u">{u}</span></div>' for a, _n, d, u in subcmds
    )
    lb_rows = "".join(
        f'<div class="lb-row"><b>{w}</b><span>{n}</span></div>'
        for w, n in [
            ("bw win", "胜利"), ("bw kill", "击杀"), ("bw beds", "摧床"),
            ("bw fk", "最终击杀"), ("bw 1k", "首杀"), ("kbw kill", "击杀"),
            ("kbw tnt", "TNT 击杀"), ("sw kill", "击杀"), ("sw win", "胜利"),
            ("am elo", "全服 ELO"), ("lp w", "幸运之柱胜利"),
            ("pt / cp / title / guild", "在线 / 情侣 / 称号 / 公会"),
        ]
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
html,body{{width:640px;
  font-family:"PingFang SC","Microsoft YaHei","Noto Sans CJK SC",sans-serif;
  -webkit-font-smoothing:antialiased;}}
.card{{width:640px;padding:34px 38px 30px;
  background:
    radial-gradient(620px 300px at 10% -6%, rgba(255,183,154,.34), transparent 60%),
    radial-gradient(620px 300px at 92% -6%, rgba(158,188,255,.36), transparent 60%),
    linear-gradient(160deg,#fbf6f0 0%,#f4f0ec 42%,#eef1f6 100%);
  border-radius:30px;position:relative;overflow:hidden;color:var(--ink);}}
.card::before{{content:"";position:absolute;inset:0;pointer-events:none;
  background-image:url('data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="140" height="140"><circle cx="8" cy="8" r="1" fill="white" opacity="0.5"/></svg>');
  opacity:.5;}}
.wrap{{position:relative;z-index:2;display:flex;flex-direction:column;gap:14px;}}
:root{{--ink:#3a332c;--muted:#8a7f76;--accent:#f0875c;}}
.top{{display:flex;align-items:baseline;gap:12px;}}
.brand{{font-size:21px;font-weight:800;color:var(--ink);letter-spacing:.02em;}}
.brand i{{font-style:normal;font-size:12.5px;color:var(--muted);font-weight:400;margin-left:8px;}}
.main{{background:rgba(255,255,255,.62);backdrop-filter:blur(14px);
  border:1px solid rgba(255,255,255,.9);border-radius:18px;padding:14px 18px;
  box-shadow:0 8px 20px rgba(120,80,60,.08);}}
.main code{{font-size:16px;font-weight:800;color:var(--accent);}}
.main .n{{font-size:12.5px;color:var(--muted);margin-top:6px;}}
.panel{{background:rgba(255,255,255,.55);backdrop-filter:blur(18px);
  border:1px solid rgba(255,255,255,.85);border-radius:20px;padding:15px 18px;
  box-shadow:0 10px 24px rgba(120,80,60,.07), inset 0 1px 0 rgba(255,255,255,.9);}}
.k{{font-size:11.5px;letter-spacing:.2em;color:var(--muted);margin-bottom:9px;font-weight:700;}}
.chips{{display:flex;flex-wrap:wrap;gap:5px 6px;}}
.chip{{font-size:12px;padding:4px 10px;border-radius:999px;background:rgba(255,255,255,.7);
  border:1px solid rgba(150,130,110,.2);color:var(--ink);}}
.chip b{{color:var(--accent);margin-right:4px;font-weight:800;}}
.row{{display:flex;align-items:baseline;gap:10px;padding:5px 0;}}
.row + .row{{border-top:1px solid rgba(150,130,110,.12);}}
.row code{{font-size:13.5px;font-weight:800;color:var(--accent);flex:0 0 66px;}}
.row .d{{font-size:12.5px;color:var(--ink);flex:0 0 210px;}}
.row .u{{font-size:11px;color:var(--muted);font-family:Consolas,monospace;}}
.lb-grid{{display:grid;grid-template-columns:1fr 1fr;gap:0 22px;}}
.lb-row{{display:flex;gap:8px;padding:3.5px 0;font-size:12px;align-items:baseline;}}
.lb-row b{{font-family:Consolas,monospace;color:#c96a42;flex:0 0 128px;font-size:11.5px;}}
.lb-row span{{color:var(--ink);}}
.foot{{display:flex;align-items:center;gap:9px;font-size:10.5px;color:var(--muted);padding:0 4px;}}
.pill{{padding:3px 12px;border-radius:999px;background:rgba(255,255,255,.75);
  border:1px solid rgba(255,255,255,.9);color:var(--muted);font-weight:700;}}
</style></head>
<body>
<div class="card">
  <div class="wrap">
    <div class="top"><div class="brand">洛花星雨战绩查询<span class="brand-i" style="font-style:normal;font-size:12.5px;color:#8a7f76;margin-left:10px;">/～wdsj 全指令</span></div></div>

    <div class="main">
      <code>/~wdsj &lt;模式&gt; &lt;玩家&gt; [text]</code>
      <div class="n">默认发官方图片卡片；结尾加 text 看文字版；玩家名留空用绑定的名字（先 bd 绑定）</div>
    </div>

    <div class="panel"><div class="k">游戏模式 MODES（18 种，支持中文名直查）</div><div class="chips">{mode_chips}</div></div>

    <div class="panel"><div class="k">子指令 SUBCOMMANDS</div>{sub_rows}</div>

    <div class="panel"><div class="k">排行榜 LEADERBOARD（lb &lt;双词榜单&gt; [周期] [img]，周期默认总榜）</div>
      <div class="lb-grid">{lb_rows}</div>
    </div>

    <div class="panel"><div class="k">日榜 DAILY（每天 0:01-20:01 六轮产出）</div>
      <div class="row"><code>daily</code><span class="d">起床战争日榜（默认）</span><span class="u">/~wdsj daily</span></div>
      <div class="row"><code>daily are</code><span class="d">竞技场日榜（冷蓝模板）</span><span class="u">/~wdsj daily are</span></div>
      <div class="row"><code>daily 7-20</code><span class="d">查过去日期（自动跨天统计）</span><span class="u">/~wdsj daily 7-20</span></div>
      <div class="row"><code>daily send</code><span class="d">推送到当前群（仅群聊）</span><span class="u">/~wdsj daily send</span></div>
    </div>

    <div class="panel"><div class="k">趋势 TREND（折线图，需 ≥2 天采集记录）</div>
      <div class="row"><code>trend</code><span class="d">指标：bw_kills / bw_wins / bw_finals / bw_deaths / arena_kills</span><span class="u">/~wdsj trend &lt;玩家&gt; bw_wins</span></div>
    </div>

    <div class="foot">
      <span class="pill">仅管理员</span><span>collect = 手动采集 ｜ /~owner wdsj groups = 推送群管理</span>
      <span style="flex:1"></span><span>Generated by {bot_name}</span>
    </div>
  </div>
</div>
</body></html>"""


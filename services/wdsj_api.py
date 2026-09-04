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

BASE_URL = "https://www.wdsj.net/nexus"
HEADERS = {"Referer": "https://www.wdsj.net/nexus/stats"}

# Cloudflare Worker 中转代理（服务器 IP 被 CrowdSec 封禁时启用）
# 环境变量 WDSJ_PROXY=https://xxx.workers.dev/proxy?url=
import os as _os
PROXY_BASE = _os.environ.get("WDSJ_PROXY", "").strip().rstrip("/")


def _api_url(path: str) -> str:
    """构造请求 URL：有代理走代理，否则直连"""
    full = f"{BASE_URL}{path}"
    if PROXY_BASE:
        import urllib.parse as _up
        return f"{PROXY_BASE}?url={_up.quote(full, safe='')}"
    return full

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
}

ALIASES = {
    "bw": "bedwars-stats", "kbw": "knockbackwars-stats",
    "are": "arena-stats", "jjc": "arena-stats", "kp": "kitpvp-stats", "sw": "skywars-stats",
    "pit": "thepit-stats", "cw": "colorwars-stats", "dg": "drawguess-stats",
    "has": "hideandseek-stats", "mm": "murdermystery-stats",
    "uhc": "uhc-stats", "wc": "watercube-stats", "bb": "buildbattle-stats",
    "vd": "villagedefense-stats", "nd": "naturaldisasters-stats",
    "cs": "csgo-stats", "csgo": "csgo-stats",
}

# v2: 玩家标识类型（templates API 返回 allowedIdentityTypes）
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
}

PERIOD_LABELS = {"ALLTIME": "总榜", "MONTHLY": "月榜", "WEEKLY": "周榜", "DAILY": "日榜"}


def resolve_template(raw: str) -> str | None:
    raw = raw.lower()
    if raw in TEMPLATES: return raw
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
                             id_type: str = "name", timeout: float = 15.0) -> Optional[dict]:
    global last_error
    encoded = build_identity(player, id_type)
    url = _api_url(f"/api/v1/players/{encoded}/templates/{urllib.parse.quote(template_id)}")
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers=HEADERS)
            if resp.status_code != 200:
                last_error = f"HTTP {resp.status_code}"
                if resp.status_code == 403:
                    logger.warning("wdsj API 被风控/拒绝 (HTTP 403): %s", url)
                elif resp.status_code == 404:
                    logger.info("wdsj 玩家不存在 (HTTP 404): %s", url)
                else:
                    logger.warning("wdsj API HTTP %s: %s", resp.status_code, url)
                return None
            data = resp.json()
            if data.get("code") != 0:
                last_error = f"API error {data.get('code')}: {data.get('message')}"
                logger.warning("wdsj API 业务错误: %s", last_error)
                return None
            last_error = ""
            return data["data"]
    except Exception as e:
        last_error = f"{type(e).__name__}: {e}"
        logger.error("wdsj 查询异常: player=%r template=%s err=%s:%r url=%s",
                     player, template_id, type(e).__name__, e, url)
        return None


async def download_stats_image(image_url: str, save_path: str, timeout: float = 15.0) -> bool:
    full_url = _api_url(image_url) if image_url.startswith("/") else image_url
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(full_url, headers=HEADERS)
            resp.raise_for_status()
            with open(save_path, "wb") as f: f.write(resp.content)
            return True
    except Exception as e:
        logger.error("下载战绩图片失败: %s", e)
        return False


async def download_player_head(name: str, save_path: str, timeout: float = 15.0) -> bool:
    """v2 新增: 玩家头像 /api/v1/player-heads/{name}/head.png"""
    url = _api_url(f"/api/v1/player-heads/{urllib.parse.quote(name)}/head.png")
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers=HEADERS)
            resp.raise_for_status()
            with open(save_path, "wb") as f: f.write(resp.content)
            return True
    except Exception as e:
        logger.error("下载玩家头像失败: %s", e)
        return False


async def query_leaderboards() -> Optional[list]:
    url = f"{BASE_URL}/api/v1/leaderboards"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers={**HEADERS, "Referer": "https://www.wdsj.net/nexus/leaderboards"})
            if resp.status_code != 200: return None
            data = resp.json()
            if data.get("code") != 0: return None
            return data["data"]["boards"]
    except Exception as e:
        logger.error("获取排行榜列表失败: %s", e)
        return None


async def query_leaderboard(board_id: str, period: str = "ALLTIME") -> Optional[dict]:
    encoded = urllib.parse.quote(board_id)
    url = f"{BASE_URL}/api/v1/leaderboards/{encoded}?type={period}"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers={**HEADERS, "Referer": "https://www.wdsj.net/nexus/leaderboards"})
            if resp.status_code != 200: return None
            data = resp.json()
            if data.get("code") != 0: return None
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
    return html


def build_help_card_html(md_path: str, bot_name: str) -> str:
    """构建 MD 帮助卡片 HTML"""
    from pathlib import Path
    md_text = Path(md_path).read_text(encoding="utf-8")
    try:
        from modules.changelog import markdown_to_enhanced_html
        body_html = markdown_to_enhanced_html(md_text)
    except Exception:
        body_html = f"<pre>{md_text}</pre>"

    template_path = Path(__file__).resolve().parent.parent / "data" / "templates" / "md_card.html"
    html = template_path.read_text(encoding="utf-8")
    html = html.replace("{{CONTENT}}", body_html)
    html = html.replace("{{BRAND}}", f"洛花星雨 Nexus · Generated by {bot_name}")
    return html

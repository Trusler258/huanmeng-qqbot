# -*- coding: utf-8 -*-
"""Steam 资料卡 HTML 构建（/~steam who 用）

模板：data/templates/steam_profile_card.html
字体：拉丁/数字内联 HarmonyOS Sans 子集（17KB），中文靠回退走服务器 Noto Sans CJK SC

⚠️ 模板里的注入标记必须唯一 —— **注释里也不要写那两个标记的字面量**，
   否则 str.replace(..., 1) 会替换到注释那一处，整个数据 JSON 被塞进注释、
   真注入点空着（踩过，表现为"卡片上字段全是 —"）。下面有 assert 防呆。
"""
from __future__ import annotations

import asyncio
import base64
import json
from datetime import datetime
from pathlib import Path

from core.logger import get_logger
from services import steam_api as S

logger = get_logger("steam_card")

ROOT = Path(__file__).resolve().parent.parent
TPL_FILE = ROOT / "data" / "templates" / "steam_profile_card.html"
FONT_FILE = ROOT / "data" / "steam_assets" / "font" / "harmonyos_medium_latin.woff2"

# 展示哪款游戏的成就（先用 ADOFAI；将来可改成"成就最多的那款"）
ACH_APPID = 977950


def _data_uri(p) -> str:
    try:
        p = Path(p)
        if p.exists() and p.stat().st_size > 0:
            return "data:image/jpeg;base64," + base64.b64encode(p.read_bytes()).decode()
    except Exception:
        pass
    return ""


async def _enrich(g: dict, key: str) -> dict:
    """一行游戏数据：中文名 + 时长 + 横幅图（并发取）"""
    aid = g.get("appid")
    name, img = await asyncio.gather(
        S.zh_name(aid, g.get("name", "")),
        S.game_header(aid),
    )
    return {
        "name": name or g.get("name", "") or ("appid " + str(aid)),
        "hours": round(g.get(key, 0) / 60.0, 1),
        "appid": aid,
        "img": _data_uri(img),
    }


async def build_profile_html(steamid: str, top_n: int = 8, recent_n: int = 6) -> str:
    """生成自包含 HTML（字体与图片全部内联）。拿不到玩家资料时返回空串"""
    prof = await S.player_summary(steamid)
    if not prof:
        return ""

    owned, recent_raw, level = await asyncio.gather(
        S.owned_games(steamid),
        S.recent_games(steamid, count=recent_n),
        S.steam_level(steamid),
    )
    ach = await S.achievements(steamid, ACH_APPID)

    games = owned.get("games") or []
    top = sorted(games, key=lambda g: g.get("playtime_forever", 0), reverse=True)[:top_n]
    recent = sorted(recent_raw, key=lambda g: g.get("playtime_2weeks", 0),
                    reverse=True)[:recent_n]

    top_items = list(await asyncio.gather(*[_enrich(g, "playtime_forever") for g in top]))
    recent_items = list(await asyncio.gather(*[_enrich(g, "playtime_2weeks") for g in recent]))
    avatar = _data_uri(await S.avatar_file(steamid, prof.get("avatar", "")))

    created = ""
    if prof.get("created"):
        try:
            created = datetime.fromtimestamp(prof["created"]).strftime("%Y-%m-%d")
        except Exception:
            created = ""

    total_hours = round(sum(g.get("playtime_forever", 0) for g in games) / 60.0)
    payload = {
        "profile": {
            "name": prof.get("name", ""),
            "steamid": steamid,
            "level": level,
            "created": created,
            "online": prof.get("online", False),
            "state_kind": prof.get("state_kind", ""),
            "state_text": prof.get("state_text", ""),
            "avatar": avatar,
        },
        "stats": {
            "games": owned.get("count", len(games)),
            "hours": total_hours,
            "ach_got": ach.get("got", 0),
            "ach_total": ach.get("total", 0),
        },
        "recent": recent_items,
        "recent_sum": round(sum(g.get("playtime_2weeks", 0) for g in recent) / 60.0, 1),
        "top": top_items,
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

    try:
        html = TPL_FILE.read_text(encoding="utf-8")
    except Exception as e:
        logger.error("卡片模板读取失败: %s", e)
        return ""

    try:
        font_b64 = base64.b64encode(FONT_FILE.read_bytes()).decode()
        html = html.replace("__FONT_B64__", font_b64, 1)
    except Exception as e:
        logger.warning("字体内联失败（中文仍可回退渲染）: %s", e)

    js = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    html = html.replace("/*__CARD_DATA__*/null", js, 1)

    # 防呆：标记若还在，说明替换被别处的字面量抢走了
    if "__CARD_DATA__" in html or "__FONT_B64__" in html:
        logger.error("卡片注入失败：占位符仍在（检查模板注释里是否写了字面量）")
        return ""

    S.flush_zh()          # 中文名缓存落盘
    return html

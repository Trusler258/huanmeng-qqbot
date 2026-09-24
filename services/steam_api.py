# -*- coding: utf-8 -*-
"""Steam Web API 数据层（/~steam 系列指令用）

职责：只管「拿数据」，不管展示。展示在 modules/steam.py，卡片在 services/steam_card.py。

指令入口：/~steam price|px|who|bd|help、/~在干嘛

注意：三条实测经验（都踩过，改这个文件前先读）：
  1. 查账号**必须**用 SteamID64 或资料链接，绝不用昵称猜 vanity ——
     曾用 ResolveVanityURL?vanityurl=trusler 解析到一个陌生人（他抢注了这个自定义 URL），
     白排查一小时并误导用户去改本来没问题的隐私设置。
  2. `communityvisibilitystate == 3`（资料页公开）**不等于**游戏库可见，两者是独立门槛，
     判断库是否可见只能实测 GetOwnedGames（返回 15 字节 `{"response":{}}` = 不可见）。
  3. `store.steampowered.com` 在国内时通时断（实测 WinError 10060），
     而服务器出口稳定 —— bot 本来就在服务器上跑，所以这里不需要特殊处理，
     但**别在本地开发机上期望它每次都通**。

key 位置：config/.env 的 STEAM_KEY（必需）；ITAD_KEY（可选，用于历史最低价）
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Optional

from core.logger import get_logger

logger = get_logger("steam")

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
ENV_FILE = ROOT / "config" / ".env"
ASSETS = DATA / "steam_assets"
BIND_FILE = DATA / "steam_bind.json"
NAMES_ZH = ASSETS / "games" / "names_zh.json"
PRICE_HIST = DATA / "steam_price_history.json"

API = "https://api.steampowered.com"
STORE = "https://store.steampowered.com"
CDN_HEADER = "https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg"
# 头像：avatars.steamstatic.com 在部分网络不通，fastly 镜像实测可用
AVATAR_HOSTS = ("avatars.fastly.steamstatic.com", "avatars.cloudflare.steamstatic.com")
UA = {"User-Agent": "Mozilla/5.0"}

STATE_TXT = {0: "离线", 1: "在线", 2: "忙碌", 3: "离开", 4: "打盹", 5: "想交易", 6: "想玩游戏"}


# ── 配置 ───────────────────────────────────────────────────────

def _env(name: str) -> str:
    """先看环境变量，再兜底解析 config/.env（bot 主进程不一定 load_dotenv）"""
    import os
    v = (os.environ.get(name) or "").strip()
    if v:
        return v
    try:
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, _, val = line.partition("=")
            if k.strip() == name:
                return val.strip().strip('"').strip("'")
    except Exception:
        pass
    return ""


def steam_key() -> str:
    return _env("STEAM_KEY")


def itad_key() -> str:
    return _env("ITAD_KEY")


def has_key() -> bool:
    return bool(steam_key())


# ── HTTP ──────────────────────────────────────────────────────

async def _get_json(url: str, params: Optional[dict] = None, timeout: float = 25.0):
    """GET → JSON（trust_env=False：绕过机器上的 http_proxy，Steam 走直连）"""
    import httpx
    async with httpx.AsyncClient(timeout=timeout, trust_env=False, verify=False,
                                 headers=UA) as c:
        r = await c.get(url, params=params)
        r.raise_for_status()
        return r.json()


async def _get_bytes(url: str, timeout: float = 30.0) -> bytes:
    import httpx
    async with httpx.AsyncClient(timeout=timeout, trust_env=False, verify=False,
                                 headers=UA, follow_redirects=True) as c:
        r = await c.get(url)
        r.raise_for_status()
        return r.content


async def _api(path: str, **params):
    params["key"] = steam_key()
    return await _get_json(f"{API}/{path}", params)


async def reachable(timeout: float = 5.0) -> bool:
    """轻量探测 api 域是否可达（免 key 的 GetServerInfo）。

    为什么需要它：Steam 对国内断的时候，第一个业务请求要干等 25s 超时才失败，
    整条「取数 → 渲染」白等半分钟。先花几秒探一下，不通就直接走缓存快照。
    """
    try:
        await _get_json(f"{API}/ISteamWebAPIUtil/GetServerInfo/v1/", timeout=timeout)
        return True
    except Exception as e:
        logger.debug("Steam api 域不可达: %s: %r", type(e).__name__, e)
        return False


# ── 绑定（QQ ↔ SteamID）──────────────────────────────────────

def _load_bind() -> dict:
    try:
        return json.loads(BIND_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_bind(m: dict) -> None:
    BIND_FILE.parent.mkdir(parents=True, exist_ok=True)
    BIND_FILE.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")


def get_bind(qq) -> str:
    return _load_bind().get(str(qq), "")


def set_bind(qq, steamid: str) -> None:
    m = _load_bind()
    m[str(qq)] = str(steamid)
    _save_bind(m)


def del_bind(qq) -> bool:
    m = _load_bind()
    if str(qq) in m:
        m.pop(str(qq))
        _save_bind(m)
        return True
    return False


# ── 解析用户输入的账号 ────────────────────────────────────────

_RE_STEAMID64 = re.compile(r"\b(7656119\d{10})\b")
_RE_PROFILES = re.compile(r"steamcommunity\.com/profiles/(\d+)")
_RE_ID = re.compile(r"steamcommunity\.com/id/([A-Za-z0-9_\-\.]+)")
_RE_APP = re.compile(r"/app/(\d+)")


def extract_appid(text: str) -> str:
    """从商店链接抠 appid（/~steam price <链接>）"""
    m = _RE_APP.search(text or "")
    return m.group(1) if m else ""


def extract_steamid(text: str) -> str:
    """从输入里抠 SteamID64（支持纯 ID / profiles 链接 / STEAM_x:y:z 不算）"""
    t = (text or "").strip()
    m = _RE_PROFILES.search(t) or _RE_STEAMID64.search(t)
    return m.group(1) if m else ""


def extract_vanity(text: str) -> str:
    """从 steamcommunity.com/id/xxx 链接里抠自定义 URL"""
    m = _RE_ID.search(text or "")
    return m.group(1) if m else ""


async def resolve_steamid(text: str) -> tuple:
    """把用户输入解析成 SteamID64

    返回 (steamid, 错误提示)。支持：SteamID64 / profiles 链接 / id/xxx 链接。
    注意：不猜昵称 —— 那是踩过的坑。
    """
    if not steam_key():
        return "", "未配置 STEAM_KEY"
    t = (text or "").strip()
    if not t:
        return "", "没给账号"
    sid = extract_steamid(t)
    if sid:
        return sid, ""
    vanity = extract_vanity(t)
    if vanity:
        try:
            r = await _api("ISteamUser/ResolveVanityURL/v1/", vanityurl=vanity)
            res = r.get("response", {})
            if res.get("success") == 1:
                return res.get("steamid", ""), ""
            return "", f"这个自定义 URL「{vanity}」在 Steam 上不存在"
        except Exception as e:
            logger.warning("ResolveVanityURL 失败: %s", e)
            return "", "解析自定义 URL 失败（网络问题？）"
    if re.fullmatch(r"[A-Za-z0-9_\-\.]{2,32}", t):
        # 看着像自定义 URL：只在用户明确给的就是这个名字时尝试（结果会标注来源）
        try:
            r = await _api("ISteamUser/ResolveVanityURL/v1/", vanityurl=t)
            res = r.get("response", {})
            if res.get("success") == 1:
                return res.get("steamid", ""), ""
        except Exception:
            pass
        return "", (f"把「{t}」当成自定义 URL 查了，没找到对应账号\n"
                    "  请用 SteamID64（17 位数字，个人资料页链接里那串）")
    return "", "认不出这是账号。请给 SteamID64 或个人资料链接"


# ── 玩家数据 ──────────────────────────────────────────────────

async def player_summary(steamid: str) -> dict:
    """昵称 / 头像 / 在线状态 / 正在玩什么（私有账号也能拿到基本字段）"""
    r = await _api("ISteamUser/GetPlayerSummaries/v2/", steamids=steamid)
    players = (r.get("response") or {}).get("players") or []
    if not players:
        return {}
    p = players[0]
    game = p.get("gameextrainfo") or ""
    online = bool(p.get("personastate", 0)) or bool(game)
    return {
        "steamid": p.get("steamid", steamid),
        "name": p.get("personaname", ""),
        "avatar": p.get("avatarfull") or p.get("avatarmedium") or "",
        "online": online,
        "in_game": game,
        "state_text": ("正在玩 " + game) if game else STATE_TXT.get(p.get("personastate"), "离线"),
        "state_kind": "ingame" if game else ("" if online else "off"),
        "visible": p.get("communityvisibilitystate") == 3,
        "created": p.get("timecreated", 0),
        "lastlogoff": p.get("lastlogoff", 0),
    }


async def owned_games(steamid: str) -> dict:
    """游戏库 + 时长

    注意：不加 include_appinfo：那个响应大 10 倍（实测 15KB 要 100s），
    游戏名走 zh_name() 单独查（反正中文名也要查 store）。
    """
    r = await _api("IPlayerService/GetOwnedGames/v1/", steamid=steamid,
                   include_played_free_games=1)
    resp = r.get("response") or {}
    return {"count": resp.get("game_count", len(resp.get("games") or [])),
            "games": resp.get("games") or [],
            "visible": bool(resp.get("games"))}


async def recent_games(steamid: str, count: int = 6) -> list:
    r = await _api("IPlayerService/GetRecentlyPlayedGames/v1/", steamid=steamid,
                   count=count)
    return (r.get("response") or {}).get("games") or []


async def steam_level(steamid: str) -> Optional[int]:
    try:
        r = await _api("IPlayerService/GetSteamLevel/v1/", steamid=steamid)
        return (r.get("response") or {}).get("player_level")
    except Exception:
        return None


async def achievements(steamid: str, appid: int) -> dict:
    """玩家成就。资料不公开时 Valve 返回 403 + "Profile is not public" """
    try:
        r = await _api("ISteamUserStats/GetPlayerAchievements/v1/",
                       steamid=steamid, appid=appid)
    except Exception as e:
        msg = str(e)
        if "403" in msg:
            return {"error": "profile_private", "got": 0, "total": 0, "list": []}
        return {"error": str(e), "got": 0, "total": 0, "list": []}
    ach = (r.get("playerstats") or {}).get("achievements") or []
    return {"error": "", "got": sum(1 for a in ach if a.get("achieved")),
            "total": len(ach), "list": ach}


async def friend_count(steamid: str) -> Optional[int]:
    """好友数。资料/好友列表不公开时返回 None（展示成 —，不是 0）"""
    try:
        r = await _api("ISteamUser/GetFriendList/v1/", steamid=steamid,
                       relationship="friend")
    except Exception as e:
        logger.debug("好友列表不可用 %s: %s", steamid, e)
        return None
    fl = (r.get("friendslist") or {}).get("friends")
    return len(fl) if fl is not None else None


async def profile_badges(steamid: str) -> dict:
    """徽章数 + 等级 XP 进度。

    XP 三个字段的语义（容易搞混）：
      player_xp                       总 XP
      player_xp_needed_current_level  当前等级起点所需 XP
      player_xp_needed_to_level_up    距下一级还需多少 XP
    → 本级的已得 XP = player_xp - player_xp_needed_current_level
    """
    try:
        r = await _api("IPlayerService/GetBadges/v1/", steamid=steamid)
    except Exception as e:
        logger.debug("徽章数据不可用 %s: %s", steamid, e)
        return {}
    b = r.get("response") or {}
    if not b:
        return {}
    total = b.get("player_xp")
    base = b.get("player_xp_needed_current_level")
    need = b.get("player_xp_needed_to_level_up")
    cur = None
    if isinstance(total, int) and isinstance(base, int):
        cur = max(0, total - base)
    return {"count": len(b.get("badges") or []), "xp": total, "level": b.get("player_level"),
            "xp_cur": cur, "xp_need": need}


# 成就定义缓存：schema 一天都不会变，进程内存缓存足够（重启即失效）
_schema_cache: dict = {}
ACH_DIR = DATA / "steam_assets" / "ach"


async def ach_schema(appid) -> dict:
    """成就定义 apiname -> {name, desc, hidden, icon}，中文优先（l=schinese）"""
    key = str(appid)
    if key in _schema_cache:
        return _schema_cache[key]
    m: dict = {}
    try:
        r = await _api("ISteamUserStats/GetSchemaForGame/v2/", appid=appid, l="schinese")
        st = ((r.get("game") or {}).get("availableGameStats") or {})
        for a in st.get("achievements") or []:
            an = a.get("name")
            if an:
                m[an] = {"name": a.get("displayName") or an,
                         "desc": a.get("description") or "",
                         "hidden": bool(a.get("hidden")),
                         "icon": a.get("icon") or ""}
    except Exception as e:
        logger.warning("成就定义获取失败 appid=%s: %s", appid, e)
    _schema_cache[key] = m
    return m


async def ach_icon(appid, apiname: str, url: str) -> Optional[Path]:
    """成就图标（小图，缓存到 data/steam_assets/ach/）

    schema 给的域名是 steamcdn-a.akamaihd.net，国内不稳；
    换 cloudflare / fastly 两个 CDN 镜像试（头像那边也是同一套路）。
    """
    if not url:
        return None
    try:
        ACH_DIR.mkdir(parents=True, exist_ok=True)
        out = ACH_DIR / ("%s_%s.jpg" % (appid, apiname))
        if out.exists() and out.stat().st_size > 0:
            return out
        for host in ("cdn.cloudflare.steamstatic.com", "cdn.akamai.steamstatic.com"):
            try:
                u = re.sub(r"^https?://[^/]+", "https://" + host, url)
                raw = await _get_bytes(u, timeout=20)
                if raw:
                    out.write_bytes(raw)
                    return out
            except Exception as e:
                logger.debug("成就图标下载失败 %s %s: %s", host, apiname, e)
    except Exception as e:
        logger.debug("成就图标缓存失败 %s: %s", apiname, e)
    return None


async def recent_achievements(steamid: str, appid, limit: int = 4) -> list:
    """最近解锁的成就（按 unlocktime 倒序）。拿不到返回空列表"""
    res = await achievements(steamid, appid)
    lst = [a for a in (res.get("list") or []) if a.get("achieved")]
    if not lst:
        return []
    lst.sort(key=lambda x: x.get("unlocktime", 0), reverse=True)
    sch = await ach_schema(appid)
    out = []
    for a in lst[:limit]:
        apiname = a.get("apiname") or ""
        info = sch.get(apiname) or {}
        ts = a.get("unlocktime") or 0
        out.append({
            "apiname": apiname,
            "name": info.get("name") or apiname,
            "date": time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else "",
            "ts": ts,
            "icon_url": info.get("icon") or "",
        })
    return out


# ── 游戏库价值（仓库价值）────────────────────────────────────
PRICE_CACHE = DATA / "steam_assets" / "prices.json"
_PRICE_TTL = 86400          # 价格一天变不了太多，缓存 24h（省掉每次 4 批请求）


def _price_cache() -> dict:
    try:
        return json.loads(PRICE_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _price_save(m: dict) -> None:
    try:
        PRICE_CACHE.parent.mkdir(parents=True, exist_ok=True)
        PRICE_CACHE.write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.debug("价格缓存写入失败: %s", e)


async def library_value(appids, cc: str = "cn", batch: int = 10) -> dict:
    """游戏库总价值：分批查 store 的 price_overview，累加原价与现价。

    ⚠️ 三个实测点：
      1. **必须带 `filters=price_overview`** —— 多 appid 时配 `filters=basic` 会 400
         （单 appid 时 basic 正常，所以这个坑只在批量时才暴露）。
      2. 批量别贪大：实测 100 个一批必超时，25 个一批在 store 慢时也整批失败
         → 默认 10 个一批，失败自动重试一次。
      3. 免费游戏 / 该区未售的没有 price_overview，计入 unpriced（不是 0 元），
         否则总价值会被严重低估。
    结果按 appid 缓存 24h，且**每批都落盘**（整轮几分钟，中途断了不该全白跑）。
    """
    ids = [str(a) for a in appids if a]
    if not ids:
        return {}

    cache = _price_cache()
    now = time.time()
    out = {"original": 0, "final": 0, "priced": 0, "unpriced": 0,
           "currency": "", "count": len(ids)}

    miss = []
    for k in ids:
        c = cache.get(k)
        if c and (now - float(c.get("t") or 0)) < _PRICE_TTL:
            out["original"] += int(c.get("o") or 0)
            out["final"] += int(c.get("f") or 0)
            out["currency"] = c.get("c") or out["currency"]
            if c.get("p"):
                out["priced"] += 1
            else:
                out["unpriced"] += 1
        else:
            miss.append(k)

    for i in range(0, len(miss), batch):
        chunk = miss[i:i + batch]
        d = None
        for attempt in (1, 2):
            try:
                d = await _get_json(f"{STORE}/api/appdetails",
                                    {"appids": ",".join(chunk),
                                     "filters": "price_overview",
                                     "cc": cc, "l": "schinese"}, timeout=35)
                break
            except Exception as e:
                logger.debug("批量查价失败（%d 个, 第 %d 次）: %s: %r",
                             len(chunk), attempt, type(e).__name__, e)
                await asyncio.sleep(1.5)
        if not isinstance(d, dict):
            out["unpriced"] += len(chunk)
            continue
        for k in chunk:
            dd = (d.get(k) or {}).get("data") or {}
            po = dd.get("price_overview") or {}
            if po:
                o = int(po.get("initial") or 0)
                f = int(po.get("final") or 0)
                c = po.get("currency") or ""
                out["original"] += o
                out["final"] += f
                out["currency"] = c or out["currency"]
                out["priced"] += 1
                cache[k] = {"o": o, "f": f, "c": c, "p": 1, "t": now}
            else:
                out["unpriced"] += 1
                cache[k] = {"o": 0, "f": 0, "c": "", "p": 0, "t": now}
        # 每批都落盘：整轮很慢（92 款分批、store 慢时可能几分钟），
        # 只在最后写的话中途超时就全白跑
        _price_save(cache)

    _price_save(cache)
    out["saved"] = max(0, out["original"] - out["final"])
    return out


# ── 游戏名（中文优先，带缓存）────────────────────────────────

_zh_cache: Optional[dict] = None
_zh_dirty = False


def _load_zh() -> dict:
    global _zh_cache
    if _zh_cache is None:
        try:
            _zh_cache = json.loads(NAMES_ZH.read_text(encoding="utf-8"))
        except Exception:
            _zh_cache = {}
    return _zh_cache


def flush_zh() -> None:
    global _zh_dirty
    if not _zh_dirty:
        return
    try:
        NAMES_ZH.parent.mkdir(parents=True, exist_ok=True)
        NAMES_ZH.write_text(json.dumps(_load_zh(), ensure_ascii=False, indent=1),
                            encoding="utf-8")
        _zh_dirty = False
    except Exception as e:
        logger.debug("中文名缓存写入失败: %s", e)


async def zh_name(appid, fallback: str = "") -> str:
    """游戏名（中文优先，查不到退英文）

    注意两点（都实测过）：
      1. 查不到时**不写缓存** —— store 时通时断，写空值会导致以后永远拿不到。
      2. `l=schinese` 对没有中文名的游戏会返回**英文名**（Steam 的行为）；
         真的返回空时再试一次 `l=english`，避免卡片上出现 "appid 1167630" 这种兜底。
    """
    global _zh_dirty
    cache = _load_zh()
    k = str(appid)
    if cache.get(k):
        return cache[k]
    name = ""
    for lang in ("schinese", "english"):
        try:
            # 超时给到 30s：国内到 store 时通时断，20s 会偶发丢名字
            # （表现为卡片上出现 "appid 1167630"，实际 store 是查得到的）
            d = await _get_json(f"{STORE}/api/appdetails",
                                {"appids": appid, "filters": "basic",
                                 "cc": "cn", "l": lang}, timeout=30)
            name = ((d.get(k) or {}).get("data") or {}).get("name") or ""
        except Exception as e:
            logger.debug("游戏名查询失败 appid=%s lang=%s: %s", appid, lang, e)
        if name:
            break
    if name:
        cache[k] = name
        _zh_dirty = True
    return name or fallback


# ── 搜索 / 价格 ───────────────────────────────────────────────

async def search_app(term: str, cc: str = "cn") -> list:
    """按名字搜游戏 → [{appid, name}]（storesearch）"""
    d = await _get_json(f"{STORE}/api/storesearch/",
                        {"term": term, "cc": cc, "l": "schinese"}, timeout=20)
    out = []
    for it in (d.get("items") or []):
        out.append({"appid": it.get("id"), "name": it.get("name") or "",
                    "is_free": bool(it.get("is_free"))})
    return out


async def app_details(appid, cc: str = "cn", lang: str = "schinese") -> dict:
    d = await _get_json(f"{STORE}/api/appdetails",
                        {"appids": appid, "cc": cc, "l": lang}, timeout=25)
    return ((d.get(str(appid)) or {}).get("data")) or {}


async def app_price(appid, cc: str = "cn") -> dict:
    """当前价 + 折扣（免费游戏没有 price_overview，用 is_free 判断）"""
    d = await app_details(appid, cc=cc)
    po = d.get("price_overview") or {}
    return {
        "name": d.get("name") or "",
        "is_free": bool(d.get("is_free")),
        "type": d.get("type") or "",
        "release": (d.get("release_date") or {}).get("date") or "",
        "coming_soon": bool(d.get("release_date", {}).get("coming_soon")),
        "currency": po.get("currency", ""),
        "initial": po.get("initial", 0),
        "final": po.get("final", 0),
        "initial_fmt": po.get("initial_formatted", ""),
        "final_fmt": po.get("final_formatted", ""),
        "discount": po.get("discount_percent", 0),
    }


async def price_multi_region(appid, regions: tuple = ("cn", "us", "ar", "ru")) -> list:
    """地区比价（并发请求，失败的区域跳过）"""
    async def one(cc):
        try:
            p = await app_price(appid, cc=cc)
            return {"cc": cc, "currency": p["currency"], "final_fmt": p["final_fmt"],
                    "is_free": p["is_free"], "discount": p["discount"]}
        except Exception:
            return None
    res = await asyncio.gather(*(one(cc) for cc in regions))
    # 有些区域查得到但没价格（俄罗斯区已停售等）→ 跳过，别在卡片上显示 "-"
    return [r for r in res if r and (r.get("final_fmt") or r.get("is_free"))]


# ── 历史最低价（ITAD，可选）──────────────────────────────────

async def history_low(appid, cc: str = "cn") -> dict:
    """历史最低价 —— Steam 官方 API **不提供**，只能走 IsThereAnyDeal

    需要 ITAD_KEY（免费申请：isthereanydeal.com/apps）。没配就返回 {}，
    调用方降级成"暂无史低数据"，不影响其它信息。

    两步：lookup（appid → ITAD id，GET）→ prices/v3（POST，body 是 id 数组）
    注意：prices/v3 必须 POST，写 GET 会 405（此段未实测，缺 key —— 拿到 key 后校验）
    """
    key = itad_key()
    if not key:
        return {}
    import httpx
    country = (cc or "cn").upper()
    try:
        s = await _get_json("https://api.isthereanydeal.com/games/lookup/v1",
                            {"key": key, "appid": appid}, timeout=20)
        gid = ((s or {}).get("game") or {}).get("id")
        if not gid:
            return {}
        async with httpx.AsyncClient(timeout=20, trust_env=False, verify=False) as c:
            r = await c.post("https://api.isthereanydeal.com/games/prices/v3",
                             params={"key": key, "country": country}, json=[gid])
            data = r.json()
        if not data:
            return {}
        item = data[0] if isinstance(data, list) else {}
        low = ((item.get("historyLow") or {}).get("all") or {})
        deal = ((item.get("deals") or [{}])[0].get("price") or {})
        return {"low": low.get("amount"), "currency": low.get("currency", ""),
                "current": deal.get("amount"), "country": country}
    except Exception as e:
        logger.debug("ITAD 查询失败 appid=%s: %s", appid, e)
        return {}


# ── 素材缓存 ──────────────────────────────────────────────────

def _shrink(raw: bytes, wh, quality: int = 85) -> bytes:
    try:
        import io

        from PIL import Image
        im = Image.open(io.BytesIO(raw)).convert("RGB")
        im.thumbnail(wh, Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=quality, optimize=True)
        return buf.getvalue()
    except Exception:
        return raw


async def game_header(appid) -> Path:
    """游戏横幅（本地缓存优先；缺则下载 + 压缩）。失败返回不存在的 Path"""
    cache = ASSETS / "games" / f"{appid}.jpg"
    if not cache.exists():
        try:
            raw = await _get_bytes(CDN_HEADER.format(appid=appid))
            if raw and len(raw) > 500:
                cache.parent.mkdir(parents=True, exist_ok=True)
                await asyncio.to_thread(cache.write_bytes, _shrink(raw, (360, 170)))
        except Exception as e:
            logger.debug("横幅下载失败 appid=%s: %s", appid, e)
    return cache


async def avatar_file(steamid: str, url: str) -> Path:
    """头像缓存（avatars.steamstatic.com 不通时换 fastly 镜像）"""
    cache = ASSETS / "avatar" / f"{steamid}.jpg"
    if not cache.exists() and url:
        for host in AVATAR_HOSTS:
            try:
                raw = await _get_bytes(url.replace("avatars.steamstatic.com", host))
                if raw and len(raw) > 500:
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    await asyncio.to_thread(cache.write_bytes, _shrink(raw, (200, 200), 88))
                    break
            except Exception:
                continue
    return cache


# ── 自记价格（Steam 官方没有史低，至少攒自己的记录）────────────

def record_price(appid, final: int, currency: str) -> Optional[int]:
    """记录本次看到的价格

    返回：破纪录时返回**旧的**最低价（用于"比你上次见到的还低"提示），否则 None。
    Steam 官方没有史低接口，这是自记数据 —— 用得越久越有价值。
    """
    if not final:
        return None
    k = f"{appid}:{currency}"
    try:
        m = json.loads(PRICE_HIST.read_text(encoding="utf-8"))
    except Exception:
        m = {}
    prev = (m.get(k) or {}).get("low")
    if prev is None:
        m[k] = {"low": final, "at": int(time.time())}
        _write_hist(m)
        return None
    if final < prev:
        m[k] = {"low": final, "at": int(time.time())}
        _write_hist(m)
        return prev
    return None


def _write_hist(m: dict) -> None:
    try:
        PRICE_HIST.parent.mkdir(parents=True, exist_ok=True)
        PRICE_HIST.write_text(json.dumps(m, ensure_ascii=False, indent=1),
                              encoding="utf-8")
    except Exception as e:
        logger.debug("价格历史写入失败: %s", e)

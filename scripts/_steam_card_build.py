#!/usr/bin/env python3
"""Steam 资料卡构建（数据源验证 + 原型）

本地运行：拉 Steam Web API → 缓存素材 → 生成自包含 HTML
    STEAM_KEY=xxx python3 scripts/_steam_card_build.py [steamid]

产物：data/_steam_card.html（可直接丢给 Playwright 截图）
素材缓存：data/steam_assets/games/{appid}.jpg、avatar/{steamid}.jpg（缺则自动下载+压缩）
"""
import base64
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "data" / "steam_assets"
NAMES_ZH = ASSETS / "games" / "names_zh.json"
TPL = ROOT / "data" / "templates" / "steam_profile_card.html"
OUT = ROOT / "data" / "_steam_card.html"

KEY = os.environ.get("STEAM_KEY", "").strip()
SID = (sys.argv[1] if len(sys.argv) > 1 else "76561199427581023").strip()
API = "https://api.steampowered.com/"
CDN = "https://cdn.cloudflare.steamstatic.com/steam/apps/"
UA = {"User-Agent": "Mozilla/5.0"}
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

STATE_TXT = {0: "离线", 1: "在线", 2: "忙碌", 3: "离开", 4: "打盹", 5: "想交易", 6: "想玩游戏"}


def api(path, **kw):
    """带重试的 API 调用（本地到 Steam 偶发慢响应/超时）"""
    if KEY:
        kw["key"] = KEY
    url = API + path + "?" + urllib.parse.urlencode(kw)
    last = None
    for _ in range(3):
        try:
            return json.loads(OPENER.open(url, timeout=45).read().decode("utf-8"))
        except Exception as e:      # 网络抖动 → 重试
            last = e
    raise last


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    return OPENER.open(req, timeout=timeout).read()


def shrink(raw: bytes, wh, quality=85) -> bytes:
    """压到指定尺寸（PIL 缺失时原样返回）"""
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


def data_uri(path: Path) -> str:
    if not path.exists():
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode()


def game_img(appid: int) -> str:
    """游戏横幅：本地缓存优先，缺则下载 + 压缩"""
    cache = ASSETS / "games" / f"{appid}.jpg"
    if not cache.exists():
        try:
            raw = fetch(CDN + f"{appid}/header.jpg", timeout=30)
            if raw and len(raw) > 500:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_bytes(shrink(raw, (360, 170)))
        except Exception as e:
            print(f"    ! {appid} 横幅下载失败: {e}")
    return data_uri(cache)


_ZH_CACHE = None


def zh_name(appid, fallback=""):
    """中文游戏名：store API + 本地缓存

    注意：Web API 的 GetOwnedGames 只返回英文名（如 "A Dance of Fire and Ice"），
    中文名只能走 store 的 appdetails（l=schinese），所以要单独查 + 缓存。
    """
    global _ZH_CACHE
    if _ZH_CACHE is None:
        try:
            _ZH_CACHE = json.loads(NAMES_ZH.read_text(encoding="utf-8"))
        except Exception:
            _ZH_CACHE = {}
    k = str(appid)
    if not _ZH_CACHE.get(k):
        # 空值不写进缓存 —— store.steampowered.com 国内时通时断（实测 WinError 10060），
        # 写空值会导致以后永远拿不到中文名
        nm = ""
        try:
            u = ("https://store.steampowered.com/api/appdetails"
                 f"?appids={appid}&filters=basic&cc=cn&l=schinese")
            dd = (json.loads(fetch(u, timeout=25).decode("utf-8")).get(k) or {}).get("data") or {}
            nm = (dd.get("name") or "").strip()
        except Exception as e:
            print(f"    ! {appid} 中文名查询失败（回退英文名）: {type(e).__name__}")
        if nm:
            _ZH_CACHE[k] = nm
    return _ZH_CACHE.get(k) or fallback


def save_zh_cache():
    try:
        NAMES_ZH.parent.mkdir(parents=True, exist_ok=True)
        NAMES_ZH.write_text(json.dumps(_ZH_CACHE or {}, ensure_ascii=False, indent=1),
                            encoding="utf-8")
    except Exception:
        pass


def avatar_uri(url: str) -> str:
    cache = ASSETS / "avatar" / f"{SID}.jpg"
    if not cache.exists() and url:
        # avatars.steamstatic.com 在部分网络不可达 → 换 fastly 镜像
        for host in ("avatars.fastly.steamstatic.com", "avatars.cloudflare.steamstatic.com"):
            try:
                u = url.replace("avatars.steamstatic.com", host)
                raw = fetch(u, timeout=25)
                if raw and len(raw) > 500:
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    cache.write_bytes(shrink(raw, (200, 200), 88))
                    break
            except Exception:
                continue
    return data_uri(cache)


def main():
    if not KEY:
        print("缺少 STEAM_KEY 环境变量")
        return 1

    print("[1] 玩家摘要")
    p = api("ISteamUser/GetPlayerSummaries/v2/", steamids=SID)["response"]["players"][0]
    game_now = p.get("gameextrainfo") or ""
    online = p.get("personastate", 0) != 0 or bool(game_now)
    state_text = ("正在玩 " + game_now) if game_now else STATE_TXT.get(p.get("personastate"), "离线")
    state_kind = "ingame" if game_now else ("" if online else "off")
    print(f"    {p.get('personaname')} / {state_text}")

    # 注意：不加 include_appinfo —— 带它响应大 10 倍（本地实测 15KB 要 100s 才回），
    #    而游戏名反正要单独查 store（为了中文名），所以这里只要 appid + 时长
    print("[2] 游戏库")
    owned = api("IPlayerService/GetOwnedGames/v1/", steamid=SID,
                include_played_free_games=1).get("response", {})
    games = owned.get("games") or []
    print(f"    {owned.get('game_count', len(games))} 款，累计 {sum(g.get('playtime_forever',0) for g in games)/60:.0f} 小时")

    print("[3] 最近两周")
    recent_raw = api("IPlayerService/GetRecentlyPlayedGames/v1/", steamid=SID,
                     count=6).get("response", {}).get("games") or []
    print(f"    {len(recent_raw)} 款")

    print("[4] 成就（ADOFAI 977950）")
    try:
        ach = api("ISteamUserStats/GetPlayerAchievements/v1/", steamid=SID,
                  appid=977950).get("playerstats", {}).get("achievements") or []
        ach_got = sum(1 for a in ach if a.get("achieved"))
    except Exception as e:
        print(f"    ! 成就失败: {e}")
        ach, ach_got = [], 0

    print("[5] 素材（缺则下载）")
    top = sorted(games, key=lambda g: g.get("playtime_forever", 0), reverse=True)[:8]
    recent = sorted(recent_raw, key=lambda g: g.get("playtime_2weeks", 0), reverse=True)[:6]
    for g in top:
        a = g.get("appid")
        print(f"    {g.get('name','?')[:28]:30s} {game_img(a) and 'ok' or 'MISS'}")
    for g in recent:
        game_img(g.get("appid"))
    av = avatar_uri(p.get("avatarfull") or p.get("avatarmedium") or "")
    print(f"    头像 {'ok' if av else 'MISS'}")

    payload = {
        "profile": {
            "name": p.get("personaname") or "",
            "steamid": SID,
            "level": None,          # 下面补
            "created": datetime.fromtimestamp(p["timecreated"]).strftime("%Y-%m-%d") if p.get("timecreated") else "",
            "online": online,
            "state_kind": state_kind,
            "state_text": state_text,
            "avatar": av,
        },
        "stats": {
            "games": owned.get("game_count", len(games)),
            "hours": round(sum(g.get("playtime_forever", 0) for g in games) / 60),
            "ach_got": ach_got,
            "ach_total": len(ach),
        },
        "recent": [{"name": zh_name(g.get("appid"), g.get("name", "")),
                    "hours": round(g.get("playtime_2weeks", 0) / 60, 1),
                    "appid": g.get("appid"), "img": game_img(g.get("appid"))} for g in recent],
        "recent_sum": round(sum(g.get("playtime_2weeks", 0) for g in recent) / 60, 1),
        "top": [{"name": zh_name(g.get("appid"), g.get("name", "")),
                 "hours": round(g.get("playtime_forever", 0) / 60, 1),
                 "appid": g.get("appid"), "img": game_img(g.get("appid"))} for g in top],
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    try:
        payload["profile"]["level"] = api("IPlayerService/GetSteamLevel/v1/",
                                          steamid=SID).get("response", {}).get("player_level")
    except Exception:
        pass

    font = (ASSETS / "font" / "harmonyos_medium_latin.woff2").read_bytes()
    html = TPL.read_text(encoding="utf-8")
    html = html.replace("__FONT_B64__", base64.b64encode(font).decode(), 1)
    js = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    html = html.replace("/*__CARD_DATA__*/null", js, 1)
    # 防呆：模板注释里若也写了这两个字面量，replace 会抢走第一处 →
    # 数据被塞进注释、真注入点空着（已踩过一次，表现为"渲染出来全是 —"）
    assert "__FONT_B64__" not in html, "字体占位符未被替换：检查模板注释里是否有字面量"
    assert "__CARD_DATA__" not in html, "数据占位符未被替换：检查模板注释里是否有字面量"
    OUT.write_text(html, encoding="utf-8")
    save_zh_cache()
    print(f"\n[6] 生成 {OUT.relative_to(ROOT)}  ({len(html)/1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

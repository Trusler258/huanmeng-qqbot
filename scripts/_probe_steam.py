#!/usr/bin/env python3
"""Steam Web API 探针（只读）

用法:
    STEAM_KEY=xxxx python3 scripts/_probe_steam.py [vanity ...]

做四件事:
    1. 验证 key 是否有效（并用它拉全量接口注册表，和不带 key 的公开子集对比）
    2. 把候选自定义 URL 解析成 SteamID64
    3. 拉玩家摘要 / 游戏库 / 最近游玩 / 等级
    4. 明确报告「哪些接口因隐私设置返回空」

key 只从环境变量读，不写文件、不进仓库。
"""
import json
import os
import sys
import urllib.parse
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

KEY = os.environ.get("STEAM_KEY", "").strip()
if not KEY:
    print("缺少 STEAM_KEY 环境变量")
    sys.exit(1)

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 直连，不走代理
BASE = "https://api.steampowered.com/"

DEFAULT_VANITY = ["huangplayer-trusler", "trusler", "trusler258", "huangplayer233"]


def api(path, use_key=True, **params):
    if use_key:
        params["key"] = KEY
    url = BASE + path + "?" + urllib.parse.urlencode(params)
    with OPENER.open(url, timeout=25) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    vanity_list = sys.argv[1:] or DEFAULT_VANITY

    # ── 1. key 有效性 + 全量注册表 ──
    print("=" * 62)
    try:
        full = api("ISteamWebAPIUtil/GetSupportedAPIList/v1/")
        ifs = full.get("apilist", {}).get("interfaces", [])
        n_methods = sum(len(i.get("methods", [])) for i in ifs)
        print("[1] key 有效 ✓  全量注册表: %d 个命名空间 / %d 个方法" % (len(ifs), n_methods))
        for want in ("IPlayerService", "ISteamUser", "ISteamUserStats", "IPublishedFileService"):
            hit = next((i for i in ifs if i.get("name") == want), None)
            if hit:
                ms = [m.get("name", "") for m in hit.get("methods", [])]
                print("    %-22s %2d 个: %s" % (want, len(ms), ", ".join(ms[:8])))
    except Exception as e:
        print("[1] key 校验失败: %s" % e)
        return 1

    # ── 2. vanity → SteamID64 ──
    print("")
    print("[2] 解析自定义 URL（域名提示 huangplayer-trusler.github.io）")
    sid = None
    for v in vanity_list:
        try:
            r = api("ISteamUser/ResolveVanityURL/v1/", vanityurl=v).get("response", {})
        except Exception as e:
            print("    %-22s 请求失败: %s" % (v, e))
            continue
        if r.get("success") == 1:
            sid = r.get("steamid")
            print("    %-22s → %s  ★" % (v, sid))
            break
        print("    %-22s 不存在 (success=%s)" % (v, r.get("success")))
    if not sid:
        print("    没能解析出 SteamID64 —— 把个人资料链接发我即可")
        return 0

    # ── 3. 玩家摘要 ──
    print("")
    print("[3] 玩家摘要 GetPlayerSummaries  steamid=%s" % sid)
    STATE = {0: "离线", 1: "在线", 2: "忙碌", 3: "离开", 4: "打盹", 5: "想交易", 6: "想玩游戏"}
    try:
        p = api("ISteamUser/GetPlayerSummaries/v2/", steamids=sid)["response"]["players"][0]
        print("    昵称     : %s" % p.get("personaname"))
        print("    状态     : %s" % STATE.get(p.get("personastate"), p.get("personastate")))
        print("    正在玩   : %s" % (p.get("gameextrainfo") or "（无）"))
        print("    资料公开 : communityvisibilitystate=%s (3=公开)" % p.get("communityvisibilitystate"))
        print("    注册时间 : %s" % __import__("datetime").datetime.fromtimestamp(
            p.get("timecreated", 0)).strftime("%Y-%m-%d") if p.get("timecreated") else "    注册时间 : (未公开)")
    except Exception as e:
        print("    失败: %s" % e)

    # ── 4. 游戏库 / 最近游玩 / 等级 ──
    print("")
    print("[4] 游戏库与时长（需「游戏详情」公开）")
    try:
        r = api("IPlayerService/GetOwnedGames/v1/", steamid=sid,
                include_appinfo=1, include_played_free_games=1)["response"]
        games = r.get("games", [])
        print("    游戏总数 : %s 个" % r.get("game_count", len(games)))
        if not games:
            print("    ⚠ 返回空 —— 多半是「游戏详情」未设为公开（不是接口坏了）")
        else:
            top = sorted(games, key=lambda g: g.get("playtime_forever", 0), reverse=True)[:6]
            print("    时长榜前 6:")
            for g in top:
                print("      %-34s %6.1f 小时" % (g.get("name", "?"),
                                                 g.get("playtime_forever", 0) / 60.0))
    except Exception as e:
        print("    失败: %s" % e)

    print("")
    print("[5] 最近两周游玩 GetRecentlyPlayedGames")
    try:
        r = api("IPlayerService/GetRecentlyPlayedGames/v1/", steamid=sid)["response"]
        gs = r.get("games", [])
        print("    总数 = %s" % r.get("total_count", len(gs)))
        for g in gs[:5]:
            print("      %-34s 两周 %.1f 小时（累计 %.1f）" % (
                g.get("name", "?"),
                g.get("playtime_2weeks", 0) / 60.0,
                g.get("playtime_forever", 0) / 60.0))
    except Exception as e:
        print("    失败: %s" % e)

    print("")
    try:
        lv = api("IPlayerService/GetSteamLevel/v1/", steamid=sid)["response"]
        print("[6] Steam 等级 = %s" % lv.get("player_level"))
    except Exception as e:
        print("[6] 等级查询失败: %s" % e)

    print("")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""跑一遍「代理 API 使用教程」里的所有示例，确认文档可照抄执行

用法（服务器上）：STEAM_PROXY_TOKEN=xxx python3 scripts/_probe_proxy_doc.py
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = os.environ.get("STEAM_PROXY", "https://steamapi.truslerweb.dpdns.org")
TOKEN = os.environ.get("STEAM_PROXY_TOKEN", "")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
SID = "76561199427581023"


def call(ops=None, token=None, ua=UA, timeout=60):
    """返回 (http_status, body_text)"""
    if ops is None:
        req = urllib.request.Request(BASE + "/", headers={"User-Agent": ua} if ua else {})
    else:
        h = {"Content-Type": "application/json"}
        if ua:
            h["User-Agent"] = ua
        tk = TOKEN if token is None else token
        if tk:
            h["X-Proxy-Token"] = tk
        req = urllib.request.Request(BASE + "/", data=json.dumps({"ops": ops}).encode(),
                                     method="POST", headers=h)
    try:
        r = OPENER.open(req, timeout=timeout)
        return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, "%s: %s" % (type(e).__name__, e)


def show(title, st, body, limit=420):
    print("── %s" % title)
    print("   HTTP %s" % st)
    print("   %s" % (body[:limit] + ("…" if len(body) > limit else "")))
    print()


def try_parse(b, fn):
    try:
        fn(json.loads(b))
    except Exception as e:
        print("   解析失败: %s: %r" % (type(e).__name__, e))
        print()


print("=== 1. 健康检查 GET / ===")
st, b = call()
show("GET /", st, b)


def _p1(d):
    p = ((d["r"][0]["data"].get("response") or {}).get("players") or [{}])[0]
    print("   解析: 昵称=%s 状态=%s 可见性=%s 在玩=%s" % (
        p.get("personaname"), p.get("personastate"),
        p.get("communityvisibilitystate"), p.get("gameextrainfo") or "（无）"))
    print()


print("=== 2. 最小可用：查在线状态（单 op）===")
st, b = call([{"k": "api", "p": "ISteamUser/GetPlayerSummaries/v2/",
               "q": {"steamids": SID}}])
show("POST 单 op", st, b)
try_parse(b, _p1)


def _p3(d):
    lv = d["r"][0]["data"]["response"].get("player_level")
    bad = d["r"][1]["data"]["response"]
    own = d["r"][2]["data"]["response"]
    print("   解析: 等级=%s 徽章=%s XP=%s 游戏=%s" % (
        lv, len(bad.get("badges") or []), bad.get("player_xp"), own.get("game_count")))
    print("   Worker 侧耗时 = %s ms" % d.get("ms"))
    print()


print("=== 3. 多 op 合并（3 个请求一趟）===")
st, b = call([
    {"k": "api", "p": "IPlayerService/GetSteamLevel/v1/", "q": {"steamid": SID}},
    {"k": "api", "p": "IPlayerService/GetBadges/v1/", "q": {"steamid": SID}},
    {"k": "api", "p": "IPlayerService/GetOwnedGames/v1/",
     "q": {"steamid": SID, "include_played_free_games": 1}},
])
show("POST 多 op", st, b, 200)
try_parse(b, _p3)


def _p4(d):
    r0 = d["r"][0]
    data = r0.get("data") or {}
    have = sum(1 for v in data.values()
               if ((v or {}).get("data") or {}).get("price_overview"))
    print("   Worker 内部批数=%s  有价格 %d/20  Worker 侧耗时 %s ms"
          % (r0.get("batches"), have, d.get("ms")))
    po = (((data.get("977950") or {}).get("data") or {}).get("price_overview") or {})
    print("   样例: 977950 -> %s" % po.get("final_formatted"))
    print()


print("=== 4. store 批量（20 个 appid，>15 自动分批）===")
ids = ["730", "570", "440", "977950", "1426210", "431960", "4000", "1144400",
       "774181", "322330", "1167630", "550", "1240210", "3036080", "2835570",
       "391540", "945360", "1905180", "1114940", "367500"]
st, b = call([{"k": "store", "p": "api/appdetails",
               "q": {"appids": ",".join(ids), "filters": "price_overview"}}])
try_parse(b, _p4)


def _p5(d):
    dd = ((d["r"][0]["data"].get("1426210") or {}).get("data") or {})
    print("   解析: %s (%s) 发售 %s" % (dd.get("name"), dd.get("type"),
                                        (dd.get("release_date") or {}).get("date")))
    print()


print("=== 5. 游戏名（单 appid + filters=basic）===")
st, b = call([{"k": "store", "p": "api/appdetails",
               "q": {"appids": "1426210", "filters": "basic"}}])
show("filters=basic 单个", st, b, 260)
try_parse(b, _p5)

print("=== 6. 错误情形 ===")
st, b = call([{"k": "api", "p": "ISteamUser/GetPlayerSummaries/v2/", "q": {"steamids": SID}}],
             token="wrong-token")
print("   6.1 错误 token          -> HTTP %s  %s" % (st, b[:90]))
st, b = call([{"k": "api", "p": "../../etc/passwd", "q": {}}])
print("   6.2 白名单外 path        -> HTTP %s  %s" % (st, b[:130]))
st, b = call([{"k": "evil", "p": "x", "q": {}}])
print("   6.3 未知 kind            -> HTTP %s  %s" % (st, b[:130]))
st, b = call([{"k": "api", "p": "ISteamUser/GetPlayerSummaries/v2/", "q": {}}] * 41)
print("   6.4 op 超 40 个          -> HTTP %s  %s" % (st, b[:130]))
st, b = call([{"k": "api", "p": "ISteamUser/GetPlayerSummaries/v2/", "q": {"steamids": SID}}],
             ua=None)
print("   6.5 无 UA（urllib 默认） -> HTTP %s  %s" % (st, b[:90]))
st, b = call([{"k": "api", "p": "NoSuchInterface/Foo/v1/", "q": {}}])
print("   6.6 白名单前缀外接口     -> HTTP %s  %s" % (st, b[:130]))
print()

print("=== 7. 连续请求耗时（连接复用）===")
for i in (1, 2, 3):
    t = time.perf_counter()
    st, b = call([{"k": "api", "p": "ISteamUser/GetPlayerSummaries/v2/",
                   "q": {"steamids": SID}}])
    print("   第 %d 次 %.0f ms (HTTP %s)" % (i, (time.perf_counter() - t) * 1000, st))

print()
print("=== 8. httpx 客户端示例（文档第 5.1 节的代码）===")
try:
    import asyncio
    import httpx

    async def _httpx_demo():
        cli = httpx.AsyncClient(timeout=60, trust_env=False, verify=False,
                                headers={"User-Agent": UA})
        r = await cli.post(BASE, json={"ops": [
            {"k": "api", "p": "ISteamUser/GetPlayerSummaries/v2/", "q": {"steamids": SID}},
            {"k": "api", "p": "IPlayerService/GetSteamLevel/v1/", "q": {"steamid": SID}},
        ]}, headers={"X-Proxy-Token": TOKEN})
        d = r.json()
        print("   多 op: HTTP %s 整批 ok=%s Worker 耗时 %s ms"
              % (r.status_code, d.get("ok"), d.get("ms")))
        nm = ((d["r"][0]["data"].get("response") or {}).get("players") or [{}])[0] \
            .get("personaname")
        print("   昵称 = %s | 等级 = %s"
              % (nm, d["r"][1]["data"]["response"].get("player_level")))

        ids = ["730", "570", "440", "977950", "1426210"]
        r2 = await cli.post(BASE, json={"ops": [
            {"k": "store", "p": "api/appdetails",
             "q": {"appids": ",".join(ids), "filters": "price_overview"}}]},
            headers={"X-Proxy-Token": TOKEN})
        data = r2.json()["r"][0]["data"]
        for aid in ids:
            po = ((data.get(aid) or {}).get("data") or {}).get("price_overview") or {}
            print("   %s -> %s" % (aid, po.get("final_formatted") or "无价格（免费/锁区）"))

        # 连续 3 次，看共享 client 的连接复用效果（对比上面 urllib 的每趟 ~1s）
        for i in (1, 2, 3):
            t0 = time.perf_counter()
            await cli.post(BASE, json={"ops": [
                {"k": "api", "p": "ISteamUser/GetPlayerSummaries/v2/",
                 "q": {"steamids": SID}}]}, headers={"X-Proxy-Token": TOKEN})
            print("   httpx 第 %d 次往返 %.0f ms" % (i, (time.perf_counter() - t0) * 1000))
        await cli.aclose()

    asyncio.run(_httpx_demo())
except Exception as e:
    print("   httpx 示例失败: %s: %r" % (type(e).__name__, e))

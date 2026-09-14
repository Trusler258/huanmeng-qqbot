"""pass3：数据结构深挖——真实玩家战绩 + 榜单样本 + 端点穷举"""
import httpx, json, pathlib
BASE = "https://www.wdsj.net/nexus"
H = {"Referer": "https://www.wdsj.net/nexus/stats",
     "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124 Safari/537.36"}
out = pathlib.Path('G:/py/qqbot/_probe/data'); out.mkdir(exist_ok=True)

def fetch(path, referer="https://www.wdsj.net/nexus/stats"):
    return httpx.get(f"{BASE}{path}", headers={**H, "Referer": referer}, timeout=25)

# 1) 从榜单拿真实玩家名
r = fetch("/api/v1/leaderboards/bedwars-wins?type=ALLTIME", "https://www.wdsj.net/nexus/leaderboards")
d = r.json()["data"]
print("=== 榜单样本结构 ===")
print("顶层键:", list(d.keys()))
ent = d.get("entries") or d.get("players") or []
if ent:
    print("条目键:", list(ent[0].keys()))
    print("前 3 名:", json.dumps(ent[:3], ensure_ascii=False)[:400])
    names = [e.get("name") or e.get("player") or e.get("username") for e in ent[:5]]
    print("可用玩家名:", names)
else:
    print("完整:", json.dumps(d, ensure_ascii=False)[:500])

# 2) 用真实玩家查战绩，看返回结构
r2 = fetch("/api/v1/templates")
tpl = r2.json()["data"]
print("\n=== /api/v1/templates 完整返回 ===")
print(json.dumps(tpl, ensure_ascii=False)[:700])

if ent:
    nm = names[0]
    r3 = fetch(f"/api/v1/players/name:{nm}/templates/bedwars-stats")
    print(f"\n=== 玩家战绩结构（{nm}）===")
    j = r3.json()
    print("code:", j.get("code"), "| data 键:", list(j.get("data", {}).keys()))
    (out/"player_stats.json").write_text(json.dumps(j, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(j, ensure_ascii=False)[:900])

# 3) 端点穷举
print("\n=== 端点穷举 ===")
for c in ["/api/v1/players/name:Notch", "/api/v1/players/name:Notch/profile",
          "/api/v1/guilds", "/api/v1/titles", "/api/v1/tags", "/api/v1/status",
          "/api/v1/player-heads", "/api/v1/players/name:Notch/head.png",
          "/api/v1/leaderboards/bedwars-wins", "/api/v1/leaderboards/bedwars-wins?type=MONTHLY"]:
    try:
        rr = fetch(c)
        body = rr.text[:110].replace("\n", " ")
        print(f"   {rr.status_code}  {c}\n        {body}")
    except Exception as e:
        print(f"   ERR {c}: {e}")

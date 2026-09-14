"""pass4：真实玩家验证——榜单字段改名 owner + 战绩 labels/卡片结构"""
import httpx, json, pathlib
BASE = "https://www.wdsj.net/nexus"
H = {"Referer": "https://www.wdsj.net/nexus/stats",
     "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124 Safari/537.36"}
out = pathlib.Path('G:/py/qqbot/_probe/data'); out.mkdir(exist_ok=True)

def fetch(path, referer="https://www.wdsj.net/nexus/stats"):
    return httpx.get(f"{BASE}{path}", headers={**H, "Referer": referer}, timeout=25)

# 1) 榜单：取值字段
r = fetch("/api/v1/leaderboards/bedwars-wins?type=ALLTIME", "https://www.wdsj.net/nexus/leaderboards")
d = r.json()["data"]
print("=== 榜单条目完整字段 ===")
e = d["entries"][0]
print(json.dumps(e, ensure_ascii=False))
print("榜单板信息:", json.dumps(d["board"], ensure_ascii=False)[:300])
print("currentSeason:", json.dumps(d.get("currentSeason"), ensure_ascii=False)[:200])

# 2) 月榜/周榜对比（看是否有 season 概念）
for t in ["MONTHLY", "WEEKLY", "DAILY", "SEASONAL"]:
    try:
        rr = fetch(f"/api/v1/leaderboards/bedwars-wins?type={t}")
        j = rr.json()
        if j.get("code") == 0:
            dd = j["data"]
            print(f"  {t}: entries={len(dd['entries'])} season={json.dumps(dd.get('currentSeason'), ensure_ascii=False)[:120]}")
        else:
            print(f"  {t}: code={j.get('code')} msg={j.get('message','')[:60]}")
    except Exception as ex:
        print(f"  {t}: ERR {ex}")

# 3) 真实玩家战绩（owner 字段）→ 店主名
name = d["entries"][0]["owner"]
r2 = fetch(f"/api/v1/players/name:{name}/templates/bedwars-stats")
j2 = r2.json()
data2 = j2["data"]
print(f"\n=== 真实玩家战绩（{name}）===")
print("player:", data2["player"])
print("values 键数:", len(data2["values"]), "| 示例:", json.dumps(dict(list(data2["values"].items())[:6]), ensure_ascii=False))
print("labels 键数:", len(data2["labels"]))
print("headerCards:", json.dumps(data2.get("headerCards"), ensure_ascii=False)[:300])
print("summaryCards:", json.dumps(data2.get("summaryCards"), ensure_ascii=False)[:300])
print("snapshotKey:", data2.get("snapshotKey"))
print("imageUrl:", data2.get("imageUrl"))
(out/"real_player_bedwars.json").write_text(json.dumps(j2, ensure_ascii=False, indent=1), encoding="utf-8")

# 4) 其他新模板各取一个真实玩家验证（luckypillars / csgo 等新领域）
tpls = ["luckypillars-stats", "csgo-stats", "naturaldisasters-stats", "buildbattle-stats"]
for t in tpls:
    try:
        rr = fetch(f"/api/v1/players/name:{name}/templates/{t}")
        jj = rr.json()
        if jj.get("code") == 0:
            dd = jj["data"]
            print(f"  ✓ {t}: displayName={dd.get('displayName')} values={len(dd['values'])} labels={len(dd['labels'])}")
        else:
            print(f"  ✗ {t}: code={jj.get('code')} {jj.get('message','')[:60]}")
    except Exception as ex:
        print(f"  ✗ {t}: ERR {ex}")

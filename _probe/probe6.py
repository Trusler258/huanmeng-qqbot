"""pass6：SEASON 周期 + uid 标识 + imageUrl 图片可下载性验证"""
import httpx, json
BASE = "https://www.wdsj.net/nexus"
H = {"Referer": "https://www.wdsj.net/nexus/stats",
     "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124 Safari/537.36"}
def fetch(path, referer="https://www.wdsj.net/nexus/stats"):
    return httpx.get(f"{BASE}{path}", headers={**H, "Referer": referer}, timeout=25)

# 1) SEASON 周期
try:
    r = fetch("/api/v1/leaderboards/bedwars-wins?type=SEASON")
    j = r.json()
    if j.get("code") == 0:
        d = j["data"]
        print("SEASON: OK", "entries=", len(d["entries"]), "season=", json.dumps(d.get("currentSeason"), ensure_ascii=False)[:150])
        print("  列表板periods:", d["board"].get("periods"))
    else:
        print("SEASON:", j)
except Exception as e:
    print("SEASON ERR:", e)

# 2) uid 标识
try:
    r = fetch("/api/v1/players/uid:4941/templates/bedwars-stats")
    j = r.json()
    if j.get("code") == 0:
        print("uid 查询 OK:", j["data"]["player"]["name"], j["data"]["player"]["uid"])
    else:
        print("uid 查询:", j.get("code"), j.get("message","")[:80])
except Exception as e:
    print("uid ERR:", e)

# 3) imageUrl 快照图
try:
    r = fetch("/api/v1/images/20260914_4495f116")
    print("imageUrl 下载:", r.status_code, r.headers.get("content-type"), "len=", len(r.content))
except Exception as e:
    print("image ERR:", e)

# 4) 头像端点（owner 里也有 headImageUrl）
try:
    r = fetch("/api/v1/player-heads/dongtians/head.png")
    print("头像下载:", r.status_code, r.headers.get("content-type"), "len=", len(r.content))
except Exception as e:
    print("head ERR:", e)

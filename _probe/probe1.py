"""wdsj API 探测 pass1：端点发现"""
import httpx, json, sys

BASE = "https://www.wdsj.net/nexus"
H = {"Referer": "https://www.wdsj.net/nexus/stats",
     "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}

def get(path, referer=None, raw=False):
    h = dict(H)
    if referer: h["Referer"] = referer
    try:
        r = httpx.get(f"{BASE}{path}", headers=h, timeout=20, follow_redirects=True)
        body = r.text[:600]
        return r.status_code, (r.content[:80] if raw else body)
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"

# 1) 排行榜列表（已知）
st, body = get("/api/v1/leaderboards", referer="https://www.wdsj.net/nexus/leaderboards")
print(f"[1] /api/v1/leaderboards → {st}")
if st == 200:
    try:
        d = json.loads(body if len(body) < 600 else body)
    except Exception:
        # 重新取完整
        r = httpx.get(f"{BASE}/api/v1/leaderboards", headers=dict(H, Referer="https://www.wdsj.net/nexus/leaderboards"), timeout=20)
        d = r.json()
    boards = d.get("data", {}).get("boards", [])
    print(f"    boards 数量: {len(boards)}")
    for b in boards[:50]:
        if isinstance(b, dict):
            print("    -", json.dumps(b, ensure_ascii=False)[:150])
        else:
            print("    -", b)

# 2) 探测可能的元数据端点
cands = [
    "/api/v1/templates", "/api/v1/meta", "/api/v1/config", "/api/v1/games",
    "/api/v1/players", "/api/v1/stats", "/api/v1/leaderboards/periods",
    "/api/v1/player-heads/Notch/head.png",
]
print("\n[2] 端点探测")
for c in cands:
    st, body = get(c)
    kind = "二进制" if isinstance(body, bytes) else str(body)[:90].replace("\n", " ")
    print(f"    {st}  {c}  {kind}")

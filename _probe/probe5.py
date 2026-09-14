"""pass5：新模板补齐 + 86 榜单 vs 现有别名覆盖缺口"""
import httpx, json, pathlib
BASE = "https://www.wdsj.net/nexus"
H = {"Referer": "https://www.wdsj.net/nexus/stats",
     "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124 Safari/537.36"}
out = pathlib.Path('G:/py/qqbot/_probe/data'); out.mkdir(exist_ok=True)

def fetch(path, referer="https://www.wdsj.net/nexus/stats"):
    return httpx.get(f"{BASE}{path}", headers={**H, "Referer": referer}, timeout=25)

# 1) arena-modern-stats 模板信息
r = fetch("/api/v1/templates")
tpls = r.json()["data"]["templates"]
tpl_items = r.json()["data"]["templateItems"]
print("=== 18 模板 ===")
for t in tpls:
    it = next((x for x in tpl_items if x["id"]==t), {})
    print(f"  {t:<30} {it.get('displayName','')}")
(out/"templates.json").write_text(json.dumps(r.json()["data"], ensure_ascii=False, indent=1), encoding="utf-8")

# 2) 榜单全量 + 别名覆盖
r2 = fetch("/api/v1/leaderboards", "https://www.wdsj.net/nexus/leaderboards")
boards = r2.json()["data"]["boards"]
(out/"boards.json").write_text(json.dumps(boards, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"\n=== 榜单 {len(boards)} 个（按领域）===")
from collections import OrderedDict
dom = OrderedDict()
for b in boards:
    dom.setdefault(b.get("domain","?"), []).append(b)
for d, bs in dom.items():
    print(f"  {d} ({len(bs)}):")
    for b in bs:
        print(f"    {b['id']:<38} {b.get('displayName','')}  unit={b.get('unit','')}")

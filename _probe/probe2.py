"""pass2：完整抓取模板 + 榜单，与现有代码对比"""
import httpx, json, pathlib, sys
sys.path.insert(0, 'G:/py/qqbot')
BASE = "https://www.wdsj.net/nexus"
H = {"Referer": "https://www.wdsj.net/nexus/stats",
     "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124 Safari/537.36"}

out = pathlib.Path('G:/py/qqbot/_probe/data'); out.mkdir(exist_ok=True)

def fetch(path, referer="https://www.wdsj.net/nexus/stats"):
    r = httpx.get(f"{BASE}{path}", headers={**H, "Referer": referer}, timeout=25)
    return r

# 1) templates
r = fetch("/api/v1/templates")
tpl = r.json()["data"]["templates"]
(out/"templates.json").write_text(json.dumps(tpl, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"=== templates: {len(tpl)} 个 ===")
for t in tpl: print("   ", t)

# 2) leaderboards
r = fetch("/api/v1/leaderboards", "https://www.wdsj.net/nexus/leaderboards")
lb = r.json()["data"]["boards"]
(out/"boards.json").write_text(json.dumps(lb, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"\n=== leaderboards: {len(lb)} 个 ===")
from collections import Counter, OrderedDict
dom = OrderedDict()
for b in lb:
    dom.setdefault(b.get("domain", "?"), []).append(b.get("id"))
print(f"领域数: {len(dom)}")
for d, ids in dom.items():
    print(f"   {d}: {len(ids)} 项 -> {', '.join(str(i) for i in ids[:6])}{' ...' if len(ids)>6 else ''}")

# 3) 与现有代码对比
from services.wdsj_api import TEMPLATES, ALIASES, BOARD_ALIASES, BOARD_SHORTHAND
print(f"\n=== 对比现有代码 ===")
new_tpl = [t for t in tpl if t not in TEMPLATES]
gone_tpl = [t for t in TEMPLATES if t not in tpl]
print(f"模板: 现有 {len(TEMPLATES)}，线上 {len(tpl)}")
print(f"  新增模板: {new_tpl or '无'}")
print(f"  已下线:  {gone_tpl or '无'}")
known_boards = set(BOARD_ALIASES.values()) | {v for v in BOARD_SHORTHAND.values()} | set(ALL if (ALL:=None) else [])
live_ids = {b["id"] for b in lb}
print(f"榜单: 现有别名覆盖 {len(known_boards & live_ids)}/{len(live_ids)}")
missing = sorted(live_ids - known_boards)
print(f"  未收录榜单 {len(missing)} 个，示例: {missing[:15]}")

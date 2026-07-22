"""全量种子采集 — 所有模板所有字段"""
import asyncio, json, sys
sys.path.insert(0, '/root/bot')
from services import wdsj_api as api

TEMPLATES = ["bedwars-stats", "arena-stats"]

async def run():
    bindings = json.load(open('/root/bot/data/wdsj_player_name.json'))
    players = list(set(bindings.values()))
    hist = {}
    sem = asyncio.Semaphore(5)
    total = 0
    done = 0
    today = __import__('datetime').date.today().isoformat()

    async def collect_one(p):
        nonlocal total, done
        async with sem:
            entry = hist.setdefault(p, {})
            for tid in TEMPLATES:
                try:
                    data = await api.query_player_stats(p, tid, timeout=5)
                    if data and data.get('values'):
                        vals = dict(data['values'])
                        series = entry.setdefault(tid, [])
                        if series and series[-1].get('date') == today:
                            series[-1]['values'] = vals
                        else:
                            series.append({'date': today, 'values': vals})
                            total += 1
                except Exception:
                    pass
                await asyncio.sleep(0.2)
            done += 1
            print(f"  [{done}/{len(players)}] {p}")

    print(f"开始全量采集 {len(players)} 个玩家 x {len(TEMPLATES)} 模板...")
    await asyncio.gather(*[collect_one(p) for p in players])

    json.dump(hist, open('/root/bot/data/wdsj_history.json', 'w'), ensure_ascii=False)
    print(f'\nDONE: {len(hist)} players, {total} snapshots')

asyncio.run(run())

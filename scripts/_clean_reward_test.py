"""清理面板测试数据 + 验证 bot 侧读取一致"""
import json
import sys
from pathlib import Path

sys.path.insert(0, '/root/bot')

P = Path('/root/bot/data/rewards.json')
d = json.loads(P.read_text(encoding='utf-8'))
sp = d.get('sponsors', [])
before = len(sp)
kept = [s for s in sp if not str(s.get('name', '')).startswith('_')]
d['sponsors'] = kept
P.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding='utf-8')
print(f"清理 {before} -> {len(kept)} 条")

print("\n=== 清理后文件 ===")
print(P.read_text(encoding='utf-8'))

print("=== bot 侧读取验证 ===")
from modules.reward import list_sponsors, sponsors_hint  # noqa: E402
print("list_sponsors:", list_sponsors())
hint = sponsors_hint()
print("sponsors_hint:", repr(hint) if hint else "(空，不注入 system —— 符合预期)")

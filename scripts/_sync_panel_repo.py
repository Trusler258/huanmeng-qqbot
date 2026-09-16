# -*- coding: utf-8 -*-
"""把 qqbot 工作副本中的面板改动同步到独立仓库 huanmeng-panel。
只同步白名单内的改动文件，绝不触碰 secret.toml / audit.log / __pycache__。
"""
import shutil
from pathlib import Path

ROOT = Path(r"G:\py")
SRC_PANEL = ROOT / "qqbot" / "panel_src_remote" / "panel"
SRC_WEB = ROOT / "qqbot" / "panel_web" / "src"
DST_PANEL = ROOT / "huanmeng-panel" / "panel"
DST_WEB = ROOT / "huanmeng-panel" / "panel_web" / "src"

FILES = [
    # 后端
    "app.py",
    "routers/assistant.py",
    "routers/database.py",
    "routers/games.py",
    "routers/groups.py",
    "routers/media.py",
    "routers/overview.py",
    "routers/reward.py",
    # 前端
    "api/panelB.ts",
    "api/panelC.ts",
    "locale/en-US/settings.ts",
    "locale/zh-CN/settings.ts",
    "router/routes/modules/fun.ts",
    "views/commands/index.vue",
    "views/dashboard/overview/index.vue",
    "views/games/index.vue",
    "views/groups/index.vue",
    "views/media/index.vue",
    "views/memory/index.vue",
]

DIRS = [
    "views/reward",
]

copied, skipped = [], []
for rel in FILES:
    s = SRC_PANEL / rel if rel.endswith(".py") else SRC_WEB / rel
    d = DST_PANEL / rel if rel.endswith(".py") else DST_WEB / rel
    if not s.is_file():
        skipped.append(str(rel))
        continue
    d.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(s, d)
    copied.append(rel)

for rel in DIRS:
    s = SRC_WEB / rel
    d = DST_WEB / rel
    if not s.is_dir():
        skipped.append(str(rel) + "/")
        continue
    for f in s.rglob("*"):
        if not f.is_file():
            continue
        if "__pycache__" in f.parts:
            continue
        t = d / f.relative_to(s)
        t.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, t)
        copied.append(str(Path(rel) / f.relative_to(s)))

print("已同步 %d 项:" % len(copied))
for c in copied:
    print("  +", c)
if skipped:
    print("跳过（源缺失）:")
    for c in skipped:
        print("  -", c)

# 安全断言：独立仓库不得出现部署产物
bad = [p for p in DST_PANEL.rglob("*")
       if p.is_file() and p.name in ("secret.toml", "audit.log")]
if bad:
    print("!! 警告：目标目录出现敏感文件:", [str(b) for b in bad])
else:
    print("安全检查通过：无 secret.toml / audit.log")

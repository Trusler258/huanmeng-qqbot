# -*- coding: utf-8 -*-
"""把 qqbot 工作副本中的面板改动同步到独立仓库 huanmeng-panel。

v2.3.37 改：从「硬编码白名单」改成「全量同步 + 排除表」。
  原因：原来是手写清单（FILES/DIRS），只有当初改过的那几个文件在列。
  这次改了 `routers/social.py` / `routers/earthquake.py` /
  `views/earthquake/index.vue` / `views/messages/index.vue`，
  都不在名单里 → **被静默丢弃**，而脚本照样打印"已同步 N 项"，看不出漏了。
  手写清单必然越用越漏，改成遍历全量 + 显式排除敏感/产物文件。

排除：
  - 部署产物：secret.toml / audit.log
  - 构建与缓存：__pycache__ / node_modules / dist / *.pyc
  - 运行时数据：*.log / *.db / *.sqlite

用法：
    python scripts/_sync_panel_repo.py          # 同步
    python scripts/_sync_panel_repo.py --dry    # 只看会动哪些文件
"""
import shutil
import sys
from pathlib import Path

ROOT = Path(r"G:\py")
SRC_PANEL = ROOT / "qqbot" / "panel_src_remote" / "panel"
SRC_WEB = ROOT / "qqbot" / "panel_web" / "src"
DST_PANEL = ROOT / "huanmeng-panel" / "panel"
DST_WEB = ROOT / "huanmeng-panel" / "panel_web" / "src"

EXCLUDE_NAMES = {"secret.toml", "audit.log"}
EXCLUDE_DIRS = {"__pycache__", "node_modules", "dist", ".pytest_cache", ".mypy_cache"}
EXCLUDE_SUFFIX = {".pyc", ".pyo", ".log", ".db", ".sqlite"}

ONLY_SUFFIX_PANEL = {".py", ".json", ".md", ".txt", ".html"}
ONLY_SUFFIX_WEB = {".ts", ".vue", ".js", ".json", ".less", ".css", ".html", ".svg", ".png"}


def _skip(p: Path) -> bool:
    if p.name in EXCLUDE_NAMES:
        return True
    if any(part in EXCLUDE_DIRS for part in p.parts):
        return True
    return p.suffix.lower() in EXCLUDE_SUFFIX


def sync(src_root: Path, dst_root: Path, suffixes: set, label: str, dry: bool) -> list:
    changed: list = []
    if not src_root.is_dir():
        print("!! 源目录不存在: %s" % src_root)
        return changed

    # 正向：源 → 目标
    for f in sorted(src_root.rglob("*")):
        if not f.is_file() or _skip(f):
            continue
        if f.suffix.lower() and f.suffix.lower() not in suffixes:
            continue
        t = dst_root / f.relative_to(src_root)
        try:
            if t.exists() and t.read_bytes() == f.read_bytes():
                continue
        except OSError:
            pass
        if not dry:
            t.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, t)
        changed.append(str(Path(label) / f.relative_to(src_root)))

    # 反向：目标里源已没有的（避免两边长期漂移），只在本次同步的树内
    if dst_root.is_dir():
        for f in sorted(dst_root.rglob("*")):
            if not f.is_file() or _skip(f):
                continue
            rel = f.relative_to(dst_root)
            if not (src_root / rel).exists():
                if not dry:
                    f.unlink()
                changed.append("[删除] %s" % (Path(label) / rel))
    return changed


def main():
    dry = "--dry" in sys.argv
    changed: list = []
    changed += sync(SRC_PANEL, DST_PANEL, ONLY_SUFFIX_PANEL, "panel", dry)
    changed += sync(SRC_WEB, DST_WEB, ONLY_SUFFIX_WEB, "panel_web/src", dry)

    print("%s %d 项：" % ("将同步" if dry else "已同步", len(changed)))
    for c in changed:
        print("  +", c)
    if not changed:
        print("  （两边已一致）")

    bad = [p for p in DST_PANEL.rglob("*")
           if p.is_file() and p.name in EXCLUDE_NAMES]
    if bad:
        print("!! 警告：目标目录出现敏感文件:", [str(b) for b in bad])
        return 1
    print("安全检查通过：无 secret.toml / audit.log")
    return 0


if __name__ == "__main__":
    sys.exit(main())

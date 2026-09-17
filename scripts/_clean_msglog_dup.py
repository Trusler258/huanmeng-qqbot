# -*- coding: utf-8 -*-
"""清理 msglog 里的重复记录（v2.3.36）。

只删除**完全一致**的重复条目 —— 判定用 `json.dumps(d, sort_keys=True)` 全等比较，
所以：
  - 同一次调用的两次写入（连 time 都同秒、字段全同）→ 会删
  - 撤回后原地更新的条目（recalled 字段不同）→ **不会**误删
  - msg_id=0 的兜底录制 → 不动（无法区分"重复写"与"不同时间发了相同内容"）

实测重复样本（msglog_247478659.jsonl 行 3655/3656）：
  {"content":"诶？你这要求也太狠了吧…","msg_id":1274171508,"recalled":false,
   "time":1789485890,"type":"bot","user_id":3682248514}   ← 两行完全相同、相邻

用法：
    python3 scripts/_clean_msglog_dup.py             # 预览（不动文件）
    python3 scripts/_clean_msglog_dup.py --write     # 写回（自动备份 .bak_日期）
"""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path

MSGLOG = Path(__file__).resolve().parent.parent / "data" / "msglog"


def clean_file(path: Path, write: bool) -> tuple[int, int, int]:
    """返回 (原行数, 新行数, 删除数)"""
    lines = [ln for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines() if ln.strip()]
    seen: set[str] = set()
    kept: list[str] = []
    removed = 0
    for ln in lines:
        try:
            d = json.loads(ln)
        except Exception:
            kept.append(ln)          # 坏行原样保留，不碰
            continue
        try:
            mid = int(d.get("msg_id") or 0)
        except (TypeError, ValueError):
            mid = 0
        if mid > 0:
            sig = json.dumps(d, sort_keys=True, ensure_ascii=False)
            if sig in seen:
                removed += 1
                continue
            seen.add(sig)
        kept.append(ln)

    if write and removed:
        bak = path.with_suffix(".jsonl.bak_dup_%s" % time.strftime("%Y%m%d_%H%M%S"))
        shutil.copy2(path, bak)
        path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    return len(lines), len(kept), removed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="实际写回（默认只预览）")
    args = ap.parse_args()

    files = sorted(MSGLOG.glob("msglog_*.jsonl"))
    print("扫描 %d 个文件%s\n" % (len(files), "（写回模式）" if args.write else "（预览模式，不改文件）"))

    tot_old = tot_new = tot_rm = 0
    touched = []
    for f in files:
        old, new, rm = clean_file(f, args.write)
        tot_old += old
        tot_new += new
        tot_rm += rm
        if rm:
            touched.append((rm, f.name, old, new))
            print("  %-32s %4d → %4d  删除 %d 条" % (f.name, old, new, rm))

    if not touched:
        print("  未发现完全一致的重复条目")
    print("\n" + "=" * 62)
    print("总行数 %d → %d，删除重复 %d 条" % (tot_old, tot_new, tot_rm))
    if args.write and tot_rm:
        print("已备份为 *.bak_dup_<时间戳>")
    elif tot_rm:
        print("\n预览模式 —— 未修改任何文件。确认无误后加 --write 执行。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

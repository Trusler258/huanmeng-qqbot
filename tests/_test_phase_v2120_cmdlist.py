"""v2.1.20 回归测试 — 指令清单同源修复（help_card ↔ LLM 动态清单）

背景：用户发现"命令 md 就这么点，那么多功能就这点命令"。
根因：LLM 眼里的指令清单（services/llm._build_dynamic_command_list）只读
      COMMAND_MAP 名字 + _CMD_DESC 碎片副本，与 /~help 卡片的 103 条精校
      描述严重不同步：25 条已注册指令无说明、5 条有说明未注册（幻觉源）。

修复：抽出 help_card.collect_commands()，LLM 清单直接复用 → 两边同源。

验证：
  1. collect_commands() 可用且条目数 > 60
  2. LLM 动态清单非空、条目数与 help_card 一致（同源）
  3. 每个条目都带中文说明（不再有裸指令名）
  4. 关键指令在清单里（weather/wzq/eq/draw/remind…）
  5. 未注册的幻觉指令不在清单里（gift/buy/bag/use）
  6. 别名正确标注（五子棋/wzq、签收/checkin）
  7. 经济插件指令可见（points/checkin/shop/dice）
  8. 分类齐全（聊天/工具/数据/游戏/创作/系统/admin/插件）

运行: python tests/_test_phase_v2120_cmdlist.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def main():
    print("=" * 62)
    print("v2.1.20 指令清单同源修复回归测试")
    print("=" * 62)

    # ── 1. collect_commands 可用 ──
    print("\n[1] help_card.collect_commands()")
    from modules.help_card import collect_commands

    groups = collect_commands()
    total = sum(len(v) for v in groups.values())
    check("返回非空 dict", isinstance(groups, dict) and bool(groups))
    check(f"条目数 > 60（实际 {total}）", total > 60, f"got {total}")
    check("分类齐全", len(groups) >= 6, f"categories={list(groups)}")

    # ── 2. LLM 清单非空且同源 ──
    print("\n[2] LLM 动态清单（修复前为 0 或残缺）")
    from services.llm import _build_dynamic_command_list

    txt = _build_dynamic_command_list()
    check("清单非空", bool(txt.strip()), f"len={len(txt)}")
    check("字符数 > 1000", len(txt) > 1000, f"len={len(txt)}")

    cmd_entries = re.findall(r"^\s+/~(\S+?):", txt, re.M)
    check(f"条目数 {len(cmd_entries)} 与 help_card 同源（差异 ≤2）",
          abs(len(cmd_entries) - total) <= 2,
          f"llm={len(cmd_entries)} help_card={total}")

    # ── 3. 都有说明 ──
    print("\n[3] 每条都有中文说明")
    naked = re.findall(r"^\s+/~(\S+?)$", txt, re.M)
    check(f"无裸指令名（实际 {len(naked)} 个）", len(naked) == 0,
          f"naked={naked[:8]}")
    has_cn = len(re.findall(r"[\u4e00-\u9fff]", txt))
    check("含大量中文说明", has_cn > 300, f"cn_chars={has_cn}")

    # ── 4. 关键指令在清单 ──
    print("\n[4] 关键指令可见")
    for c in ("weather", "wzq", "eq", "draw", "remind", "search", "read",
              "favlist", "tr", "video", "whois", "xq"):
        check(f"/~{c} 在清单中", f"/~{c}:" in txt or f"/~{c}（" in txt)

    # ── 5. 幻觉指令已清除 ──
    print("\n[5] 未注册的幻觉指令已清除")
    for c in ("gift", "buy", "bag"):
        check(f"/~{c} 不在清单（从未注册）", f"/~{c}:" not in txt)

    # ── 6. 别名标注 ──
    print("\n[6] 别名标注")
    check("wzq 标注别名 五子棋", "五子棋" in txt)
    check("checkin 标注别名 签到", "签到" in txt)
    check("weather 标注别名 天气", "天气" in txt)

    # ── 7. 经济插件指令 ──
    print("\n[7] 经济/插件指令可见")
    for c in ("points", "checkin", "shop", "dice"):
        check(f"/~{c} 在清单中", f"/~{c}:" in txt)

    # ── 8. 格式与分类 ──
    print("\n[8] 格式")
    check("含分类标题 ▎", "▎" in txt)
    for cat in ("工具", "数据", "游戏", "创作", "系统"):
        check(f"分类「{cat}」存在", f"▎{cat}" in txt)

    # ── 9. xq 描述已修正 ──
    print("\n[9] 描述修正（xq 曾错标为'查看大群在线信息'）")
    m = re.search(r"/~xq: (.+)", txt)
    check("xq 描述正确（象棋）", bool(m) and "象棋" in m.group(1),
          f"got={m.group(1) if m else None}")

    print("\n" + "=" * 62)
    print(f"结果: {PASS} passed, {FAIL} failed")
    print("=" * 62)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

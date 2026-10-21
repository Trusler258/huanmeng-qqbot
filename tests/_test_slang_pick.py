"""黑话注入改造单测（v2.3.105）：只注入命中条目 + ASCII 边界。

跑法：python tests/_test_slang_pick.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS = 0
FAIL = 0


def ck(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


def main():
    from services.llm import _slang_pick, _slang_term_hit, _SLANG_MAX_HITS
    from services.llm import _SKILLS_DIR

    print("[1] 词形判定（ASCII 边界）")
    ck("独立 ascii 命中", _slang_term_hit("这波 yyds", "yyds") is True)
    ck("粘连不命中", _slang_term_hit("yydsxxxx", "yyds") is False)
    ck("长串内部不命中", _slang_term_hit("fileid=Ehrdefghij", "def") is False)
    ck("两侧标点算独立", _slang_term_hit("x-yyds-z", "yyds") is True)
    ck("中文子串命中", _slang_term_hit("我破防了", "破防") is True)
    ck("排除表生效", _slang_term_hit("这个触屏", "触") is False)
    ck("太短不算", _slang_term_hit("w", "w") is False)

    print("[2] 只注入命中条目")
    text = (_SKILLS_DIR / "15_slang.md").read_text(encoding="utf-8")
    pick = _slang_pick(text, "我破防了")
    ck("命中非空", bool(pick), pick[:80])
    ck("带表头说明", pick.startswith("【本轮黑话参考】"), pick[:40])
    ck("只含命中的条目", pick.count("\n· ") == 0 or pick.count("\n· ") <= 2, pick)
    ck("体积远小于整章", len(pick) < len(text) * 0.5, f"{len(pick)} vs {len(text)}")
    ck("含破防释义", "破防" in pick, pick)

    print("[3] 未命中 / 上限")
    none = _slang_pick(text, "今天天气不错我去吃饭了")
    ck("未命中返回空", none == "", none[:60])
    many = _slang_pick(text, "yyds 破防 硬控 包得 触烂 估咩 各各 已读不回 要确欸")
    ck("多条命中受上限约束", many.count("\n· ") + 1 <= _SLANG_MAX_HITS, str(many.count("\n· ") + 1))

    print("[4] learn_slang 追加后立刻能命中（并自清理）")
    from services.llm import append_slang_term
    p_slang = _SKILLS_DIR / "15_slang.md"
    ok, msg = append_slang_term("测试词abc", "单测用词", "单测")
    ck("追加成功", ok is True, msg)
    if ok:
        _t2 = p_slang.read_text(encoding="utf-8")
        ck("新词可命中", "测试词abc" in _slang_pick(_t2, "这啥测试词abc"), "未命中")
    # 无条件清理（含空分组标题），避免污染真实词表
    kept = [l for l in p_slang.read_text(encoding="utf-8").split("\n")
            if "测试词abc" not in l and l.strip() != "## 单测"]
    p_slang.write_text("\n".join(kept).rstrip("\n") + "\n", encoding="utf-8")
    ck("已清理测试词", "测试词abc" not in p_slang.read_text(encoding="utf-8"))

    total = len(_slang_pick(text, "我破防了"))
    print(f"\n注入体积：整章 {len(text)} 字 → 命中一条 {total} 字"
          f"（压缩到 {total * 100 // len(text)}%）")
    print(f"结果: {PASS} 通过 / {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

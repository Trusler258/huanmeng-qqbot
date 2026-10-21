"""黑话注入单测（v2.3.105）：JSON 词库 + 只注入命中条目 + ASCII 边界。

跑法：python tests/_test_slang_pick.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS = 0
FAIL = 0
JSON_PATH = ROOT / "data" / "slang.json"
TEST_TERM = "测试词abc"


def ck(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


def _drop_test_term():
    """从 slang.json 删掉单测词，避免污染真实词库。"""
    from services import llm
    try:
        raw = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    except Exception:
        return
    ents = [e for e in (raw.get("entries") or [])
            if str(e.get("name", "")).lower() != TEST_TERM]
    if len(ents) != len(raw.get("entries") or []):
        raw["entries"] = ents
        JSON_PATH.write_text(json.dumps(raw, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        llm._slang_json_cache = None
        llm._slang_terms_cache = None


def main():
    from services.llm import (_slang_pick, _slang_term_hit, _slang_entries,
                              _slang_terms, _SLANG_MAX_HITS, _build_skill_refs)

    print("[1] 词库已迁 JSON")
    ents = _slang_entries()
    ck("JSON 可读", len(ents) > 50, str(len(ents)))
    ck("字段齐全", all(k in ents[0] for k in
                   ("name", "alias", "circles", "risk", "meaning", "last_seen")), str(ents[0]))
    ck("触发词含别名", len(_slang_terms()) >= len(ents), f"{len(_slang_terms())} vs {len(ents)}")

    print("[2] 词形判定（ASCII 边界）")
    ck("独立 ascii 命中", _slang_term_hit("这波 yyds", "yyds") is True)
    ck("粘连不命中", _slang_term_hit("yydsxxxx", "yyds") is False)
    ck("长串内部不命中", _slang_term_hit("fileid=Ehrdefghij", "def") is False)
    ck("两侧标点算独立", _slang_term_hit("x-yyds-z", "yyds") is True)
    ck("中文子串命中", _slang_term_hit("我破防了", "破防") is True)
    ck("排除表生效", _slang_term_hit("这个触屏", "触") is False)
    ck("太短不算", _slang_term_hit("w", "w") is False)

    print("[3] 只注入命中条目（JSON 渲染）")
    pick = _slang_pick("", "我破防了")
    ck("命中非空", bool(pick), pick[:80])
    ck("带表头说明", pick.startswith("【本轮黑话参考】"), pick[:40])
    ck("含破防释义", "破防" in pick, pick)
    ck("带圈子标签", "（" in pick and "）" in pick, pick)
    ck("mild 标慎用", "慎用" in pick, pick)
    ck("体积可控", len(pick) < 400, str(len(pick)))

    print("[4] 未命中 / 上限")
    ck("未命中返回空", _slang_pick("", "今天天气不错我去吃饭了") == "")
    many = _slang_pick("", "yyds 破防 硬控 包得 触烂 估咩 各各 已读不回")
    ck("多条命中受上限约束", many.count("\n· ") + 1 <= _SLANG_MAX_HITS,
       str(many.count("\n· ") + 1))

    print("[5] learn_slang 写 JSON 并即时生效")
    from services.llm import append_slang_term
    ok, msg = append_slang_term(TEST_TERM, "单测用词", "单测")
    ck("追加成功", ok is True, msg)
    if ok:
        ck("即时可命中", TEST_TERM in _slang_pick("", f"这啥{TEST_TERM}"), "未命中")
        _drop_test_term()
        ck("已清理测试词", TEST_TERM not in json.dumps(
            json.loads(JSON_PATH.read_text(encoding="utf-8")), ensure_ascii=False))
    else:
        _drop_test_term()

    print("[6] 与 skills 解耦：注入不依赖 15_slang.md")
    out = _build_skill_refs(set(), True, "这波操作 yyds 吧")
    ck("_build_skill_refs 有黑话段", "本轮黑话参考" in out, out[:120])
    ck("无黑话时该段不出现", "本轮黑话参考" not in _build_skill_refs(set(), True, "今天天气不错"))

    print(f"\n结果: {PASS} 通过 / {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

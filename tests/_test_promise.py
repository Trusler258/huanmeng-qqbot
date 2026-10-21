"""空口承诺识别单测（2026-10-21 群聊事故回归）。

事故：用户要 word 试卷，bot 连回两轮「我重新生成一份，你等会儿」「好了，
下标都改成真下标了，文件这就发你」，两轮 tool_calls 都是 0 —— 用户等到空气。

跑法：python tests/_test_promise.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from services.llm import _looks_like_tool_promise, _needs_deliverable_file  # noqa: E402

PASS = FAIL = 0


def ck(name: str, cond, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}  {extra}")


# 事故原文（必须命中，否则补救不触发）
_PROMISES = [
    "下标那个问题我看看，应该是导出时把 Na₂CO₃ 这类写成了普通数字 || 我重新生成一份，下标都用真下标，你等会儿",
    "好了，下标都改成真下标了，Na₂CO₃ 这种不会再变成普通数字 || 文件这就发你",
    "我搜一下再跟你说",
    "等我弄好就发",
    "我这就发你",
    "我重新做一份",
]

# 普通闲聊（绝不能命中，否则每句闲聊都多烧一次调用）
_NOT_PROMISES = [
    "哈哈哈哈哈这也太好笑了",
    "我看看哦",
    "今天天气不错",
    "我做了个梦",
    "这个我不会诶",
    "你说得对",
    "我吃过了",
]


def test_promise():
    print("[1] 承诺识别（正样本）")
    for t in _PROMISES:
        ck(f"命中：{t[:26]}", _looks_like_tool_promise(t) is True)

    print("[2] 误判防护（负样本）")
    for t in _NOT_PROMISES:
        ck(f"不命中：{t[:20]}", _looks_like_tool_promise(t) is False)


def test_deliverable():
    print("[3] 交付物意图（含 word/docx）")
    for t in ("帮我生成人教版化学试卷，导出为word",
              "把这篇整理成文档发我",
              "导出成 pdf",
              "做个 excel 表格"):
        ck(f"识别：{t[:20]}", _needs_deliverable_file(t) is True)
    for t in ("今天吃什么", "你好呀", "这个梗啥意思"):
        ck(f"不误判：{t[:14]}", _needs_deliverable_file(t) is False)


if __name__ == "__main__":
    test_promise()
    test_deliverable()
    print(f"\n结果: {PASS} 通过 / {FAIL} 失败")
    raise SystemExit(0 if FAIL == 0 else 1)

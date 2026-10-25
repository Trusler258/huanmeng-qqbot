# -*- coding: utf-8 -*-
"""回归测试：承诺补调用的三道闸（v2.3.107）。

事故背景（2026-10-26 01:41 群 2000000003）：
  bot 回「你要真嘴馋，我帮你查查附近有什么好吃的」
  → 被判成"承诺调用工具"→ 强制补调用
  → 模型凭空挑了 write_code，description 还是两小时前 MIDI 聊天的旧内容
  → 生成「群友发言记录.docx」发进群，用户一脸问号

三道闸分别验证：
  1. _looks_like_tool_promise  条件/假设/提议句不算承诺
  2. _promise_tool_subset      无文件诉求时不给 write_code
  3. _looks_like_file_request  write_code 执行前再确认用户真要文件

运行：python tests/_test_promise_guard.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from services.llm import (  # noqa: E402
    _looks_like_tool_promise, _promise_tool_subset, _needs_deliverable_file,
)
from core.tools import _looks_like_file_request  # noqa: E402

ok = fail = 0


def ck(name, cond):
    global ok, fail
    if cond:
        ok += 1
        print("ok  ", name)
    else:
        fail += 1
        print("FAIL", name)


# ── 闸一：承诺识别 ──────────────────────────────────────────
PROMISE_CASES = [
    # 事故原句：提议，不是承诺
    ("你要真嘴馋，我帮你查查附近有什么好吃的，比啃猫尾巴强多啦", False),
    ("如果你是学生的话我帮你查查", False),
    ("想看的话我去搜一下", False),
    ("要不要我帮你搜一下", False),
    ("哪天我帮你查查", False),
    ("你可以把嘴凑过来", False),
    ("我喜欢你笨笨的样子", False),
    # 真承诺：必须触发补调用
    ("我这就帮你查一下", True),
    ("我去搜一下这个梗", True),
    ("文件这就发你", True),
    ("好的我马上帮你查一下资料", True),
    ("我这就重新生成一份", True),
]
for text, expect in PROMISE_CASES:
    ck(f"承诺判定 {text!r} → {expect}", _looks_like_tool_promise(text) == expect)

# JSON 外壳（replies 数组）也要能判
import json  # noqa: E402
j = json.dumps({"replies": ["你要真嘴馋，我帮你查查附近有什么好吃的"]}, ensure_ascii=False)
ck("承诺判定 JSON 提议 → False", _looks_like_tool_promise(j) is False)
j2 = json.dumps({"replies": ["我这就帮你查一下"]}, ensure_ascii=False)
ck("承诺判定 JSON 承诺 → True", _looks_like_tool_promise(j2) is True)


# ── 闸二：补调用工具子集 ────────────────────────────────────
TOOLS = [{"type": "function", "function": {"name": n}}
         for n in ("write_code", "search_web", "note", "ignore_user", "weather")]


def names(subset):
    return [((t or {}).get("function") or {}).get("name") for t in subset]


sub = names(_promise_tool_subset(TOOLS, "那我要吃掉你的尾巴",
                                 "你要真嘴馋，我帮你查查附近有什么好吃的"))
ck("无文件诉求 → 不含 write_code", "write_code" not in sub)
ck("无文件诉求 → 不含 note/ignore_user",
   "note" not in sub and "ignore_user" not in sub)
ck("无文件诉求 → 仍保留 search_web", "search_web" in sub)

sub2 = names(_promise_tool_subset(TOOLS, "帮我整理成md发我", "好，我这就整理"))
ck("要文档 → 保留 write_code", "write_code" in sub2)


# ── 闸三：write_code 前置确认 ──────────────────────────────
FILE_CASES = [
    ("那我要吃掉你的尾巴", False),
    ("你好呀", False),
    ("我喜欢你", False),
    ("今天天气不错", False),
    ("帮我整理成md发我", True),
    ("写个贪吃蛇", True),
    ("导出成word", True),
    ("生成一份报告", True),
]
for text, expect in FILE_CASES:
    ck(f"文件闸门 {text!r} → {expect}", _looks_like_file_request(text) == expect)

# 空消息返回 False；execute_tool 里用 `original_msg and not ...` 短路，
# 所以管道没传原文时闸门自动放行，不会误拦
ck("空文本 → False（闸门处短路放行）", _looks_like_file_request("") is False)

# 与 llm 侧口径一致：要文档时两边都为真
ck("口径一致：'整理成md发我'", _needs_deliverable_file("整理成md发我")
   and _looks_like_file_request("整理成md发我"))

print()
print("通过 %d / 失败 %d" % (ok, fail))
sys.exit(1 if fail else 0)

"""离线单测：legacy calls → 原生 tool_calls 转换（v2.3.81e）

背景：模型按 reply_schema 可输出 {"replies":[...],"calls":[...]}。有时它不用原生
function-calling 而把调用写进 content（legacy 协议），此时 FC 循环的先找后写机制
（素材累积/多轮/兜底）会整个失效。本脚本只测转换函数本身，不联网、不调 LLM。

用法（/root/bot 下）：
    python3 scripts/_diag_legacy_calls.py
退出码 0 = 全通过。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.llm import _legacy_calls_to_tool_calls as conv  # noqa: E402


def main() -> None:
    # A：纯 legacy search_web（线上出事的那条）
    a = json.dumps({
        "replies": ["好呀，我去扒一圈最近的 AI 新闻～", "搜完整理成 md 文档发你"],
        "fav": 2,
        "calls": [
            {"name": "search_web", "args": "2026年10月 AI 最新新闻"},
            {"name": "search_web", "args": "OpenAI Anthropic Google 最新发布 2026年9月"},
        ],
    }, ensure_ascii=False)
    tcs, stripped = conv(a)
    print("A tcs:", [(t["name"], t["arguments"]) for t in tcs])
    assert len(tcs) == 2, tcs
    assert tcs[0]["arguments"] == {"query": "2026年10月 AI 最新新闻"}, tcs[0]
    assert '"calls": []' in stripped, stripped
    assert "search_web" not in stripped, stripped

    # B：legacy write_code（dict args 原样保留）
    b = json.dumps({
        "replies": ["好"],
        "calls": [{"name": "write_code",
                   "args": {"language": "markdown", "description": "写个文档"}}],
    }, ensure_ascii=False)
    tcs, _ = conv(b)
    assert tcs and tcs[0]["name"] == "write_code", tcs
    assert tcs[0]["arguments"]["language"] == "markdown", tcs[0]

    # B2：legacy write_code（字符串 args → 默认 markdown）
    b2 = json.dumps({
        "replies": ["好"],
        "calls": [{"name": "write_code", "args": "整理成 md"}],
    }, ensure_ascii=False)
    tcs, _ = conv(b2)
    assert tcs[0]["arguments"] == {"language": "markdown", "description": "整理成 md"}, tcs[0]

    # C：混入非原生工具 note → 必须整体放弃（交回 pipeline）
    c = json.dumps({
        "replies": ["x"],
        "calls": [{"name": "search_web", "args": "q"}, {"name": "note", "args": "hi"}],
    }, ensure_ascii=False)
    tcs, stripped = conv(c)
    print("C tcs:", tcs, "| 放弃原样返回:", stripped == c)
    assert tcs == [] and stripped == c

    # D：内联指令（replies 里的 dict 项也要提取）
    d = json.dumps({
        "replies": ["先说一句", {"cmd": "search_web", "args": "今日要闻"}],
        "calls": [],
    }, ensure_ascii=False)
    tcs, _ = conv(d)
    assert len(tcs) == 1 and tcs[0]["arguments"] == {"query": "今日要闻"}, tcs

    # E：非 JSON / 无 calls → 放弃
    tcs, stripped = conv("随便一句话")
    assert tcs == [] and stripped == "随便一句话"
    tcs, stripped = conv(json.dumps({"replies": ["hi"], "calls": []}))
    assert tcs == []

    print("ALL UNIT OK")


if __name__ == "__main__":
    main()

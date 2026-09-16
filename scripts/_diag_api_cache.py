"""直接用真实 DeepSeek API 做缓存对照实验，确定命中规则

问：为什么 messages 前缀稳定，命中量却恒定在 6,147（38%）？
答：需要直接观察 API 的 cached_tokens 行为。
"""
import asyncio
import sys

sys.path.insert(0, '/root/bot')

from core.config import get_config          # noqa: E402
from services.llm import _build_messages, _create_client  # noqa: E402
from core.context_manager import get_context_mgr  # noqa: E402

cfg = get_config()
m = cfg.reply_model
client = _create_client(m, timeout=120)


def send(msgs, tools=None):
    kw = {"model": m.name, "messages": msgs, "temperature": 0.3, "max_tokens": 8}
    if tools:
        kw["tools"] = tools
    r = client.chat.completions.create(**kw)
    u = r.usage
    cached = getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", 0) or 0
    return u.prompt_tokens, u.completion_tokens, cached


async def main():
    mgr = get_context_mgr()
    chat = max(mgr.group_context.keys(), key=lambda c: len(mgr.get_context(c)))
    hist = mgr.get_context(chat)
    print(f"群 {chat}，历史 {len(hist)} 条\n")

    from core.tools import get_tool_schemas
    _all = get_tool_schemas()
    from collections import Counter
    _names = [t["function"]["name"] for t in _all]
    _dup = [k for k, v in Counter(_names).items() if v > 1]
    print(f"!! tools 共 {len(_all)} 个，重名: {_dup}")
    _seen = set()
    tools = []
    for t in _all:
        n = t["function"]["name"]
        if n not in _seen:
            _seen.add(n); tools.append(t)
    print(f"   去重后 {len(tools)} 个\n")

    def build(h, cur):
        return _build_messages(h, "测试者", cur, cfg.bot_name, cfg.system_prompt, True, "")

    # ── T1: 只有 system ──
    p, c, hit = send([{"role": "system", "content": build([], "x")[0]["content"]}])
    print(f"T1 仅 system                     prompt={p:6,} 命中={hit:6,}")
    sys_tok = p

    # ── T2: system + 历史 200 条（不带 tools）──
    msgs200 = build(hist[-200:], "占位")
    p2, c2, h2 = send(msgs200)
    print(f"T2 system+历史200 无tools(第1次)  prompt={p2:6,} 命中={h2:6,} ({h2/p2*100:4.1f}%)")

    # ── T3: 同样内容再发（不带 tools）──
    p3, c3, h3 = send(msgs200)
    print(f"T3 system+历史200 无tools(第2次)  prompt={p3:6,} 命中={h3:6,} ({h3/p3*100:4.1f}%)")

    # ── T4: 追加一条新消息 ──
    msgs201 = build(hist[-200:], "占位")[:-1] + [{"role": "user", "content": "占位2"}]
    p4, c4, h4 = send(msgs201)
    print(f"T4 同前缀+新末条 无tools          prompt={p4:6,} 命中={h4:6,} ({h4/p4*100:4.1f}%)")

    # ── T5: 带 tools（真实 FC 场景）──
    p5, c5, h5 = send(msgs200, tools=tools)
    print(f"T5 system+历史200 带tools(第1次)  prompt={p5:6,} 命中={h5:6,} ({h5/p5*100:4.1f}%)")
    p6, c6, h6 = send(msgs200, tools=tools)
    print(f"T6 system+历史200 带tools(第2次)  prompt={p6:6,} 命中={h6:6,} ({h6/p6*100:4.1f}%)")

    # ── T7: 无 tools 之后再带 tools ──
    p7, c7, h7 = send(msgs200, tools=tools)
    print(f"T7 带tools(第3次)                 prompt={p7:6,} 命中={h7:6,} ({h7/p7*100:4.1f}%)")

    print(f"\n结论线索：system 单独 = {sys_tok} token")
    print(f"  历史 200 条约 {p2 - sys_tok} token")
    print(f"  带 tools 后 prompt 增加 {p5 - p2} token（tools 定义占这么多）")
    print(f"  重复发送时命中变化：无tools {h3:,} vs 有tools {h6:,}")


asyncio.run(main())

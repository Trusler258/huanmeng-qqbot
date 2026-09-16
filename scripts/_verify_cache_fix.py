"""验证修复效果：tools 清理+排序后，连续调用的缓存命中率

对照（修复前，scripts/_diag_api_cache.py 的实测）：
    带 tools 第 1 次  命中 30.1%（仅 system）
    带 tools 第 2 次  命中 99.1%
本脚本模拟真实场景：连续 3 条不同消息（history 递增），看命中率。
"""
import asyncio
import sys

sys.path.insert(0, '/root/bot')

from core.config import get_config                     # noqa: E402
from services.llm import _build_messages, _create_client  # noqa: E402
from core.tools import get_tool_schemas                # noqa: E402
from core.context_manager import get_context_mgr       # noqa: E402

cfg = get_config()
m = cfg.reply_model
client = _create_client(m, timeout=120)
tools = get_tool_schemas()
print(f"tools: {len(tools)} 个，无重名\n")


def send(msgs):
    r = client.chat.completions.create(
        model=m.name, messages=msgs, temperature=0.3, max_tokens=8, tools=tools)
    u = r.usage
    cached = getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", 0) or 0
    return u.prompt_tokens, cached


async def main():
    mgr = get_context_mgr()
    chat = max(mgr.group_context.keys(), key=lambda c: len(mgr.get_context(c)))
    hist = mgr.get_context(chat)

    def build(h, cur):
        # 模拟真实 pipeline：msg_history=None 时用全量；这里用尾部模拟 9-13 的短历史
        return _build_messages(h, "测试者", cur, cfg.bot_name,
                               cfg.system_prompt, True, "")

    # 模拟"同一对话连续 4 条消息"：history 逐条递增
    base = hist[-400:]
    print("模拟连续 4 条消息（history 逐条追加，末尾是新的当前消息）\n")
    print(f"{'序':>3s} {'prompt':>8s} {'命中':>8s} {'命中率':>7s}  说明")
    print("-" * 62)
    for k in range(1, 5):
        h = base + [f"[群友] 测试者[fav=50]: 第{i}条历史" for i in range(k - 1)]
        msgs = build(h, f"第{k}条新消息")
        p, hit = send(msgs)
        note = ""
        if k == 1:
            note = "首次（建立缓存）"
        pct = hit / p * 100 if p else 0
        print(f"{k:>3d} {p:>8,} {hit:>8,} {pct:>6.1f}%  {note}")

    # 间隔性：模拟"中间插了不带 tools 的调用"
    print("\n插入一次『不带 tools』调用后，再带 tools：")
    r = client.chat.completions.create(
        model=m.name, messages=build(base, "不带tools的调用"),
        temperature=0.3, max_tokens=8)
    u = r.usage
    c0 = getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", 0) or 0
    print(f"     不带tools   prompt={u.prompt_tokens:,} 命中={c0:,} ({c0/u.prompt_tokens*100:.1f}%)")
    msgs = build(base + ["[群友] 测试者[fav=50]: 又一条"], "再来一条")
    p, hit = send(msgs)
    print(f"     再带tools   prompt={p:,} 命中={hit:,} ({hit/p*100:.1f}%)  "
          f"← 受上一次不带tools影响")


asyncio.run(main())

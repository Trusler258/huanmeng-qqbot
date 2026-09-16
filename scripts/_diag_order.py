"""对照实验：消息顺序对 DeepSeek 上下文缓存命中率的影响

验证三个假设：
  A. [system][history][动态] —— 动态在末尾 → 前缀稳定，命中应高
  B. [system][动态][history] —— 动态插在中间 → 前缀被打碎，命中应低
  C. 在位置 1 插一条固定 system（归因提醒）→ 是否破坏与上一轮的共享前缀

用真实 API + 真实 system/history，直接读 cached_tokens。
"""
import asyncio
import sys

sys.path.insert(0, '/root/bot')

from core.config import get_config                       # noqa: E402
from services.llm import _create_client, _build_system_text  # noqa: E402
from core.context_manager import get_context_mgr          # noqa: E402

cfg = get_config()
m = cfg.reply_model
client = _create_client(m, timeout=120)

# 固定的「归因提醒」文本（与 llm.py 里的一致）
ATTRIB = ("你刚才调用了工具查询/搜索，现在请基于工具返回的结果回答。"
          "回复必须如实体现信息是刚查到的：开头可用'我刚查了下''搜到啦''查到啦'等，"
          "禁止说'这个我知道''我记得''早就知道'这类装作自己本来就会的话。")

DYNAMIC = "【上下文】\n这是一个每次都会变化的动态片段，内容不同才符合真实情况。"
DYNAMIC2 = "【上下文】\n这是第二次的另一个动态片段，和上一次完全不一样。"


def send(msgs, tag):
    r = client.chat.completions.create(model=m.name, messages=msgs,
                                       temperature=0.3, max_tokens=8)
    u = r.usage
    cached = getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", 0) or 0
    pct = cached / u.prompt_tokens * 100 if u.prompt_tokens else 0
    print(f"  {tag:44s} prompt={u.prompt_tokens:7,} 命中={cached:7,} ({pct:5.1f}%)")
    return u.prompt_tokens, cached


async def main():
    mgr = get_context_mgr()
    chat = max(mgr.group_context.keys(), key=lambda c: len(mgr.get_context(c)))
    hist = mgr.get_context(chat)
    sysc = _build_system_text(cfg.bot_name, cfg.system_prompt, True)

    H = [{"role": "user", "content": h} for h in hist[-300:]]
    print(f"用群 {chat} 的最近 {len(H)} 条历史，system {len(sysc)} 字符\n")

    print("=" * 74)
    print("A. 动态在末尾 [system][history][动态]  —— 期望：第二次命中高")
    print("=" * 74)
    a1 = [{"role": "system", "content": sysc}] + H + [{"role": "user", "content": DYNAMIC}]
    send(a1, "A1 首次")
    a2 = [{"role": "system", "content": sysc}] + H + [{"role": "user", "content": DYNAMIC2}]
    send(a2, "A2 换动态(前缀未变)")

    print()
    print("=" * 74)
    print("B. 动态插在中间 [system][动态][history]  —— 期望：命中暴跌")
    print("=" * 74)
    b1 = [{"role": "system", "content": sysc},
          {"role": "user", "content": DYNAMIC}] + H
    send(b1, "B1 首次")
    b2 = [{"role": "system", "content": sysc},
          {"role": "user", "content": DYNAMIC2}] + H
    send(b2, "B2 换动态(位置1变了)")

    print()
    print("=" * 74)
    print("C. 位置1插固定 system（归因提醒）—— 是否会打断与上一轮的共享前缀")
    print("=" * 74)
    c1 = [{"role": "system", "content": sysc}] + H + [{"role": "user", "content": "占位"}]
    send(c1, "C1 基准 [system][history][末条]")
    c2 = [{"role": "system", "content": sysc},
          {"role": "system", "content": ATTRIB}] + H + [{"role": "user", "content": "占位"}]
    send(c2, "C2 位置1插入归因提醒")
    c3 = [{"role": "system", "content": sysc}] + H + [
        {"role": "user", "content": "占位"},
        {"role": "system", "content": ATTRIB},
    ]
    send(c3, "C3 归因提醒放末尾（对照 C2）")

    print()
    print("=" * 74)
    print("D. 滑动窗口 [system][history[-40:]] —— 每轮前移一位")
    print("=" * 74)
    d1 = [{"role": "system", "content": sysc}] + H[-40:] + [{"role": "user", "content": "第1条"}]
    send(d1, "D1 窗口 = H[-40:]")
    d2 = [{"role": "system", "content": sysc}] + H[-39:] + [
        {"role": "user", "content": "第1条"}, {"role": "user", "content": "第2条"}]
    send(d2, "D2 窗口前移1位 = H[-39:]")


asyncio.run(main())

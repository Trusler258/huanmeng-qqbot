"""精确量化：一次真实 FC 调用的 token 构成，区分「可缓存」与「永不可缓存」

目的：找出还有多少 token 花在"每次必不命中"的动态内容上。
"""
import asyncio
import hashlib
import json
import sys
from types import SimpleNamespace

sys.path.insert(0, '/root/bot')

import services.llm as L                       # noqa: E402
from core.config import get_config             # noqa: E402
from core.context_manager import get_context_mgr  # noqa: E402

cfg = get_config()
CAP = {}


def tc(s):
    s = str(s or "")
    cn = sum(1 for c in s if '\u4e00' <= c <= '\u9fff')
    return int(cn * 0.9 + (len(s) - cn) * 0.3)


class _FC:
    def create(self, **kw):
        CAP["messages"] = kw.get("messages") or []
        CAP["tools"] = kw.get("tools")
        return SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content='{"replies":["好的喵"],"fav":0,"calls":[]}',
                                        reasoning_content=None, tool_calls=None),
                finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0,
                                  prompt_tokens_details=None))


class _Client:
    def __init__(self, *a, **k):
        self.chat = SimpleNamespace(completions=_FC())


L._create_client = _Client


async def main():
    mgr = get_context_mgr()
    chat = max(mgr.group_context.keys(), key=lambda c: len(mgr.get_context(c)))
    hist = mgr.get_context(chat)
    print(f"群 {chat}，历史 {len(hist)} 条\n")

    # 用一个会触发 tools+deep 的消息（贴近真实）
    await L.generate_multi_reply_with_tools(
        msg_history=hist, speaker_name="测试者",
        current_msg="幻梦 帮我看看 trusler 的战绩，顺便解释下 KD 怎么算的",
        bot_name=cfg.bot_name, system_prompt=cfg.system_prompt,
        reply_model=cfg.reply_model, is_group=True,
        extra_info="【记忆】trusler 是群主\n【笔记】群主喜欢 MC",
        max_tokens=None, user_id=cfg.admin_qq, group_id=chat,
        bot_qq=cfg.bot_qq, thinking=False,
    )

    msgs = CAP["messages"]
    tools = CAP["tools"] or []
    print(f"共 {len(msgs)} 段消息，{len(tools)} 个工具\n")

    sys_tok = tc(msgs[0].get("content"))
    hist_tok = sum(tc(m.get("content")) for m in msgs[1:-1])
    last = msgs[-1].get("content") or ""
    last_tok = tc(last)
    tools_tok = tc(json.dumps(tools, ensure_ascii=False))

    # 拆解最后一段（动态内容都在这）
    import re
    parts = {}
    # 参考资料块
    if "【本轮参考资料】" in last:
        m = re.search(r'【本轮参考资料】(.*?)(?=\n\n【|\Z)', last, re.S)
        if m:
            parts["参考资料(skill_refs)"] = tc(m.group(1))
    if "【上下文】" in last:
        m = re.search(r'【上下文】\n(.*?)(?=\n\n|\Z)', last, re.S)
        if m:
            parts["extra_info(记忆/笔记)"] = tc(m.group(1))
    # 提醒块（从第一个 ★★★ 开始到当前消息前）
    m = re.search(r'(★★★.*?)(?=\n\S+说：)', last, re.S)
    if m:
        parts["格式提醒(reminder)"] = tc(m.group(1))
    # 当前消息
    m = re.search(r'(\S+说：.*)$', last, re.S)
    if m:
        parts["当前消息+发言人"] = tc(m.group(1))

    print("=" * 66)
    print(f"{'段':26s} {'≈token':>8s}  可否缓存")
    print("=" * 66)
    print(f"{'system':26s} {sys_tok:8,}  可（稳定）")
    print(f"{'history':26s} {hist_tok:8,}  可（只追加）")
    print(f"{'tools':26s} {tools_tok:8,}  可（已排序修复）")
    print(f"{'-' * 50}")
    for k, v in parts.items():
        cacheable = "是（全固定）" if "提醒" in k else "否"
        print(f"{k:26s} {v:8,}  {cacheable}")
    print(f"{'-' * 50}")
    total = sys_tok + hist_tok + tools_tok + last_tok
    print(f"{'合计':26s} {total:8,}")

    stable = sys_tok + hist_tok + tools_tok
    print(f"\n可缓存: {stable:,}  永不可缓存: {last_tok:,} "
          f"({last_tok / max(1, total) * 100:.1f}%)")
    print(f"→ 命中率理论上限 ≈ {stable / max(1, total) * 100:.1f}%")

    # 提醒里有多少是完全固定的
    if "格式提醒(reminder)" in parts:
        m = re.search(r'(★★★.*?)(?=\n\S+说：)', last, re.S)
        rem = m.group(1)
        fixed = rem
        for ph in ("40", "20", "优先用上下文", "如果你不了解"):
            fixed = fixed.replace(ph, "")
        # 去掉 no_repeat 段（【防复读】那行）
        fixed2 = re.sub(r'【防复读】.*?\n', '', fixed)
        print(f"\n提醒块 {tc(rem):,} token 中，去掉 3 个占位符后固定部分 "
              f"≈ {tc(fixed2):,} token")
        print("→ 这部分完全可以移进 system（4 倍便宜）")


asyncio.run(main())

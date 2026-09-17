# -*- coding: utf-8 -*-
"""端到端验证：真实跑一次 FC，确认先导语不再泄漏 ||。

做法：调 generate_multi_reply_with_tools，用一个 recording interim_cb 捕获
先导语，断言捕获到的每一条都不含竖线。

问题设计成「需要联网搜索」以提高触发工具调用的概率（FC 轮1 才会发先导语）。
多试几个问题，只要有一轮出现先导语就算验证到路径。

跑法：python3 scripts/_test_lead_e2e.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import get_config  # noqa: E402
from services.llm import generate_multi_reply_with_tools  # noqa: E402

# 容易触发工具（搜索/状态）的问题，用来把 FC 轮1 的先导语路径跑出来
QUESTIONS = [
    "帮我搜一下 DeepSeek V4.1 Flash 的发布时间",
    "现在电脑上在听什么歌",
    "查一下 openai.com 这个域名什么时候到期",
]


async def main():
    cfg = get_config()
    got_leads = []
    bad = []

    async def rec_cb(text: str):
        got_leads.append(text)
        print("   [先导语] %r" % text)
        if "|" in text:
            bad.append(text)

    for q in QUESTIONS:
        print("\n=== 提问: %s ===" % q)
        try:
            await generate_multi_reply_with_tools(
                msg_history=["[用户] 你好"],
                speaker_name="测试",
                current_msg=q,
                bot_name=cfg.bot_name or "幻梦",
                system_prompt=cfg.build_system_prompt(),
                reply_model=cfg.reply_model,
                is_group=False,
                extra_info="",
                max_tokens=800,
                user_id=0, group_id=0, bot_qq=cfg.bot_qq,
                interim_cb=rec_cb,
            )
        except Exception as e:
            print("   调用异常: %s: %s" % (type(e).__name__, str(e)[:150]))

    print("\n" + "=" * 52)
    print("捕获到先导语 %d 条" % len(got_leads))
    for s in got_leads:
        print("  -", repr(s))
    if got_leads:
        print("\n含竖线的先导语: %d 条" % len(bad))
        for s in bad:
            print("  ! ", repr(s))
        print("结论:", "仍有泄漏" if bad else "全部干净，修复生效")
    else:
        print("结论: 本次没触发先导语路径（LLM 未在轮1写话或未调工具），")
        print("      需换问题重试；单元测试 scripts/_test_lead_split.py 已覆盖该逻辑")
    return 1 if bad else 0


sys.exit(asyncio.get_event_loop().run_until_complete(main()))

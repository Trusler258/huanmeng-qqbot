# -*- coding: utf-8 -*-
"""提示词精简直测 —— 4 个真实场景，对比重写前后的回复质量

用法: python3 tests/_test_prompt_ab.py
说明: 每次运行都会读取当前 data/skills/*.md（新进程无缓存），
      所以「先跑 = 旧提示词基线」→ 换文件 →「再跑 = 新提示词结果」即为 A/B 对比。

关注点（对应诊断出的 6 个问题）:
  用例1 知识碎片化 —— 回复是否被剁成关键词短语
  用例2 追问必展开 —— 说"详细点"后信息量是否真的增加
  用例3 只答当前   —— 别人在聊游戏，用户只发"噗"，是否乱点评
  用例4 闲聊简短   —— 是否简短自然、不啰嗦不搭话
"""
import asyncio, sys, os

for p in (r"G:\py\qqbot", "/root/bot", os.getcwd()):
    if p not in sys.path and os.path.isdir(p):
        sys.path.insert(0, p)

from services.llm import generate_multi_reply_with_tools, _detect_skill_needs
from core.config import get_config


def analyze(tag: str, sentences: list):
    """量化分析：句数、每句字数、是否疑似碎片"""
    if not sentences:
        print(f"  [{tag}] 空回复")
        return
    lens = [len(str(s)) for s in sentences]
    total = sum(lens)
    # 碎片判定：>=3 句 且 平均句长 < 18 字 且 没有超过 40 字的长句
    frag = len(sentences) >= 3 and (total / len(sentences)) < 18 and max(lens) < 40
    print(f"  [{tag}] {len(sentences)} 句 / 共 {total} 字 / 句长 {lens} / 疑似碎片: {'是 ←' if frag else '否'}")


async def run_case(title: str, history: list, current: str, is_group=True):
    cfg = get_config()
    thinking = "deep" in _detect_skill_needs(current, is_group)
    print(f"\n{'=' * 62}\n{title}\n  输入: {current[:70]}\n  思考模式: {'开' if thinking else '关'}")
    sentences, *_ = await generate_multi_reply_with_tools(
        msg_history=history,
        speaker_name="测试者",
        current_msg=current,
        bot_name=cfg.bot_name,
        system_prompt=cfg.system_prompt,
        reply_model=cfg.reply_model,
        is_group=is_group,
        max_tokens=None,
        user_id=0, group_id=0, bot_qq=cfg.bot_qq,
        thinking=thinking,
    )
    analyze("结果", sentences)
    for i, s in enumerate(sentences or [], 1):
        print(f"    {i}. {s}")
    return sentences


async def main():
    # 用例1：知识类提问（原问题：回复被剁成关键词碎片）
    await run_case(
        "用例1 · 知识碎片化",
        [],
        "[群友] 测试者[fav=50]发了: 雷石东直放站是啥",
    )

    # 用例2：追问展开（原问题：说"详细点"反而更短）
    await run_case(
        "用例2 · 追问必展开",
        [
            "[群友] 测试者[fav=50]: 雷石东直放站是啥",
            "幻梦: 查到啦～是老MC梗 || 红石中继器的机翻 || 2012年翻译事故闹的",
        ],
        "[群友] 测试者[fav=50]发了: 详细点",
    )

    # 用例3：只答当前（原问题：规则被截断导致乱点评别人的话题）
    await run_case(
        "用例3 · 只答当前（别人聊游戏，我只发个语气词）",
        [
            "[群友] 夜煞[fav=50]: 我刚那把狙击太拉了，被路人杀了",
            "[admin] Trusler[fav=50]: potato_man KD才0.37还嘴硬，笑死",
            "[群友] 夜煞[fav=50]: 网卡了没办法",
        ],
        "[群友] 落雨[fav=20]发了: 噗",
    )

    # 用例4：闲聊简短（原问题：容易搭话 + 啰嗦）
    await run_case(
        "用例4 · 闲聊简短",
        [],
        "[群友] 测试者[fav=50]发了: 今天好无聊啊",
    )

    # 用例5：轻度解释请求（用户实际抱怨：13 句 3800 字太长 + 句号 + 百科腔割裂）
    await run_case(
        "用例5 · 轻度解释（不该写成百科长文）",
        [],
        "[admin] Trusler[fav=50]发了: 帮我解释这是什么意思\n【缓存命中率分析】近7天 命中率57.3% 命中输入2,345,678 总输入4,092,331 缓存命中省下约12.4元",
    )


asyncio.run(main())

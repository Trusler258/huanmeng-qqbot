#!/usr/bin/env python3
"""戳一戳（摸头）回应 A/B 验证：新规则是否真的结合上下文（v2.3.65）

复现方式与 handle_poke_event 一致：system + 会话上下文 + extra_info(含戳一戳规则) + reply_reminder。
每个场景跑两遍：
  OLD = v2.3.65 之前硬编码的规则（不要展开话题 + 从 6 种情绪随机抽）
  NEW = data/skills/40_reminders.md::poke_reminder（必须长在上下文上）
期望：OLD 的回应与本轮话题无关（泛泛的"被摸了"）；
      NEW 的回应能看出接着上下文那条线；安静群场景两者都应是自然小反应。

只走「组装提示词 + 调 LLM」，不发消息、不写文件。
"""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "/root/bot")

_env = Path("/root/bot/config/.env")
if _env.exists():
    for _line in _env.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip())

from core.config import load_bot_config  # noqa: E402
from utils.format_lang import format_lang  # noqa: E402
from services.llm import (  # noqa: E402
    _build_messages,
    _build_reminder,
    call_llm,
)

cfg = load_bot_config()
BOT = cfg.bot_name

OLD_RULES = [
    "【戳一戳规则：只用 1 句简短回应，不要展开话题，不要超过 20 字】",
    "【禁止重复：绝对不要说摸头很舒服、摸摸头、被摸了之类的前一次用过的句式，每次必须想全新的回应】",
    "【随机语气：可以从疑惑、开心、害羞、吓一跳、嫌弃、淡定中随机选一种情绪回应】",
    "【禁止调用任何工具/指令/搜索，只输出纯文本回复】",
]
NEW_RULE = _build_reminder("poke_reminder", append_plain=False)

# ── 场景：(名称, 会话上下文, 话题关键词, 说明) ──
SCENARIOS = [
    (
        "A 正聊得投入（bot 正在帮人排障）",
        [
            "群友说：幻梦帮我看看这个红石电路为啥不亮",
            f"{BOT}说：{{\"replies\":[\"我看下哦\",\"你这个中继器朝向反了，信号传不过去\"]}}",
            "群友说：哦哦原来是朝向问题，那我把两个都转一下",
        ],
        ["中继器", "朝向", "电路", "信号", "转"],
        "应顺着「在帮人看电路」这条线，而不是无视话题",
    ),
    (
        "B 刚被怼过（还在斗嘴）",
        [
            "Trusler说：幻梦你好菜啊，这都不会",
            f"{BOT}说：{{\"replies\":[\"哪有，我明明很强\",\"你才菜呢，你自己来\"]}}",
        ],
        ["菜", "你才", "明明", "自己", "强", "认输", "服", "输", "哼"],
        "应顺着「斗嘴」的劲儿，不服气/反手来一句",
    ),
    (
        "C 安静群（没有任何话题在跑）",
        [
            "群友说：早",
            f"{BOT}说：{{\"replies\":[\"早呀～\"]}}",
        ],
        [],
        "这里才应该是单纯「被拍了一下」的自然小反应",
    ),
]


def parse_replies(raw: str) -> list:
    try:
        d = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
        rs = d.get("replies") or []
        return rs if isinstance(rs, list) else []
    except Exception:
        return []


async def run(history: list, rules: list) -> list:
    # _build_messages 内部自己会插 system（用 cfg.system_prompt），这里不要再插一次
    speaker = "Trusler"
    system_msg = format_lang("poke.message", name=speaker, bot_name=BOT)
    extra = "\n".join([
        "当前时间：2026年09月28日 11:20:00.000 周一",
        f"当前{speaker}对你的好感度：30/100",
        *rules,
    ])
    msgs = _build_messages(
        history, speaker, f"[系统] {system_msg}", BOT, cfg.system_prompt, True, extra,
    )
    raw = await call_llm(cfg.reply_model, msgs, max_tokens=600,
                         temperature=0.7, scene="probe")
    return parse_replies(raw)


def overlap(rs: list, kws: list) -> int:
    if not kws:
        return -1
    txt = "".join(str(x) for x in rs)
    return sum(1 for k in kws if k in txt)


async def main():
    print("=" * 70)
    print("NEW poke_reminder（%d 字）:" % len(NEW_RULE))
    print(NEW_RULE[:200] + " ...")
    print("=" * 70)
    for label, hist, kws, note in SCENARIOS:
        print("\n" + "=" * 70)
        print("【%s】" % label)
        print("  期望：%s" % note)
        for variant, rules in (("OLD", OLD_RULES), ("NEW", [NEW_RULE])):
            try:
                rs = await run(hist, rules)
            except Exception as e:
                print("  [%s] 调用失败: %s" % (variant, e))
                continue
            hit = overlap(rs, kws)
            txt = "".join(str(x) for x in rs)
            tag = "" if hit < 0 else ("  上下文词命中=%d" % hit)
            if not kws:
                # C 场景：没有话题，出现"硬凑上文言词"反而是坏事
                tag = "  硬凑上文=%s" % ("是" if "早" in txt else "否")
            print("  [%s] %s%s" % (variant, " / ".join(str(x) for x in rs), tag))
    print("\n" + "=" * 70)
    print("判读：A/B 场景 NEW 应能看出接着上文；OLD 多为泛泛的「被摸了」反应。")


if __name__ == "__main__":
    asyncio.run(main())

#!/usr/bin/env python3
"""语境感知验证：陈述句不该被当成求教来开课（v2.3.55）

复现 09-24 09:31 的原始场景：
    bot 问「你那边课讲什么呢」→ 用户答「mysql 多表查询」
对照两种输入：
    A 陈述（原场景）：mysql 多表查询
    B 明确求教      ：mysql 多表查询是什么意思
期望：A = 接话/反问，1~4 句；B = 展开讲，5~15 句。

只走「组装提示词 + 调 LLM」，不发送任何消息、不写任何文件。
"""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "/root/bot")

# ── 先加载 .env（cfg 的模型 url/key 来自这里），再读配置 ──
_env = Path("/root/bot/config/.env")
if _env.exists():
    for _line in _env.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip())

from core.config import load_bot_config  # noqa: E402
from services.llm import (  # noqa: E402
    _build_reminder,
    _build_skill_refs,
    _build_system_text,
    _detect_skill_needs,
    call_llm,
)

cfg = load_bot_config()

# 原始对话：bot 刚问完「课讲什么呢」
HIST = [
    {"role": "user", "content": "Trusler说：我在学校机房。"},
    {"role": "assistant",
     "content": '{"replies":["机房啊，那你还敢明目张胆跟我聊","悠着点，别被逮了"],"fav":0}'},
    {"role": "user", "content": "Trusler说：我在最后一排小问题"},
    {"role": "assistant",
     "content": '{"replies":["最后一排是吧，那也别太嚣张","你那边课讲什么呢，听不听得进去"],"fav":0}'},
]

CASES = [
    ("A 陈述（原场景）", "mysql 多表查询"),
    ("B 明确求教", "mysql 多表查询是什么意思"),
]


def parse_replies(raw: str) -> list:
    try:
        d = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
        rs = d.get("replies") or []
        return rs if isinstance(rs, list) else []
    except Exception:
        return []


async def run(msg: str):
    needs = _detect_skill_needs(msg, is_group=False)
    system = _build_system_text(cfg.bot_name, cfg.system_prompt, is_group=False)
    refs = _build_skill_refs(needs, False, msg)
    reminder = _build_reminder("reply_reminder", ctx_hint="", max_chars="20", no_repeat="")
    msgs = [{"role": "system", "content": system + ("\n\n" + refs if refs else "")}]
    msgs += HIST
    msgs.append({"role": "user", "content": reminder + "\n\nTrusler说：" + msg})
    raw = await call_llm(cfg.reply_model, msgs, max_tokens=1000,
                         temperature=0.7, scene="probe")
    return needs, raw


async def main():
    print("=" * 64)
    for label, msg in CASES:
        needs, raw = await run(msg)
        rs = parse_replies(raw)
        chars = sum(len(str(x)) for x in rs)
        print("")
        print("【%s】输入：%s" % (label, msg))
        print("  needs = %s" % sorted(needs))
        print("  句数 = %d   总字数 = %d" % (len(rs), chars))
        for i, s in enumerate(rs, 1):
            if isinstance(s, dict):
                print("   %d. <指令 %s>" % (i, s.get("cmd") or s.get("name")))
            else:
                print("   %d. %s" % (i, s))
    print("")
    print("=" * 64)
    print("判读：A 应为 1~4 句接话/反问（不开课）；B 才允许铺开")


if __name__ == "__main__":
    asyncio.run(main())

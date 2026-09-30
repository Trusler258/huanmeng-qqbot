#!/usr/bin/env python3
"""戳一戳「起手式口癖 / 模板化」量化探针（真调 LLM，输出硬指标）

背景（2026-09-30 用户反馈"有点公式化尴尬了" + 实测）：
  · msglog 里 bot 文本发言首字「诶」占 **18.3%**（161/881），是绝对第一起手字
  · 用户贴的 7 条戳一戳/回复里 **7 条都**以「诶，」开头
  · 怀疑是**自回归放大**：`core/pipeline.py::_poke_history` 把 bot 自己前 5 条戳一戳回复
    喂回上下文（本意是防复读），模型看到自己连续「诶，XXX」→ 继续这个模式

本探针复现这个状态：上下文里**故意放 3 条 bot 自己的「诶，」戳一戳回应**，
然后重复采样 N 次，统计：
  ① 起手词为「诶」的比例
  ② 起手词与上文自己那几条雷同的比例
  ③ 复述本轮话题词的比例（"复述+点评"结构）
  ④ 平均字数

⚠️ 必须用 `_build_reminder("poke_reminder")` 取规则（与生产同一条路径），
   不要手抄规则文本。

用法（服务器）:
  python3 scripts/_probe_poke_habit.py                       # 当前生效规则，采样 8 次
  python3 scripts/_probe_poke_habit.py 8 变体名              # 指定采样次数与标签
  python3 scripts/_probe_poke_habit.py 8 变体名 规则文件     # 用外部规则文件（A/B 用）
  python3 scripts/_probe_poke_habit.py 8 变体名 规则文件 2   # 再限制「只喂最近 2 条上下文」
"""
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_env = ROOT / "config" / ".env"
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
    _build_reminder,
    generate_multi_reply_with_tools,
)

cfg = load_bot_config()
BOT = cfg.bot_name

# ── 场景：(名称, 会话上下文, 话题关键词, bot 上文的起手词) ──
# 上下文按真实形态构造：最后几条是 **bot 自己刚发的戳一戳回应**（都「诶，」开头），
# 这正是 _poke_history 喂回去的样子。
SCENARIOS = [
    (
        "A 考试/卷子（用户截图原场景）",
        [
            "月夜: @幻梦 它不会替我考试那你替我考吧qwq",
            f"{BOT}: 诶，替你考试？那我得先看看卷子上有没有小鱼干奖励～",
            "月夜: @幻梦 我手动放几个owo",
            f"{BOT}: 诶，手动放几个，那你这鱼干是画上去的还是真能咬得动呀～",
            "月夜: @幻梦 那我就只能边考试边摸你了qwq",
            f"{BOT}: 诶，边考试边摸我，那你这卷子怕是要写满我的名字咯～",
        ],
        ["卷子", "考试", "鱼干", "答题卡", "摸"],
    ),
    (
        "B 披风（用户截图原场景）",
        [
            "空无声岁月: 有个披风肘子朋友就是好qwq",
            "空无声岁月: 3个披风都给我弄到了",
            f"{BOT}: 诶，披风还没同步就先来摸我啦",
            "空无声岁月: 好感度",
            f"{BOT}: 诶，披风的事还没着落，你倒先来摸头啦～",
        ],
        ["披风", "同步", "正版", "登录"],
    ),
]

SPEAKER = "月夜"
POKE_HEAD = "诶"

TITLE = sys.argv[2] if len(sys.argv) > 2 else "当前规则"
# 可选：只喂最近 N 条上下文（测「素材量 → 复述率」的关系）。0=不限
TAIL = int(sys.argv[4]) if len(sys.argv) > 4 else 0
# LIST=1：把上文里"自己说过的话"抽成 extra_info 清单，history 只留对方的
LIST = (len(sys.argv) > 5 and sys.argv[5] in ("1", "list"))


def load_rule() -> str:
    if len(sys.argv) > 3:
        p = Path(sys.argv[3])
        return p.read_text(encoding="utf-8").strip()
    return _build_reminder("poke_reminder", append_plain=False)


async def run(history: list, rule: str, said: list | None = None) -> list:
    """⚠️ 必须走 generate_multi_reply_with_tools（= 生产真实入口），不能直接 call_llm：
    直接调 call_llm 拿到的可能是纯文本（没有 JSON schema 强约束与兜底解析），
    统计口径会和线上不一致 —— 第一版探针就栽在这。"""
    system_msg = format_lang("poke.message", name=SPEAKER, bot_name=BOT)
    extra = "\n".join([
        "当前时间：2026年09月30日 10:18:00.000 周三",
        f"当前{SPEAKER}对你的好感度：100/100",
        rule,
    ])
    sentences, *_ = await generate_multi_reply_with_tools(
        msg_history=history,
        speaker_name=SPEAKER,
        current_msg=f"[系统] {system_msg}",
        bot_name=BOT,
        system_prompt=cfg.system_prompt,
        reply_model=cfg.reply_model,
        is_group=True,
        extra_info=extra,
        max_tokens=None,
        user_id=10001, group_id=20002, bot_qq=cfg.bot_qq,
    )
    return [str(x) for x in (sentences or [])]


def head_of(text: str, n: int = 2) -> str:
    t = text.lstrip(" \u3000")
    # 去掉可能的前缀标记
    for p in ("[已思考", "【"):
        if t.startswith(p):
            return t[:n]
    return t[:n]


async def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    rule = load_rule()

    print("=" * 72)
    print("规则来源: %s（%d 字）" % (TITLE, len(rule)))
    print("每场景采样 %d 次，temperature=0.4（与生产一致）" % n)
    print("=" * 72)

    for label, hist, kws in SCENARIOS:
        print("\n" + "=" * 72)
        print("【%s】上文里 bot 自己那几条都以「%s，」开头%s"
              % (label, POKE_HEAD,
                 ("；本组只喂最近 %d 条" % TAIL) if TAIL else ""))
        print("  话题关键词: %s" % "/".join(kws))
        print("-" * 72)

        same_head = 0
        echo_kw = 0
        lens = []
        seen = []
        for i in range(n):
            if LIST:
                said = [h[len(BOT) + 2:] for h in hist if h.startswith(BOT + ": ")]
                human = [h for h in hist if not h.startswith(BOT + ": ")]
                used = human[-TAIL:] if TAIL else human
            else:
                said = None
                used = hist[-TAIL:] if TAIL else hist
            try:
                rs = await run(used, rule, said)
            except Exception as e:
                print("  第%d次调用失败: %s" % (i + 1, e))
                continue
            if not rs:
                print("  第%d次: （没解析出 replies）" % (i + 1))
                continue
            line = " / ".join(rs)
            seen.append(line)
            h = head_of(rs[0])
            hit_head = (h == (POKE_HEAD + "，") or h == POKE_HEAD + "，")
            same_head += 1 if hit_head else 0
            hit_kw = [k for k in kws if k in line]
            echo_kw += 1 if hit_kw else 0
            lens.append(len(line))
            print("  %2d. %-58s %s%s" % (
                i + 1, line[:58],
                "[同起手]" if hit_head else "",
                (" 复述:%s" % ",".join(hit_kw)) if hit_kw else ""))

        print("-" * 72)
        print("  ① 起手词＝「%s，」: %d/%d = %.0f%%"
              % (POKE_HEAD, same_head, n, same_head * 100.0 / max(1, n)))
        print("  ② 复述本轮话题词 : %d/%d = %.0f%%"
              % (echo_kw, n, echo_kw * 100.0 / max(1, n)))
        print("  ③ 平均长度       : %.0f 字" % (sum(lens) / max(1, len(lens))))
        uniq = len(set(seen))
        print("  ④ 不同回应条数   : %d/%d（多样性，越高越好）" % (uniq, len(seen)))


if __name__ == "__main__":
    asyncio.run(main())

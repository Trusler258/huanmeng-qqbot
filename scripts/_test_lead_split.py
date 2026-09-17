# -*- coding: utf-8 -*-
"""FC 先导语拆分测试（v2.3.36）。

事故背景：LLM 在轮1把先导语写成一串多句（用 || 分隔），
而先导语回调直接把整条发出，`||` 泄漏到群里。实测记录：

    16:53:52 LLM原始输出 [轮1]: content=诶？主人你这是让我把赞赏码发出来呀～ || 那我试试，
              不过文件要是还没传上去，我这边也变不出来哦 | tool_calls=1
    16:53:52 开始分批发送 1 条句子        ← 只有 1 条，|| 没被拆
    16:53:52 已发送第 1/1 条: 诶？主人你这是让我把赞赏码发出来呀～ || 那我试试…

跑法：python3 scripts/_test_lead_split.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core.pipeline as P  # noqa: E402

PASS, FAIL = [], []


def ck(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("OK" if cond else "FAIL", name, ("  " + str(extra)) if extra else ""))


async def capture(text, thought_ctx=None, sent_lead=None):
    """跑一次先导语发送，返回实际发出的句子列表"""
    sent = []

    async def fake_send(sentences, chat_id, is_group, **kw):
        sent.extend(sentences)

    orig = P.send_sentences
    P.send_sentences = fake_send
    try:
        cb = P._make_interim_sender(123, True, 456, thought_ctx, sent_lead)
        await cb(text)
    finally:
        P.send_sentences = orig
    return sent


async def main():
    print("\n=== 一、|| 必须拆开（事故现场原文）===")
    accident = ("诶？主人你这是让我把赞赏码发出来呀～ || 那我试试，"
                "不过文件要是还没传上去，我这边也变不出来哦")
    out = await capture(accident)
    ck("拆成 2 条", len(out) == 2, out)
    ck("第 1 条不含 ||", "||" not in out[0], out[0] if out else "")
    ck("第 2 条不含 ||", len(out) > 1 and "||" not in out[1])
    ck("内容未被截断", out and out[0].startswith("诶？主人你这是") and "变不出来" in out[-1])

    print("\n=== 二、三种分隔符写法都要拆 ===")
    for label, raw, want in (
        ("无空格 ||", "甲||乙", 2),
        ("带空格 ||", "甲 || 乙", 2),
        ("三竖线 |||", "甲|||乙", 2),
        ("多个 ||", "甲 || 乙 || 丙", 3),
        ("首尾多余 ||", "|| 甲 || 乙 ||", 2),
    ):
        o = await capture(raw)
        ck("%s → %d 条" % (label, want), len(o) == want, o)
        ck("%s 无残留竖线" % label, all("|" not in s for s in o), o)

    print("\n=== 三、不含分隔符 → 仍单条（不能改变原行为）===")
    o = await capture("帮你搜搜看吧")
    ck("单条", o == ["帮你搜搜看吧"], o)

    print("\n=== 四、上限 3 条（防刷屏）===")
    o = await capture("一 || 二 || 三 || 四 || 五")
    ck("最多 3 条", len(o) == 3, o)

    print("\n=== 五、思考标记只挂第一条 ===")
    tc = {"secs": 7, "applied": False}
    o = await capture("甲 || 乙", thought_ctx=tc)
    ck("第一条带 [已思考7秒]", o and o[0].startswith("[已思考7秒]"), o)
    ck("第二条不带", len(o) > 1 and not o[1].startswith("[已思考"), o)
    ck("applied 置位", tc["applied"] is True)

    print("\n=== 六、sent_lead 记录全部拆出的句子（去重依赖它）===")
    lead = []
    await capture("甲 || 乙 || 丙", sent_lead=lead)
    ck("记录了 3 条", len(lead) == 3, lead)

    print("\n=== 七、空/纯竖线输入不炸、且不把竖线发出去 ===")
    for bad in ("", "||", "|||", "   ", " || "):
        try:
            o = await capture(bad)
            ck("输入 %r 不发竖线" % bad, all("|" not in s for s in o), o)
        except Exception as e:
            ck("输入 %r 不抛异常" % bad, False, e)

    print("\n=== 八、混了竖线但仍有正文（兜底去竖线后发出）===")
    o = await capture("|| 只剩一句 ||")
    ck("发出 1 条且无竖线", len(o) == 1 and "|" not in o[0], o)

    print("\n" + "=" * 52)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  -", f)
    print("=" * 52)
    return 0 if not FAIL else 1


sys.exit(asyncio.get_event_loop().run_until_complete(main()))

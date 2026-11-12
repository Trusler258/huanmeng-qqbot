"""回归：@ 后面只有指令时，必须只走指令层，绝不进 LLM。

起因 2026-11-09：用户要求「@ 机器人后如果后面只有指令的话就不走 LLM」。
关键在分流前的正文清理——零宽字符 / 各种 @ 记号只要漏一个，
`startswith("/")` 就是假，指令会被当成聊天送进模型。

这里只测 `_clean_incoming()`（纯函数），不碰网关与 LLM。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import qq_official_bot as m   # noqa: E402

C = m._clean_incoming


def main() -> int:
    fails = []

    def ck(name, got, want, cmd=None):
        if got != want:
            fails.append(f"{name}\n  got ={got!r}\n  want={want!r}")
        if cmd is not None and m._is_command(got) != cmd:
            fails.append(f"{name} 判定\n  _is_command={m._is_command(got)}")

    # 1. 零宽字符夹在 @ 和指令之间（真实形态）
    ck("零宽+<@>", C("\u200b<@ABC123> /~wdsj bw"), "/~wdsj bw", True)
    ck("零宽在指令前", C("\u200d\u200b/~help"), "/~help", True)

    # 2. 三种 @ 记号
    ck("<qqbot-at-user>", C('<qqbot-at-user id="x" /> /~wdsj'), "/~wdsj", True)
    ck("CQ at", C("[CQ:at,qq=123] /~dice"), "/~dice", True)

    # 3. 客户端拼的纯文本 @
    ck("纯文本 @", C("@织梦 /~help"), "/~help", True)
    ck("纯文本 @ 带空格", C("  @织梦   /~wdsj bw"), "/~wdsj bw", True)

    # 4. 多种记号交替（要反复剥到稳定）
    ck("交替", C('\u200b<@A> <qqbot-at-user id="b" />@织梦 /~ping'), "/~ping", True)

    # 5. 正常聊天不能被误伤
    ck("普通聊天", C("你好呀"), "你好呀", False)
    ck("@了但没指令", C("@织梦 你好呀"), "@织梦 你好呀", False)
    ck("空内容", C(""), "", False)
    ck("只有 @", C("\u200b<@A>  "), "", False)

    # 6. 指令后面的正文属于指令参数，不做聊天
    ck("指令带参数", C("@织梦 /~wdsj bw 我菜不菜"), "/~wdsj bw 我菜不菜", True)

    # 7. @ 判定：内容带 mention 记号就算 @（事件类型可能是全量消息）
    def at(text):
        return bool(m._REAL_MENTION_HEAD_RE.match(m._INVISIBLE_RE.sub("", text)))

    for name, text, want in [
        ("<@> 打头", "\u200b<@ABC> 你好", True),
        ("qqbot-at-user 打头", '<qqbot-at-user id="x" /> 你好', True),
        ("CQ at 打头", "[CQ:at,qq=1] 你好", True),
        ("前导空格不算 @", "  你好", False),
        ("普通文字不算 @", "你好", False),
        ("纯文本 @ 不算标签", "@织梦 你好", False),
    ]:
        if at(text) != want:
            fails.append(f"@判定 {name}\n  got={at(text)}")

    # 8. 分流优先级：@ 之后**先看是不是只有指令**，是则走指令、绝不进 LLM
    def route(text, is_at):
        t = C(text)
        if m._is_command(t):
            return "cmd"
        return "chat" if is_at else "ignore"

    for name, text, is_at, want in [
        ("@ + 纯指令", "@织梦 /~wdsj bw", True, "cmd"),
        ("@ + 纯指令(带零宽)", "\u200b<@A> /~reload", True, "cmd"),
        ("@ + 指令带参数", "@织梦 /~wdsj bw 玩家名", True, "cmd"),
        ("@ + 聊天", "@织梦 你好", True, "chat"),
        ("@ + 聊天里夹指令", "@织梦 你好 /~wdsj bw", True, "chat"),
        ("未 @ 的聊天", "你好", False, "ignore"),
        ("未 @ 的指令", "/~ping", False, "cmd"),
    ]:
        got = route(text, is_at)
        if got != want:
            fails.append(f"分流 {name}\n  got ={got}\n  want={want}")

    if fails:
        print("FAIL")
        for f in fails:
            print(" -", f)
        return 1
    print("OK 19/19")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

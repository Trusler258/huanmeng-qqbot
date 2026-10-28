"""_strip_leading_mentions 回归测试（2026-10-28 重写后建）。

覆盖四类：
1. 单 @ + 昵称含斜杠 + 指令      → 应剥成指令
2. 多个 @ 且正文中间才有 /xxx     → 应保持原样（判成聊天，别误判指令）
3. CQ 码形态 @                   → 应剥成指令
4. 零宽字符 / 疑问句 / 正文中间斜杠 → 不应剥

跑法：python tests/_test_mention_strip.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.dispatcher import _strip_leading_mentions as S  # noqa: E402

CASES = [
    # (说明, 输入, 期望输出)
    ("单 @ 昵称含斜杠 + 指令", "@//rick/ /mc 53cfec gif", "/mc 53cfec gif"),
    ("单 @ 普通昵称 + /~ 指令", "@小明 /~help", "/~help"),
    ("CQ 码 @ + 指令", "[CQ:at,qq=123456] /~wdsj me", "/~wdsj me"),
    ("零宽字符插在 @ 与正文之间", "@小明\u200b/~help", "/~help"),
    ("两个 @ + 正文中间才有 /mc", "@//rick/ 53cfec @//rick/ /mc 53cfec gif",
     "@//rick/ 53cfec @//rick/ /mc 53cfec gif"),
    ("两个 @ 连续 + 指令", "@小明 @小红 /~help", "/~help"),
    ("剥完不是指令（正文开头是 hash）", "@小明 53cfec /mc", "@小明 53cfec /mc"),
    ("疑问句不剥", "@小明 /~draw 是啥意思", "@小明 /~draw 是啥意思"),
    ("正文中间斜杠不动", "我也想 /mc 一下", "我也想 /mc 一下"),
    ("CQ 码 + 多个 @", "[CQ:at,qq=1] [CQ:at,qq=2] /~help", "/~help"),
]


def main() -> int:
    ok = bad = 0
    for name, src, want in CASES:
        got = S(src)
        if got == want:
            ok += 1
        else:
            bad += 1
            print(f"✗ {name}")
            print(f"   输入: {src!r}")
            print(f"   期望: {want!r}")
            print(f"   实际: {got!r}")
    print(f"\n通过 {ok} / 失败 {bad}")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

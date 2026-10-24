"""v2.3.106 群聊观感修复的单元测试。

覆盖两处：
1. _merge_short_replies —— 短回复不拆条
2. _strip_leading_mentions —— @机器人 + /~指令 能被识别
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS = FAIL = 0


def ck(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s | %s" % (name, detail))


def test_merge():
    from core.pipeline import _merge_short_replies
    print("[1] 碎片合并（v2.3.107：只并碎片，不并完整句）")

    # ⚠️ 反向断言：两句完整的话**不许**被压成一条（v2.3.106 的 70 字阈值已废）
    s, merged = _merge_short_replies(["会一点啦，写个小脚本还行～", "太复杂的得慢慢琢磨"])
    ck("两句完整的话不合并", (not merged) and len(s) == 2, str(s))

    s, merged = _merge_short_replies(["嗯。", "好。"])
    ck("纯应答碎片合并", merged and len(s) == 1, str(s))

    s, merged = _merge_short_replies(["嗯", "今天群里好安静啊，都没人说话"])
    ck("碎片并进正文", merged and len(s) == 1 and s[0].startswith("嗯"), str(s))

    s, merged = _merge_short_replies(["在呢在呢", "刚在忙别的", "你说啥"])
    ck("连续碎片不刷屏", merged and len(s) <= 2, str(s))

    long_a = "我叫幻梦，十六岁，浅蓝到银白渐变的猫耳和尾巴，琥珀色眼睛，头发上别着星星发夹。"
    long_b = "平时爱被摸头、看小说，也会写点 Python 小脚本，群里能搭把手的有查天气、查地震、搜资料。"
    s, merged = _merge_short_replies([long_a, long_b])
    ck("长句不合并", (not merged) and len(s) == 2, "总长 %d" % (len(long_a) + len(long_b)))

    s, merged = _merge_short_replies(["a", "b"], has_calls=True)
    ck("有工具调用不合并", (not merged) and len(s) == 2, str(s))

    s, merged = _merge_short_replies(["a", "b"], has_flow=True)
    ck("有交错调用不合并", (not merged) and len(s) == 2, str(s))

    s, merged = _merge_short_replies(["只有一句"])
    ck("单句本来就不动", (not merged) and s == ["只有一句"])

    s, merged = _merge_short_replies([])
    ck("空列表不炸", (not merged) and s == [])


def test_strip():
    from core.dispatcher import _strip_leading_mentions
    print("[2] 剥离前导 @")

    ck("CQ码 + 指令", _strip_leading_mentions("[CQ:at,qq=3682248514] /~draw 1:1 猫")
       == "/~draw 1:1 猫")
    ck("明文@ + 指令", _strip_leading_mentions("@幻梦 /~draw 1:1 猫") == "/~draw 1:1 猫")
    ck("带空格", _strip_leading_mentions("  @幻梦   /~luck") == "/~luck")
    ck("多个@", _strip_leading_mentions("@a @b /~help") == "/~help")

    # 不该动的
    ck("普通聊天不剥", _strip_leading_mentions("@幻梦 你好呀") == "@幻梦 你好呀")
    ck("提问不误判", _strip_leading_mentions("@幻梦 /~draw 是啥意思")
       == "@幻梦 /~draw 是啥意思")
    ck("无@原样", _strip_leading_mentions("/~luck") == "/~luck")
    ck("空串", _strip_leading_mentions("") == "")
    ck("@在中间不动", _strip_leading_mentions("看看 @幻梦 /~help")
       == "看看 @幻梦 /~help")

    # ★ v2.3.107：@ 别人 + 指令（用户 2026-10-25 报「不触发」）
    print("    -- @别人后带指令 --")
    ck("昵称带斜杠", _strip_leading_mentions("@//rick/ /mc") == "/mc")
    ck("昵称带斜杠+中文昵称", _strip_leading_mentions("@//rick/ 天在水 /mc") == "/mc")
    ck("零宽字符", _strip_leading_mentions("@//rick/ \u200b/mc") == "/mc")
    ck("零宽在@后", _strip_leading_mentions("@幻梦\u200b /~luck") == "/~luck")
    ck("@QQ号", _strip_leading_mentions("@123456789 /mc") == "/mc")
    ck("多个@", _strip_leading_mentions("@a @b /mc") == "/mc")
    # 不能误触发：昵称带斜杠但后面没有指令
    ck("斜杠昵称+闲聊不动", _strip_leading_mentions("@//rick/ 你好呀")
       == "@//rick/ 你好呀")
    ck("句中指令不误触发", _strip_leading_mentions("@幻梦 他说 /~draw 很好用")
       == "@幻梦 他说 /~draw 很好用")


def test_command_detect():
    """模拟 dispatcher 的判定：剥完是 /~ 开头即指令。"""
    from core.dispatcher import _strip_leading_mentions
    print("[3] 指令判定链路")

    def is_cmd(msg):
        t = _strip_leading_mentions(msg)
        return (t.startswith("/~") or t.startswith("/#")
                or (t.startswith("/") and len(t) > 1 and t[1:2] not in "~#/ "))

    ck("原 bug：@+/~draw 现在是指令", is_cmd("@3682248514 /~draw 1:1 1k 背景白底"))
    ck("CQ 形态同样识别", is_cmd("[CQ:at,qq=3682248514] /~wdsj me"))
    ck("纯指令仍识别", is_cmd("/~draw 猫"))
    ck("纯聊天不是指令", not is_cmd("@3682248514 你今晚绕来绕去"))


if __name__ == "__main__":
    test_merge()
    test_strip()
    test_command_detect()
    print("\n结果: %d 通过 / %d 失败" % (PASS, FAIL))
    raise SystemExit(0 if FAIL == 0 else 1)

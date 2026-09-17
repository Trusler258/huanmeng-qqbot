# -*- coding: utf-8 -*-
"""CQ 码路径规范化测试（v2.3.36）。

背景：多处手工拼 `f"[CQ:image,file=file:///{绝对路径}]"`，绝对路径以 `/` 开头
→ `file:////root/...`（四斜杠）。实测 msglog 175 条四斜杠 vs 108 条三斜杠。
修在发送出口（`sender.fix_cq_paths`）而非逐个改手拼点。

**关键**：这个修复必须"精确"，不能误伤正文里正常的 URL/代码/路径。

跑法：python3 scripts/_test_cq_paths.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.sender import fix_cq_paths  # noqa: E402

PASS, FAIL = [], []


def ck(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("OK" if cond else "FAIL", name, ("  " + str(extra)) if extra else ""))


def case(name, src, want):
    got = fix_cq_paths(src)
    ck(name, got == want, "" if got == want else "\n        得到 %r\n        期望 %r" % (got, want))


# ── 链路接线验证：函数写对了但没接上 = 没修 ──────────────
# 单独一节，用 mock 捕获真正传给 NapCat 的 message，确认出口确实调用了 fix_cq_paths
async def test_wiring():
    import services.sender as S

    captured = []

    class FakeMgr:
        async def call_api(self, action, params, timeout=5.0):
            captured.append((action, dict(params)))
            return {"message_id": 1}

    bad = "[CQ:image,file=file:////root/bot/x.png]"
    want = "file:///root/bot/x.png"

    orig = S.get_ws_manager
    S.get_ws_manager = lambda: FakeMgr()
    try:
        await S.send_group_msg(bad, 123)
        await S.send_private_msg(bad, 456)
        await S._send_and_record(bad, 123, True, None,
                                 type("C", (), {"bot_qq": 1, "bot_name": "t",
                                                "group_list": []})())
    finally:
        S.get_ws_manager = orig

    ck("三个出口都被捕获", len(captured) == 3, len(captured))
    for i, (action, params) in enumerate(captured):
        msg = str(params.get("message"))
        ck("出口%d(%s) 已规范化" % (i + 1, action), want in msg and "file:////" not in msg,
           msg[:60])


def main():
    print("\n=== 一、四斜杠被修正（事故原型）===")
    case("图片 CQ 四斜杠",
         "[CQ:image,file=file:////root/bot/data/img_temp/daily_767190084.jpg]",
         "[CQ:image,file=file:///root/bot/data/img_temp/daily_767190084.jpg]")
    case("五斜杠也归一",
         "[CQ:image,file=file://///root/bot/x.png]",
         "[CQ:image,file=file:///root/bot/x.png]")
    case("CQ:file 类型",
         "[CQ:file,file=file:////root/bot/x.zip,name=a.zip]",
         "[CQ:file,file=file:///root/bot/x.zip,name=a.zip]")

    print("\n=== 二、已正确的三斜杠保持不变（幂等）===")
    good = "[CQ:image,file=file:///root/bot/data/faces/smug_053.png]"
    case("三斜杠不动", good, good)
    case("重复调用幂等", fix_cq_paths(fix_cq_paths(good)), good)

    print("\n=== 三、★ 不误伤正文（这条最关键）===")
    # 正文里的普通 URL：双斜杠在域名后是正常的，绝不能动
    case("正文 URL 不动",
         "看这个 https://example.com/path//double 和 http://a.b/c",
         "看这个 https://example.com/path//double 和 http://a.b/c")
    # 正文里提到 file:// 但不在 CQ 码内
    case("正文 file:// 不误伤",
         "语法是 file:///path 或者 file://host/path",
         "语法是 file:///path 或者 file://host/path")
    # 正文里出现四斜杠但不在 CQ 码 file= 参数内 → 保守不动
    case("非 CQ 上下文不动",
         "我写的是 file:////root 这样",
         "我写的是 file:////root 这样")
    # 代码片段
    case("代码片段不动",
         "unshare --map-root-user 和 C:\\path\\to 都行",
         "unshare --map-root-user 和 C:\\path\\to 都行")
    # 正文里的 || （上一个修复的对象，不能被这个修复影响）
    case("正文 || 不受影响",
         "甲 || 乙",
         "甲 || 乙")

    print("\n=== 四、混合场景：正文 + 多个 CQ 码 ===")
    case("正文夹两个 CQ 码",
         "给你看图 [CQ:image,file=file:////root/a.png] 还有 https://x.com//y "
         "以及 [CQ:image,file=file:////root/b.png]",
         "给你看图 [CQ:image,file=file:///root/a.png] 还有 https://x.com//y "
         "以及 [CQ:image,file=file:///root/b.png]")
    case("同一 CQ 码含 URL 参数",
         "[CQ:image,file=file:////root/a.png,url=https://x.com//y]",
         "[CQ:image,file=file:///root/a.png,url=https://x.com//y]")

    print("\n=== 五、无 file 参数 / 边界 ===")
    case("普通文本", "你好呀", "你好呀")
    case("空字符串", "", "")
    case("CQ:at 无 file", "[CQ:at,qq=123]", "[CQ:at,qq=123]")
    case("CQ:face", "[CQ:face,id=1]", "[CQ:face,id=1]")
    case("file= 空值", "[CQ:image,file=]", "[CQ:image,file=]")
    case("file=http 远程", "[CQ:image,file=http://x.com//a.png]",
         "[CQ:image,file=http://x.com//a.png]")

    print("\n=== 六、性能：无四斜杠时不走正则（快路径）===")
    import time
    big = "正常文本" * 2000
    t0 = time.perf_counter()
    for _ in range(200):
        fix_cq_paths(big)
    dt = (time.perf_counter() - t0) / 200 * 1000
    ck("无命中时单次 < 0.1ms", dt < 0.1, "%.4f ms" % dt)

    print("\n=== 七、链路接线（mock 捕获真实发出的 message）===")
    asyncio.get_event_loop().run_until_complete(test_wiring())

    print("\n" + "=" * 52)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  -", f)
    print("=" * 52)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())

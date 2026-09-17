# -*- coding: utf-8 -*-
"""msglog 写入次数测试（v2.3.36 修重复记录）。

验证 `_send_and_record` 在每种场景下**恰好写 1 条** msglog。
原来群聊白名单里写 2 条（`_log_bot_sent` + `record_incoming_message`），
fallback 里也写 2 条（`send_by_chat_type` 内部 + 显式再写）。

用假群号 999999999 与 mock 的 WS 管理器，不会真发消息、不污染真实 msglog。

跑法：python3 scripts/_test_msglog_once.py
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import services.sender as S  # noqa: E402
import modules.recall as R  # noqa: E402

MSGLOG = Path(__file__).resolve().parent.parent / "data" / "msglog"
TEST_GROUP = 999999999          # 白名单测试群
TEST_GROUP2 = 888888888         # 非白名单测试群
TEST_USER = 777777777

PASS, FAIL = [], []


def ck(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("OK" if cond else "FAIL", name, ("  " + str(extra)) if extra else ""))


def count_lines(chat_id) -> int:
    p = MSGLOG / f"msglog_{chat_id}.jsonl"
    if not p.exists():
        return 0
    return sum(1 for ln in p.open(encoding="utf-8", errors="ignore") if ln.strip())


class FakeMgr:
    """假 WS 管理器：记录 call_api 调用，可配置首次抛异常"""

    def __init__(self, fail_first=False):
        self.calls = []
        self.fail_first = fail_first
        self._first = True

    async def call_api(self, action, params, timeout=5.0):
        self.calls.append((action, params))
        if self.fail_first and self._first:
            self._first = False
            raise RuntimeError("模拟首次发送失败")
        return {"message_id": 424242, "status": "ok"}


class FakeCfg:
    bot_qq = 10000
    bot_name = "测试bot"
    group_list = [TEST_GROUP]      # 只有 TEST_GROUP 在白名单


def cleanup():
    for cid in (TEST_GROUP, TEST_GROUP2, TEST_USER):
        p = MSGLOG / f"msglog_{cid}.jsonl"
        if p.exists():
            p.unlink()
    # 顺带清理 _send_and_record 可能写出的 stats 文件
    for f in (Path(__file__).resolve().parent.parent / "data").glob("stats_999999999_*.json"):
        try:
            f.unlink()
        except OSError:
            pass
    for f in (Path(__file__).resolve().parent.parent / "data").glob("stats_888888888_*.json"):
        try:
            f.unlink()
        except OSError:
            pass


async def run_case(name, chat_id, is_group, expected=1, fail_first=False):
    """跑一次 _send_and_record，返回 msglog 新增行数"""
    before = count_lines(chat_id)
    mgr = FakeMgr(fail_first=fail_first)
    orig = S.get_ws_manager
    S.get_ws_manager = lambda: mgr
    try:
        await S._send_and_record("测试内容-%s" % name, chat_id, is_group,
                                 None if is_group else chat_id, FakeCfg())
    finally:
        S.get_ws_manager = orig
    after = count_lines(chat_id)
    delta = after - before
    ck("%s → 写 %d 条" % (name, expected), delta == expected,
       "实际 %d 条%s" % (delta, "" if delta == expected else "  ← 重复/丢失"))
    return delta


async def main():
    cleanup()
    print("\n=== 一、正常发送：每种场景恰好 1 条 ===")
    await run_case("群聊-白名单(走recall)", TEST_GROUP, True)
    await run_case("群聊-非白名单(走_log_bot_sent)", TEST_GROUP2, True)
    await run_case("私聊(走_log_bot_sent)", TEST_USER, False)

    print("\n=== 二、fallback 路径（首次 call_api 抛异常）也恰好 1 条 ===")
    await run_case("群聊-白名单-fallback", TEST_GROUP, True, fail_first=True)

    print("\n=== 三、recall 抛异常时兜底写 1 条（不能因异常丢记录）===")
    before = count_lines(TEST_GROUP)
    mgr = FakeMgr()
    orig_mgr, orig_rec = S.get_ws_manager, R.record_incoming_message

    def boom(*a, **kw):
        raise RuntimeError("模拟 recall 失败")

    S.get_ws_manager = lambda: mgr
    R.record_incoming_message = boom
    try:
        await S._send_and_record("测试内容-recall挂了", TEST_GROUP, True, None, FakeCfg())
    finally:
        S.get_ws_manager = orig_mgr
        R.record_incoming_message = orig_rec
    delta = count_lines(TEST_GROUP) - before
    ck("recall 异常 → 兜底写 1 条", delta == 1, "实际 %d 条" % delta)

    print("\n=== 四、内容与 msg_id 正确落盘 ===")
    p = MSGLOG / f"msglog_{TEST_GROUP}.jsonl"
    last = json.loads([ln for ln in p.open(encoding="utf-8") if ln.strip()][-1])
    ck("type=bot", last.get("type") == "bot", last.get("type"))
    # 注意：_log_bot_sent 内部是自己 get_config() 取 bot_qq，不接传入的 cfg，
    # 所以这里比对真实 bot_qq，而不是 FakeCfg.bot_qq
    from core.config import get_config
    ck("user_id=真实bot_qq", last.get("user_id") == get_config().bot_qq,
       "%s vs %s" % (last.get("user_id"), get_config().bot_qq))
    ck("msg_id 为真实值", last.get("msg_id") == 424242, last.get("msg_id"))
    ck("内容正确", "测试内容" in str(last.get("content")), str(last.get("content"))[:40])

    print("\n=== 五、每个文件都无重复，总写入 = 调用次数 ===")
    from collections import Counter
    grand = 0
    for cid in (TEST_GROUP, TEST_GROUP2, TEST_USER):
        f = MSGLOG / f"msglog_{cid}.jsonl"
        if not f.exists():
            continue
        c = Counter()
        for ln in f.open(encoding="utf-8", errors="ignore"):
            if not ln.strip():
                continue
            d = json.loads(ln)
            c[(d.get("msg_id"), str(d.get("content")))] += 1
        grand += sum(c.values())
        dup = {k: v for k, v in c.items() if v > 1}
        ck("%s 内每条只落盘一次" % f.name, not dup,
           "重复 %d 组: %s" % (len(dup), list(dup)[:2]) if dup else "")
    # 本测试共 5 次 _send_and_record 调用，分别落进 3 个文件
    ck("总写入 = 5 次调用", grand == 5, grand)

    cleanup()
    print("\n" + "=" * 52)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  -", f)
    print("=" * 52)
    return 0 if not FAIL else 1


sys.exit(asyncio.get_event_loop().run_until_complete(main()))

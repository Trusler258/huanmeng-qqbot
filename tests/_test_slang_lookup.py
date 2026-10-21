"""黑话查证 service 单测（不打外网：萌娘/搜索全 mock）。

跑法：python tests/_test_slang_lookup.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from services import slang_lookup as SL   # noqa: E402

PASS = FAIL = 0


def ck(name: str, cond, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}  {extra}")


def _tmp_env():
    """把 slang.json / pending 指向临时目录，绝不碰真实词库。"""
    tmp = Path(tempfile.mkdtemp(prefix="slangtest_"))
    real_json, real_pending = SL._SLANG_JSON, SL.PENDING_PATH
    SL._SLANG_JSON = tmp / "slang.json"
    SL.PENDING_PATH = tmp / "slang_pending.json"
    SL._SLANG_JSON.write_text(json.dumps({
        "_meta": {"updated_at": "2026-10-21"},
        "entries": [{
            "name": "破防", "alias": ["破大防"], "circles": ["通用"], "risk": "mild",
            "meaning": "心理防线被突破。", "example": "", "source": "test",
            "last_seen": "2026-10-21", "added_by": "test",
        }],
    }, ensure_ascii=False), encoding="utf-8")
    return real_json, real_pending


def test_local():
    print("[1] 本地词库查找")
    ck("精确命中", (SL.find_local("破防") or {}).get("name") == "破防")
    ck("别名命中", (SL.find_local("破大防") or {}).get("name") == "破防")
    ck("未收录返回 None", SL.find_local("不存在的词xyz") is None)
    ck("空词返回 None", SL.find_local("") is None)


def test_reject():
    print("[2] 不该收录的词")
    ck("单字给 被拒", bool(SL._reject_reason("给")), SL._reject_reason("给"))
    ck("单字拉 被拒", bool(SL._reject_reason("拉")))
    ck("多字给佬 通过", SL._reject_reason("给佬") == "", SL._reject_reason("给佬"))
    ck("纯符号被拒", bool(SL._reject_reason("！！！")))
    ck("空被拒", bool(SL._reject_reason("")))
    ck("去括号引号", SL._normalize("「破防」") == "破防")


def test_pending_flow():
    print("[3] 待审队列（写入/列出/收录/丢弃）")
    ok, msg = SL.add_pending("给佬", "gay 的音译 + 佬，玩笑用法可能冒犯",
                             source="explain", confidence="high")
    ck("写入待审", ok is True, msg)
    ck("计数 1", SL.pending_count() == 1, str(SL.pending_count()))
    ok2, msg2 = SL.add_pending("给佬", "改过的释义", confidence="mid")
    ck("同词覆盖不堆积", SL.pending_count() == 1, msg2)
    ok3, msg3 = SL.add_pending("破防", "已在词库")
    ck("已在词库被拒", ok3 is False and "已有" in msg3, msg3)
    ok4, msg4 = SL.add_pending("给", "单字")
    ck("单字入队被拒", ok4 is False, msg4)

    txt = SL.format_pending()
    ck("列表含词", "给佬" in txt, txt[:120])
    ck("列表无 md 符号", "**" not in txt and "`" not in txt, txt[:120])

    # 收录：写进（临时）slang.json
    SL.add_pending("测试入库词", "单测用，临时目录内", confidence="mid")
    ok5, msg5 = SL.approve(1)          # 1 = 最新一条（牢大）
    ck("收录成功", ok5 is True, msg5)
    saved = json.loads(SL._SLANG_JSON.read_text(encoding="utf-8"))
    names = [e["name"] for e in saved["entries"]]
    ck("已写入 slang.json", "测试入库词" in names, str(names))
    ck("added_by=explain", any(e["name"] == "测试入库词" and e["added_by"] == "explain"
                              for e in saved["entries"]))
    ck("收录后出队", all(x["name"] != "测试入库词" for x in SL._load_pending()["items"]))

    ok6, msg6 = SL.reject(1)           # 剩下最新的 给佬
    ck("丢弃成功", ok6 is True and "给佬" in msg6, msg6)
    ck("丢弃后队列空", SL.pending_count() == 0, str(SL.pending_count()))
    ok7, msg7 = SL.reject(99)
    ck("越界序号友好报错", ok7 is False and "没有第" in msg7, msg7)


def test_explain_branches():
    print("[4] explain 分支（mock 外网）")
    real_moe, real_web = SL.moegirl_probe, SL.web_probe

    async def fake_moe(term):
        return {"exists": term == "牢大", "title": term,
                "url": f"https://zh.moegirl.org.cn/{term}"}

    async def fake_web(term):
        if term == "查不到xyz":
            return {"text": "", "hits": [], "trusted": 0}
        return {"text": f"[1] {term}_百度百科\n    释义…",
                "hits": [{"title": "x", "site": "某百科", "summary": "s"},
                         {"title": "y", "site": "某论坛", "summary": "s"}],
                "trusted": 1 if term != "查不到xyz" else 0}

    try:
        SL.moegirl_probe, SL.web_probe = fake_moe, fake_web

        r = asyncio.run(SL.explain("破防"))
        ck("已收录走 local", r["status"] == "local", r["status"])
        ck("local 文本无 md", "**" not in SL.format_explain(r))

        r = asyncio.run(SL.explain("给"))
        ck("单字被 reject", r["status"] == "reject", r["status"])

        r = asyncio.run(SL.explain("查不到xyz"))
        ck("无结果 status", r["status"] == "noresult", r["status"])
        txt = SL.format_explain(r)
        ck("无结果建议直接问", "问对方" in txt, txt[:100])

        r = asyncio.run(SL.explain("牢大"))
        ck("双源 → high", r["confidence"] == "high", str(r["confidence"]))
        ck("sources=2", r["sources"] == 2, str(r["sources"]))
        txt = SL.format_explain(r)
        ck("文本含萌娘条目", "萌娘百科有条目" in txt)
        ck("文本含检索资料", "联网检索" in txt)
        ck("不编造提示在", "别编" in txt)

        r = asyncio.run(SL.explain("一般词"))
        ck("单源 → mid", r["confidence"] == "mid", str(r["confidence"]))
    finally:
        SL.moegirl_probe, SL.web_probe = real_moe, real_web


def test_authority():
    print("[5] 来源权威度排序")
    ck("百度百科 → 3", SL._authority_of({"url": "https://baike.baidu.com/x"}) >= 3)
    ck("萌娘 → 3", SL._authority_of({"url": "https://zh.moegirl.org.cn/x"}) >= 3)
    ck("营销号 → 低", SL._authority_of({"url": "https://mp.weixin.qq.com/x"}) < 2)
    ck("site 含百科 → 2", SL._authority_of({"url": "http://a.com", "site_name": "某百科"}) >= 2)


if __name__ == "__main__":
    real_json, real_pending = _tmp_env()
    try:
        test_local()
        test_reject()
        test_pending_flow()
        test_explain_branches()
        test_authority()
    finally:
        SL._SLANG_JSON, SL.PENDING_PATH = real_json, real_pending
    print(f"\n结果: {PASS} 通过 / {FAIL} 失败")
    raise SystemExit(0 if FAIL == 0 else 1)

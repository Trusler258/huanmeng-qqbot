"""v2.3.106 话题疲劳 + 自我忽略 的单元测试。"""
import sys
import time
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


def test_similarity():
    from services import topic_fatigue as TF
    print("[1] 话题相似度")

    s = TF._similarity("你是我婆娘", "你是我婆娘吧")
    ck("近似句判同话题", s >= 0.3, "%.2f" % s)

    s = TF._similarity("你是我婆娘", "今天天气不错出去走走")
    ck("无关句判不同", s < 0.3, "%.2f" % s)

    s = TF._similarity("你主人不喜欢你", "你主人把你当孩子养")
    ck("同族挑衅判同话题", s >= 0.15, "%.2f" % s)

    ck("空串不炸", TF._similarity("", "abc") == 0.0)


def test_levels():
    from services import topic_fatigue as TF
    print("[2] 疲劳分级")

    TF.reset(999)
    lv, hits, span = TF.note(999, "你是我婆娘")
    ck("首条 level=0", lv == 0 and hits == 1, f"L{lv} {hits}")

    for _ in range(3):
        lv, hits, span = TF.note(999, "你是我婆娘")
    ck("4 次 → L1", lv == 1, f"L{lv} hits={hits}")

    for _ in range(2):
        lv, hits, span = TF.note(999, "你是我婆娘")
    ck("6 次 → L2", lv == 2, f"L{lv} hits={hits}")

    for _ in range(3):
        lv, hits, span = TF.note(999, "你是我婆娘")
    ck("9 次 → L3", lv == 3, f"L{lv} hits={hits}")

    # 换话题衰减但不清零
    lv2, hits2, _ = TF.note(999, "今天吃什么")
    ck("换话题衰减", hits2 < hits and hits2 >= 1, f"{hits} → {hits2}")

    # 短消息不计数
    TF.reset(1000)
    TF.note(1000, "今天聊点正经事")
    before = TF.snapshot(1000)["hits"]
    TF.note(1000, "嗯")
    after = TF.snapshot(1000)["hits"]
    ck("短消息不计数", before == after, f"{before} vs {after}")

    # 不同群互不影响
    TF.reset(2001)
    TF.reset(2002)
    TF.note(2001, "你是我婆娘")
    ck("分群隔离", TF.snapshot(2002) == {})


def test_ttl():
    from services import topic_fatigue as TF
    print("[3] 30 分钟 TTL")

    TF.reset(3001)
    for _ in range(5):
        TF.note(3001, "你是我婆娘")
    st = TF.snapshot(3001)
    ck("TTL 前有状态", st.get("hits", 0) >= 4, str(st))

    # 手动把 last 拨到 31 分钟前
    TF._state[3001]["last"] = time.time() - 31 * 60
    lv, hits, _ = TF.note(3001, "你是我婆娘")
    ck("超 TTL 后重新计数", hits == 1 and lv == 0, f"L{lv} hits={hits}")

    TF.reset(3001)


def test_advice():
    from services import topic_fatigue as TF
    print("[4] 提示文案")

    ck("L0 无提示", TF.advice(0, 1, 0) == "")
    a1 = TF.advice(1, 4, 6)
    ck("L1 提一句", "刚说过" in a1, a1)
    a2 = TF.advice(2, 6, 12)
    ck("L2 打断/换话题", "打断" in a2 or "换话题" in a2, a2)
    a3 = TF.advice(3, 10, 25)
    ck("L3 提到 ignore_user", "ignore_user" in a3, a3)
    ck("L2/L3 禁止元话语", "不要" in a2)


def test_self_ignore():
    from services import self_ignore as SI
    print("[5] 自我忽略时长")

    SI.unignore_user(8888)
    ck("默认未忽略", SI.is_ignored(8888) is False)

    m = SI.ignore_user(8888, 10)
    ck("设置 10 分钟", m == 10, str(m))
    ck("已忽略", SI.is_ignored(8888) is True)
    left = SI.remaining_seconds(8888)
    ck("剩余接近 600s", 590 <= left <= 600, str(left))

    SI.unignore_user(8888)
    ck("解除后不忽略", SI.is_ignored(8888) is False)

    # 边界夹取
    m = SI.ignore_user(8888, 0.1)
    ck("下限夹到 1 分钟", m == 1, str(m))
    m = SI.ignore_user(8888, 999)
    ck("上限夹到 120 分钟", m == 120, str(m))
    SI.unignore_user(8888)

    # 默认值（不传 minutes）
    m = SI.ignore_user(8888)
    ck("默认 5 分钟", m == 5, str(m))
    SI.unignore_user(8888)


def test_tool():
    import asyncio
    print("[6] ignore_user 工具")

    from core import tools as T
    from services import self_ignore as SI

    class _Cfg:
        admin_qq = 111111

    # 管理员不能被忽略
    import core.config as CC
    _orig = CC.get_config
    CC.get_config = lambda: _Cfg()
    try:
        r = asyncio.run(T.execute_tool("ignore_user", {"minutes": 5},
                                       111111, 0, "主人", True, 0))
        ck("主人不可忽略", "不能忽略主人" in (r or ""), str(r))
        ck("主人未进忽略表", SI.is_ignored(111111) is False)

        r = asyncio.run(T.execute_tool("ignore_user", {"minutes": 5},
                                       222222, 0, "群友", True, 0))
        ck("普通用户被忽略", SI.is_ignored(222222) is True, str(r))
        ck("返回带时长", "5" in (r or ""), str(r))
        SI.unignore_user(222222)
    finally:
        CC.get_config = _orig


if __name__ == "__main__":
    test_similarity()
    test_levels()
    test_ttl()
    test_advice()
    test_self_ignore()
    test_tool()
    print("\n结果: %d 通过 / %d 失败" % (PASS, FAIL))
    raise SystemExit(0 if FAIL == 0 else 1)

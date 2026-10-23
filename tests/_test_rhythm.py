"""v2.3.107 发言节奏（services/rhythm.py）测试。

背景：长度档位恒判 short（线上 93%）+ 事后合并，导致群里永远只有一句话。
现在改成按形态抽样，这里验证三件事：
1. 四种形态都能出现，比例不离谱（不是又退化成一个档）
2. 连发形态的条数在 2~3
3. 连续三轮都很短时，能触发反向惯性（下一轮不再是一条短话）
"""
import sys
from collections import Counter
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


def test_basic():
    from services import rhythm
    print("[1] 形态抽样")

    rhythm._state.clear()
    c = Counter()
    hints = set()
    for i in range(3000):
        p = rhythm.plan(10001, "今天上课好困啊，老师讲了一堆mysql多表查询", needs=set())
        c[p["shape"]] += 1
        hints.add(p["shape"])
    print("     分布:", dict(c))
    ck("四种形态都出现过", len(c) == 4, str(c))
    ck("没有单一形态垄断（最高 <65%）", max(c.values()) / 3000 < 0.65,
       "最高 %.2f" % (max(c.values()) / 3000))
    ck("每条都有提示文案", all(p for p in hints))

    rhythm._state.clear()


def test_hint_text():
    from services import rhythm
    print("[2] 提示文案")
    rhythm._state.clear()
    seen = {}
    for i in range(400):
        p = rhythm.plan(10002, "嗯")
        seen.setdefault(p["shape"], p["hint"])
    ck("提示里带【本轮节奏】标记",
       all("【本轮节奏】" in h for h in seen.values()), str(list(seen)))
    ck("连发形态写明条数",
       all(("2 条" in h) or ("3 条" in h) for k, h in seen.items() if k == "burst"),
       str(seen.get("burst", "")[:40]))
    rhythm._state.clear()


def test_burst_count():
    from services import rhythm
    print("[3] 连发条数")
    rhythm._state.clear()
    ns = set()
    for i in range(800):
        p = rhythm.plan(10003, "讲点啥吧")
        if p["shape"] == "burst":
            ns.add(p["sentences"])
    ck("burst 条数在 2~3", ns and ns <= {2, 3}, str(ns))
    rhythm._state.clear()


def test_anti_short():
    from services import rhythm
    print("[4] 反向惯性：连续太短要纠正")
    rhythm._state.clear()
    cid = 10004
    # 手工灌入「最近三轮都极短」
    rhythm.record(cid, "one", 5)
    rhythm.record(cid, "one", 6)
    rhythm.record(cid, "one", 4)
    st = rhythm._state[cid]
    ck("历史已记录三短", st["chars"] == [5, 6, 4], str(st["chars"]))

    hits = 0
    for i in range(600):
        p = rhythm.plan(cid, "嗯")
        if p["anti_short"]:
            hits += 1
    # one/burst 命中 anti_short；采样 600 次里应占相当比例
    ck("能触发纠正标记", hits > 0, "hits=%d" % hits)

    # 纠正生效后 one 的占比应明显低于无历史时的基线
    rhythm._state.clear()
    base = sum(1 for _ in range(600) if rhythm.plan(cid, "嗯")["shape"] == "one")
    rhythm._state.clear()
    for _ in range(3):
        rhythm.record(cid, "one", 5)
    fixed = sum(1 for _ in range(600) if rhythm.plan(cid, "嗯")["shape"] == "one")
    print("     one 占比 基线=%d/600  三短后=%d/600" % (base, fixed))
    ck("三短后不再偏爱一句话", fixed < base, "%d -> %d" % (base, fixed))
    rhythm._state.clear()


def test_heavy():
    from services import rhythm
    print("[5] 求教/工具场景倾向讲完")
    rhythm._state.clear()
    para = 0
    for i in range(600):
        p = rhythm.plan(10005, "能不能详细讲讲 zookeeper 的选举机制？", needs={"deep"})
        if p["shape"] == "para":
            para += 1
    print("     para 占比 %.2f" % (para / 600))
    ck("深度问题偏长消息", para / 600 > 0.35, "%.2f" % (para / 600))
    rhythm._state.clear()


if __name__ == "__main__":
    test_basic()
    test_hint_text()
    test_burst_count()
    test_anti_short()
    test_heavy()
    print("\n结果: %d 通过 / %d 失败" % (PASS, FAIL))
    raise SystemExit(0 if FAIL == 0 else 1)

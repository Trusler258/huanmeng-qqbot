"""v2.3.68 日报载荷层回归测试：与原实现产出的 HTML **逐字节一致**

基准从哪来：
  在改 `modules/stats.py` 之前，用 `scripts/_capture_daily_html.py` 把
  `render_card_to_image` 换成记录桩，直接调用**原** `generate_daily_report_image()`
  抓下它实际产出的 HTML（连同输入 stats 一起存进 fixture）。
  这样"原始算法"不用手抄，杜绝抄错；比对面也不会混进数据差异。

为什么必须过这一关：
  迁移到 Pillow 且要求 1:1，前提是两条渲染路**输入完全相同**。若载荷层有偏差，
  像素差里就同时混着"数据错"和"画得不像"，根本没法定位。

用法（本地）:
  python tests/_test_v2368_daily_payload.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS = 0
FAIL = 0


def ok(msg: str) -> None:
    global PASS
    PASS += 1
    print(f"  \033[32mOK\033[0m   {msg}")


def bad(msg: str) -> None:
    global FAIL
    FAIL += 1
    print(f"  \033[31mFAIL\033[0m {msg}")


class _CfgStub:
    """默认配置行为：无昵称映射时返回 str(uid)，于是代码回退到记录里的 name 字段
    —— 与基准抓取时服务器的默认行为一致（基准里的展示名就是 fixture 里的名字）"""

    def get_display_name(self, uid, group_id=None):
        return str(uid)


def _first_diff(a: str, b: str, ctx: int = 60) -> str:
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            return (f"首个差异 @{i}:\n"
                    f"    基准: ...{a[max(0,i-ctx):i+ctx]!r}\n"
                    f"    本版: ...{b[max(0,i-ctx):i+ctx]!r}")
    if len(a) != len(b):
        longer = "基准" if len(a) > len(b) else "本版"
        return f"长度不同：基准 {len(a)} / 本版 {len(b)}（{longer}更长）"
    return "完全相同"


def main() -> None:
    import core.config as cfgmod

    cfgmod.get_config = lambda: _CfgStub()      # 必须在 import 数据层之前替换

    from services.daily_report_data import build_payload, payload_to_html

    fixture = json.loads(
        (ROOT / "tests" / "fixtures" / "daily_html_golden.json").read_text(encoding="utf-8"))
    template = (ROOT / "data" / "templates" / "daily_report.html").read_text(encoding="utf-8")

    print("=== 1. 载荷层 HTML 必须与迁移前逐字节一致 ===")
    for key in ("night", "morning"):
        g = fixture[key]
        gold = g["html"]
        # 报告时间取基准里的真实值（原实现用 datetime.now()，不能拿本机时间比）
        m = re.search(r"每日 (\d\d:\d\d)", gold)
        report_time = m.group(1) if m else "00:00"

        payload = build_payload(g["stats"], g["group_id"], g["date_str"],
                               g["group_name"], report_time=report_time)
        if payload is None:
            bad(f"[{key}] build_payload 返回 None")
            continue
        html = payload_to_html(payload, template)
        if html == gold:
            ok(f"[{key}] HTML 逐字节一致（{len(html)} 字符）")
        else:
            bad(f"[{key}] HTML 不一致 —— {_first_diff(gold, html)}")

    print("\n=== 2. 关键字段抽查（防止'整体一致但语义错'）===")
    g = fixture["night"]
    payload = build_payload(g["stats"], g["group_id"], g["date_str"], g["group_name"],
                           report_time=re.search(r"每日 (\d\d:\d\d)", g["html"]).group(1))
    exp = {
        "total": 386, "participants": 9, "peak_label": "21:00", "avg": "42.9",
    }
    for k, v in exp.items():
        (ok if payload[k] == v else bad)(f"{k} = {payload[k]!r}（期望 {v!r}）")
    if len(payload["ranking"]) == 8:
        ok("排行取前 8 条")
    else:
        bad(f"排行条数 {len(payload['ranking'])}（期望 8）")
    if payload["ranking"][0]["pct"] == 100 and payload["ranking"][-1]["count"] == 7:
        ok("排行占比/计数正确（100% … 7）")
    else:
        bad(f"排行占比异常: {payload['ranking'][0]['pct']} / {payload['ranking'][-1]['count']}")
    if [f["icon"] for f in payload["facts"]] == ["mic", "diver", "sleep", "moon"]:
        ok("锐评条目与图标正确（mic/diver/sleep/moon）")
    else:
        bad(f"锐评图标异常: {[f['icon'] for f in payload['facts']]}")
    if len(payload["hours"]) == 24 and {h["level"] for h in payload["hours"]} <= {0, 1, 2, 3, 4, 5}:
        ok("24 小时格子齐备且等级在 0~5")
    else:
        bad("小时格子异常")

    print("\n=== 3. 边界：空数据 / 单人 ===")
    if build_payload({"_meta": {"total": 0}}, 1, "2026.09.27") is None:
        ok("total=0 → 返回 None（与原实现一致，不上报）")
    else:
        bad("total=0 却没返回 None")
    one = {"_meta": {"total": 5}, "77": {"count": 5, "hours": {"9": 5}, "name": "独苗"}}
    p1 = build_payload(one, 1, "2026.09.27", "小群")
    # 不足 3 人不给「深海潜水员」；hours 全在 9 点 → morning=5 night=0 → sun 命中
    if p1 and len(p1["ranking"]) == 1 and [f["icon"] for f in p1["facts"]] == ["mic", "sleep", "sun"]:
        ok("单人时：排行 1 条，锐评 mic+sleep+sun（<3 人无潜水员；morning>night 命中早鸟）")
    else:
        bad(f"单人边界异常: {p1 and [f['icon'] for f in p1['facts']]}")

    print("\n=== 4. 两种昼夜分支都要能命中 ===")
    gm = fixture["morning"]
    pm = build_payload(gm["stats"], gm["group_id"], gm["date_str"], gm["group_name"],
                       report_time=re.search(r"每日 (\d\d:\d\d)", gm["html"]).group(1))
    icons = [f["icon"] for f in pm["facts"]]
    (ok if "sun" in icons else bad)(f"早鸟分支命中 sun（实际 {icons}）")

    print(f"\n=== 结果: {PASS} passed, {FAIL} failed ===")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()

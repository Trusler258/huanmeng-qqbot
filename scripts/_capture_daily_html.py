#!/usr/bin/env python3
"""抓取**迁移前** generate_daily_report_image() 实际产出的 HTML（1:1 基准）。

做法：把 modules.changelog.render_card_to_image 换成一个只记录 html 的桩，
再喂固定的 stats 载荷调用原函数 → 打印 json（含 html）。
这样"原始算法"不用手抄，直接从跑着的代码里取，避免抄错。

用法（服务器，必须在改 stats.py 之前跑）:
  python3 scripts/_capture_daily_html.py > /tmp/daily_html_golden.json
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, "/root/bot")

# ── 固定载荷：覆盖 夜猫子 / 早鸟 两个分支，以及 8 人以上的排行截断 ──
def mk_stats(night_heavy: bool):
    users = {}
    # 9 个人 → 排行取前 8；最后一名用于"深海潜水员"
    data = [
        (1001, "话痨小王", 120), (1002, "阿强", 88), (1003, "喵喵", 61),
        (1004, "小美", 44), (1005, "老张", 30), (1006, "阿飞", 21),
        (1007, "小陈", 13), (1008, "静静", 7), (1009, "深海咸鱼", 2),
    ]
    total = 0
    for uid, name, cnt in data:
        hours = {}
        # 把消息分布到小时上：制造明显的昼夜差异 + 让 8/9/10 或 21/22/23 有值
        heavy = ["21", "22", "23"] if night_heavy else ["8", "9", "10"]
        rest = ["12", "15", "19"]
        per_heavy = max(1, int(cnt * 0.5 / 3))
        per_rest = max(0, int(cnt * 0.3 / 3))
        for h in heavy:
            hours[h] = hours.get(h, 0) + per_heavy
        for h in rest:
            hours[h] = hours.get(h, 0) + per_rest
        # 补齐差额，保证 hours 之和等于 count
        got = sum(hours.values())
        if got < cnt:
            hours["21" if night_heavy else "9"] += cnt - got
        users[str(uid)] = {"count": cnt, "hours": hours, "name": name}
        total += cnt
    users["_meta"] = {"total": total}
    return users


CASES = {
    "night": mk_stats(True),
    "morning": mk_stats(False),
}


async def main():
    from modules import changelog as cl

    captured = {}

    async def fake_render(html, filename, width=720):
        captured["html"] = html
        captured["filename"] = filename
        captured["width"] = width
        return "/tmp/_captured.jpg"

    cl.render_card_to_image = fake_render
    # 原函数内部是 `from modules.changelog import render_card_to_image` → 直接查模块属性，替换生效

    import modules.stats as st
    out = {}
    for key, stats in CASES.items():
        captured.clear()
        await st.generate_daily_report_image(stats, 123456789, "2026.09.27", "测试群名")
        out[key] = {
            "html": captured.get("html", ""),
            "filename": captured.get("filename", ""),
            # ★ 把输入载荷一起存下来：本地测试才能喂**完全相同**的数据做逐字节比对
            "stats": stats,
            "group_id": 123456789,
            "date_str": "2026.09.27",
            "group_name": "测试群名",
        }
        print(f"[{key}] html {len(out[key]['html'])} 字符", file=sys.stderr)

    # 顺带记录模板文件本身，防止两边读到的模板不同
    out["_template_path"] = str(st._DAILY_TPL_PATH)
    print(json.dumps(out, ensure_ascii=False))


asyncio.run(main())

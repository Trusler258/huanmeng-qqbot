# -*- coding: utf-8 -*-
"""DeepSeek 计价测试（v2.3.35）。

覆盖：峰谷时段判定、价格表按生效日期匹配、模型族映射、
      单条记录计费、汇总口径一致性（token 与费用同口径）。

跑法：python3 scripts/_test_token_cost.py
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.token_tracker import (  # noqa: E402
    is_peak, price_for, cost_of_record, _agg,
    _FLASH_TABLE, _PRO_TABLE,
)

PASS, FAIL = [], []


def ck(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("OK" if cond else "FAIL", name, ("  " + str(extra)) if extra else ""))


# 2026-09-14 是周一
MON = datetime(2026, 9, 14)
SAT = datetime(2026, 9, 19)
SUN = datetime(2026, 9, 20)


def main():
    print("\n=== 一、峰谷时段（官方：周一至周五 9:00-12:00、14:00-18:00）===")
    ck("周一 09:00 高峰", is_peak(MON.replace(hour=9, minute=0)))
    ck("周一 11:59 高峰", is_peak(MON.replace(hour=11, minute=59)))
    ck("周一 12:00 空闲（左闭右开）", not is_peak(MON.replace(hour=12, minute=0)))
    ck("周一 13:59 空闲", not is_peak(MON.replace(hour=13, minute=59)))
    ck("周一 14:00 高峰", is_peak(MON.replace(hour=14, minute=0)))
    ck("周一 17:59 高峰", is_peak(MON.replace(hour=17, minute=59)))
    ck("周一 18:00 空闲", not is_peak(MON.replace(hour=18, minute=0)))
    ck("周一 03:00 空闲", not is_peak(MON.replace(hour=3, minute=0)))
    ck("周一 23:00 空闲", not is_peak(MON.replace(hour=23, minute=0)))
    ck("周六 10:00 空闲（周末全天）", not is_peak(SAT.replace(hour=10)))
    ck("周日 15:00 空闲（周末全天）", not is_peak(SUN.replace(hour=15)))

    print("\n=== 二、当前价（2026-09-10 12:00 起）===")
    idle = datetime(2026, 9, 17, 3, 0)
    peak = datetime(2026, 9, 17, 10, 0)
    ck("空闲 0.02/1/4", price_for("deepseek-flash", idle) == (0.02, 1.0, 4.0),
       price_for("deepseek-flash", idle))
    ck("高峰 0.04/2/8", price_for("deepseek-flash", peak) == (0.04, 2.0, 8.0),
       price_for("deepseek-flash", peak))
    ck("高峰价 = 空闲价 ×2",
       all(abs(p - i * 2) < 1e-9 for p, i in
           zip(price_for("deepseek-flash", peak), price_for("deepseek-flash", idle))))

    print("\n=== 三、历史价格按生效日期匹配 ===")
    # 注意 9-10 11:59 属高峰（9:00-12:00），要比空闲就取凌晨
    ck("9-10 12:00 前旧价·空闲 0.05/1.5/4.5",
       price_for("deepseek-flash", datetime(2026, 9, 10, 3, 0)) == (0.05, 1.5, 4.5))
    ck("9-10 12:00 前旧价·高峰 0.10/3/9",
       price_for("deepseek-flash", datetime(2026, 9, 10, 11, 59)) == (0.10, 3.0, 9.0))
    ck("9-10 12:00 起用新价",
       price_for("deepseek-flash", datetime(2026, 9, 10, 12, 0)) == (0.02, 1.0, 4.0))
    ck("8-17 起启用峰谷（旧档空闲 0.05）",
       price_for("deepseek-flash", datetime(2026, 8, 20, 3, 0)) == (0.05, 1.5, 4.5))
    ck("8-17 前为平价（无峰谷，2 元输出）",
       price_for("deepseek-flash", datetime(2026, 7, 1, 10, 0)) == (0.02, 1.0, 2.0))
    ck("8-17 前高峰/空闲同价",
       price_for("deepseek-flash", datetime(2026, 7, 1, 10, 0))
       == price_for("deepseek-flash", datetime(2026, 7, 1, 3, 0)))

    print("\n=== 四、模型族映射 ===")
    for m in ("deepseek-flash", "deepseek-v4-flash", "deepseek-chat",
              "deepseek-reasoner", "deepseek-v4.1-flash"):
        ck("%s 计入 flash 族" % m, price_for(m, idle) == (0.02, 1.0, 4.0))
    ck("大小写无关", price_for("DeepSeek-Flash", idle) == (0.02, 1.0, 4.0))
    ck("9-14 起 pro 按 flash 计费",
       price_for("deepseek-v4-pro", datetime(2026, 9, 17, 3, 0)) == (0.02, 1.0, 4.0))
    ck("9-14 前 pro 用 pro 价（闲 0.15/4.5/13.5）",
       price_for("deepseek-v4-pro", datetime(2026, 8, 20, 3, 0)) == (0.15, 4.5, 13.5))
    ck("非 DeepSeek 模型不硬套价（返回 None）",
       price_for("Qwen/Qwen2.5-7B-Instruct", idle) is None)
    ck("未知模型返回 None", price_for("gpt-4", idle) is None)
    ck("空模型名返回 None", price_for("", idle) is None)

    print("\n=== 五、单条计费 ===")
    rec = {"time": "2026-09-17T03:00:00", "model": "deepseek-flash",
           "prompt_tokens": 1000, "cached_tokens": 800, "completion_tokens": 100}
    # 空闲: 800*0.02 + 200*1 + 100*4 = 0.016 + 200 + 400 (per 1e6)
    c, pk = cost_of_record(rec)
    exp = (800 * 0.02 + 200 * 1.0 + 100 * 4.0) / 1_000_000
    ck("空闲档计费正确", abs(c - exp) < 1e-12, "%.8f vs %.8f" % (c, exp))
    ck("标记为空闲", pk is False)

    rec_peak = dict(rec, time="2026-09-17T10:00:00")
    c2, pk2 = cost_of_record(rec_peak)
    ck("高峰档计费是空闲的 2 倍", abs(c2 - exp * 2) < 1e-12, "%.8f" % c2)
    ck("标记为高峰", pk2 is True)

    ck("未知模型不计费", cost_of_record(dict(rec, model="Qwen/Qwen2.5-7B"))[0] is None)
    ck("坏时间格式不计费", cost_of_record(dict(rec, time="坏了"))[0] is None)

    # 脏数据：cached 超过 prompt
    ck("cached > prompt 不产生负输入",
       cost_of_record(dict(rec, cached_tokens=99999))[0]
       == (1000 * 0.02 + 100 * 4.0) / 1_000_000)

    print("\n=== 六、汇总口径一致性 ===")
    recs = [
        {"time": "2026-09-17T03:00:00", "model": "deepseek-flash",
         "prompt_tokens": 1000, "cached_tokens": 500, "completion_tokens": 100},
        {"time": "2026-09-17T10:30:00", "model": "deepseek-flash",
         "prompt_tokens": 2000, "cached_tokens": 1000, "completion_tokens": 200},
        {"time": "2026-09-17T03:10:00", "model": "Qwen/Qwen2.5-7B-Instruct",
         "prompt_tokens": 5000, "cached_tokens": 0, "completion_tokens": 500},
    ]
    a = _agg(recs)
    ck("只统计可计价的调用数", a["calls"] == 2, a["calls"])
    ck("未计价单独计数", a["unpriced_calls"] == 1)
    ck("token 与费用同口径（不含未计价）", a["prompt"] == 3000, a["prompt"])
    ck("未计价 token 单独记", a["unpriced_tokens"] == 5500, a["unpriced_tokens"])
    ck("峰值拆分覆盖全部可计价调用", a["peak_calls"] == 1)
    ck("费用 = 高峰 + 空闲", abs(a["cost"] - (a["peak_cost"] + a["idle_cost"])) < 1e-12)
    ck("total_calls = 可计价 + 未计价", a["total_calls"] == 3)
    manual = ((500 * 0.02 + 500 * 1 + 100 * 4) + (1000 * 0.04 + 1000 * 2 + 200 * 8)) / 1_000_000
    ck("总费用与手算一致", abs(a["cost"] - manual) < 1e-12, "%.8f vs %.8f" % (a["cost"], manual))

    print("\n" + "=" * 56)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  -", f)
    print("=" * 56)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())

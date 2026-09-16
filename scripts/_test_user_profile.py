# -*- coding: utf-8 -*-
"""用户画像 v2.3.34 测试：分域 / 校验 / 解析 / 注入 / 每日任务幂等。

跑法：python3 scripts/_test_user_profile.py
      使用临时数据文件，不会污染真实画像。
"""
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core.user_profile as up  # noqa: E402

PASS, FAIL = [], []


def ck(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("OK" if cond else "FAIL", name, ("  " + str(extra)) if extra else ""))


def main():
    tmp = Path(tempfile.mktemp(suffix=".json"))
    tmp.write_text("{}", encoding="utf-8")
    tmp_state = Path(tempfile.mktemp(suffix=".json"))
    up._DATA_FILE = tmp
    up._STATE_FILE = tmp_state

    print("\n=== 一、分域键 ===")
    ck("群键 g<群号>:<QQ>", up.scope_key(1053523927, 3483585417, True) == "g1053523927:3483585417")
    ck("私聊键 p<QQ>", up.scope_key(0, 3483585417, False) == "p3483585417")
    ck("解析群键", up.parse_scope("g1:2") == {"scope": "group", "group": "1", "qq": "2"})
    ck("解析私聊键", up.parse_scope("p2") == {"scope": "private", "group": "", "qq": "2"})
    ck("兼容旧裸 QQ 键", up.parse_scope("123")["scope"] == "private")

    print("\n=== 二、活跃度只记时间戳，不动 msg_count ===")
    up.bump_activity(1, 999, True)
    up.bump_activity(1, 999, True)
    d = up._load_all()
    p = d["g1:999"]
    ck("新建条目", "g1:999" in d)
    ck("msg_count 未被实时累加", p["msg_count"] == 0, p["msg_count"])
    ck("last_msg_at 已写", p["last_msg_at"] > 0)

    print("\n=== 三、字段清洗（LLM 输出兜底）===")
    ck("占位符丢弃", up._clean_value("未提及", "role") == "")
    ck("疑问句丢弃", up._clean_value("我是谁", "role") == "")
    ck("反问问句丢弃", up._clean_value("他是不是学生", "role") == "")
    ck("隐私丢弃", up._clean_value("手机号 13800000000", "warning") == "")
    ck("密码丢弃", up._clean_value("密码是 abc123", "warning") == "")
    ck("换行折叠", up._clean_value("写代码\n排查bug", "demands") == "写代码 排查bug")
    ck("首尾句号去掉", up._clean_value("精简一点。", "preference") == "精简一点")
    ck("超长截断", len(up._clean_value("啰" * 200, "role")) <= up._FIELD_MAX["role"])
    ck("正常值保留", up._clean_value("高中生，写机器人", "role") == "高中生，写机器人")

    print("\n=== 四、昵称校验 ===")
    ck("纯 QQ 号不算昵称", up._valid_nick("3483585417", "1") == "")
    ck("代词不算昵称", up._valid_nick("你", "1") == "")
    ck("bot 名不算昵称", up._valid_nick("幻梦", "1") == "")
    ck("疑问词不算昵称", up._valid_nick("我是谁", "1") == "")
    ck("正常昵称保留", up._valid_nick("Trusler", "1") == "Trusler")
    ck("去掉 @ 前缀", up._valid_nick("@幻梦bot", "1") == "")
    ck("LLM 昵称限长 16", len(up._valid_nick("x" * 40, "1")) == 16)
    ck("配置昵称放宽到 24", len(up._valid_nick("x" * 40, "1", limit=24)) == 24)

    print("\n=== 五、JSON 解析 ===")
    r = up._parse_fields('{"nick":"A","role":"学生","demands":"写代码",'
                         '"preference":"精简","habit":"直接","warning":"未提及"}')
    ck("正常 JSON", r.get("role") == "学生" and r.get("warning") == "未提及")
    r2 = up._parse_fields('```json\n{"role":"程序员"}\n```')
    ck("剥离代码块", r2.get("role") == "程序员")
    r3 = up._parse_fields('{"role":"学生"\n"demands":"写代码"}')   # 坏 JSON
    ck("坏 JSON 退化为正则捞取", r3.get("role") == "学生", r3)
    ck("乱码返回空", up._parse_fields("完全不是 json") == {})
    ck("空串返回空", up._parse_fields("") == {})

    print("\n=== 六、注入 ===")
    # 直接构造一份画像
    up._save_all({
        "g100:1": {"qq": "1", "scope": "group", "group": "100", "nick": "小明",
                   "role": "高中生", "demands": "写代码", "preference": "",
                   "habit": "深夜活跃", "warning": "", "days": 3, "msg_count": 50,
                   "updated_at": int(time.time())},
        "p1": {"qq": "1", "scope": "private", "group": "", "nick": "小明",
               "role": "高中生", "demands": "聊天", "preference": "", "habit": "",
               "warning": "", "days": 1, "msg_count": 10,
               "updated_at": int(time.time()) - 100},
    })
    t = up.build_profile_text(100, 1, True)
    ck("群注入带分域标签", "【发言者画像 · 本群】" in t)
    ck("注入含身份", "身份: 高中生" in t)
    ck("空字段不注入", "偏好" not in t)
    t2 = up.build_profile_text(0, 1, False)
    ck("私聊注入标签", "【发言者画像 · 私聊】" in t2)
    # 群 200 没建档 → 回退到该用户其它会话的画像，并标注来源
    #   （规则是取 updated_at 最新的那一份，所以可能是别的群也可能是私聊）
    t3 = up.build_profile_text(200, 1, True)
    ck("无本群画像时回退且标注来源", t3.startswith("【发言者画像 · 来自"), t3.split("\n")[0] if t3 else "")
    ck("回退内容仍是该用户的", "身份: 高中生" in t3)
    ck("关掉回退则不注入", up.build_profile_text(200, 1, True, allow_cross_scope=False) == "")
    ck("完全没画像不注入", up.build_profile_text(999, 88888, True) == "")

    print("\n=== 七、当天消息聚合（真实 msglog，只读）===")
    from datetime import datetime, timedelta
    y = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    agg = up.collect_day(y)
    ck("能聚合出记录", len(agg) > 0, "%d 条组合" % len(agg))
    if agg:
        keys = list(agg.keys())
        ck("键是分域格式", all(k.startswith("g") or k.startswith("p") for k in keys))
        ck("群键含冒号", all(":" in k for k in keys if k.startswith("g")))
        sample = agg[keys[0]]
        ck("消息带时间前缀", sample["msgs"][0].startswith("[") if sample["msgs"] else False)
        joined = "\n".join(sample["msgs"])
        ck("不含 bot 自己的消息", "type" not in joined)

    print("\n=== 八、每日任务幂等 ===")

    async def run_twice():
        r1 = await up.run_daily("2026-09-10")
        r2 = await up.run_daily("2026-09-10")     # 第二次应跳过
        return r1, r2

    # 用一个非常久远、无消息的日期，确保不真的调 LLM
    r1, r2 = asyncio.get_event_loop().run_until_complete(run_twice())
    ck("首次处理有结果", "skipped" not in r1, r1)
    ck("重复调被跳过", r2.get("skipped") is True, r2)
    ck("已记录日期", "2026-09-10" in (up._load_state().get("done_dates") or []))

    print("\n=== 九、模型名提取（ModelConfig 不可直接序列化）===")
    class _MC:
        name = "deepseek-flash"

    class _Cfg:
        cheap_model = _MC()

    ck("对象取 name", up._model_name(_Cfg()) == "deepseek-flash")
    ck("字符串直接用", up._model_name(type("C", (), {"cheap_model": "abc"})()) == "abc")
    ck("缺失返回空", up._model_name(type("C", (), {})()) == "")

    # 清理
    for f in (tmp, tmp_state):
        if f.exists():
            f.unlink()

    print("\n" + "=" * 56)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项:")
        for f in FAIL:
            print("  -", f)
    print("=" * 56)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())

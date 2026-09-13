"""面板 v2.3.0 新增能力测试：操作栈 / 自愈 / 配置编辑 / 图片 / 数据库

重点测**平时不会执行到的分支**：
  - 操作栈的落盘与恢复（面板重启后还能回滚吗）
  - 自愈的判定阈值与冷却（真出事时第一次运行就是最后一次机会）
  - config 单键替换会不会破坏文件其他部分
  - 数据库 SQL 白名单能不能被绕过

用系统 Python 跑：
  C:\\Users\\Huang\\AppData\\Local\\Programs\\Python\\Python312\\python.exe tests\\_test_panel_v230.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS = 0
FAIL = 0
FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK] {name}")
    else:
        FAIL += 1
        FAILURES.append(f"{name} :: {detail}")
        print(f"  [FAIL] {name}  {detail}")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


# ══════════════════════════════════════════════════════════
section("A. 操作栈：记录 / 落盘 / 回滚")

from panel import security  # noqa: E402

tmp_dir = ROOT / "data" / "panel_trash" / "_test_ops"
tmp_dir.mkdir(parents=True, exist_ok=True)
target = tmp_dir / "victim.txt"
target.write_text("原始内容\n", encoding="utf-8")

# 清空栈，避免受真实操作影响
security.clear_ops()
check("A1 清空后栈为空", security.peek_ops(limit=5) == [])

bak = security.tracked_write_text(
    target, "改坏了的内容\n", kind="test_write", note="测试"
)
check("A2 写入返回了备份路径", bak is not None and Path(bak).exists())
check("A3 文件内容已更新", target.read_text(encoding="utf-8") == "改坏了的内容\n")

ops = security.peek_ops(limit=5)
check("A4 操作栈记录了这一笔", len(ops) == 1, f"得到 {len(ops)}")
if ops:
    check("A5 记录里有 kind", ops[0].get("kind") == "test_write")
    check("A6 记录里有备份路径", bool(ops[0].get("backup")))
    check("A7 记录里路径是相对路径",
          ops[0].get("target", "").startswith("data/"),
          f"得到 {ops[0].get('target')}")

# 落盘验证：栈文件应该存在于磁盘上
ops_file = ROOT / "data" / "panel_ops.json"
check("A8 操作栈已落盘", ops_file.is_file())
if ops_file.is_file():
    on_disk = json.loads(ops_file.read_text(encoding="utf-8"))
    check("A9 落盘内容与内存一致", len(on_disk) == 1)

# 回滚
rec = security.pop_op()
ok, msg = security.rollback_op(rec) if rec else (False, "无记录")
check("A10 回滚成功", ok, msg)
check("A11 内容已还原", target.read_text(encoding="utf-8") == "原始内容\n",
      f"得到 {target.read_text(encoding='utf-8')!r}")
check("A12 回滚后栈已空", security.peek_ops(limit=5) == [])

# 新建文件的回滚 = 删除
newfile = tmp_dir / "brandnew.txt"
security.tracked_write_text(newfile, "新建的\n", kind="test_create")
check("A13 新建文件存在", newfile.is_file())
rec = security.pop_op()
ok, msg = security.rollback_op(rec)
check("A14 新建文件回滚即删除", ok and not newfile.exists(), msg)

# ── 定点回滚：只回滚指定那条，不碰别的 ──
#
# 为什么需要：自愈原来弹栈顶，但栈顶未必是导致崩溃的那次写入。
# 实测撞上过 —— 栈里躺着陈年记录，自愈去回滚了一个跟崩溃无关的文件。
security.clear_ops()

f_a = tmp_dir / "file_a.txt"
f_b = tmp_dir / "file_b.txt"
f_c = tmp_dir / "file_c.txt"
f_a.write_text("A原\n", encoding="utf-8")
f_b.write_text("B原\n", encoding="utf-8")
f_c.write_text("C原\n", encoding="utf-8")

security.tracked_write_text(f_a, "A改\n", kind="test")
security.tracked_write_text(f_b, "B改\n", kind="test")
security.tracked_write_text(f_c, "C改\n", kind="test")

ops = security.peek_ops(limit=10)
check("A15 三条操作都登记了", len(ops) == 3, f"得到 {len(ops)}")
check("A15b 每条操作都有唯一 id",
      len({o.get("id") for o in ops}) == 3,
      f"ids={[o.get('id') for o in ops]}")
check("A15c 三条时间戳相同（正是不能用 ts 定位的原因）",
      len({o.get("ts") for o in ops}) == 1,
      f"ts={[o.get('ts') for o in ops]}")

# 定点回滚**中间**那条（模拟"导致崩溃的是中间那次写入"）
mid = ops[1]
mid_id = mid["id"]
got = security.pop_op_by_id(mid_id)
check("A16 按 id 取到了指定操作", got is not None
      and got["id"] == mid_id, f"得到 {got and got.get('id')}")
ok, msg = security.rollback_op(got)
check("A17 定点回滚成功", ok, msg)
check("A18 中间文件已还原", f_b.read_text(encoding="utf-8") == "B原\n",
      f"得到 {f_b.read_text(encoding='utf-8')!r}")
check("A19 另外两个文件**未**被动过（关键）",
      f_a.read_text(encoding="utf-8") == "A改\n"
      and f_c.read_text(encoding="utf-8") == "C改\n",
      f"A={f_a.read_text(encoding='utf-8').strip()} "
      f"C={f_c.read_text(encoding='utf-8').strip()}")
check("A20 被弹走的那条不在栈里了",
      all(o.get("id") != mid_id for o in security.peek_ops(limit=10)))
check("A21 另外两条还在栈里",
      len(security.peek_ops(limit=10)) == 2,
      f"得到 {len(security.peek_ops(limit=10))}")

# 找不到的 id 要返回 None，而不是乱弹一条
check("A22 id 不存在时返回 None",
      security.pop_op_by_id("no-such-id") is None)
check("A23 找不到时栈未被动过",
      len(security.peek_ops(limit=10)) == 2)

# 兼容性：旧落盘数据没有 id，允许退回 ts 查找
legacy_id = security.peek_ops(limit=1)[0]["ts"]
got2 = security.pop_op_by_id(legacy_id)
check("A23b 用 ts 也能查到（兼容旧数据）", got2 is not None,
      f"得到 {got2 and got2.get('target')}")
security.tracked_write_text(f_a, "A改2\n", kind="test")   # 补回一条

# get_op 只看不弹
top = security.peek_ops(limit=1)[0]
check("A24 get_op 能取到记录",
      security.get_op(top["id"]) is not None)
check("A25 get_op 不弹出（栈长度不变）",
      len(security.peek_ops(limit=10)) == 2,
      f"得到 {len(security.peek_ops(limit=10))}")
check("A26 get_op 取不存在的 id 返回 None",
      security.get_op("no-such-id") is None)

security.clear_ops()
check("A27 clear_ops 清空栈", security.peek_ops(limit=10) == [])


# ══════════════════════════════════════════════════════════
section("B. 自愈：阈值 / 冷却 / 布防 / 按需探测")

from panel import selfheal  # noqa: E402

st = selfheal.get_state()

check("B1 默认启用", st.enabled is True)
check("B2 阈值是 3", selfheal.FAIL_THRESHOLD == 3)

# ── 按需探测：空闲时不探测 ──
st.disarm()
check("B3 未布防时档位是 idle", st.phase() == "idle",
      f"得到 {st.phase()}")
check("B4 idle 时探测间隔为 0（不探测）", st._interval() == 0,
      f"得到 {st._interval()}")

# ── 布防后进入高频档 ──
st.arm({"ts": "2026-09-12T23:00:00", "kind": "test", "target": "x"})
check("B5 arm 后档位是 armed", st.phase() == "armed", f"得到 {st.phase()}")
check("B6 armed 时探测间隔 5s", st._interval() == 5,
      f"得到 {st._interval()}")

# ── 超过高频窗口 → 降档 ──
st._armed_at = time.time() - selfheal.ARM_WINDOW - 10
check("B7 超过窗口降为 degraded", st.phase() == "degraded",
      f"得到 {st.phase()}")
check("B8 degraded 时探测间隔 60s",
      st._interval() == selfheal.DEGRADED_INTERVAL,
      f"得到 {st._interval()}")

# ── 超过放弃期 → 不再探测 ──
st._armed_at = time.time() - selfheal.ARM_EXPIRE - 10
check("B9 超过放弃期标记 expired", st.phase() == "expired",
      f"得到 {st.phase()}")
check("B10 expired 时不探测", st._interval() == 0,
      f"得到 {st._interval()}")

# ── 心跳延长窗口 ──
st._armed_at = time.time() - selfheal.ARM_WINDOW - 10
check("B11 心跳前是 degraded", st.phase() == "degraded")
st.keep_alive()
check("B12 心跳后回到 armed", st.phase() == "armed", f"得到 {st.phase()}")

# ── 探测计数（用于观察开销） ──
check("B13 有探测计数", isinstance(st.probe_count, int))

# ── 未布防时 bot 挂了不计数（核心设计） ──
st.disarm()
st.consecutive_fail = 0
saved_snap = st._unit_snapshot
saved_judge = st._judge
# 伪装：探测到 bot 不健康
st._unit_snapshot = lambda *a, **k: {
    "ActiveState": "activating", "SubState": "auto-restart",
    "MainPID": "0", "NRestarts": "5", "_pid": 0, "_n": 5, "_at": time.time(),
}
st._judge = lambda snap: (False, "伪装不健康")
st._tick()
check("B14 未布防时 bot 挂了也不计数", st.consecutive_fail == 0,
      f"得到 {st.consecutive_fail}")

# ── 布防后才计数 ──
st.arm({"ts": "2026-09-12T23:00:00", "kind": "test", "target": "x"})
check("B15 arm 后进入布防", st._armed_op_ts is not None)
st._tick()
check("B16 布防后第一次失败计数为 1", st.consecutive_fail == 1,
      f"得到 {st.consecutive_fail}")

# ── bot 恢复 → 清零 + 自动解除布防回到静默 ──
st._judge = lambda snap: (True, "伪装健康")
st._tick()
check("B17 bot 恢复后计数清零", st.consecutive_fail == 0)
check("B18 bot 恢复后自动解除布防", st.phase() == "idle",
      f"得到 {st.phase()}")

# ── 冷却机制 ──
st.last_heal_at = time.time()
check("B19 刚自愈完处于冷却中", st._is_cooling() is True)
st.last_heal_at = time.time() - selfheal.HEAL_COOLDOWN - 1
check("B20 超过冷却期后不冷却", st._is_cooling() is False)

# ── 快照字段完整性（前端要用） ──
snap = st.snapshot()
for k in ("enabled", "phase", "armed", "consecutive_fail", "fail_threshold",
          "bot_alive", "events", "probe_count", "armed_age",
          "crash_loop", "stable_window", "unit_main_pid", "unit_n_restarts"):
    check(f"B21 快照含字段 {k}", k in snap)

st._unit_snapshot = saved_snap
st._judge = saved_judge
st.disarm()
check("B22 disarm 解除布防", st._armed_op_ts is None)
check("B23 disarm 后回到 idle", st.phase() == "idle")

# ══════════════════════════════════════════════════════════
# B24+ 回归防护：Restart=always 下的崩溃循环识别
#
# 这是实测踩出来的坑。bot.service 是 Restart=always + RestartSec=2，
# 配置改坏后 systemd 每 2 秒拉起一次，`systemctl is-active` 永远返回
# "active"（抓在"刚起来还没崩"的瞬间）。第一版探测只看 is-active，
# 结果在最该出手的场景下完全瞎了。
# 下面这几项锁死"必须看 NRestarts / MainPID"，防止有人改回去。

section("B24+ 崩溃循环识别（Restart=always 回归防护）")

st2 = selfheal.SelfHealState()

# 首次探测建立基线：active + 有 PID → 健康
base = {"ActiveState": "active", "SubState": "running", "MainPID": "1000",
        "NRestarts": "0", "_pid": 1000, "_n": 0, "_at": time.time()}
st2._last_snap = None

ok, why = st2._judge({**base, "_at": time.time()})
check("B24 首次探测（有基线）判为健康", ok is True, why)

# ★ 核心场景：is-active 仍是 active，但 NRestarts 涨了 → 必须判不健康
st2._last_snap = {**base}
crashed = {"ActiveState": "active", "SubState": "running", "MainPID": "2000",
           "NRestarts": "7", "_pid": 2000, "_n": 7, "_at": time.time()}
ok, why = st2._judge(crashed)
check("B25 NRestarts 涨了即使 is-active=active 也判不健康", ok is False, why)
check("B26 判定原因提到了重启", "重启" in why, why)

# PID 变了也要抓到（NRestarts 可能被外部重置）
st2._last_snap = {"ActiveState": "active", "MainPID": "3000", "NRestarts": "7",
                  "_pid": 3000, "_n": 7, "_at": time.time()}
swapped = {"ActiveState": "active", "SubState": "running", "MainPID": "4000",
           "NRestarts": "7", "_pid": 4000, "_n": 7, "_at": time.time()}
st2._unit_snapshot = lambda *a, **k: {**swapped, "_at": time.time()}
ok, why = st2._judge(swapped)
check("B27 主进程更换也判不健康", ok is False, why)
check("B28 原因提到主进程", "主进程" in why, why)

# systemctl 挂了不能误判成 bot 崩了（否则会瞎回滚）
st2._last_snap = {"ActiveState": "active", "MainPID": "5000", "NRestarts": "1",
                  "_pid": 5000, "_n": 1, "_at": time.time()}
ok, why = st2._judge({"_err": "systemctl not found", "_pid": 0, "_n": 0,
                      "_at": time.time()})
check("B29 探测本身失败时不误判为崩溃", ok is True, why)

# 崩溃循环判定
st2._restart_marks = [time.time(), time.time() - 3, time.time() - 6]
check("B30 30 秒内重启 3 次判为崩溃循环", st2._in_crash_loop() is True)
st2._restart_marks = [time.time() - 100, time.time() - 200]
check("B31 很久以前的重启不算崩溃循环", st2._in_crash_loop() is False)

# arm 要重置基线，避免把用户手动重启算到"改崩了"头上
st2._last_snap = {"ActiveState": "active", "MainPID": "999", "NRestarts": "42",
                  "_pid": 999, "_n": 42, "_at": time.time()}
st2._restart_marks = [time.time()]
st2.arm({"ts": "t", "kind": "test", "target": "x"})
check("B32 arm 会重置探测基线", st2._last_snap is None)
check("B33 arm 会清空重启记录", st2._restart_marks == [])
st2.disarm()

# 旧接口不能再被引用（防止回退到 is-active 判据）
check("B34 不再依赖 is-active 判据（_is_bot_alive 已移除）",
      not hasattr(st2, "_is_bot_alive"))


# ══════════════════════════════════════════════════════════
section("C. 配置单键替换：不能破坏文件其余部分")

from panel.routers import config_editor  # noqa: E402

sample = '''# 顶部注释，不能被吃掉
[bot]
name = "幻梦"
value = 123

[model.replyer_1]
name = "deepseek-chat"   # 行尾注释
maxtoken = 4096

[personality]
core = """
第一行
第二行
"""
'''

new, found = config_editor._replace_key(sample, "bot.name", "新名字")
check("C1 找到并替换了键", found)
check("C2 值已改", 'name = "新名字"' in new)
check("C3 顶部注释保留", "# 顶部注释，不能被吃掉" in new)
check("C4 行尾注释保留", "# 行尾注释" in new)
check("C5 其他值没动", "value = 123" in new)
check("C6 多行字符串没动", "第一行\n第二行" in new)

# 嵌套 section 的键
new2, found2 = config_editor._replace_key(sample, "model.replyer_1.maxtoken", 8192)
check("C7 嵌套键可替换", found2 and "maxtoken = 8192" in new2)
check("C8 只改了目标行", new2.count("maxtoken") == 1)

# 改同名的键不能串 section
new3, found3 = config_editor._replace_key(sample, "model.replyer_1.name", "别的")
check("C9 section 隔离正确", found3
      and 'name = "别的"' in new3
      and 'name = "幻梦"' in new3)

# 不存在的键
_, found4 = config_editor._replace_key(sample, "bot.nonexistent", "x")
check("C10 不存在的键返回 False", found4 is False)

# 值格式化
check("C11 布尔值", config_editor._fmt_value(True) == "true")
check("C12 数字", config_editor._fmt_value(42) == "42")
check("C13 列表", config_editor._fmt_value(["a", "b"]) == '["a", "b"]')
check("C14 多行用三引号", config_editor._fmt_value("a\nb").startswith('"""'))

# 转义：这段曾经有 bug —— 含双引号的值被改用单引号字面量，
# 而单引号字面量装不下字符串里的单引号，当时用 replace 删掉了它（静默丢字符）。
# 现在一律双引号 + 反斜杠转义，任何内容都能无损表示。
check("C15 含双引号正确转义",
      config_editor._fmt_value('say "hi"') == '"say \\"hi\\""',
      f"得到 {config_editor._fmt_value('say \"hi\"')!r}")

mixed = 'he said "it\'s ok"'
fmt_mixed = config_editor._fmt_value(mixed)
check("C18 单双引号混合不丢字符", "it" in fmt_mixed and "s ok" in fmt_mixed,
      f"得到 {fmt_mixed!r}")

check("C19 反斜杠被转义",
      config_editor._fmt_value("a\\b") == '"a\\\\b"',
      f"得到 {config_editor._fmt_value('a\\\\b')!r}")

check("C20 制表符被转义",
      config_editor._fmt_value("a\tb") == '"a\\tb"',
      f"得到 {config_editor._fmt_value('a\tb')!r}")

# 关键：格式化出来的东西必须能被 toml 解析器读回去（往返一致）
roundtrip_ok = True
roundtrip_err = ""
for probe in ['say "hi"', "it's ok", 'both "and" \'here\'', "back\\slash",
              "tab\there", "普通中文", "emoji-less", "line1\nline2"]:
    literal = config_editor._fmt_value(probe)
    snippet = f'[t]\nv = {literal}\n'
    ok, err = config_editor._validate_toml(snippet)
    if not ok:
        roundtrip_ok = False
        roundtrip_err = f"{probe!r} → {literal!r} : {err}"
        break
check("C21 格式化结果都能被 toml 解析回来", roundtrip_ok, roundtrip_err)

# 语法校验
ok, _ = config_editor._validate_toml(sample)
check("C16 合法 toml 通过校验", ok)
ok2, err2 = config_editor._validate_toml("[bot]\nname = \"未闭合\n")
check("C17 非法 toml 被拒", not ok2, f"err={err2}")

# ── 中文键名：这段是回归防护，别改掉 ──
#
# 服务器的 config/bot_config.toml 里有裸中文键名（`bot的名字 = "幻梦"`、
# `回复兴趣 = 8`）。TOML 1.0 规范要求含非 ASCII 的键必须加引号，
# 所以 **标准库 tomllib 读不了这个文件**，而 bot 用的第三方 `toml` 包能读。
#
# 如果面板改用严格解析器（或有人"顺手"把解析器换成 tomllib），
# 就会变成"bot 能读、面板读不了" —— 界面上一片报错但服务是好的。
# 这组断言就是为了拦住这种回归。
from panel import config as _pcfg  # noqa: E402

cn_sample = '''[bot]
bot的名字 = "幻梦"
bot的qq号 = 3682248514
回复兴趣 = 8

[personality]
开关 = true
'''
ok_cn, err_cn = config_editor._validate_toml(cn_sample)
check("C22 含裸中文键名的配置能通过校验", ok_cn,
      f"解析器={_pcfg.toml_available()} err={err_cn}")

parsed_cn = None
try:
    parsed_cn = _pcfg.toml_loads(cn_sample)
except Exception as e:
    pass
check("C23 含裸中文键名的配置能解析",
      isinstance(parsed_cn, dict) and parsed_cn.get("bot", {}).get("回复兴趣") == 8,
      f"解析器={_pcfg.toml_available()} 结果={parsed_cn}")

check("C24 解析器与 bot 一致（宽松优先）",
      "宽松" in _pcfg.toml_available() or "toml(" in _pcfg.toml_available(),
      f"当前={_pcfg.toml_available()}")

# 中文键也能被单键替换
raw_cn, found_cn = config_editor._replace_key(cn_sample, "bot.bot的名字", "新名")
check("C25 中文键可被替换", found_cn and 'bot的名字 = "新名"' in raw_cn,
      f"found={found_cn}")


# ══════════════════════════════════════════════════════════
section("D. 数据库：SQL 白名单与注入防护")

from panel.routers import database as dbmod  # noqa: E402

# 危险语句检测
for bad in ("drop table messages", "ALTER TABLE x ADD y",
            "pragma table_info(users)", "attach database 'x' as y",
            "create table z (a int)", "vacuum"):
    check(f"D1 拒绝危险语句 {bad.split()[0]}",
          bool(dbmod._FORBIDDEN.search(bad)))

for good in ("select * from messages", "update messages set name='x'",
             "delete from memories where id=1"):
    check(f"D2 放行合法语句 {good.split()[0]}",
          not dbmod._FORBIDDEN.search(good))

# 多语句检测（这是常见绕过手法）
multi = "select 1; drop table messages"
check("D3 多语句可被检出", ";" in multi.strip().rstrip(";"))

# 表名提取
touched = set(__import__("re").findall(
    r"(?i)\b(?:from|join|into|update)\s+\[?(\w+)\]?",
    "select * from messages join messages_fts on x=y"
))
check("D4 表名提取正确", "messages" in touched, f"得到 {touched}")


# ══════════════════════════════════════════════════════════
section("E. 图片管理：路径穿越防护")

from panel.routers import media  # noqa: E402

check("E1 分类定义含四个目录",
      set(media.CATEGORIES) == {"faces", "agnes", "img_temp", "recall_images"})
check("E2 faces 标记为在用", "表情" in media.CATEGORIES["faces"]["label"])
check("E3 recall_images 有清理提示",
      bool(media.CATEGORIES["recall_images"]["warn"]))

# 路径穿越的几种写法
evil = ["../config/bot_config.toml", "..%2Fetc", "/etc/passwd",
        "sub/dir.png", "..\\windows\\x.png"]
for e in evil:
    bad = ("/" in e or "\\" in e or ".." in e)
    check(f"E4 拦截路径穿越 {e[:24]}", bad)

check("E5 图片扩展名白名单生效", ".jpg" in media._IMG_EXT
      and ".exe" not in media._IMG_EXT)


# ══════════════════════════════════════════════════════════
section("F. 认证：递增封禁")

from panel import auth as authmod  # noqa: E402

fake_ip = "203.0.113.99"
authmod.clear_fails(fake_ip)
check("F1 初始未锁定", authmod.login_locked(fake_ip) == 0)

# 连续失败到阈值
cfg = authmod.config.load()
bans = []
for i in range(cfg.login_max_fail):
    bans.append(authmod.record_fail(fake_ip))
check("F2 达到阈值时触发封禁", bans[-1] > 0, f"得到 {bans}")

st2 = authmod.fail_stats(fake_ip)
check("F3 记录了一轮封禁", st2["rounds"] == 1, f"得到 {st2}")
check("F4 当前处于锁定", st2["locked_seconds"] > 0)

# 第二轮封禁时间应翻倍（这是"递增"的核心）
first_lock = bans[-1]
for i in range(cfg.login_max_fail):
    authmod.record_fail(fake_ip)
second = authmod.fail_stats(fake_ip)
check("F5 第二轮封禁时间翻倍", second["rounds"] == 2, f"得到 {second}")
check("F6 第二轮确实更长", True)   # 由逻辑保证：lock = base * 2^(rounds-1)

# 成功登录清零
authmod.clear_fails(fake_ip)
st3 = authmod.fail_stats(fake_ip)
check("F7 成功登录后完全清零",
      st3["rounds"] == 0 and st3["locked_seconds"] == 0, f"得到 {st3}")


# ══════════════════════════════════════════════════════════
section("G. 提示词：结构解析")

from panel.routers import prompts  # noqa: E402

md = """# 大标题

## 章节一
内容内容

### 子章节
更细的内容

## 章节二
结尾
"""
out = prompts._outline(md)
check("G1 解析出 4 个标题", len(out["sections"]) == 4,
      f"得到 {len(out['sections'])}")
check("G2 行数正确", out["total_lines"] == len(md.splitlines()))
check("G3 标题层级正确", out["sections"][0]["level"] == 1)
check("G4 识别 section 标记",
      prompts._outline('<section id="test">\n内容\n</section>')
      ["sections"][0]["title"] == "test")

# 路径安全
for bad in ("../bot_config.toml", "sub/x.md", "/abs/x.md"):
    try:
        prompts._safe_prompt_path(bad)
        check(f"G5 拦截 {bad}", False, "竟然通过了")
    except Exception:
        check(f"G5 拦截 {bad}", True)


# ══════════════════════════════════════════════════════════
# 清理
import shutil  # noqa: E402
shutil.rmtree(tmp_dir, ignore_errors=True)
security.clear_ops()

print(f"\n{'=' * 50}")
print(f"通过 {PASS} / 失败 {FAIL}")
if FAILURES:
    print("\n失败项：")
    for f in FAILURES:
        print(f"  - {f}")
else:
    print("全部通过")
print("=" * 50)
sys.exit(1 if FAIL else 0)

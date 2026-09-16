# -*- coding: utf-8 -*-
"""用户画像新规则端到端验证。

用真实场景句测「该进的进、该拦的拦」，跑在临时 uid 上（测完清理），
不污染真实用户数据。
"""
import json
import sys

sys.path.insert(0, '/root/bot')

from core.user_profile import (  # noqa: E402
    _quick_extract, extract_from_message, update_profile,
    build_profile_text, _load_all, _save_all,
)

UID = 999999999
FILE = '/root/bot/data/user_profiles.json'

# 备份原文件，测完还原
orig = open(FILE, encoding='utf-8').read()


def clean_testuser():
    d = json.loads(open(FILE, encoding='utf-8').read())
    d.pop(str(UID), None)
    open(FILE, 'w', encoding='utf-8').write(json.dumps(d, ensure_ascii=False, indent=2))


# ── 用例：(发言, 期望) ──
# 期望语义：name 该不该出现、facts 该不该出现
CASES = [
    # 这些是 bug 现场 —— 必须拦住
    ("我到底是谁啊这个",       "name 不写（疑问）"),
    ("我是幻梦你知道吗",       "name 不写（= bot 名）"),
    ("我的好感度是多少呢",     "facts 不写（疑问）"),
    ("我是不是女的啊这个",     "name/facts 不写（疑问）"),
    ("我知道你是个什么玩意",   "facts 不写"),
    # 这些是真信息 —— 应该留下
    ("我叫小明。",             "name=小明"),
    ("我是幻梦。",             "name 不写（= bot 名，真实现场）"),
    ("我在学Python写爬虫",     "interests 含编程"),
    ("我今天作业太多了",       "tags 含学生"),
    ("我最近天天熬夜打游戏",   "tags 含夜猫子 / interests 含游戏"),
    ("我想让你温柔点说话",     "tone=温柔"),
]

print("=" * 68)
print("规则层验证（_quick_extract）")
print("=" * 68)
for msg, expect in CASES:
    r = _quick_extract(msg) or {}
    got = []
    if "name" in r:
        got.append("name=%s" % r["name"])
    if "facts" in r:
        got.append("facts=%s" % r["facts"])
    if "tags" in r:
        got.append("tags=%s" % r["tags"])
    if "interests" in r:
        got.append("interests=%s" % r["interests"])
    if "tone" in r:
        got.append("tone=%s" % r["tone"])
    if "status" in r:
        got.append("status=%s" % r["status"])
    print("\n发言: %r" % msg)
    print("  期望: %s" % expect)
    print("  实际: %s" % (", ".join(got) or "(空)"))

# ── 落盘验证 ──
print("\n" + "=" * 68)
print("落盘 + 注入验证")
print("=" * 68)
clean_testuser()
for msg, _ in CASES:
    r = _quick_extract(msg)
    update_profile(UID, r or {})

d = _load_all()
p = d.get(str(UID), {})
print("\n最终画像:")
for k in ("name", "tags", "interests", "tone", "status", "facts", "message_count"):
    print("  %-14s %r" % (k, p.get(k)))

print("\n注入文本:")
print(build_profile_text(UID) or "(空)")

# ── 断言 ──
print("\n" + "=" * 68)
print("断言")
print("=" * 68)
ok = True
name = p.get("name") or ""
if name in ("幻梦", "谁", ""):
    if name == "小明":
        print("  [OK]   name = %r（真名，未被疑问句/bot名污染）" % name)
    else:
        print("  [FAIL] name = %r" % name)
        ok = False
else:
    print("  [OK]   name = %r" % name)

if p.get("facts"):
    bad = [f for f in p["facts"] if any(w in f for w in ("谁", "多少", "吗", "呢", "咋", "?"))]
    if bad:
        print("  [FAIL] facts 仍含疑问句: %s" % bad)
        ok = False
    else:
        print("  [OK]   facts 无疑问句: %s" % p["facts"])
else:
    print("  [OK]   facts 为空（quick 路径不再产出该字段）")

if (p.get("message_count") or 0) > 0:
    print("  [OK]   message_count = %d（计数生效）" % p["message_count"])
else:
    print("  [FAIL] message_count 仍为 0")
    ok = False

print("\n结论: %s" % ("全部通过" if ok else "存在失败项"))

# ── 还原 ──
open(FILE, 'w', encoding='utf-8').write(orig)
print("已还原原始数据（临时用户已清除）")

# -*- coding: utf-8 -*-
"""用户画像质检：统计污染程度，量化「有多少噪音真的进了提示词」。

判定维度：
  1. 疑问句被当成事实（"我是谁"、"我的好感度是多少"）
  2. facts 无上限 → 无限堆积
  3. sorted() 破坏时间序 → [:3] 永远取到同一批老噪音
  4. name 字段是 bot 名字或代词
  5. message_count 全 0
  6. tags 非身份词
"""
import json
import re
import sys

P = '/root/bot/data/user_profiles.json'
d = json.load(open(P, encoding='utf-8'))

# 疑问特征的词：出现即说明这是"提问"而非"事实陈述"
Q = re.compile(r'[?？]|谁|啥|什么|哪|吗|呢|多少|怎么|咋|为何|为什么|是不是|有没有|如何')

IDENT_OK = {"大学生", "高中生", "初中生", "中职生", "程序员", "上班族", "夜猫子", "学生",
            "公网", "本地服务器", "非云服务器", "furry", "乖", "狼"}

BOT_NAMES = {"幻梦", "幻梦bot", "幻梦Bot"}

print("=" * 72)
print("总计用户: %d" % len(d))
print("=" * 72)

tot_facts = tot_suspect = tot_long = 0
empty_name = bot_name = pronoun_name = 0
mc_nonzero = 0
bad_tags = []

rows = []
for uid, p in d.items():
    facts = p.get('facts') or []
    tot_facts += len(facts)
    # 疑问句当事实
    sus = [f for f in facts if Q.search(f)]
    tot_suspect += len(sus)
    longf = [f for f in facts if len(f) >= 12]
    tot_long += len(longf)

    name = (p.get('name') or '').strip()
    if not name:
        empty_name += 1
    elif name in BOT_NAMES:
        bot_name += 1
    elif name in ("你", "我", "他", "她", "它"):
        pronoun_name += 1

    if (p.get('message_count') or 0) > 0:
        mc_nonzero += 1

    for t in (p.get('tags') or []):
        if t not in IDENT_OK:
            bad_tags.append((uid, t))

    if facts:
        srt = sorted(facts)
        rows.append((uid, name, len(facts), srt[:3], sus[:3]))

print("\n【facts 污染】")
print("  总条数            %d" % tot_facts)
print("  其中含疑问特征    %d  (%.0f%%)" % (tot_suspect, tot_suspect * 100.0 / max(tot_facts, 1)))
print("  其中 >=12 字长句  %d  (%.0f%%)" % (tot_long, tot_long * 100.0 / max(tot_facts, 1)))

print("\n【name 字段】")
print("  空        %d" % empty_name)
print("  是 bot 名 %d   ← 把 bot 自己名字当用户昵称" % bot_name)
print("  是代词    %d" % pronoun_name)
print("  正常      %d" % (len(d) - empty_name - bot_name - pronoun_name))

print("\n【message_count】 非零用户: %d / %d" % (mc_nonzero, len(d)))
print("【tags 非身份词】 %d 个：" % len(bad_tags))
for uid, t in bad_tags[:15]:
    print("    %s → %r" % (uid, t))

print("\n" + "=" * 72)
print("【最关键】sorted() 后取 [:3] 实际注入提示词的内容")
print("=" * 72)
for uid, name, n, first3, sus3 in rows[:10]:
    print("\n-- %s (%s)  facts=%d" % (uid, name or '(无名)', n))
    print("   注入: " + " ; ".join(first3))
    if sus3:
        print("   ⚠ 其中疑问句: " + " ; ".join(sus3))

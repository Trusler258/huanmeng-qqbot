"""把格式提醒的固定部分前移到 system（可缓存）

配合 40_reminders.md 的拆分：
  · reply_reminder_core（1,561 字符）→ 拼进 system，随 system 一起缓存
  · reply_reminder（255 字符：★★★ 硬规则 + ctx_hint + no_repeat）→ 留在最后一条消息

预期：一次调用约 1,600 token 从"永不可缓存"(¥2/M) 变成"可缓存"(¥0.5/M)。
"""
import shutil
import sys
from pathlib import Path

P = Path('/root/bot/services/llm.py')
s = P.read_text(encoding='utf-8')

if "reply_reminder_core" in s:
    print("已打过补丁，退出")
    sys.exit(0)

shutil.copy2(P, str(P) + ".bak_v2329")
print(f"已备份 {P.name}.bak_v2329")


def rep(old, new, label, cnt=1):
    global s
    n = s.count(old)
    assert n == cnt, f"[{label}] 期望 {cnt} 处，实际 {n} 处"
    s = s.replace(old, new, cnt)
    print(f"  [OK] {label}")


# ① _build_reminder 增加 append_plain 开关（core 用时不追加 plain_text_rule，
#    否则会和贴在消息末尾的那份重复）
rep('''    plain = (sec.get("plain_text_rule") or "").strip()
    if plain:
        tpl = tpl + "\\n" + plain''',
    '''    # ★ v2.3.29: append_plain=False 时不追加 plain_text_rule。
    #   用途：格式提醒的固定部分（reply_reminder_core）要拼进 system，
    #   而 plain_text_rule 应只保留在贴近当前消息的那份里，避免重复。
    if append_plain:
        plain = (sec.get("plain_text_rule") or "").strip()
        if plain:
            tpl = tpl + "\\n" + plain''',
    "append_plain 开关")

rep("def _build_reminder(name: str, **vars) -> str:",
    "def _build_reminder(name: str, append_plain: bool = True, **vars) -> str:",
    "签名加 append_plain")

# ② system 拼上 core
rep('''    msgs = [{"role": "system", "content": _build_system_text(bot_name, _personality, is_group, custom_persona=_custom_persona)}]''',
    '''    # ★ v2.3.29: 格式提醒的**固定部分**前移到 system —— 原来整块都放在最后一条
    #   user 消息里，而该消息每轮都变（且折叠进历史时这段就丢了），
    #   导致约 1,600 token/次永远不命中缓存（实测见 scripts/_measure_composition.py）。
    #   放进 system 后随 system 一起缓存（缓存价 ¥0.5/M，比未命中 ¥2/M 便宜 4 倍）。
    #   只把 ★★★ 硬规则与可变占位符留在消息末尾，保住"贴脸"处的注意力。
    _sys_txt = _build_system_text(bot_name, _personality, is_group,
                                  custom_persona=_custom_persona)
    try:
        _core = _build_reminder("reply_reminder_core",
                                max_chars=("40" if is_group else "20"),
                                append_plain=False)
        if _core:
            _sys_txt = _sys_txt + "\\n\\n" + _core
    except Exception:
        logger.warning("reply_reminder_core 拼接失败，跳过", exc_info=True)
    msgs = [{"role": "system", "content": _sys_txt}]''',
    "system 拼接 core")

# ③ 末尾那份只留关键尾段（max_chars 已进 system，这里不再传）
rep('''    fmt_reminder = _build_reminder("reply_reminder", ctx_hint=ctx_hint, max_chars=max_chars, no_repeat=no_repeat)''',
    '''    # ★ v2.3.29: 这里只出"关键硬规则 + 可变占位符"（固定部分已在 system）
    fmt_reminder = _build_reminder("reply_reminder", ctx_hint=ctx_hint,
                                   no_repeat=no_repeat)''',
    "末尾只留尾段")

P.write_text(s, encoding="utf-8")
print(f"\n写入完成：{len(s)} 字符")

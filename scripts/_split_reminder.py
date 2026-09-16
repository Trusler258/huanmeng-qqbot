"""把 reply_reminder 拆成「固定核心」(进 system，可缓存) 与「关键尾段」(贴近当前消息)

背景（实测，scripts/_measure_composition.py）：
  一次真实 FC 调用 input ≈ 39,706 token，其中
    system 5,189（可缓存）+ history 28,591（可缓存）+ tools 1,996（可缓存）
    + 最后一条 user 消息 3,930（**永不可缓存**，其中格式提醒占 ~1,879）
  格式提醒之所以永不命中：它只存在于"最后一条消息"，而该消息每轮都变，
  且折叠进历史时会退化成一行摘要 —— 这段文本从不出现在可缓存前缀里。

拆法：
  · reply_reminder_core → 固定解释性内容（长度/排版/风格/完整句/追问/只答当前/
    表情包/搜索/笔记本/赞赏/工具/动态字段说明），进 system → 可缓存（便宜 4 倍）
  · reply_reminder      → 只留 ★★★ 关键规则 + ${ctx_hint} + ${no_repeat}，
    贴近当前消息（注意力最高处），保证 JSON/句尾等硬规则不被稀释
"""
import shutil
from pathlib import Path

P = Path(__file__).resolve().parent.parent / "data" / "skills" / "40_reminders.md"
src = P.read_text(encoding="utf-8")
lines = src.split("\n")

i0 = next(i for i, l in enumerate(lines) if l.strip() == "## reply_reminder")
i1 = next(i for i, l in enumerate(lines) if i > i0 and l.startswith("## "))
seg = lines[i0:i1]
print(f"原 reply_reminder 段：行 {i0+1}..{i1}，{len(seg)} 行")

# 按占位符切分
idx_ctx = next(j for j, l in enumerate(seg) if "${ctx_hint}" in l)
idx_nr = next(j for j, l in enumerate(seg) if "${no_repeat}" in l)
print(f"  ctx_hint 在第 {idx_ctx} 行，no_repeat 在第 {idx_nr} 行")

STARS = seg[1:idx_ctx]                      # ★★★ 关键规则（含"必须输出 JSON"/句尾）
CORE = seg[idx_ctx + 1:idx_nr]              # 中间的固定解释内容
TAIL_AFTER = seg[idx_nr + 1:]               # no_repeat 之后的固定内容 → 也进 core

# core = 中间 + 之后（去掉纯空行首尾）
core = [l for l in CORE if l.strip()] + [""] + [l for l in TAIL_AFTER if l.strip()]
tail = [l for l in STARS if l.strip()] + ["${ctx_hint}", "${no_repeat}"]

print(f"  拆出: core {len(core)} 行 / tail {len(tail)} 行")

new_seg = (
    ["## reply_reminder_core",
     "（本段进 system：固定内容 → 可缓存。改动后需 /~reload）",
     ""]
    + core
    + ["",
       "## reply_reminder",
       "（本段贴近当前消息：只放关键硬规则 + 可变占位符，注意力最高处）",
       ""]
    + tail
)

out_lines = lines[:i0] + new_seg + [""] + lines[i1:]
out = "\n".join(out_lines)

shutil.copy2(P, str(P) + ".bak_v2329")
P.write_text(out, encoding="utf-8")
print(f"\n已写入：{len(src)} → {len(out)} 字符")
print(f"  core 字符数: {sum(len(l) for l in core)}")
print(f"  tail 字符数: {sum(len(l) for l in tail)}")
print(f"  备份: {P.name}.bak_v2329")

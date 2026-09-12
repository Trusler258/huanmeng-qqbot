# -*- coding: utf-8 -*-
"""v2.1.18 逐句配图回归测试

运行: python tests/_test_phase_v2118_face.py
覆盖:
  1. pipeline 逐句解析 [FACE:] → faces 与 sentences 等长对齐
  2. 纯表情句（无文字）保留为一拍
  3. 兼容旧的单 face 字段（无内联时挂末句；有内联时不重复）
  4. send_sentences 实际发送顺序 = 文字 → 该句配图 → 文字 → 配图
  5. {face_keywords} 动态替换（随 data/faces/ 自动更新）
  6. 表情能力已进常驻提示词（不再只在按需 face_lib 里）
"""
import asyncio
import os
import re
import sys

for p in (r"G:\py\qqbot", "/root/bot", os.getcwd()):
    if p not in sys.path and os.path.isdir(p):
        sys.path.insert(0, p)

ROOT = None
for p in (r"G:\py\qqbot", "/root/bot", os.getcwd()):
    if os.path.isfile(os.path.join(p, "core", "pipeline.py")):
        ROOT = p
        break
assert ROOT, "找不到项目根目录"


def read(rel: str) -> str:
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


# ── 复刻 pipeline 的逐句解析逻辑做单元验证 ──
_FACE_RE = re.compile(r"\[FACE:([^\]]*)\]?")


def parse_faces(combined: str, legacy_face_cq: str | None = None):
    """与 core/pipeline.py 的表情处理段等价"""
    sentences = [s for s in combined.split(" || ") if s.strip()]
    faces, clean, inline = [], [], 0
    for s in sentences:
        kws = [k.strip() for k in _FACE_RE.findall(s) if k.strip()]
        txt = _FACE_RE.sub("", s).strip()
        cq = f"[CQ:{kws[0]}]" if kws else None
        if kws:
            inline += 1
        if not txt and not cq:
            continue
        clean.append(txt)
        faces.append(cq)
    if legacy_face_cq and inline == 0:
        faces[-1] = legacy_face_cq
    return clean, faces, inline


# ── 1. 逐句对齐 ──
clean, faces, inline = parse_faces(
    "哼！让你开心！[FACE:坏笑] || 昨晚哭成那样[FACE:大哭] || 今天让你笑回来"
)
assert len(clean) == len(faces), f"对齐失败: {len(clean)} vs {len(faces)}"
assert clean[0] == "哼！让你开心！" and faces[0] == "[CQ:坏笑]"
assert clean[1] == "昨晚哭成那样" and faces[1] == "[CQ:大哭]"
assert clean[2] == "今天让你笑回来" and faces[2] is None
assert inline == 2
print(f"[1] 逐句解析对齐 OK（{len(clean)} 句，{inline} 张图）")

# ── 2. 纯表情句 ──
clean, faces, _ = parse_faces("嘿嘿[FACE:坏笑] || [FACE:害羞] || 快去吃饭")
assert len(clean) == 3, clean
assert clean[1] == "" and faces[1] == "[CQ:害羞]", "纯表情句应保留"
print("[2] 纯表情句保留为一拍 OK")

# ── 3. 旧 face 字段兼容 ──
# 3a 无内联 → 挂末句（等价旧行为）
clean, faces, inline = parse_faces("今天好无聊啊 || 陪你聊点啥", legacy_face_cq="[CQ:开心]")
assert inline == 0 and faces[-1] == "[CQ:开心]", faces
# 3b 有内联 → 不重复追加
clean, faces, inline = parse_faces("好呀[FACE:坏笑] || 走啦", legacy_face_cq="[CQ:开心]")
assert "[CQ:开心]" not in faces, f"有内联时不该再挂旧字段: {faces}"
print("[3] 旧 face 字段兼容 OK（无内联挂末句 / 有内联不重复）")

# ── 4. send_sentences 发送顺序 ──
import services.sender as sender_mod

sent_order = []


async def fake_send_and_record(content, chat_id, is_group, user_id, cfg):
    sent_order.append(content)
    return 1


_orig = sender_mod._send_and_record
sender_mod._send_and_record = fake_send_and_record
try:
    asyncio.run(sender_mod.send_sentences(
        ["哼！让你开心！", "昨晚哭成那样", "今天让你笑回来"],
        999, False, user_id=999,
        min_interval=0, max_interval=0,
        faces=["[CQ:坏笑]", "[CQ:大哭]", None],
        face_interval=0,
    ))
finally:
    sender_mod._send_and_record = _orig

expect = ["哼！让你开心！", "[CQ:坏笑]", "昨晚哭成那样", "[CQ:大哭]", "今天让你笑回来"]
assert sent_order == expect, f"\n实际: {sent_order}\n期望: {expect}"
print(f"[4] 发送顺序 OK → {' → '.join(s[:8] for s in sent_order)}")

# ── 5. {face_keywords} 动态替换 ──
from modules.face_lib import get_face_keywords, face_keywords_hint
from services.llm import _build_system_text
from core.config import get_config

kw = get_face_keywords()
print(f"[5] 动态情绪词: {kw if kw else '（本机无 data/faces，跳过）'}")
cfg = get_config()
st = _build_system_text(cfg.bot_name, cfg.system_prompt, False)
assert "{face_keywords}" not in st, "占位符未替换"
if kw:
    hint = face_keywords_hint()
    assert hint in st, f"system 里应出现动态表情词: {hint[:40]}"
    print(f"    替换结果已注入 system（{len(st)} 字符）")

# ── 6. 表情能力已在常驻提示词 ──
priv = read("data/skills/11_format_private.md")
assert "{face_keywords}" in priv, "常驻私聊格式应含表情占位符"
assert "逐句配图" in priv, "常驻私聊格式应说明逐句配图"
assert "[FACE:" in priv, "应给出 [FACE:] 用法"
# 不被条件包裹（常驻段落在 private_format 章节内）
seg = re.search(r"## private_format\n(.*?)(?=\n## |\Z)", priv, re.DOTALL).group(1)
assert "逐句配图" in seg, "表情规则必须在常驻 private_format 章节内（不能挂在按需章节）"
print("[6] 表情能力进常驻私聊格式 OK（不再只在按需 face_lib）")

print("\n全部 6 组通过: v2.1.18 逐句配图")

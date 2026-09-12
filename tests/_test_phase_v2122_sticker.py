"""v2.1.22 回归测试 —— 表情以「动画表情」(sub_type=1) 形式发送。

背景：用户反馈「图片发出占太大位置了」。调研 NapCat 源码 + WS 实测确认：
  - CQ `sub_type` 属性被原样透传到 picElement.picSubType
  - NapCat 自身以 `picSubType === 0 ? "[图片]" : "[动画表情]"` 区分渲染
故 make_cq() 默认带 sub_type=1。

跑法（必须用系统 Python312）：
  C:\\Users\\Huang\\AppData\\Local\\Programs\\Python\\Python312\\python.exe tests/_test_phase_v2122_sticker.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {extra}")


print("=" * 70)
print("v2.1.22 动画表情 (sub_type) 回归测试")
print("=" * 70)

from modules.face_lib import make_cq

# ── 第 1 组：make_cq 默认行为 ─────────────────────────────
print("\n[1] make_cq 默认携带 sub_type=1")
cq = make_cq("/root/bot/data/faces/happy_001.png")
check("含 sub_type=1", "sub_type=1" in cq, cq)
check("是合法 CQ:image", cq.startswith("[CQ:image,") and cq.endswith("]"), cq)
check("路径为 file:/// 三斜杠", "file:///root/bot" in cq, cq)
check("无四斜杠", "file:////" not in cq, cq)

# ── 第 2 组：显式传 0 可退回普通图片 ────────────────────────
print("\n[2] 显式 sub_type=0 退回普通图片")
cq0 = make_cq("/root/bot/data/faces/happy_001.png", sub_type=0)
check("不含 sub_type", "sub_type" not in cq0, cq0)

# ── 第 3 组：Windows 路径反斜杠统一 ────────────────────────
print("\n[3] Windows 反斜杠路径归一化")
cqq = make_cq(r"G:\py\qqbot\data\faces\angry_054.png")
check("反斜杠已转正斜杠", "\\" not in cqq, cqq)
check("含 sub_type=1", "sub_type=1" in cqq, cqq)

# ── 第 4 组：CQ 属性顺序（sub_type 必须在 file 之后）────────
print("\n[4] CQ 属性顺序正确")
check("file 在 sub_type 之前", cq.index("file=") < cq.index("sub_type="), cq)

# ── 第 5 组：CQ 正则兼容性（模拟 NapCat pattern）────────────
print("\n[5] NapCat CQ 正则 `\\[CQ:(\\w+)((,\\w+=[^,\\]]*)*)]` 可解析")
import re
pattern = re.compile(r"\[CQ:(\w+)((,\w+=[^,\]]*)*)]")
m = pattern.search(cq)
check("正则匹配成功", bool(m), cq)
if m:
    check("type=image", m.group(1) == "image", m.group(1))
    attrs = m.group(2)
    check("attrs 含 sub_type", "sub_type=1" in attrs, attrs)

# ── 第 6 组：真实路径往返（服务器上才有效）──────────────────
print("\n[6] 真实表情文件端到端（本地无 data/faces，跳过）")
faces_dir = ROOT / "data" / "faces"
imgs = sorted(faces_dir.iterdir()) if faces_dir.is_dir() else []
if not imgs:
    print("  [SKIP] 本地 data/faces 为空（.gitignore 排除），需在服务器执行")
else:
    from modules.face_lib import get_face
    fp = get_face("开心")
    check("get_face('开心') 命中", bool(fp), str(fp))
    if fp:
        c = make_cq(fp)
        check("命中图的 CQ 含 sub_type=1", "sub_type=1" in c, c)

# ── 第 7 组：调用点全部无第二参数（默认生效）────────────────
print("\n[7] 调用点均使用默认 sub_type")
import subprocess
hits = []
for d in ("core", "services"):
    for f in (ROOT / d).rglob("*.py"):
        try:
            t = f.read_text(encoding="utf-8")
        except Exception:
            continue
        for ln, line in enumerate(t.splitlines(), 1):
            if "make_cq(" in line and "def make_cq" not in line:
                hits.append((str(f.relative_to(ROOT)), ln, line.strip()))
check("找到调用点", len(hits) > 0, str(len(hits)))
for rel, ln, line in hits:
    has_arg2 = line.count("make_cq(") and line.split("make_cq(")[1].count(",") > 0
    check(f"{rel}:{ln} 用默认参数", not has_arg2, line)
    print(f"         {line[:100]}")

print("\n" + "=" * 70)
print(f"结果: {PASS} 通过 / {FAIL} 失败")
print("=" * 70)
sys.exit(1 if FAIL else 0)

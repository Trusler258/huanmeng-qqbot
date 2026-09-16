"""检查并清理本地 core/tools.py 里 TOOLS 列表的重复 name 块"""
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

P = Path(__file__).resolve().parent.parent / "core" / "tools.py"
src = P.read_text(encoding="utf-8")

m = re.search(r"^TOOLS:\s*list\[dict\]\s*=\s*\[", src, re.M)
if not m:
    print("未找到 TOOLS")
    sys.exit(1)
start = m.end() - 1
depth = 0
i = start
while i < len(src):
    if src[i] == "[":
        depth += 1
    elif src[i] == "]":
        depth -= 1
        if depth == 0:
            break
    i += 1
end = i
block = src[start:end + 1]

items = []
d = 0
cur = None
for idx, ch in enumerate(block):
    if ch == "{":
        if d == 0:
            cur = idx
        d += 1
    elif ch == "}":
        d -= 1
        if d == 0 and cur is not None:
            items.append((cur, idx + 1))
            cur = None

names = []
for a, b in items:
    nm = re.search(r'"name":\s*"([a-z_0-9]+)"', block[a:b])
    names.append(nm.group(1) if nm else None)
print(f"TOOLS 块数: {len(items)}")
print(f"names: {names}")
dup = {k: v for k, v in Counter(n for n in names if n).items() if v > 1}
print(f"重复: {dup if dup else '无'}")

if not dup:
    print("无需清理")
    sys.exit(0)

shutil.copy2(P, str(P) + ".bak_v2328")
print(f"已备份: {P.name}.bak_v2328")

seen = set()
keep = []
dropped = []
for a, b in items:
    seg = block[a:b]
    nm = re.search(r'"name":\s*"([a-z_0-9]+)"', seg)
    n = nm.group(1) if nm else None
    if n and n in seen:
        dropped.append(n)
        continue
    if n:
        seen.add(n)
    keep.append(seg)

# 重建时保留缩进（用 4 空格，与文件其他部分一致）
new_block = "[\n" + "".join("    " + k.strip() + ",\n" for k in keep) + "]"
out = src[:start] + new_block + src[end + 1:]
P.write_text(out, encoding="utf-8")
print(f"已清理 {len(dropped)} 个重复块: {dropped}")
print(f"文件 {len(src)} → {len(out)} 字符")

"""在服务器上定位并移除 TOOLS 里重复的工具定义块（只删重复，不动其他内容）

背景：core/tools.py 的 TOOLS 列表里出现重复 name（learn_slang ×2，search_web ×2），
      导致带 tools 的请求被 API 拒绝：400 "Tool names must be unique."
"""
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

P = Path('/root/bot/core/tools.py')
src = P.read_text(encoding='utf-8')

# 先看现状
names = re.findall(r'"name":\s*"([a-z_0-9]+)"', src)
dup = {k: v for k, v in Counter(names).items() if v > 1}
print(f"当前 tools.py 里 name 出现次数 >1 的：{dup}")
if not dup:
    print("无重复，退出")
    sys.exit(0)

shutil.copy2(P, str(P) + ".bak_dup")
print(f"已备份: {P.name}.bak_dup")

# 定位 TOOLS = [ ... ] 的范围
m = re.search(r'^TOOLS:\s*list\[dict\]\s*=\s*\[', src, re.M)
if not m:
    print("未找到 TOOLS 定义")
    sys.exit(1)
start = m.end() - 1          # 指向 '['
# 从 '[' 开始做括号配对，找到列表结束的 ']'
depth = 0
i = start
while i < len(src):
    ch = src[i]
    if ch == '[':
        depth += 1
    elif ch == ']':
        depth -= 1
        if depth == 0:
            break
    i += 1
end = i                      # 指向 ']'
block = src[start:end + 1]
print(f"TOOLS 列表范围: {start}..{end}（{len(block)} 字符）")

# 按顶层 '{...}' 切分每个工具定义
items = []
d = 0
cur = None
for idx, ch in enumerate(block):
    if ch == '{':
        if d == 0:
            cur = idx
        d += 1
    elif ch == '}':
        d -= 1
        if d == 0 and cur is not None:
            items.append((cur, idx + 1))
            cur = None
print(f"切出 {len(items)} 个候选定义块")

# 逐个提取 name，去重（保留首次出现）
seen = set()
keep_ranges = []
drop_ranges = []
for a, b in items:
    seg = block[a:b]
    nm = re.search(r'"name":\s*"([a-z_0-9]+)"', seg)
    n = nm.group(1) if nm else None
    if not n:
        keep_ranges.append((a, b))
        continue
    if n in seen:
        drop_ranges.append((a, b, n))
        print(f"  丢弃重复块: {n}")
    else:
        seen.add(n)
        keep_ranges.append((a, b))

if not drop_ranges:
    print("无需删除")
    sys.exit(0)

# 重建列表内容（保留未识别的部分，如注释）
new_block = "[\n"
for a, b in keep_ranges:
    new_block += block[a:b] + ",\n"
new_block += "]"

out = src[:start] + new_block + src[end + 1:]
P.write_text(out, encoding="utf-8")
print(f"\n已清理：删除 {len(drop_ranges)} 个重复块，"
      f"文件 {len(src)} → {len(out)} 字符")

# 复核
names2 = re.findall(r'"name":\s*"([a-z_0-9]+)"', out)
dup2 = {k: v for k, v in Counter(names2).items() if v > 1}
print(f"清理后重复: {dup2 if dup2 else '无'}")
print(f"工具总数: {len(names2)}")

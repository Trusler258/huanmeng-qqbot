#!/usr/bin/env python3
"""Revert unnecessary cmd_analyze changes from commands.py"""
c = open("G:/py/qqbot/modules/commands.py", "r", encoding="utf-8").read()

# Remove the cmd_analyze function block (between "Minecraft 日志分析" and "提示词注入管理")
import re
pattern = r'\n# ─── Minecraft.*?提示词注入管理 ──'
match = re.search(pattern, c, re.DOTALL)
if match:
    replacement = '\n# \u2500\u2500\u2500 \u63d0\u793a\u8bcd\u6ce8\u5165\u7ba1\u7406 \u2500'  # 提示词注入管理 ──
    c = c[:match.start()] + '\n\n' + replacement + c[match.end():]

# Remove from COMMAND_MAP
c = c.replace('\n    "analyze":    cmd_analyze,', '')

# Remove from help
c = c.replace('\n        lines.append("   /~analyze ...      零上下文日志分析")', '')

open("G:/py/qqbot/modules/commands.py", "w", encoding="utf-8").write(c)
import ast
ast.parse(c)
print("OK")

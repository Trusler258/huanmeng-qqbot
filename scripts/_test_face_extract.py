"""验证 extract_inline_face：剥标记 + 表情解析"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.pipeline import extract_inline_face  # noqa: E402

CASES = [
    "……省着点用，别乱烧token[FACE:疲惫]",       # 实测泄漏原文
    "[FACE:开心]今天真不错",                     # 句首标记
    "前半句[FACE:开心]后半句",                    # 中间标记
    "普通句子，没有标记",
    "结尾半截[FACE:开心",                         # 括号不闭合（正则兼容）
    "[FACE:这个关键词肯定不存在]测试",             # 词库未匹配
]

for c in CASES:
    txt, cq = extract_inline_face(c)
    has = "有" if cq else "无"
    left = "[FACE:" in txt
    flag = "  <<< 仍有残留！" if left else ""
    print(f"输入: {c}")
    print(f"  → 文本={txt!r}  表情={has}{flag}")

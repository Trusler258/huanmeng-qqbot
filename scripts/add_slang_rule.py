"""把「看不懂的词就查」规则插进 00_core.md（幂等，可重复执行）。

用法：python scripts/add_slang_rule.py <00_core.md 路径>
"""

import io
import sys
from pathlib import Path

SECTION = """
## slang_unknown
【看不懂的词】
- 群里出现看不懂的缩写/梗/黑话（yyds、牢大、破防、给佬这类），先调 explain_slang 查，再说话。
- 不要猜着解释，也不要说"你这句接得没头没尾""我没听懂你在应哪句"这种点评对方的话。
- 查到了就正常接话，懂了也不用特意解释；查不到就直接问一句"这啥梗"，别解释自己在查。
- 不要为了用而用：知道了不代表要说，除非正好顺口。
"""


def main(p: str) -> int:
    path = Path(p)
    if not path.exists():
        print(f"找不到 {path}")
        return 1
    text = io.open(path, encoding="utf-8").read()
    if "## slang_unknown" in text:
        print("已存在 slang_unknown，跳过")
        return 0
    new = text.rstrip("\n") + "\n" + SECTION
    io.open(path, "w", encoding="utf-8", newline="\n").write(new)
    print(f"已插入，文件现在 {len(new.splitlines())} 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "data/skills/00_core.md"))

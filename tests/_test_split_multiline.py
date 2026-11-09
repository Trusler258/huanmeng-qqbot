"""回归：模型用换行把多段话塞进同一条 replies 时，要拆成多条发。

起因 2026-11-09：官方渠道一条回复
  「就是陪大家聊天呀…也能帮你搜一下～\n不过发文件、改设置、跑命令这些我都不行…」
被整条发出，用户要求拆开（上限 5 句）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.pipeline import _split_multiline_replies as S   # noqa: E402


def main() -> int:
    fails = []

    def ck(name, got, want):
        if got != want:
            fails.append(f"{name}\n  got ={got!r}\n  want={want!r}")

    # 1. 真实事故原文
    src = ["就是陪大家聊天呀，接接梗、说说小观察，你想查点什么的话我也能帮你搜一下～\n"
           "不过发文件、改设置、跑命令这些我都不行，别指望我啦 qwq"]
    ck("换行拆两句", S(src), [
        "就是陪大家聊天呀，接接梗、说说小观察，你想查点什么的话我也能帮你搜一下～",
        "不过发文件、改设置、跑命令这些我都不行，别指望我啦 qwq",
    ])

    # 2. 无换行不动
    ck("单句不动", S(["嗯，在呢"]), ["嗯，在呢"])

    # 3. 连续空行只留实义行
    ck("空行", S(["第一段\n\n\n第二段"]), ["第一段", "第二段"])

    # 4. 代码块整段保留
    code = "看这个\n```\na = 1\nb = 2\n```"
    ck("代码块不拆", S([code]), [code])

    # 5. 行数过多（像列表/排版）不拆，压成单行
    many = "\n".join(f"第{i}行" for i in range(1, 9))
    ck("超过 6 行不拆", S([many]), [" ".join(f"第{i}行" for i in range(1, 9))])

    # 6. 首尾空行
    ck("首尾空行", S(["\n只有一段\n"]), ["只有一段"])

    # 7. 多条各自拆
    ck("混合", S(["甲\n乙", "丙"]), ["甲", "乙", "丙"])

    if fails:
        print("FAIL")
        for f in fails:
            print(" -", f)
        return 1
    print("OK 7/7")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""docx 导出 + 化学式下标 单测。

跑法：python tests/_test_docx_export.py
（需要 python-docx，服务器与本地 venv 都有）
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from services.docx_export import split_runs, md_to_docx, _is_chem   # noqa: E402

PASS = FAIL = 0


def ck(name: str, cond, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}  {extra}")


def joined(text: str) -> tuple[str, str]:
    """返回 (完整文本, 被标下标的片段拼起来)。"""
    runs = split_runs(text)
    return "".join(r[0] for r in runs), "".join(r[0] for r in runs if r[1])


def test_chem_detect():
    print("[1] 化学式识别（真空 vs 误判）")
    for t in ("Na2CO3", "NH4NO3", "Ca3(PO4)2", "CO(NH2)2", "(NH4)2SO4",
              "H2O", "Fe2O3", "K2SO4", "NaOH", "KCl"):
        ck(f"{t} 判为化学式", _is_chem(t) is True)
    # C3PO 形式上是合法组合（C/P/O 都是元素），会当下标处理 —— 星战梗在试卷里
    # 基本不出现，接受这点误判；真要排除得建元素组合白名单，收益不抵成本。
    for t in ("MP3", "B2B", "A4", "3D", "API2"):
        ck(f"{t} 不误判", _is_chem(t) is False, "被当成化学式")


def test_subscript():
    print("[2] 纯数字化学式 → 下标")
    full, subs = joined("物质是Na2CO3吧")
    ck("文本不被吞", full == "物质是Na2CO3吧", full)
    ck("Na2CO3 的 2,3 是下标", subs == "23", subs)

    full, subs = joined("Ca3(PO4)2 与 (NH4)2SO4")
    ck("Ca3(PO4)2 三处下标", subs.startswith("342"), subs)
    ck("(NH4)2SO4 一起被处理", len(subs) == 6, subs)
    ck("(NH4)2SO4 三处下标", "NH4" in full and subs.count("4") >= 2, subs)

    full, subs = joined("这是 MP3 播放器和 B2B 平台")
    ck("MP3 不动", subs == "", subs)
    ck("B2B 不动", "B2B" in full)


def test_unicode_and_latex():
    print("[3] Unicode 下标 / LaTeX 记号")
    full, subs = joined("Na₂CO₃ 和 H₂O")
    ck("Unicode 下标保留", subs == "232", subs)
    ck("文本无乱码", full == "Na2CO3 和 H2O", full)

    full, subs = joined(r"碳酸钠是 $\mathrm{Na_2CO_3}$")
    ck("LaTeX \\mathrm 被剥掉", "mathrm" not in full and "$" not in full, full)
    ck("LaTeX 下标转真下标", subs == "23", subs)

    full, subs = joined("Na_2CO_3 裸写")
    ck("裸 LaTeX 下标也转", subs == "23", subs)

    full, subs = joined("file_name_1 和 item_2")
    ck("普通下划线标识符不动", subs == "" and "file_name_1" in full, (full, subs))


def test_docx_file():
    print("[4] 真写 docx 并回读校验")
    md = """# 单元测试卷

一、选择题

1. 下列物质中属于盐的是（　）
A. 烧碱　B. Na2CO3　C. CaCO3　D. 盐酸

2. 化肥中属于氮肥的是（　）
A. KCl　B. NH4NO3　C. Ca3(PO4)2　D. K2SO4

- 列表项 H2O
"""
    tmp = Path(tempfile.mkdtemp(prefix="docxtest_"))
    out = tmp / "t.docx"
    ok = md_to_docx(md, out, title="单元测试卷")
    ck("导出成功", ok is True and out.exists())
    ck("文件非空", out.stat().st_size > 3000, str(out.stat().st_size if out.exists() else 0))

    from docx import Document
    d = Document(str(out))
    text = "\n".join(p.text for p in d.paragraphs)
    ck("标题只出现一次", text.count("单元测试卷") == 1, text[:80].replace("\n", "|"))
    ck("正文含 Na2CO3", "Na2CO3" in text)
    ck("列表项在", "列表项 H2O" in text)

    subs = [r.text for p in d.paragraphs for r in p.runs if r.font.subscript]
    ck("下标 run 存在", len(subs) >= 8, f"共 {len(subs)} 个: {subs[:10]}")
    ck("下标内容是数字", all(s.isdigit() for s in subs), str(subs[:10]))
    # Na2CO3 的 2 和 3 都应在下标里
    ck("Na2CO3 下标成对", subs.count("2") >= 2 and subs.count("3") >= 2, str(subs))
    # 字体：中文宋体
    from docx.oxml.ns import qn
    east = set()
    for p in d.paragraphs:
        for r in p.runs:
            rf = r._element.find(qn("w:rPr"))
            if rf is not None:
                f = rf.find(qn("w:rFonts"))
                if f is not None:
                    east.add(f.get(qn("w:eastAsia")))
    ck("中文字体已设置", "宋体" in east, str(east))


def test_empty():
    print("[5] 边界")
    ck("空文本不崩", split_runs("") == [])
    ck("纯中文不产生下标", joined("食盐______；纯碱______")[1] == "")


if __name__ == "__main__":
    test_chem_detect()
    test_subscript()
    test_unicode_and_latex()
    test_docx_file()
    test_empty()
    print(f"\n结果: {PASS} 通过 / {FAIL} 失败")
    raise SystemExit(0 if FAIL == 0 else 1)

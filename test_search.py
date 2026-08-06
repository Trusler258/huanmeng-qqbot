"""
Agent 搜索验证脚本
用法: python test_search.py [查询]
不传参数则跑预设用例
输出写入 test_search_result.txt
"""
import sys
import time
from pathlib import Path

try:
    from core.logger import init_logger
    init_logger()
except Exception:
    pass

from modules.web_search import agent_search, preprocess_query

CASES = [
    "GeForce RTX 5090 D 参数",
    "鹤壁市淇滨区2025-2026学年下期期末教学质量检测卷八年级数学",
    "RTX 4090 显卡价格",
    "Python asyncio 用法",
]

OUT_PATH = Path(__file__).parent / "test_search_result.txt"


def run_one(query, out):
    out.write(f"\n{'='*60}\n")
    out.write(f"查询: {query}\n")
    out.write('='*60 + "\n")

    opt, main, kws, year = preprocess_query(query)
    out.write(f"优化词: {opt}\n")
    out.write(f"主体词: {main}\n")
    out.write(f"关键词: {kws}\n")
    out.write(f"年份: {year}\n")
    out.write('-'*60 + "\n")

    t0 = time.time()
    try:
        result = agent_search(query, limit=5, deep_fetch=False)
    except Exception as e:
        result = f"[异常] {e}"
    elapsed = time.time() - t0

    if result:
        out.write(result + "\n")
    else:
        out.write("无结果\n")
    out.write(f"\n耗时: {elapsed:.1f}s\n")
    out.flush()


def main():
    with open(OUT_PATH, "w", encoding="utf-8") as out:
        if len(sys.argv) > 1:
            query = " ".join(sys.argv[1:])
            run_one(query, out)
        else:
            for q in CASES:
                run_one(q, out)
            out.write(f"\n{'='*60}\n全部用例完成\n")
    print(f"结果已写入: {OUT_PATH}")


if __name__ == "__main__":
    main()

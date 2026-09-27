"""线上端到端验证：LLM 的 FC 路径（get_tool_schemas + execute_tool）能正常用 run_code。

在服务器 /root/bot 下运行：python3 tests/_verify_run_code_e2e.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.tools import execute_tool, get_tool_schemas  # noqa: E402
from services.llm import _CMD_DESC  # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  OK   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}   {extra}")


async def main():
    names = [(t.get("function") or {}).get("name") for t in get_tool_schemas()]
    print(f"[工具清单] 共 {len(names)} 个")
    check("发给 LLM 的工具清单含 run_code", "run_code" in names)
    check("工具清单不再含 calc", "calc" not in names)
    check("_CMD_DESC 已改名 run_code", "run_code" in _CMD_DESC and "calc" not in _CMD_DESC)

    print("[E2E] execute_tool — Python")
    r = await execute_tool("run_code", {"code": "print(6*7)"}, 1, 1, "tester", False, 10000)
    check("Python 6*7 → 42", (r or "").strip() == "42", repr(r))

    print("[E2E] execute_tool — C++")
    cpp = ('#include <iostream>\n'
           'int main(){for(int i=1;i<=5;i++)std::cout<<i*i<<(i<5?" ":"");return 0;}')
    r = await execute_tool("run_code", {"language": "cpp", "code": cpp}, 1, 1, "tester", False, 10000)
    check("C++ 5 个平方 → 1 4 9 16 25", (r or "").strip() == "1 4 9 16 25", repr(r))

    print("[E2E] execute_tool — 危险代码 / 默认语言")
    r = await execute_tool("run_code", {"code": "import socket"}, 1, 1, "tester", False, 10000)
    check("危险代码被拦", "禁止的操作" in (r or ""), repr(r))
    r = await execute_tool("run_code", {"code": "print(sum(range(101)))"}, 1, 1, "tester", False, 10000)
    check("缺 language 时默认 Python", (r or "").strip() == "5050", repr(r))

    print(f"\n=== 结果: {PASS} passed, {FAIL} failed ===")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

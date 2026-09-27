"""run_code 工具本地自测（Python 侧；C++ 在服务器验，本机无 g++）。

覆盖：
  · schema 已改名 run_code、旧 calc 已消失
  · Python 正常执行 / 语法错误 / 超时
  · 危险代码拦截（联网、起进程、读敏感路径）
  · `import os` 仍可用，但 env 已清洗（不泄漏 DEEPSEEK_KEY）
  · stdin 传入、产物列出、临时目录清理
"""
import asyncio
import glob
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.tools import (  # noqa: E402
    TOOLS, _TOOL_CMD_MAP, TOOL_TIMEOUTS, _run_code,
)
import core.tools as _tools  # noqa: E402

PASS = FAIL = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  OK   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}   {extra}")


def _names() -> list[str]:
    return [(t.get("function") or {}).get("name") for t in TOOLS]


async def main() -> int:
    print("=== 1. schema / 注册表 ===")
    names = _names()
    check("TOOLS 含 run_code", "run_code" in names, str(names))
    check("TOOLS 不再含 calc", "calc" not in names, str(names))
    check("run_code 在 _TOOL_CMD_MAP（空实现标记）",
          _TOOL_CMD_MAP.get("run_code") == "", repr(_TOOL_CMD_MAP.get("run_code")))
    check("calc 已从 _TOOL_CMD_MAP 移除", "calc" not in _TOOL_CMD_MAP)
    check("TOOL_TIMEOUTS 有 run_code 且 > 沙箱时限",
          TOOL_TIMEOUTS.get("run_code", 0) > _tools._CODE_TIMEOUT_CPP,
          repr(TOOL_TIMEOUTS.get("run_code")))
    # schema 结构
    schema = next(t for t in TOOLS if t["function"]["name"] == "run_code")
    props = schema["function"]["parameters"]["properties"]
    check("schema 有 language/code/files/stdin",
          {"language", "code", "files", "stdin"} <= set(props), str(list(props)))
    check("language 枚举 python/cpp",
          props["language"].get("enum") == ["python", "cpp"], repr(props["language"]))

    print("=== 2. Python 正常执行 ===")
    r = await _run_code("python", "print(1/3)")
    check("1/3 输出正确", r.strip() == str(1 / 3), repr(r))

    r = await _run_code("", "print(2**100)")  # 空 language 走默认
    check("默认 language=python", r.strip() == str(2 ** 100), repr(r))

    code_eq = (
        "from fractions import Fraction\n"
        "import itertools\n"
        "# 解 2x+3y=12, x-y=1\n"
        "for x in range(-50, 51):\n"
        "    for y in range(-50, 51):\n"
        "        if 2*x+3*y == 12 and x-y == 1:\n"
        "            print(f'x={x} y={y}')\n"
    )
    r = await _run_code("python", code_eq)
    check("方程组求解 x=3 y=2", "x=3" in r and "y=2" in r, repr(r))

    print("=== 3. stdin ===")
    r = await _run_code("python", "import sys; print(sys.stdin.read().strip().upper())",
                        None, "abc123")
    check("stdin 传给程序", r.strip() == "ABC123", repr(r))

    print("=== 4. 报错 / 超时 ===")
    r = await _run_code("python", "print(undefined_name)")
    check("语法/运行错误返回失败并带 stderr",
          "执行失败" in r and "NameError" in r, repr(r))

    r = await _run_code("python", "")
    check("空代码给友好提示", "未提供代码" in r, repr(r))

    _old_t = _tools._CODE_TIMEOUT_PY
    _tools._CODE_TIMEOUT_PY = 2.0
    try:
        r = await _run_code("python", "while True:\n    pass")
    finally:
        _tools._CODE_TIMEOUT_PY = _old_t
    check("死循环被超时强杀", "超时" in r, repr(r))

    print("=== 5. 危险代码拦截 ===")
    for label, bad in [
        ("联网 socket", "import socket\nprint(socket.gethostname())"),
        ("联网 urllib", "from urllib.request import urlopen\nprint(urlopen('http://x').read())"),
        ("起进程 subprocess", "import subprocess\nprint(subprocess.run(['ls']))"),
        ("os.system", "import os\nos.system('id')"),
        ("删目录 rmtree", "import shutil\nshutil.rmtree('/tmp')"),
        ("读 .env 敏感路径", "print(open('/root/bot/config/.env').read())"),
        ("读私钥", "print(open('/home/u/.ssh/id_rsa').read())"),
    ]:
        r = await _run_code("python", bad)
        check(f"拦截 {label}", "禁止的操作" in r, repr(r))

    print("=== 6. import os 可用但 env 已清洗 ===")
    os.environ["DEEPSEEK_KEY"] = "SECRET_LEAK_TEST_VALUE"
    r = await _run_code("python", "import os; print('KEY=' + os.environ.get('DEEPSEEK_KEY', 'NONE'))")
    check("os.environ 拿不到 DEEPSEEK_KEY（已清洗）",
          "SECRET_LEAK_TEST_VALUE" not in r and "KEY=NONE" in r, repr(r))
    r = await _run_code("python", "import os; print('CWD=' + os.getcwd()); print('PATH_OK=' + str(bool(os.environ.get('PATH'))))")
    check("import os / os.getcwd / PATH 仍正常（没一刀切封 os）",
          "PATH_OK=True" in r, repr(r))

    print("=== 7. 产物列出 + 临时目录清理 ===")
    tmp_root = tempfile.gettempdir()
    before = set(glob.glob(os.path.join(tmp_root, "bot_sandbox_*")))
    r = await _run_code("python", "open('out.txt','w').write('hi')\nprint('done')")
    after = set(glob.glob(os.path.join(tmp_root, "bot_sandbox_*")))
    check("产物文件名被列出", "out.txt" in r, repr(r))
    check("临时目录已清理（无残留）", after <= before,
          f"新增 {sorted(after - before)}")

    print("=== 8. C++（有 g++ 则真编译执行，否则验降级文案）===")
    import shutil
    has_gpp = shutil.which("g++") is not None
    hello = "#include <iostream>\nint main(){std::cout<<\"hi\"<<std::endl;return 0;}"
    r = await _run_code("cpp", hello)
    if has_gpp:
        check("C++ 单文件真编译并输出 hi", r.strip() == "hi", repr(r))
    else:
        check("C++ 无 g++ 时降级提示", "C++" in r and ("g++" in r or "执行失败" in r), repr(r))

    # C++ 多文件 + stdin
    files = {
        "main.cpp": "#include <iostream>\n#include \"util.h\"\n"
                    "int main(){int n;std::cin>>n;std::cout<<dbl(n)<<std::endl;return 0;}",
        "util.h": "int dbl(int x);",
        "util.cpp": "#include \"util.h\"\nint dbl(int x){return x*2;}",
    }
    r = await _run_code("cpp", "", files, "21")
    if has_gpp:
        check("C++ 多文件编译 + stdin 输出 42", r.strip() == "42", repr(r))
    else:
        check("C++ 多文件在无 g++ 时也走完流程", isinstance(r, str) and r, repr(r))

    # C++ 编译错误
    r = await _run_code("cpp", "int main(){ this is not cpp }")
    if has_gpp:
        check("C++ 编译错误被捕获并带 error",
              "执行失败" in r or "编译失败" in r, repr(r))

    # C++ 危险代码拦截
    r = await _run_code("cpp", '#include <cstdlib>\nint main(){system("ls");}')
    check("C++ 里 system() 被拦截", "禁止的操作" in r, repr(r))

    r = await _run_code("cpp", "")
    check("C++ 无源码给提示", "未提供" in r, repr(r))

    print(f"\n=== 结果: {PASS} passed, {FAIL} failed ===")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

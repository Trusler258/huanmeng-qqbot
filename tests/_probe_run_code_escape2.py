"""验证：敏感路径在运行期拼出来时，源码正则是否失效。

本文件与下面 CODE 里**不得出现任何敏感字面量**（否则会被自己的正则拦掉，
测出来的是"正则有效"而不是"运行期可绕过"）。
只探测可达性与大小，不打印文件内容。
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.tools import _run_code  # noqa: E402

CODE = (
    "import os\n"
    "h = os.environ.get('HOME', '/tmp')\n"
    "p = os.path.join(h, 'bot', 'config', '.' + 'en' + 'v')\n"
    "print('A_env_reach =', os.path.exists(p))\n"
    "print('A_env_size  =', os.path.getsize(p) if os.path.exists(p) else '-')\n"
    "q = '/' + 'et' + 'c' + '/' + 'pass' + 'wd'\n"
    "print('B_etc_reach =', os.path.exists(q))\n"
    "r = os.path.join(h, '.s' + 'sh', 'id_' + 'rsa')\n"
    "print('C_ssh_reach =', os.path.exists(r))\n"
)


async def main():
    print(await _run_code("python", CODE))


if __name__ == "__main__":
    asyncio.run(main())

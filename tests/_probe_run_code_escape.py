"""探测 run_code 沙箱的逃逸面（只探查可达性，不输出任何密钥内容）。

背景：_SANDBOX_BLOCK_RE 扫的是**源码文本**，而路径可以在运行期算出来，
所以「字面量里没有 /root/」≠「读不到 /root」。

在服务器 /root/bot 下运行：python3 tests/_probe_run_code_escape.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.tools import _run_code  # noqa: E402

CASES = [
    ("A. 靠 expanduser 求值绕过正则（源码无 /root/ 字面量）",
     "import os\n"
     "print('HOME   =', os.environ.get('HOME'))\n"
     "print('TILDE  =', os.path.expanduser('~'))\n"
     "print('可达配置文件 =', os.path.exists(os.path.expanduser('~/bot/config/bot_config.toml')))"),
    ("B. 直接写敏感路径（对照组，应被拦）",
     "print(open('/root/bot/config/.env').read())"),
    ("C. 走 HOME 环境变量拼路径（不含敏感子串）",
     "import os\n"
     "p = os.path.join(os.environ.get('HOME','/tmp'), 'bot', 'config', 'bot_config.toml')\n"
     "print('拼接路径可达 =', os.path.exists(p))"),
]


async def main():
    for title, code in CASES:
        print(f"\n=== {title} ===")
        try:
            print(await _run_code("python", code))
        except Exception as e:  # noqa: BLE001
            print("执行异常:", e)


if __name__ == "__main__":
    asyncio.run(main())

"""服务器端面板初始化脚本（一次性）

在服务器上跑：
    cd /root/bot && python3 deploy/panel_init.py [初始密码]

未给密码时自动生成一个强密码并打印（只打印这一次）。
"""

import secrets
import string
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from panel import config  # noqa: E402


def gen_password(n: int = 16) -> str:
    """生成强密码：字母+数字+符号，排除易混淆字符"""
    alphabet = (
        string.ascii_letters.replace("O", "").replace("l", "").replace("I", "")
        + string.digits.replace("0", "").replace("1", "")
        + "!@#$%^&*-_=+"
    )
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(n))
        if (any(c.islower() for c in pw) and any(c.isupper() for c in pw)
                and any(c.isdigit() for c in pw) and any(not c.isalnum() for c in pw)):
            return pw


def main():
    if config.SECRET_FILE.exists():
        print(f"[!] 密钥文件已存在: {config.SECRET_FILE}")
        print("    改密码请用: python3 -m panel.cli passwd")
        print("    查看状态:   python3 -m panel.cli status")
        return

    pw = sys.argv[1] if len(sys.argv) > 1 else gen_password()
    if len(pw) < 8:
        print("[x] 密码至少 8 位")
        sys.exit(1)

    config._generate_secret_file(pw)
    print("[+] 面板已初始化")
    print(f"    密钥文件: {config.SECRET_FILE}")
    print(f"    初始密码: {pw}")
    print("    ^^^ 请立即保存到密码管理器，并尽快用面板内的『修改密码』换掉")
    cfg = config.load()
    print(f"    监听: {cfg.bind_host}:{cfg.port}")


if __name__ == "__main__":
    main()

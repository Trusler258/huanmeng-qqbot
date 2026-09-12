"""面板管理 CLI

用法：
    python -m panel.cli init            初始化（生成密钥文件 + 设置密码）
    python -m panel.cli passwd          改密码（会同时使所有旧 token 失效）
    python -m panel.cli status          查看面板状态
    python -m panel.cli gen-secret      只重新生成 JWT 密钥
"""

from __future__ import annotations

import getpass
import sys

from panel import config


def _read_password(prompt: str = "请输入面板密码: ") -> str:
    pw = getpass.getpass(prompt)
    if len(pw) < 8:
        print("❌ 密码至少 8 位")
        sys.exit(1)
    pw2 = getpass.getpass("再输一次确认: ")
    if pw != pw2:
        print("❌ 两次输入不一致")
        sys.exit(1)
    return pw


def cmd_init():
    if config.SECRET_FILE.exists():
        print(f"⚠️ 密钥文件已存在：{config.SECRET_FILE}")
        print("   要改密码请用: python -m panel.cli passwd")
        return
    pw = _read_password()
    config._generate_secret_file(pw)
    print(f"✅ 面板已初始化")
    print(f"   密钥文件: {config.SECRET_FILE} (chmod 600)")
    print(f"   监听:     {config.load().bind_host}:{config.load().port}")
    print(f"   下一步:   systemctl restart panel.service")


def cmd_passwd():
    if not config.SECRET_FILE.exists():
        print("❌ 尚未初始化，请先执行: python -m panel.cli init")
        return
    import bcrypt
    from datetime import datetime

    pw = _read_password("请输入新密码: ")
    pw_hash = bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode()

    text = config.SECRET_FILE.read_text(encoding="utf-8")
    import re
    text = re.sub(r'password_hash\s*=\s*".*?"', f'password_hash = "{pw_hash}"', text)

    # session_version +1 → 所有已签发 token 立即失效
    m = re.search(r'session_version\s*=\s*(\d+)', text)
    if m:
        new_v = int(m.group(1)) + 1
        text = text[:m.start()] + f"session_version = {new_v}" + text[m.end():]
    else:
        text = re.sub(r'(token_ttl_hours\s*=\s*\d+)', r'\1\nsession_version = 2', text)

    config.SECRET_FILE.write_text(text, encoding="utf-8")
    try:
        import os
        os.chmod(config.SECRET_FILE, 0o600)
    except Exception:
        pass
    print(f"✅ 密码已更新（{datetime.now():%Y-%m-%d %H:%M:%S}）")
    print("   所有旧的登录状态已失效，请重新登录")


def cmd_gen_secret():
    """重新生成 JWT 密钥——所有 token 失效，不改密码"""
    import secrets
    import re

    if not config.SECRET_FILE.exists():
        print("❌ 尚未初始化")
        return
    text = config.SECRET_FILE.read_text(encoding="utf-8")
    new_secret = secrets.token_urlsafe(48)
    text = re.sub(r'jwt_secret\s*=\s*".*?"', f'jwt_secret = "{new_secret}"', text)
    config.SECRET_FILE.write_text(text, encoding="utf-8")
    print("✅ JWT 密钥已重置，全部现有登录状态失效")


def cmd_status():
    cfg = config.load()
    print("── 幻梦面板状态 ──")
    print(f"  密钥文件:   {'✅ 已存在' if config.SECRET_FILE.exists() else '❌ 不存在'}")
    print(f"  密码已设置: {'✅' if cfg.password_hash else '❌'}")
    print(f"  JWT 密钥:   {'✅' if cfg.jwt_secret else '❌'}")
    print(f"  监听地址:   {cfg.bind_host}:{cfg.port}")
    print(f"  Token 有效期: {cfg.token_ttl_hours} 小时")
    print(f"  会话版本:   {cfg.session_version}")
    print(f"  审计日志:   {config.AUDIT_FILE}")
    if config.AUDIT_FILE.exists():
        n = sum(1 for _ in open(config.AUDIT_FILE, encoding="utf-8"))
        print(f"  审计条数:   {n}")


if __name__ == "__main__":
    cmds = {
        "init": cmd_init,
        "passwd": cmd_passwd,
        "gen-secret": cmd_gen_secret,
        "status": cmd_status,
    }
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        print(__doc__)
        sys.exit(1)
    cmds[sys.argv[1]]()

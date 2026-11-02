"""运行主机环境（Linux / Windows）差异的唯一出口。

## 起因（2026-11-02）

主服务器断电离线，启用第二台备用机顶上。那台是 Windows、4G 内存、没有公网 IP。
原先 `Path("/www/wwwroot/bot-img")` 这种 Linux 绝对路径写死在 qq_official_bot.py 里，
到了 Windows 上会被解析成 `\\www\\wwwroot\\bot-img`（当前盘符下的相对路径）——
**不报错、也不落到预期位置**，属于最难查的那类问题。

所以把平台差异收口到这里：其它模块统一 import 本模块，
不再各写 `os.name` / `sys.platform` 判断。

## 目前真正需要分平台的只有图床

    Linux     /www/wwwroot/bot-img      ← nginx `location ^~ /bot-img/` 直出
    Windows   <项目>/bot-img            ← 本地 http.server + cloudflared quick tunnel

其余平台差异（进程组、资源限制、SIGTERM）散在 `core/sandbox.py` 与 `main.py`，
已由 `os.name != "posix"` / `sys.platform != "win32"` 就地守卫，暂不收口——
为它们加抽象层只会让改动面变大，收益为零。

## 覆盖方式

两个环境变量优先于平台默认值，服务器上不设则行为与改动前完全一致：

    BOT_IMG_DIR   图床落盘目录
    BOT_IMG_BASE  图床对外地址（末尾斜杠自动去掉）
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 项目根目录（本文件在 core/ 下，往上一层）
_ROOT = Path(__file__).resolve().parent.parent

# ── 平台判定（全项目唯一一处）──────────────────────────────
IS_WINDOWS: bool = os.name == "nt"
IS_LINUX: bool = sys.platform.startswith("linux")
PLATFORM: str = "windows" if IS_WINDOWS else ("linux" if IS_LINUX else sys.platform)

# ── 图床默认值 ────────────────────────────────────────────
if IS_WINDOWS:
    # 备用机没有 /www，也没有公网 IP。目录放项目内，地址只够本机自测，
    # 真正对外必须用 BOT_IMG_BASE 填 cloudflared 给的 trycloudflare 域名。
    DEFAULT_IMG_DIR = _ROOT / "bot-img"
    DEFAULT_IMG_BASE = "http://127.0.0.1:9871/bot-img"
else:
    DEFAULT_IMG_DIR = Path("/www/wwwroot/bot-img")
    # 用 qq. 子域：它由 ddns-go 跟着家宽 IP 走（at. 子域 CNAME 到 mghost.site，
    # 解析到别人机器上，拉图必 404 —— 2026-10-31 踩过）
    DEFAULT_IMG_BASE = "http://qq.01240820.xyz:9871/bot-img"

IMG_DIR: Path = Path(os.environ.get("BOT_IMG_DIR") or DEFAULT_IMG_DIR)
IMG_BASE: str = (os.environ.get("BOT_IMG_BASE") or DEFAULT_IMG_BASE).rstrip("/")

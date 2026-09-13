"""面板自身配置

与 Bot 的 config/bot_config.toml **完全独立**——面板崩了不影响 Bot，
Bot 配置格式变了也不影响面板。

密钥文件：panel/secret.toml（chmod 600，已加入 .gitignore）
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

# ── TOML 解析器选择：为什么优先用第三方 `toml` 而不是标准库 `tomllib` ──
#
# ⚠️ 这不是随便选的，是踩出来的：
#
# 服务器的 `config/bot_config.toml` 里有**裸中文键名**（`bot的名字 = "幻梦"`、
# `回复兴趣 = 8`），而 TOML 1.0 规范要求含非 ASCII 的键必须加引号。
#
#   tomllib（标准库，严格）→ 直接报错 "Expected '=' after a key"
#   toml  （PyPI 包，宽松）→ 正常解析
#
# 而 bot 自己的 `core/config.py` 用的是 `import toml`（宽松版），
# 所以它一直跑得好好的。面板若坚持用 tomllib，就会出现
# "bot 能读、面板读不了"的荒唐局面 —— 面板必须跟 bot 保持一致。
#
# 优先级：toml（跟 bot 一致）→ tomllib → tomli → 正则兜底
try:
    import toml as _toml_loose          # 与 core/config.py 保持一致
except ModuleNotFoundError:
    _toml_loose = None

try:
    import tomllib as _toml_strict      # Python 3.11+
except ModuleNotFoundError:
    try:
        import tomli as _toml_strict    # type: ignore  # Python 3.10 的替代品
    except ModuleNotFoundError:
        _toml_strict = None             # type: ignore


def toml_loads(text: str) -> dict:
    """按与 bot 一致的宽松规则解析 toml。

    抛 ValueError 表示两个解析器都不认这个文件。
    """
    if _toml_loose is not None:
        try:
            return _toml_loose.loads(text)
        except Exception as e:
            last = e
    else:
        last = RuntimeError("未安装 toml 包")

    if _toml_strict is not None:
        try:
            return _toml_strict.loads(text)
        except Exception as e:
            last = e

    raise ValueError(str(last)[:300])


def toml_available() -> str:
    """返回当前可用的解析器名字，供 /api/system/info 展示"""
    if _toml_loose is not None:
        return "toml(宽松，与 bot 一致)"
    if _toml_strict is not None:
        return "tomllib(严格)"
    return "无(仅正则兜底)"


# 兼容旧引用（panel/routers/config_editor.py 曾用 panel_config.tomllib）
tomllib = _toml_loose or _toml_strict

PANEL_API_VERSION = "v2.3.0"

# 项目根目录（panel/ 的上一级）
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
PANEL_DIR = ROOT / "panel"
SECRET_FILE = PANEL_DIR / "secret.toml"
AUDIT_FILE = PANEL_DIR / "audit.log"

# 前端 dev server 跨域白名单（生产走 nginx 同源，不需要）
CORS_ORIGINS = [
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:5174",
    "http://localhost:5174",
]


@dataclass
class PanelConfig:
    bind_host: str = "127.0.0.1"
    port: int = 59300

    # 认证
    password_hash: str = ""          # bcrypt 哈希，绝不存明文
    jwt_secret: str = ""             # HS256 密钥
    token_ttl_hours: int = 12
    session_version: int = 1         # 改密后 +1，旧 token 立即失效

    # 限速
    login_max_fail: int = 5
    login_lock_seconds: int = 300

    # 允许在面板中直接编辑的文件白名单（相对 ROOT 的路径 glob）
    editable_globs: list[str] = field(default_factory=lambda: [
        "data/features.json",
        "data/skills/*.md",
        "data/notes/*.md",
        "data/self_knowledge.md",
    ])
    # 允许在线编辑的 config 文件（强脱敏 + 二次确认）
    sensitive_config_globs: list[str] = field(default_factory=lambda: [
        "config/lang.toml",
        "config/bot_config.toml",
    ])

    @property
    def secret_configured(self) -> bool:
        return bool(self.password_hash and self.jwt_secret)


def _parse_secret_toml(text: str) -> dict:
    """解析 secret.toml。

    走标准 tomllib（3.11+）/ tomli（3.10 若装了）；
    都没有时用极简正则兜底——密钥文件是本项目自己写的，
    格式固定为 `key = "value"`，正则足够且不引入新依赖。
    """
    if tomllib is not None:
        try:
            return tomllib.loads(text)
        except Exception:
            pass

    # ── 极简兜底解析（只认 [section] 与 key = "value" / key = 123）──
    import re

    out: dict = {}
    section = out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = out.setdefault(line[1:-1].strip(), {})
            continue
        m = re.match(r'^([A-Za-z0-9_\-]+)\s*=\s*(.+)$', line)
        if not m:
            continue
        k, raw = m.group(1), m.group(2).strip()
        if raw.startswith('"') and raw.endswith('"'):
            section[k] = raw[1:-1]
        elif raw.startswith("'") and raw.endswith("'"):
            section[k] = raw[1:-1]
        else:
            try:
                section[k] = int(raw)
            except ValueError:
                section[k] = raw
    return out


def _read_secret_file() -> dict:
    if not SECRET_FILE.exists():
        return {}
    try:
        return _parse_secret_toml(SECRET_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _generate_secret_file(password: str) -> PanelConfig:
    """首次初始化：生成密钥文件并写入密码哈希"""
    import bcrypt

    pw_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode()
    jwt_secret = secrets.token_urlsafe(48)

    SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    SECRET_FILE.write_text(
        "# 幻梦面板密钥文件 —— 切勿提交到 git，切勿外传\n"
        "# 修改密码：python -m panel.cli passwd\n\n"
        "[panel]\n"
        f'password_hash = "{pw_hash}"\n'
        f'jwt_secret = "{jwt_secret}"\n'
        "token_ttl_hours = 12\n"
        "session_version = 1\n",
        encoding="utf-8",
    )
    try:
        os.chmod(SECRET_FILE, 0o600)
    except Exception:
        pass
    return PanelConfig(password_hash=pw_hash, jwt_secret=jwt_secret)


@lru_cache(maxsize=1)
def load() -> PanelConfig:
    """加载面板配置（带缓存；改配置后重启服务生效）"""
    sec = _read_secret_file().get("panel", {})
    cfg = PanelConfig(
        bind_host=os.environ.get("PANEL_BIND_HOST", "127.0.0.1"),
        port=int(os.environ.get("PANEL_PORT", "59300")),
        password_hash=sec.get("password_hash", ""),
        jwt_secret=sec.get("jwt_secret", ""),
        token_ttl_hours=int(sec.get("token_ttl_hours", 12)),
        session_version=int(sec.get("session_version", 1)),
    )
    return cfg


def reload() -> PanelConfig:
    load.cache_clear()
    return load()

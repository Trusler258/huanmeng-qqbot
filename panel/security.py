"""路径安全 + 敏感信息脱敏

这两件事必须集中在一处做，散落各处必然漏。所有涉及文件读写的接口
都必须先过 `safe_path()`，所有回显配置/日志的接口都必须先过 `sanitize()`。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from panel.config import DATA_DIR, ROOT


# ── 路径安全 ──────────────────────────────────────────────

class PathEscape(Exception):
    """路径逃逸尝试"""


def safe_path(rel: str, roots: list[Path] | None = None) -> Path:
    """把用户给的相对路径解析成真实路径，并校验没有跳出允许的根目录。

    拒绝：绝对路径、`..`、软链接跳出、非白名单根。

    >>> safe_path("data/notes/123.md")   # ok
    >>> safe_path("../../etc/passwd")    # raise PathEscape
    """
    if not rel or rel.startswith("/") or rel.startswith("\\"):
        raise PathEscape("不允许绝对路径")
    if "\x00" in rel:
        raise PathEscape("路径含非法字符")
    # Windows 盘符
    if re.match(r"^[a-zA-Z]:", rel):
        raise PathEscape("不允许盘符路径")

    allow = roots if roots is not None else [ROOT]
    target = (ROOT / rel).resolve()

    for base in allow:
        try:
            base_r = base.resolve()
        except Exception:
            continue
        if target == base_r or base_r in target.parents:
            # resolve() 已展开软链接，此处比对的是真实路径
            return target

    raise PathEscape(f"路径超出允许范围: {rel}")


def assert_in_data(rel: str) -> Path:
    """限定只能碰 data/ 下的文件"""
    return safe_path(rel, roots=[DATA_DIR])


def glob_match(rel: str, patterns: list[str]) -> bool:
    """判断相对路径是否命中 glob 白名单"""
    from fnmatch import fnmatch
    rel_norm = rel.replace("\\", "/").lstrip("./")
    return any(fnmatch(rel_norm, p) for p in patterns)


# ── 敏感信息脱敏 ──────────────────────────────────────────

# 顺序敏感：先长后短，避免 sk- 规则吃掉 sk-ant-
_PATTERNS: list[tuple[re.Pattern, str]] = [
    # LLM / 各类 API Key
    (re.compile(r"sk-[A-Za-z0-9_\-]{8,}"), "[密钥]"),
    (re.compile(r"(?i)(api[_-]?key|apikey|access[_-]?key)\s*[=:]\s*[\"']?([A-Za-z0-9_\-]{12,})[\"']?"), None),
    # Bearer / token
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{12,}"), "Bearer [凭据]"),
    (re.compile(r"(?i)(token|secret|passwd|password|passwd|pwd)\s*[=:]\s*[\"']?([^\s\"',}]{6,})[\"']?"), None),
    # QQ 邮箱授权码
    (re.compile(r"(?i)(smtp|mail)[_a-z]*[=:]\s*[\"']?([a-z0-9]{16})[\"']?"), None),
    # IP（排除 127.0.0.1 / 0.0.0.0 / 内网回环）
    (re.compile(r"\b(?!127\.0\.0\.1|0\.0\.0\.0)((?:\d{1,3}\.){3}\d{1,3})\b"), "[IP]"),
    # 域名
    (re.compile(r"\b([a-z0-9][a-z0-9\-]{1,60}\.(?:com|cn|net|org|xyz|top|io|dev|me|dpdns\.org))\b", re.I), "[域名]"),
]

# 邮箱放在最后单独处理：先整封替换成 [邮箱]，
# 避免先被「域名」规则吃掉后缀、导致本地部分残留（如 aaa@[域名]）
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")

# 明确要保留的敏感键名（值一律打码，无论格式）
_SECRET_KEYS = re.compile(
    r"(?i)(key|token|secret|passwd|password|pwd|credential|auth|cookie|session_id)"
)


def sanitize_text(text: str) -> str:
    """对任意文本做脱敏，用于日志/配置回显"""
    # 邮箱必须先整体吃掉，否则本地部分会残留
    out = _EMAIL.sub("[邮箱]", text)
    for pat, repl in _PATTERNS:
        if repl is None:
            # 键值对：保留键名，打码值
            def _kv(m: re.Match) -> str:
                return f"{m.group(1)}=[已打码]"
            out = pat.sub(_kv, out)
        else:
            out = pat.sub(repl, out)
    return out


def sanitize_obj(obj: Any) -> Any:
    """递归脱敏 dict / list / str。

    规则：
      - key 命中 _SECRET_KEYS → 值替换为 "[已打码]"
      - 字符串值 → sanitize_text()
    """
    if isinstance(obj, dict):
        out: dict = {}
        for k, v in obj.items():
            if isinstance(k, str) and _SECRET_KEYS.search(k):
                # 允许回显"是否已配置"，但不回显内容
                if v in (None, "", False):
                    out[k] = v
                else:
                    out[k] = "[已打码]"
            else:
                out[k] = sanitize_obj(v)
        return out
    if isinstance(obj, list):
        return [sanitize_obj(x) for x in obj]
    if isinstance(obj, str):
        return sanitize_text(obj)
    return obj


# ── 原子写 ────────────────────────────────────────────────

def atomic_write_text(path: Path, content: str, *, backup: bool = True) -> Path | None:
    """原子写文本文件。先写临时文件再 rename，避免写一半损坏。

    backup=True 时，若目标已存在，先在同目录留一份 .bak 副本。
    返回备份文件路径（无备份则 None）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    bak: Path | None = None
    if backup and path.exists():
        from datetime import datetime
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        bak = path.with_name(f"{path.name}.bak_{stamp}")
        bak.write_bytes(path.read_bytes())

    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)
    return bak


def atomic_write_json(path: Path, obj: Any, *, backup: bool = True) -> Path | None:
    return atomic_write_text(
        path, json.dumps(obj, ensure_ascii=False, indent=2), backup=backup
    )


def read_json(path: Path, default: Any = None) -> Any:
    """读 JSON，失败时返回 default（不抛异常，只读接口不该 500）"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default

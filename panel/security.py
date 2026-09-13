"""路径安全 + 敏感信息脱敏

这两件事必须集中在一处做，散落各处必然漏。所有涉及文件读写的接口
都必须先过 `safe_path()`，所有回显配置/日志的接口都必须先过 `sanitize()`。
"""

from __future__ import annotations

import json
import re
import threading
import uuid
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

    ⚠️ 这个函数**不登记操作栈**。需要参与崩溃自愈的写操作请用
    `tracked_write_text()`（或写完手动调 `push_op()`），否则回滚时找不到它。
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


# ── 操作栈（崩溃自愈的基础） ──────────────────────────────
#
# 目的：bot 改崩了要能自动退回上一步。为此必须记住
#   "谁在什么时候把哪个文件从什么样子改成了什么样子"。
#
# 只记**能回滚的**操作：写文件（有 .bak 就能还原）。
# 积分增减、开关切换这类业务数据不记 —— 它们改不崩 bot，
# 回滚它们反而把用户想要的效果抹掉。
#
# 栈是内存态 + 落盘双份：
#   - 内存给当前进程快速读写
#   - 落盘给面板重启后仍能回滚（否则重启一次自愈就失效了）

_OPS_FILE = DATA_DIR / "panel_ops.json"
_OPS_MAX = 50          # 只留最近 50 次，够回溯了
_ops_lock = threading.RLock()


def _load_ops() -> list[dict]:
    try:
        raw = _OPS_FILE.read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_ops(ops: list[dict]) -> None:
    try:
        _OPS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _OPS_FILE.with_name(f".{_OPS_FILE.name}.tmp")
        tmp.write_text(
            json.dumps(ops[-_OPS_MAX:], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(_OPS_FILE)
    except Exception:
        # 落盘失败不影响主流程：内存栈还在，只是重启后丢
        pass


def push_op(
    *,
    kind: str,
    target: str,
    backup: Path | None,
    note: str = "",
) -> dict:
    """登记一次可回滚的写操作。返回这条记录。

    kind   — 操作类型，如 "write_config" / "write_prompt" / "delete_file"
    target — 被改动的文件（相对 ROOT 的路径，便于展示）
    backup — 改前的备份文件；None 表示原本不存在（回滚 = 删除）
    """
    from datetime import datetime
    now = datetime.now()
    rec = {
        # ★ 唯一 id：同一秒内可能连着写好几个文件，只有秒级时间戳区分不开，
        #   定点回滚会误伤。实测撞上过 —— 三笔同秒写入，按 ts 查找
        #   弹掉的是最后一个匹配，回滚错了文件。
        "id": f"{now.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}",
        "ts": now.isoformat(timespec="seconds"),
        "kind": kind,
        "target": str(target),
        "backup": str(backup) if backup else None,
        "note": note,
        "existed_before": backup is not None,
    }
    with _ops_lock:
        ops = _load_ops()
        # 同一次请求可能写多个文件，这里逐条记，回滚按逆序还原
        ops.append(rec)
        _save_ops(ops)
    return rec


def peek_ops(limit: int = 20) -> list[dict]:
    """看最近的操作（新的在前），给前端展示"可回滚到哪一步" """
    with _ops_lock:
        return list(reversed(_load_ops()[-limit:]))


def pop_op() -> dict | None:
    """弹出栈顶操作（最近的），用于自愈回滚。"""
    with _ops_lock:
        ops = _load_ops()
        if not ops:
            return None
        rec = ops.pop()
        _save_ops(ops)
        return rec


def get_op(op_id: str) -> dict | None:
    """按 id（或时间戳）取一条操作记录，不弹出。"""
    with _ops_lock:
        return _find_op(_load_ops(), op_id)


def _find_op(ops: list[dict], op_id: str) -> dict | None:
    """按 id 找，找不到再退化为按 ts 找（兼容旧落盘数据）。"""
    for rec in ops:
        if rec.get("id") == op_id:
            return rec
    for rec in ops:
        if rec.get("ts") == op_id:
            return rec
    return None


def pop_op_by_id(op_id: str) -> dict | None:
    """弹出**指定**的那条操作。

    为什么需要它（2026-09-13 实测发现）：
    自愈原来的做法是 pop_op() 弹栈顶。但栈顶未必是**导致这次崩溃**的那次写入 ——
    实测时栈里躺着两条陈年记录（前几轮探测留下的 bot_config.toml 写入），
    新版 roles.toml 写入在最上面，可一旦有别的写入插队，
    自愈就会去回滚一个跟崩溃毫无关系的文件，等于白折腾还把好配置毁了。

    所以布防时记住是哪条操作，自愈只回滚那一条。找不到就退化为弹栈顶。

    ⚠️ 为什么用 id 而不是 ts：时间戳只精确到秒，实测一次操作里连着写
    三个文件时三条记录 ts 完全相同，按 ts 查找会弹错那一条。
    """
    with _ops_lock:
        ops = _load_ops()
        rec = _find_op(ops, op_id)
        if rec is None:
            return None
        ops.remove(rec)
        _save_ops(ops)
        return rec


def clear_ops() -> None:
    """清空操作栈（确认改动稳定后手动清，避免误回滚）"""
    with _ops_lock:
        _save_ops([])


def tracked_write_text(
    path: Path,
    content: str,
    *,
    kind: str = "write_file",
    note: str = "",
) -> Path | None:
    """原子写 + 自动登记操作栈。**需要参与自愈的写操作都应该走这里。**

    相比裸的 atomic_write_text，它多做了两件事：
      1. 算出相对 ROOT 的路径存进栈（绝对路径服务器上会变，不可靠）
      2. 把备份文件路径一起记下来，回滚时直接拿它还原
    """
    bak = atomic_write_text(path, content, backup=True)
    try:
        rel = path.relative_to(ROOT).as_posix()
    except ValueError:
        rel = str(path)
    push_op(kind=kind, target=rel, backup=bak, note=note)
    return bak


def rollback_op(rec: dict) -> tuple[bool, str]:
    """按一条操作记录回滚。返回 (是否成功, 说明)。

    规则：
      - 改前有备份 → 从备份还原
      - 改前不存在 → 删除该文件（撤销"新建"）
    """
    target = ROOT / rec["target"]
    bak = rec.get("backup")
    try:
        if bak and Path(bak).exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(Path(bak).read_bytes())
            return True, f"已从备份还原 {rec['target']}"
        if not rec.get("existed_before"):
            if target.exists():
                target.unlink()
                return True, f"已删除新建的 {rec['target']}"
            return True, f"{rec['target']} 已不存在，无需处理"
        return False, f"备份文件缺失，无法回滚 {rec['target']}：{bak}"
    except Exception as e:
        return False, f"回滚 {rec['target']} 失败：{e}"


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

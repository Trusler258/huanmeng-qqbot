"""配置编辑：toml 文件的可视化读写

为什么这个模块必须小心：`config/bot_config.toml` 是 bot 的命门 ——
人格、模型、权限全在里面。改错一个引号，bot 就起不来。

三条规矩：
  1. **写入前必须能被解析回来**（写进去先 toml 解析一遍，不通过就不落盘）
  2. **一律走操作栈**（tracked_write_text），改崩了自愈能回滚
  3. **绝不 scp 整体覆盖** —— 服务器的配置可能含私有模块段落，是本地版的超集。
     所以这里只做"读原文 → 改指定键 → 写回"，不做"用模板生成新文件"。

关于 tomllib：服务器是 Python 3.10，**没有 tomllib**（3.11 才有）。
panel/config.py 已做三级兼容（tomllib → tomli → 正则兜底），这里直接复用。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from panel import auth, config as panel_config, security
from panel.config import ROOT

router = APIRouter(
    prefix="/config", tags=["配置"],
    dependencies=[Depends(auth.require_user)],
)

CONFIG_DIR = ROOT / "config"

# 允许编辑的配置文件。.env 单独处理（含密钥，只给键名与是否已填）
EDITABLE: dict[str, dict] = {
    "bot_config.toml": {"label": "主配置", "desc": "人格/模型/权限核心配置",
                        "sensitive": False},
    "roles.toml": {"label": "权限角色", "desc": "管理员与权限分层",
                   "sensitive": False},
    "lang.toml": {"label": "文案", "desc": "所有指令提示文案",
                  "sensitive": False},
    "update.toml": {"label": "更新配置", "desc": "自动更新相关",
                    "sensitive": False},
    "adapter_config.toml": {"label": "适配器", "desc": "NapCat 连接配置",
                            "sensitive": False},
    "version.toml": {"label": "版本号", "desc": "当前版本与更新记录",
                     "sensitive": False},
    "reply_schema.json": {"label": "回复结构", "desc": "LLM JSON 输出 schema",
                          "sensitive": False},
}

# 这些文件名一律不出现在列表里（备份、损坏文件、密钥）
_HIDDEN_PAT = re.compile(r"\.(bak|broken|old|tmp)|^\.env")


def _safe_config_path(name: str) -> Path:
    """只允许 CONFIG_DIR 下的单层文件，且必须在白名单或 .env"""
    if "/" in name or "\\" in name or ".." in name:
        raise HTTPException(400, "非法文件名")
    if Path(name).name != name:
        raise HTTPException(400, "只允许文件名")
    if _HIDDEN_PAT.search(name):
        raise HTTPException(403, "备份/隐藏文件不允许访问")
    p = (CONFIG_DIR / name).resolve()
    base = CONFIG_DIR.resolve()
    if p != base and base not in p.parents:
        raise HTTPException(400, "路径越界")
    return p


@router.get("/files", summary="可编辑的配置文件列表")
async def config_files():
    out = []
    if CONFIG_DIR.is_dir():
        for f in sorted(CONFIG_DIR.iterdir()):
            if not f.is_file():
                continue
            if _HIDDEN_PAT.search(f.name):
                continue
            if f.name == ".env":
                continue
            meta = EDITABLE.get(f.name, {})
            try:
                st = f.stat()
            except Exception:
                continue
            out.append({
                "name": f.name,
                "label": meta.get("label", ""),
                "desc": meta.get("desc", ""),
                "known": f.name in EDITABLE,
                "size": st.st_size,
                "mtime": int(st.st_mtime),
            })
    out.sort(key=lambda x: (not x["known"], x["name"]))
    return {"ok": True, "count": len(out), "files": out}


@router.get("/env", summary=".env 键名一览（值只显示是否已填）")
async def env_view():
    """密钥永远不回显。只告诉你"这一项填了没" —— 排查漏配够用了。"""
    p = CONFIG_DIR / ".env"
    if not p.is_file():
        return {"ok": True, "exists": False, "items": []}
    items = []
    try:
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if not k:
                continue
            items.append({
                "key": k,
                "configured": bool(v),
                "length": len(v),
                # 给个极短的指纹，方便判断"是不是还是旧的那个值"
                # 不足以还原原值（只留首尾各 2 字符）
                "hint": (v[:2] + "…" + v[-2:]) if len(v) >= 8 else "",
            })
    except Exception as e:
        raise HTTPException(500, f"读取失败：{e}")
    return {"ok": True, "exists": True, "count": len(items), "items": items}


@router.get("/file/{name}", summary="读配置文件（原文 + 解析后的结构化视图）")
async def read_config(name: str, parse: bool = Query(True)):
    p = _safe_config_path(name)
    if not p.is_file():
        raise HTTPException(404, f"文件不存在：{name}")

    raw = p.read_text(encoding="utf-8", errors="ignore")

    result: dict[str, Any] = {
        "ok": True,
        "name": name,
        "size": len(raw.encode("utf-8")),
        "raw": raw,
        "sanitized": security.sanitize_text(raw),
        "parsed": None,
        "parse_error": "",
    }

    if parse:
        if name.endswith(".json"):
            import json
            try:
                result["parsed"] = security.sanitize_obj(json.loads(raw))
            except Exception as e:
                result["parse_error"] = str(e)[:200]
        elif name.endswith(".toml"):
            result["parsed"] = _parse_toml(raw, result)

    return result


def _parse_toml(raw: str, result: dict) -> Any:
    """解析 toml。

    用 panel.config.toml_loads —— 它优先走第三方 `toml` 包，
    与 core/config.py 保持一致。原因见 panel/config.py 顶部注释：
    服务器配置里有裸中文键名，严格解析器（tomllib）读不了。
    """
    try:
        return security.sanitize_obj(panel_config.toml_loads(raw))
    except Exception as e:
        result["parse_error"] = str(e)[:300]
        return None


def _validate_toml(raw: str) -> tuple[bool, str]:
    """写前校验：能不能被解析回来。解析不了就不许落盘。

    ⚠️ 注意这里用的是跟读取同一套宽松解析器。如果用严格解析器校验，
       现存的合法配置文件（含裸中文键）会被判为非法，导致根本改不了
       —— 那才是真正的问题。
    """
    try:
        panel_config.toml_loads(raw)
        return True, ""
    except Exception as e:
        return False, str(e)[:300]


# ── 写操作 ────────────────────────────────────────────────

class ConfigWriteReq(BaseModel):
    content: str = Field(max_length=500_000)
    confirm: str = Field("", max_length=80)


@router.put("/file/{name}", summary="写配置文件（校验 + 备份 + 可回滚）")
async def write_config(
    name: str,
    body: ConfigWriteReq,
    user: auth.CurrentUser = Depends(auth.require_user),
):
    """写配置。三道闸：
      1. 语法校验不过 → 拒绝落盘（避免直接写坏）
      2. confirm 必须等于文件名（防止误点保存）
      3. 走操作栈，自愈可回滚
    """
    p = _safe_config_path(name)
    if not p.is_file():
        raise HTTPException(404, f"文件不存在：{name}")

    if body.confirm != name:
        raise HTTPException(
            400, f"改配置需确认：请在 confirm 里原样填入文件名 {name}"
        )

    if name.endswith(".toml"):
        ok, err = _validate_toml(body.content)
        if not ok:
            auth.audit(user, "config_write_rejected", name, f"语法错误: {err}")
            raise HTTPException(
                400, f"TOML 语法校验未通过，已拒绝写入：{err}"
            )
    elif name.endswith(".json"):
        import json
        try:
            json.loads(body.content)
        except Exception as e:
            auth.audit(user, "config_write_rejected", name, f"JSON 错误: {e}")
            raise HTTPException(400, f"JSON 语法错误，已拒绝写入：{e}")

    old_size = p.stat().st_size
    bak = security.tracked_write_text(
        p, body.content, kind="write_config", note=f"面板改配置 {name}"
    )
    auth.audit(user, "config_write", name,
               f"{old_size} → {len(body.content.encode('utf-8'))} 字节")

    # 布防自愈：这次改动如果让 bot 起不来，看门狗要能退回
    _arm_selfheal(name)

    return {
        "ok": True, "name": name,
        "old_size": old_size,
        "new_size": len(body.content.encode("utf-8")),
        "backup": bak.name if bak else "",
        "need_restart": True,
        "armed": True,
    }


def _arm_selfheal(name: str) -> None:
    """通知自愈看门狗：刚写了个关键文件，接下来盯紧点。

    只对**影响 bot 启动**的文件布防。改个 lang.toml 里的错别字
    不至于让 bot 起不来，没必要盯着。
    """
    critical = {"bot_config.toml", "roles.toml", "adapter_config.toml",
                "version.toml", "reply_schema.json"}
    if name not in critical:
        return
    try:
        from panel import selfheal
        ops = security.peek_ops(limit=1)
        if ops:
            selfheal.get_state().arm(ops[0])
    except Exception:
        pass


# ── 单键编辑（可视化表单用） ──────────────────────────────

class KeyUpdateReq(BaseModel):
    key: str = Field(min_length=1, max_length=200,
                     description="点分路径，如 bot.name 或 model.replyer_1.name")
    value: Any = None
    confirm: str = Field("", max_length=80)


@router.post("/file/{name}/set", summary="改单个键（保持文件其余部分原样）")
async def set_key(
    name: str,
    body: KeyUpdateReq,
    user: auth.CurrentUser = Depends(auth.require_user),
):
    """按点分路径改一个键，**不动文件里的其他任何内容**。

    为什么不用"解析→改→重新 dump"：那样会把注释全丢掉、键顺序打乱，
    对一个被人工维护的配置文件来说是不可接受的破坏。
    这里用的是原地文本替换，改完只有那一行变。
    """
    p = _safe_config_path(name)
    if not p.is_file():
        raise HTTPException(404, f"文件不存在：{name}")
    if not name.endswith(".toml"):
        raise HTTPException(400, "单键编辑目前仅支持 toml")

    if body.confirm != name:
        raise HTTPException(
            400, f"改配置需确认：请在 confirm 里原样填入文件名 {name}"
        )

    raw = p.read_text(encoding="utf-8")
    new_raw, found = _replace_key(raw, body.key, body.value)
    if not found:
        raise HTTPException(
            404, f"没找到键 {body.key}。若该键尚不存在，请用整文件编辑模式添加。"
        )

    ok, err = _validate_toml(new_raw)
    if not ok:
        raise HTTPException(400, f"改完语法不通，已拒绝：{err}")

    bak = security.tracked_write_text(
        p, new_raw, kind="write_config_key",
        note=f"改 {name} 的 {body.key}",
    )
    auth.audit(user, "config_set_key", name,
               f"{body.key} = {str(body.value)[:60]}")
    _arm_selfheal(name)

    return {"ok": True, "name": name, "key": body.key,
            "backup": bak.name if bak else "", "armed": True}


def _replace_key(raw: str, dotted: str, value: Any) -> tuple[str, bool]:
    """在 toml 原文里把 `dotted` 指向的键改成 value。

    支持 [section] 下的 `key = ...`，也支持 `[a.b]` 形式的嵌套。
    **只替换那一行的 = 右侧**，左侧与缩进保持原样。

    ⚠️ 键名匹配必须允许**中文与带引号的键**。
       服务器的 bot_config.toml 里有 `bot的名字 = "幻梦"` 这种裸中文键，
       早期的 ASCII-only 正则会静默匹配不到 → 界面上"改了没反应"。
       toml 规范里键可以是裸的（字母数字_-）或引号包裹的任意字符串。
    """
    section, _, leaf = dotted.rpartition(".")
    lines = raw.splitlines(keepends=True)
    in_section = (section == "")

    value_str = _fmt_value(value)

    for i, line in enumerate(lines):
        stripped = line.strip()
        # 跟踪当前所在 section
        m_sec = re.match(r"^\[([^\]]+)\]", stripped)
        if m_sec:
            cur = m_sec.group(1).strip().strip('"').strip("'")
            in_section = (cur == section)
            continue
        if not in_section:
            continue
        if stripped.startswith("#") or not stripped:
            continue

        m_kv = _KV_RE.match(line)
        if not m_kv:
            continue

        # 键名可能是裸的、单引号包的、双引号包的 —— 统一比较实际名字
        raw_key = m_kv.group(2)
        key_name = _unquote_key(raw_key)
        if key_name != leaf:
            continue

        # 保留原键名的书写形式（不擅自给它加引号，那会改变文件风格）
        nl = "\n" if line.endswith("\n") else ""
        lines[i] = f"{m_kv.group(1)}{raw_key}{m_kv.group(3)}{value_str}{nl}"
        return "".join(lines), True

    return raw, False


# toml 键：裸键（字母/数字/_/-/中文等非 ASCII 都算裸键的一部分）
# 或引号包裹的任意内容
_KV_RE = re.compile(
    r"""^(\s*)
        (
            "(?:[^"\\]|\\.)*"        # 双引号键
          | '(?:[^']*)'              # 单引号键（toml 里单引号不做转义）
          | [^\s=\[\]{}#,]+          # 裸键：不含空白与分隔符都行（含中文）
        )
        (\s*=\s*)
        (.*)$
    """,
    re.VERBOSE,
)


def _unquote_key(k: str) -> str:
    """把键的书写形式还原成实际名字（去掉包裹引号与转义）"""
    k = k.strip()
    if len(k) >= 2 and k[0] == '"' and k[-1] == '"':
        return k[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if len(k) >= 2 and k[0] == "'" and k[-1] == "'":
        return k[1:-1]
    return k


def _fmt_value(v: Any) -> str:
    """把 Python 值渲染成 toml 字面量。

    ⚠️ 这里曾经踩过一次：含双引号的值改用**单引号字面量**，
       但单引号字面量无法表示字符串里的单引号，当时用
       `s.replace("'", "")` 处理 —— 那是**静默丢字符**，
       值里同时有单双引号时数据就坏了。
       正确做法：一律用双引号 + 正确转义（\\" 与 \\\\），这样任何内容都能表示。
    """
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(_fmt_value(x) for x in v) + "]"
    s = "" if v is None else str(v)
    if "\n" in s:
        # 多行字符串用三引号。注意 toml 规矩：三引号后的首行必须为空，
        # 否则第一个换行会被吃掉；结尾三引号前若要保留换行需写成四个引号。
        # 这里做得保守些：收尾不留空行。
        body = s
        if body.endswith("\n"):
            body = body[:-1] + "\\\n"   # 行尾反斜杠续行，保住最后的换行
        return '"""\n' + body + '"""'
    # 转义双引号与反斜杠 —— 反斜杠必须先转（否则会二次转义）
    esc = s.replace("\\", "\\\\").replace('"', '\\"')
    # 制表符等控制字符也要转义，否则 toml 解析失败
    esc = esc.replace("\t", "\\t").replace("\r", "\\r")
    return '"' + esc + '"'


@router.get("/file/{name}/backups", summary="配置的历史备份")
async def config_backups(name: str, limit: int = Query(30, ge=1, le=200)):
    p = _safe_config_path(name)
    out = []
    for f in CONFIG_DIR.glob(f"{name}.bak*"):
        try:
            st = f.stat()
        except Exception:
            continue
        out.append({"file": f.name, "size": st.st_size,
                    "mtime": int(st.st_mtime)})
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return {"ok": True, "count": len(out), "backups": out[:limit]}


class RestoreReq(BaseModel):
    backup: str = Field(max_length=200)
    confirm: str = Field("", max_length=80)


@router.post("/file/{name}/restore", summary="从某个备份还原")
async def restore_config(
    name: str,
    body: RestoreReq,
    user: auth.CurrentUser = Depends(auth.require_user),
):
    if body.confirm != name:
        raise HTTPException(
            400, f"还原需确认：请在 confirm 里原样填入文件名 {name}"
        )
    p = _safe_config_path(name)
    if not p.is_file():
        raise HTTPException(404, f"文件不存在：{name}")

    if "/" in body.backup or "\\" in body.backup or ".." in body.backup:
        raise HTTPException(400, "非法备份文件名")
    bp = CONFIG_DIR / body.backup
    if not bp.is_file():
        raise HTTPException(404, f"备份不存在：{body.backup}")
    if not body.backup.startswith(name):
        raise HTTPException(400, "备份文件名与被还原文件不匹配")

    content = bp.read_text(encoding="utf-8", errors="ignore")
    if name.endswith(".toml"):
        ok, err = _validate_toml(content)
        if not ok:
            raise HTTPException(400, f"备份本身语法不通，拒绝还原：{err}")

    bak = security.tracked_write_text(
        p, content, kind="restore_config",
        note=f"从 {body.backup} 还原 {name}",
    )
    auth.audit(user, "config_restore", name, f"来源 {body.backup}")
    _arm_selfheal(name)
    return {"ok": True, "name": name, "from": body.backup,
            "backup": bak.name if bak else "", "armed": True}

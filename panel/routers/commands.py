"""指令中心：全量指令清单 + LLM 可见性检查

**与 Bot 同源**（v2.1.20 铁律）：直接调用 `help_card.collect_commands()`，
不自己维护第二份清单。这样"面板看到的"永远等于"Bot 实际会的"。
"""

from __future__ import annotations

import sys

from fastapi import APIRouter, Depends

from panel import auth, config

router = APIRouter(prefix="/commands", tags=["指令"], dependencies=[Depends(auth.require_user)])


def _collect() -> list[dict]:
    """调用 Bot 的 collect_commands()，统一成扁平列表。

    ⚠️ 真实签名是 `collect_commands() -> dict[str, list[tuple]]`：
         {分类: [(主名, 描述, 是否插件, 别名表), ...]}
    不是 list[dict]。别想当然——这里踩过一次坑，整个接口返回 0 条。
    """
    root = str(config.ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    from modules import help_card  # type: ignore

    data = help_card.collect_commands()
    out: list[dict] = []

    if isinstance(data, dict):
        for cat, rows in data.items():
            for r in rows or []:
                # 元组顺序：(主名, 描述, 是否插件, 别名表)
                if isinstance(r, (list, tuple)):
                    name = r[0] if len(r) > 0 else ""
                    desc = r[1] if len(r) > 1 else ""
                    is_plugin = r[2] if len(r) > 2 else False
                    aliases = r[3] if len(r) > 3 else []
                elif isinstance(r, dict):
                    name = r.get("cmd") or r.get("name") or ""
                    desc = r.get("desc", "")
                    is_plugin = r.get("plugin", False)
                    aliases = r.get("aliases", []) or []
                else:
                    continue
                if not name:
                    continue
                out.append({
                    "cmd": str(name),
                    "desc": str(desc or ""),
                    "category": str(cat),
                    "admin": bool(is_plugin),   # 插件指令视作需管理员（保守）
                    "plugin": bool(is_plugin),
                    "aliases": list(aliases or []),
                    "params": "",
                })
    elif isinstance(data, list):
        for r in data:
            if isinstance(r, dict):
                out.append({
                    "cmd": str(r.get("cmd", r.get("name", ""))),
                    "desc": str(r.get("desc", "")),
                    "category": str(r.get("category", r.get("cat", "其他"))),
                    "admin": bool(r.get("admin", False)),
                    "plugin": bool(r.get("plugin", False)),
                    "aliases": list(r.get("aliases", []) or []),
                    "params": str(r.get("params", "")),
                })
            elif isinstance(r, (list, tuple)):
                out.append({
                    "cmd": str(r[0]) if len(r) > 0 else "",
                    "desc": str(r[1]) if len(r) > 1 else "",
                    "category": str(r[2]) if len(r) > 2 else "其他",
                    "admin": False, "plugin": False, "aliases": [], "params": "",
                })
    return [x for x in out if x["cmd"]]


@router.get("", summary="全量指令清单")
async def list_commands(
    q: str = "",
    category: str = "",
    admin: str = "",   # "" 全部 / "1" 仅管理员 / "0" 仅普通
):
    try:
        rows = _collect()
    except Exception as e:
        return {"ok": False, "error": f"加载指令清单失败: {e}", "items": [], "total": 0}

    cats: dict[str, int] = {}
    for r in rows:
        cats[r["category"]] = cats.get(r["category"], 0) + 1

    out = rows
    if q:
        ql = q.lower()
        out = [r for r in out if ql in r["cmd"].lower() or ql in r["desc"].lower()
               or any(ql in str(a).lower() for a in r["aliases"])]
    if category:
        out = [r for r in out if r["category"] == category]
    if admin in ("0", "1"):
        want = (admin == "1")
        out = [r for r in out if r["admin"] == want]

    return {
        "ok": True,
        "total": len(rows),
        "filtered": len(out),
        "categories": cats,
        "items": out,
    }


@router.get("/llm-audit", summary="LLM 可见性审计（查『会但没说』的指令）")
async def llm_audit():
    """对比 COMMAND_MAP 注册的键 与 LLM 动态清单里的指令。

    v2.1.20 的教训：曾有 25 条指令 LLM 完全没有说明，导致"bot 不知道自己会什么"。
    这个接口就是那个排查动作的常驻化版本。
    """
    root = str(config.ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)

    result = {"ok": True, "registered": [], "in_llm_prompt": [],
              "missing_in_llm": [], "extra_in_llm": [], "error": ""}
    try:
        from modules.commands import COMMAND_MAP  # type: ignore
        registered = sorted(str(k) for k in COMMAND_MAP.keys())
        result["registered"] = registered

        from services.llm import _build_dynamic_command_list  # type: ignore
        doc = _build_dynamic_command_list()
        in_prompt = [c for c in registered if c in doc]
        result["in_llm_prompt"] = sorted(in_prompt)
        result["missing_in_llm"] = sorted(set(registered) - set(in_prompt))

        # 反向：提示词里提到但没注册的
        import re
        mentioned = set(re.findall(r"/~([A-Za-z0-9_]+)", doc))
        result["extra_in_llm"] = sorted(
            m for m in mentioned if m not in set(registered)
        )
    except Exception as e:
        result["ok"] = False
        result["error"] = f"{type(e).__name__}: {e}"

    return result

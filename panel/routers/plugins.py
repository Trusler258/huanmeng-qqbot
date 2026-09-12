"""插件管理：plugins/ 与 modules_private/ 清单 + .hmp 插件

只读为主。安装/卸载 .hmp 复用 modules/plugin_share.py 的能力时**必须谨慎**——
那是会改文件系统的动作，一律要求二次确认 + 审计。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from panel import auth, config
from panel.security import read_json

CST = timezone(timedelta(hours=8))

router = APIRouter(prefix="/plugins", tags=["插件"], dependencies=[Depends(auth.require_user)])

ROOT = config.ROOT


def _scan_dir(d: Path) -> list[dict]:
    if not d.exists():
        return []
    out = []
    for p in sorted(d.iterdir()):
        try:
            if p.is_file():
                st = p.stat()
                out.append({
                    "name": p.name, "type": "file", "size": st.st_size,
                    "mtime": datetime.fromtimestamp(st.st_mtime, CST).strftime("%Y-%m-%d %H:%M"),
                })
            elif p.is_dir():
                manifest = None
                for cand in ("manifest.json", "plugin.json", "manifest.toml"):
                    f = p / cand
                    if f.exists():
                        manifest = read_json(f, None) if cand.endswith(".json") else {"path": cand}
                        break
                n_py = len(list(p.glob("*.py")))
                st = p.stat()
                out.append({
                    "name": p.name, "type": "dir", "files": n_py,
                    "manifest": manifest,
                    "mtime": datetime.fromtimestamp(st.st_mtime, CST).strftime("%Y-%m-%d %H:%M"),
                })
        except Exception:
            pass
    return out


@router.get("", summary="插件目录清单")
async def plugins():
    return {
        "ok": True,
        "plugins_dir": {"path": "plugins/", "items": _scan_dir(ROOT / "plugins")},
        "private_dir": {"path": "modules_private/", "items": _scan_dir(ROOT / "modules_private")},
        "data_plugins": {"path": "data/plugins/", "items": _scan_dir(config.DATA_DIR / "plugins")},
    }


@router.get("/hmp", summary="已安装的 .hmp 插件包")
async def hmp_list():
    found = []
    for d in (config.DATA_DIR / "plugins", config.DATA_DIR / "hmp", ROOT / "plugins"):
        if not d.exists():
            continue
        for p in d.glob("*.hmp"):
            try:
                st = p.stat()
                found.append({
                    "name": p.name,
                    "dir": str(d.relative_to(ROOT)),
                    "size": st.st_size,
                    "mtime": datetime.fromtimestamp(st.st_mtime, CST).strftime("%Y-%m-%d %H:%M:%S"),
                })
            except Exception:
                pass
    return {"ok": True, "count": len(found), "items": found}


@router.get("/capabilities", summary="能力注册表：指令/工具/技能/插件")
async def capabilities():
    """读 core/capability 的统一能力注册表。

    真实 API（见 core/capability/__init__.py）：
        from core.capability import get_capability_registry
        reg = get_capability_registry()   # CapabilityRegistry
        reg.discover()                    # 惰性：首次调用才扫描
        reg._caps  -> dict[id, Capability]
    """
    root = str(ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from core.capability import get_capability_registry  # type: ignore

        reg = get_capability_registry()
        try:
            reg.discover()
        except Exception:
            pass

        caps: dict = getattr(reg, "_caps", {}) or {}
        by_cat: dict[str, list[dict]] = {}
        for cid, cap in caps.items():
            cat = getattr(cap, "category", "unknown")
            by_cat.setdefault(str(cat), []).append({
                "id": str(cid),
                "name": getattr(cap, "name", str(cid)),
                "description": (getattr(cap, "description", "") or "")[:200],
                "runtime": getattr(cap, "runtime", ""),
                "source": getattr(cap, "source", ""),
                "permissions": getattr(cap, "permissions", "") or "",
                "aliases": getattr(cap, "aliases", []) or [],
                "always_on": bool(getattr(cap, "always_on", False)),
            })
        for items in by_cat.values():
            items.sort(key=lambda x: x["id"])

        return {
            "ok": True,
            "total": len(caps),
            "by_category": {k: len(v) for k, v in by_cat.items()},
            "items": by_cat,
            "handlers": len(getattr(reg, "_handlers", {}) or {}),
            "tool_schemas": len(getattr(reg, "_tool_schemas", {}) or {}),
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "items": {}, "total": 0}


@router.get("/eventbus", summary="事件总线订阅情况")
async def eventbus():
    """读 core/eventbus 的订阅情况。

    真实 API（见 core/eventbus.py）：
        from core.eventbus import get_event_bus
        bus = get_event_bus()
        bus._handlers  -> dict[event_name, list[handler]]
        bus._history   -> list[(event_name, ts)]
    """
    root = str(ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from core.eventbus import get_event_bus  # type: ignore

        bus = get_event_bus()
        handlers: dict = getattr(bus, "_handlers", {}) or {}

        topics = []
        for name, hs in handlers.items():
            names = []
            for h in hs:
                n = getattr(h, "__qualname__", None) or getattr(h, "__name__", None)
                if n is None:
                    n = type(h).__name__
                names.append(str(n))
            topics.append({"event": str(name), "count": len(hs), "handlers": names})
        topics.sort(key=lambda x: x["event"])

        history = list(getattr(bus, "_history", []) or [])
        # 统计各事件触发次数
        freq: dict[str, int] = {}
        for item in history:
            try:
                freq[str(item[0])] = freq.get(str(item[0]), 0) + 1
            except Exception:
                pass

        return {
            "ok": True,
            "topics": topics,
            "topic_count": len(topics),
            "handler_total": sum(t["count"] for t in topics),
            "history_size": len(history),
            "recent_freq": sorted(
                ({"event": k, "count": v} for k, v in freq.items()),
                key=lambda x: x["count"], reverse=True,
            ),
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "topics": []}

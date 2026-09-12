"""概览仪表盘：Bot 状态 / 版本 / 数据规模 / 今日活跃 / 服务态"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends

from panel import auth, config
from panel.security import read_json

CST = timezone(timedelta(hours=8))

router = APIRouter(prefix="/overview", tags=["概览"], dependencies=[Depends(auth.require_user)])

DATA = config.DATA_DIR
ROOT = config.ROOT
_START = time.time()

# ⚠️ 统计文件名里的日期是 **紧凑格式 YYYYMMDD**（如 stats_123_20260912.json），
# 不是带横线的 ISO 格式。这是踩过的坑：用 "%Y-%m-%d" 拼文件名会一个都匹配不到，
# 表现为「今日消息量恒为 0」。改这里之前先看一眼 data/ 里的真实文件名。


def compact_date(d: datetime) -> str:
    """datetime -> 20260912"""
    return d.strftime("%Y%m%d")


def iso_to_compact(s: str) -> str:
    """2026-09-12 -> 20260912（已经是紧凑格式则原样返回）"""
    return s.replace("-", "")


def _dir_size(path: Path, limit: int = 200000) -> int:
    """目录大小（字节），带文件数上限防卡死"""
    total = 0
    n = 0
    try:
        for p in path.rglob("*"):
            if p.is_file():
                try:
                    total += p.stat().st_size
                except Exception:
                    pass
                n += 1
                if n > limit:
                    break
    except Exception:
        pass
    return total


def _count_files(path: Path, pattern: str = "*") -> int:
    try:
        return sum(1 for p in path.glob(pattern) if p.is_file())
    except Exception:
        return 0


@router.get("", summary="概览数据")
async def overview():
    now = datetime.now(CST)
    today = now.strftime("%Y-%m-%d")
    today_compact = compact_date(now)

    # ── 版本 ──
    version = "unknown"
    vf = ROOT / "config" / "version.toml"
    if vf.exists():
        m = re.search(r'current\s*=\s*"([^"]+)"', vf.read_text(encoding="utf-8"))
        if m:
            version = m.group(1)

    # ── 进程状态 ──
    def proc_alive(name: str) -> bool:
        try:
            out = subprocess.run(
                ["pgrep", "-f", name], capture_output=True, timeout=3, text=True
            )
            return out.returncode == 0 and bool(out.stdout.strip())
        except Exception:
            return False

    # ── 数据规模 ──
    stats_files = sorted(DATA.glob("stats_*_*.json"), reverse=True)
    today_stats = [f for f in stats_files if f.name.endswith(f"_{today_compact}.json")]

    # 今日消息总量（汇总所有群今日统计）
    today_msgs = 0
    today_users: set[str] = set()
    today_groups = 0
    for f in today_stats:
        obj = read_json(f, {})
        meta = obj.get("_meta", {}) if isinstance(obj, dict) else {}
        today_msgs += int(meta.get("total", 0) or 0)
        today_groups += 1
        for k, v in (obj or {}).items():
            if k == "_meta" or not isinstance(v, dict):
                continue
            if int(v.get("count", 0) or 0) > 0:
                today_users.add(k)

    # ── 关系数据 ──
    fav = read_json(DATA / "fav.json", {}) or {}
    profiles = read_json(DATA / "user_profiles.json", {}) or {}
    economy = read_json(DATA / "economy.json", {}) or {}
    features = read_json(DATA / "features.json", {}) or {}

    notes_dir = DATA / "notes"
    stm_dir = DATA / "stm"

    # ── 服务状态 ──
    services = ["bot.service", "panel.service", "napcat", "cloudflared"]
    svc_state = {}
    for s in services:
        try:
            out = subprocess.run(
                ["systemctl", "is-active", s],
                capture_output=True, timeout=3, text=True,
            )
            svc_state[s] = out.stdout.strip() or "unknown"
        except Exception:
            svc_state[s] = "n/a"

    # ── 数据库 ──
    db_path = ROOT / "data" / "huanmeng.db"
    db_size = db_path.stat().st_size if db_path.exists() else 0

    # ── 系统 ──
    mem = {}
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                k, _, v = line.partition(":")
                mem[k.strip()] = int(v.strip().split()[0])  # kB
    except Exception:
        pass

    load = os.getloadavg() if hasattr(os, "getloadavg") else (0, 0, 0)

    return {
        "ok": True,
        "version": version,
        "server_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "uptime_seconds": int(time.time() - _START),
        "bot": {
            "alive": proc_alive("main.py") or proc_alive("bot"),
            "service": svc_state.get("bot.service", "unknown"),
        },
        "services": svc_state,
        "today": {
            "date": today,
            "messages": today_msgs,
            "groups": today_groups,
            "active_users": len(today_users),
        },
        "totals": {
            "groups_tracked": _count_files(DATA, "stats_*_*.json"),
            "fav_entries": len(fav),
            "profiles": len(profiles),
            "notes_files": _count_files(notes_dir, "*.md"),
            "stm_files": _count_files(stm_dir, "*.json"),
            "msglog_files": _count_files(DATA / "msglog", "*.jsonl"),
            "data_size": _dir_size(DATA),
            "db_size": db_size,
        },
        "features": features,
        "system": {
            "mem_total_kb": mem.get("MemTotal", 0),
            "mem_available_kb": mem.get("MemAvailable", 0),
            "load_avg": [round(x, 2) for x in load],
            "cpu_count": os.cpu_count() or 1,
        },
    }


@router.get("/trend", summary="近 N 天消息趋势（用于折线图）")
async def trend(days: int = 14, group: str = ""):
    days = max(1, min(days, 90))
    out = []
    today = datetime.now(CST).date()

    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        ds = d.strftime("%Y-%m-%d")
        dc = compact_date(datetime.combine(d, datetime.min.time()))
        total = 0
        users = 0
        if group:
            files = [DATA / f"stats_{group}_{dc}.json"]
        else:
            files = list(DATA.glob(f"stats_*_{dc}.json"))
        for f in files:
            obj = read_json(f, {})
            if not isinstance(obj, dict):
                continue
            total += int((obj.get("_meta") or {}).get("total", 0) or 0)
            users += sum(
                1 for k, v in obj.items()
                if k != "_meta" and isinstance(v, dict) and int(v.get("count", 0) or 0) > 0
            )
        out.append({"date": ds, "messages": total, "active_users": users})

    return {"ok": True, "days": days, "group": group, "data": out}

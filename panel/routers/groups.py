"""群管理：群列表、群配置、成员概览

数据来自多处，这里做聚合：
  data/msglog/msglog_<群号>.jsonl      —— 消息记录（存在 = 群里有活动）
  data/stats_<群号>_<YYYYMMDD>.json    —— 每日统计
  data/memory_<群号>.md                —— 长期记忆
  data/notes/<群号>.md                 —— 笔记
  data/economy.json                    —— 积分
  data/fav.json                        —— 好感度
  data/quake_subs.json / eq_subs.json  —— 地震订阅

⚠️ 群号来源不统一是历史事实，别指望有单一"群列表"文件。
   这里的做法：把上述所有来源的群号取并集，缺哪块就显示哪块为空。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from panel import auth, security
from panel.config import DATA_DIR, ROOT

router = APIRouter(
    prefix="/groups", tags=["群管理"],
    dependencies=[Depends(auth.require_user)],
)

_MSGLOG = DATA_DIR / "msglog"
_STATS_RE = re.compile(r"^stats_(\d+)_(\d{8})\.json$")
_EQ_SUB_FILES = ("quake_subs.json", "eq_subs.json", "earthquake_subs.json")


def _collect_group_ids() -> dict[int, set[str]]:
    """把所有能推出群号的地方扫一遍，返回 {群号: {来源标记}}"""
    src: dict[int, set[str]] = {}

    def add(gid: int, why: str) -> None:
        src.setdefault(int(gid), set()).add(why)

    # msglog 文件名
    if _MSGLOG.is_dir():
        for f in _MSGLOG.glob("msglog_*.jsonl"):
            m = re.match(r"^msglog_(\d+)\.jsonl$", f.name)
            if m:
                add(int(m.group(1)), "msglog")

    # 每日统计文件名
    for f in DATA_DIR.glob("stats_*_*.json"):
        m = _STATS_RE.match(f.name)
        if m:
            add(int(m.group(1)), "stats")

    # 长期记忆
    for f in DATA_DIR.glob("memory_*.md"):
        m = re.match(r"^memory_(\d+)\.md$", f.name)
        if m:
            add(int(m.group(1)), "memory")

    # 笔记
    notes_dir = DATA_DIR / "notes"
    if notes_dir.is_dir():
        for f in notes_dir.glob("*.md"):
            if f.stem.isdigit():
                add(int(f.stem), "notes")

    # 地震订阅
    for name in _EQ_SUB_FILES:
        p = DATA_DIR / name
        if not p.is_file():
            continue
        raw = security.read_json(p, {})
        keys = raw.keys() if isinstance(raw, dict) else (
            raw if isinstance(raw, list) else []
        )
        for k in keys:
            s = str(k)
            if s.isdigit():
                add(int(s), "eq_sub")

    return src


def _group_name(gid: int) -> str:
    """群名。bot 不存群名 —— 从统计数据里捡，捡不到就空着让前端显示群号。"""
    stats = sorted(DATA_DIR.glob(f"stats_{gid}_*.json"), reverse=True)
    for f in stats:
        obj = security.read_json(f, {})
        if isinstance(obj, dict):
            for k in ("group_name", "name", "group_card"):
                if obj.get(k):
                    return str(obj[k])
    return ""


def _last_active(gid: int) -> int:
    """最后一次有消息的时间（unix 秒）。0 表示查不到。"""
    p = _MSGLOG / f"msglog_{gid}.jsonl"
    if not p.is_file():
        return 0
    try:
        sz = p.stat().st_size
        with p.open("rb") as fh:
            fh.seek(max(0, sz - 8192))        # 只读尾巴，别把整个文件拖进来
            tail = fh.read().decode("utf-8", "ignore").strip().splitlines()
        for line in reversed(tail):
            try:
                rec = json.loads(line)
                if rec.get("time"):
                    return int(rec["time"])
            except Exception:
                continue
    except Exception:
        pass
    return 0


def _line_count(p: Path) -> int:
    try:
        with p.open("rb") as fh:
            return sum(1 for _ in fh)
    except Exception:
        return 0


@router.get("", summary="群列表（含活跃度与数据存量）")
async def list_groups():
    src = _collect_group_ids()
    today = time.strftime("%Y%m%d")

    out = []
    for gid, why in src.items():
        msglog_p = _MSGLOG / f"msglog_{gid}.jsonl"
        today_stats = DATA_DIR / f"stats_{gid}_{today}.json"
        mem_p = DATA_DIR / f"memory_{gid}.md"
        note_p = DATA_DIR / "notes" / f"{gid}.md"

        today_obj = security.read_json(today_stats, {}) if today_stats.is_file() else {}

        out.append({
            "group_id": gid,
            "name": _group_name(gid),
            "sources": sorted(why),
            "last_active": _last_active(gid),
            "msglog_lines": _line_count(msglog_p) if msglog_p.is_file() else 0,
            "msglog_size": msglog_p.stat().st_size if msglog_p.is_file() else 0,
            "memory_lines": _line_count(mem_p) if mem_p.is_file() else 0,
            "memory_size": mem_p.stat().st_size if mem_p.is_file() else 0,
            "note_count": _line_count(note_p) if note_p.is_file() else 0,
            "today": {
                "messages": today_obj.get("messages", today_obj.get("total", 0)),
                "users": today_obj.get("users", today_obj.get("active_users", 0)),
            } if isinstance(today_obj, dict) else {},
        })

    out.sort(key=lambda x: x["last_active"], reverse=True)
    return {"ok": True, "count": len(out), "groups": out}


@router.get("/{group_id}", summary="单个群的配置与数据概况")
async def group_detail(group_id: int):
    entries = _collect_group_ids()
    if group_id not in entries:
        raise HTTPException(404, f"没有群 {group_id} 的任何数据")

    msglog_p = _MSGLOG / f"msglog_{group_id}.jsonl"
    stats = sorted(
        (f for f in DATA_DIR.glob(f"stats_{group_id}_*.json")),
        reverse=True,
    )[:30]

    return {
        "ok": True,
        "group_id": group_id,
        "name": _group_name(group_id),
        "sources": sorted(entries[group_id]),
        "last_active": _last_active(group_id),
        "msglog_lines": _line_count(msglog_p) if msglog_p.is_file() else 0,
        "stats_days": [
            {"date": _STATS_RE.match(f.name).group(2),
             "size": f.stat().st_size}
            for f in stats if _STATS_RE.match(f.name)
        ],
        "note": _read_text(DATA_DIR / "notes" / f"{group_id}.md"),
        "memory_head": _read_text(DATA_DIR / f"memory_{group_id}.md")[:3000],
    }


def _read_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


@router.get("/{group_id}/members", summary="群成员概览（从 msglog 聚合）")
async def group_members(group_id: int, limit: int = 100):
    """从消息记录里聚合出成员发言情况。

    注意：这只是"说过话的人"，不是完整群成员列表 ——
    bot 拿不到群成员名单（OneBot 需额外接口且 NapCat 未开放）。
    """
    p = _MSGLOG / f"msglog_{group_id}.jsonl"
    if not p.is_file():
        raise HTTPException(404, f"群 {group_id} 没有消息记录")

    stat: dict[int, dict] = {}
    with p.open("r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            uid = rec.get("user_id")
            if not uid:
                continue
            uid = int(uid)
            s = stat.setdefault(uid, {"user_id": uid, "count": 0,
                                      "last": 0, "recalled": 0})
            s["count"] += 1
            if rec.get("time"):
                s["last"] = max(s["last"], int(rec["time"]))
            if rec.get("recalled"):
                s["recalled"] += 1

    rows = sorted(stat.values(), key=lambda x: x["count"], reverse=True)[:limit]
    return {"ok": True, "group_id": group_id, "count": len(rows),
            "members": rows}


# ── 写操作 ────────────────────────────────────────────────

class NoteWriteReq(BaseModel):
    group_id: int
    content: str = Field(min_length=1, max_length=500)


@router.post("/{group_id}/note", summary="给群追加一条笔记")
async def add_group_note(
    group_id: int,
    body: NoteWriteReq,
    user: auth.CurrentUser = Depends(auth.require_user),
):
    d = DATA_DIR / "notes"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{group_id}.md"
    old = _read_text(p)
    new = old + body.content.strip() + "\n"
    security.tracked_write_text(p, new, kind="group_note",
                                note=f"群 {group_id} 加笔记")
    auth.audit(user, "group_note_add", str(group_id), body.content[:80])
    return {"ok": True, "lines": new.count("\n")}


class MemoryWriteReq(BaseModel):
    content: str = Field(max_length=200_000)
    confirm: str = Field("", max_length=80)


@router.put("/{group_id}/memory", summary="覆盖群的长期记忆")
async def write_group_memory(
    group_id: int,
    body: MemoryWriteReq,
    user: auth.CurrentUser = Depends(auth.require_user),
):
    """长期记忆是 bot 的"这个群的印象"，改错了它就不认识这个群了。

    所以要求输入群号确认，并走操作栈（可被自愈回滚）。
    """
    if body.confirm != str(group_id):
        raise HTTPException(
            400, f"改长期记忆需确认：请在 confirm 里原样填入群号 {group_id}"
        )
    p = DATA_DIR / f"memory_{group_id}.md"
    if not p.is_file():
        raise HTTPException(404, f"群 {group_id} 没有长期记忆文件")
    security.tracked_write_text(p, body.content, kind="group_memory",
                                note=f"覆盖群 {group_id} 长期记忆")
    auth.audit(user, "group_memory_write", str(group_id),
               f"{len(body.content)} 字符")
    return {"ok": True, "size": len(body.content)}


@router.get("/{group_id}/backups", summary="该群记忆的备份列表")
async def group_backups(group_id: int):
    """列出可回滚的历史版本 —— 面板改过、bot 自己也可能留过"""
    pats = [
        f"memory_{group_id}.md.bak*",
        f"notes/{group_id}.md.bak*",
    ]
    out = []
    for pat in pats:
        for f in DATA_DIR.glob(pat):
            try:
                st = f.stat()
                out.append({
                    "file": f.name,
                    "rel": f.relative_to(ROOT).as_posix(),
                    "size": st.st_size,
                    "mtime": int(st.st_mtime),
                })
            except Exception:
                continue
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return {"ok": True, "count": len(out), "backups": out[:50]}

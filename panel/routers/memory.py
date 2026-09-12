"""长期记忆：笔记本 / 短期记忆 / 自身认知 / 技能文件

所有写操作都限死在 data/ 内（`assert_in_data`），且走编辑器白名单。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from panel import auth, config
from panel.security import (
    PathEscape,
    assert_in_data,
    atomic_write_text,
    glob_match,
    read_json,
    safe_path,
)

CST = timezone(timedelta(hours=8))

router = APIRouter(prefix="/memory", tags=["记忆"], dependencies=[Depends(auth.require_user)])

DATA = config.DATA_DIR
NOTES = DATA / "notes"
STM = DATA / "stm"
SKILLS = DATA / "skills"


def _safe(rel: str, roots: list[Path]) -> Path:
    try:
        return safe_path(rel, roots=roots)
    except PathEscape as e:
        raise HTTPException(400, str(e))


# ── 笔记本 ────────────────────────────────────────────────

@router.get("/notes", summary="笔记本文件列表")
async def notes_list():
    if not NOTES.exists():
        return {"ok": True, "items": []}
    items = []
    for p in sorted(NOTES.glob("*.md")):
        try:
            st = p.stat()
            lines = p.read_text(encoding="utf-8").splitlines()
            items.append({
                "chat_id": p.stem,
                "file": p.name,
                "lines": len([x for x in lines if x.strip()]),
                "size": st.st_size,
                "mtime": datetime.fromtimestamp(st.st_mtime, CST).strftime("%Y-%m-%d %H:%M:%S"),
                "preview": (lines[0][:60] if lines else ""),
            })
        except Exception:
            pass
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return {"ok": True, "items": items}


@router.get("/notes/{chat_id}", summary="读某会话的笔记本")
async def notes_read(chat_id: str):
    if not chat_id.isdigit():
        raise HTTPException(400, "会话 ID 必须是数字")
    path = _safe(f"data/notes/{chat_id}.md", [DATA])
    if not path.exists():
        return {"ok": True, "chat_id": chat_id, "lines": [], "exists": False}
    raw = path.read_text(encoding="utf-8")
    lines = [x for x in raw.splitlines() if x.strip()]
    return {
        "ok": True, "chat_id": chat_id, "exists": True,
        "lines": lines, "total_lines": len(lines),
        "raw": raw,
    }


class NoteLineReq(BaseModel):
    chat_id: str = Field(max_length=20)
    content: str = Field(min_length=1, max_length=1000)


@router.post("/notes/{chat_id}/lines", summary="追加一条笔记")
async def notes_append(
    chat_id: str, body: NoteLineReq,
    user: auth.CurrentUser = Depends(auth.require_user),
):
    if not chat_id.isdigit():
        raise HTTPException(400, "会话 ID 必须是数字")
    path = _safe(f"data/notes/{chat_id}.md", [DATA])
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = [x for x in old.splitlines() if x.strip()]
    lines.append(body.content.strip())
    bak = atomic_write_text(path, "\n".join(lines) + "\n")
    auth.audit(user, "note_append", chat_id, body.content)
    return {"ok": True, "total_lines": len(lines), "backup": bak.name if bak else None}


class NoteEditReq(BaseModel):
    index: int = Field(ge=0)
    content: str = Field(min_length=1, max_length=1000)


@router.put("/notes/{chat_id}/lines", summary="修改第 N 条笔记")
async def notes_edit(
    chat_id: str, body: NoteEditReq,
    user: auth.CurrentUser = Depends(auth.require_user),
):
    if not chat_id.isdigit():
        raise HTTPException(400, "会话 ID 必须是数字")
    path = _safe(f"data/notes/{chat_id}.md", [DATA])
    if not path.exists():
        raise HTTPException(404, "笔记本不存在")
    lines = [x for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    if body.index >= len(lines):
        raise HTTPException(400, f"索引超范围（共 {len(lines)} 条）")
    old = lines[body.index]
    lines[body.index] = body.content.strip()
    atomic_write_text(path, "\n".join(lines) + "\n")
    auth.audit(user, "note_edit", chat_id, f"[{body.index}] {old} -> {body.content}")
    return {"ok": True, "index": body.index, "old": old, "new": body.content}


@router.delete("/notes/{chat_id}/lines", summary="删除第 N 条笔记")
async def notes_del_line(
    chat_id: str, index: int = Query(..., ge=0),
    user: auth.CurrentUser = Depends(auth.require_user),
):
    if not chat_id.isdigit():
        raise HTTPException(400, "会话 ID 必须是数字")
    path = _safe(f"data/notes/{chat_id}.md", [DATA])
    if not path.exists():
        raise HTTPException(404, "笔记本不存在")
    lines = [x for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    if index >= len(lines):
        raise HTTPException(400, f"索引超范围（共 {len(lines)} 条）")
    removed = lines.pop(index)
    atomic_write_text(path, "\n".join(lines) + "\n")
    auth.audit(user, "note_delete", chat_id, f"[{index}] {removed}")
    return {"ok": True, "removed": removed, "total_lines": len(lines)}


# ── 短期记忆 ──────────────────────────────────────────────

@router.get("/stm", summary="短期记忆会话列表")
async def stm_list():
    if not STM.exists():
        return {"ok": True, "items": []}
    items = []
    for p in sorted(STM.glob("stm_*.json")):
        try:
            obj = read_json(p, [])
            st = p.stat()
            items.append({
                "chat_id": p.stem.replace("stm_", ""),
                "file": p.name,
                "entries": len(obj) if isinstance(obj, list) else 0,
                "size": st.st_size,
                "mtime": datetime.fromtimestamp(st.st_mtime, CST).strftime("%Y-%m-%d %H:%M:%S"),
            })
        except Exception:
            pass
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return {"ok": True, "items": items}


@router.get("/stm/{chat_id}", summary="读某会话的短期记忆")
async def stm_read(
    chat_id: str,
    limit: int = Query(200, ge=1, le=2000),
    tag: str = Query("", max_length=30),
):
    if not chat_id.isdigit():
        raise HTTPException(400, "会话 ID 必须是数字")
    path = _safe(f"data/stm/stm_{chat_id}.json", [DATA])
    obj = read_json(path, [])
    if not isinstance(obj, list):
        obj = []
    if tag:
        obj = [x for x in obj if isinstance(x, dict) and tag in str(x.get("tag", ""))]
    return {"ok": True, "chat_id": chat_id, "total": len(obj), "items": obj[-limit:]}


# ── 自身认知 ──────────────────────────────────────────────

@router.get("/self-knowledge", summary="自身认知文档")
async def self_knowledge():
    path = DATA / "self_knowledge.md"
    if not path.exists():
        raise HTTPException(404, "self_knowledge.md 不存在")
    raw = path.read_text(encoding="utf-8")
    # 按 ## 切章节
    sections = []
    cur = {"title": "(前言)", "body": ""}
    for line in raw.splitlines():
        if line.startswith("## "):
            if cur["body"].strip() or cur["title"] != "(前言)":
                sections.append(cur)
            cur = {"title": line[3:].strip(), "body": ""}
        else:
            cur["body"] += line + "\n"
    if cur["body"].strip() or cur["title"] != "(前言)":
        sections.append(cur)
    return {
        "ok": True,
        "size": len(raw),
        "sections": [{"title": s["title"], "chars": len(s["body"]), "body": s["body"].strip()}
                     for s in sections],
        "raw": raw,
    }


# ── 技能文件（叠加提示词）───────────────────────────────────

@router.get("/skills", summary="技能提示词文件列表")
async def skills_list():
    if not SKILLS.exists():
        return {"ok": True, "items": []}
    items = []
    for p in sorted(SKILLS.glob("*.md")):
        try:
            st = p.stat()
            items.append({
                "name": p.name,
                "size": st.st_size,
                "mtime": datetime.fromtimestamp(st.st_mtime, CST).strftime("%Y-%m-%d %H:%M:%S"),
            })
        except Exception:
            pass
    return {"ok": True, "items": items}


@router.get("/skills/{name}", summary="读技能文件")
async def skills_read(name: str):
    if "/" in name or "\\" in name or ".." in name or not name.endswith(".md"):
        raise HTTPException(400, "非法文件名")
    path = _safe(f"data/skills/{name}", [DATA])
    if not path.exists():
        raise HTTPException(404, "文件不存在")
    return {"ok": True, "name": name, "raw": path.read_text(encoding="utf-8")}


class FileWriteReq(BaseModel):
    rel_path: str = Field(max_length=200)
    content: str = Field(max_length=200_000)
    confirm: str = Field("", max_length=50)


@router.post("/file", summary="写文件（白名单 + 二次确认）")
async def write_file(body: FileWriteReq, user: auth.CurrentUser = Depends(auth.require_user)):
    """通用文件写入。三道闸：路径在 data/ 内、命中编辑白名单、confirm == 'CONFIRM'。"""
    cfg = config.load()
    try:
        path = assert_in_data(body.rel_path)
    except PathEscape as e:
        auth.audit(user, "file_write_escape", body.rel_path, str(e))
        raise HTTPException(400, str(e))

    rel = body.rel_path.replace("\\", "/").lstrip("./")
    if not glob_match(rel, cfg.editable_globs):
        auth.audit(user, "file_write_denied", rel, "不在可编辑白名单")
        raise HTTPException(
            403,
            f"该路径不在可编辑白名单内。允许：{', '.join(cfg.editable_globs)}",
        )
    if body.confirm != "CONFIRM":
        raise HTTPException(400, "缺少二次确认（需传 confirm='CONFIRM'）")

    old_size = path.stat().st_size if path.exists() else 0
    bak = atomic_write_text(path, body.content)
    auth.audit(user, "file_write", rel, f"{old_size}B -> {len(body.content)}B")
    return {
        "ok": True, "path": rel,
        "old_size": old_size, "new_size": len(body.content),
        "backup": bak.name if bak else None,
    }


@router.get("/file", summary="读文件（白名单，只读）")
async def read_file(rel_path: str = Query(..., max_length=200)):
    cfg = config.load()
    try:
        path = assert_in_data(rel_path)
    except PathEscape as e:
        raise HTTPException(400, str(e))
    if not path.exists() or not path.is_file():
        raise HTTPException(404, "文件不存在")
    rel = rel_path.replace("\\", "/").lstrip("./")
    if not glob_match(rel, cfg.editable_globs + cfg.sensitive_config_globs):
        raise HTTPException(403, "该路径不允许在面板中读取")
    size = path.stat().st_size
    if size > 500_000:
        raise HTTPException(413, f"文件过大（{size} 字节），请下载查看")
    return {"ok": True, "path": rel, "size": size, "raw": path.read_text(encoding="utf-8")}

"""提示词编辑：data/skills/*.md 与 self_knowledge.md 的在线读写

这些文件是 bot 的"行为说明书"。改它们的风险跟改代码不一样 ——
改坏了 bot 不崩，但**会变得很蠢**（说话不像人、答非所问），
而且很难察觉。所以这里除了备份回滚，还额外提供两样：

  1. **章节结构解析** —— 让你看清这份提示词是由哪些章节拼起来的
  2. **审计提示** —— 改提示词后，LLM 的行为变化不会立刻显现，
     前端应该提示"改了之后要观察几轮对话"

热加载：skills 目录是运行时读的（`_merge_skills_dir()`），
改完不用重启 bot —— 但**必须确认章节是否被 _OPTIONAL_SECTIONS 管着**，
受开关控制的章节改了不一定生效（开关关了就不注入）。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from panel import auth, security
from panel.config import DATA_DIR, ROOT

router = APIRouter(
    prefix="/prompts", tags=["提示词"],
    dependencies=[Depends(auth.require_user)],
)

SKILLS_DIR = DATA_DIR / "skills"
EXTRA_FILES = {
    "self_knowledge.md": DATA_DIR / "self_knowledge.md",
    "faces_tag_corrected.md": DATA_DIR / "faces_tag_corrected.md",
    "wdsj_help.md": DATA_DIR / "wdsj_help.md",
}

# 这些章节受实验开关控制：开关关了就不注入，改了也不生效
OPTIONAL_HINT = {
    "face_lib": "face_inline 开关控制",
    "60_face_lib": "face_inline 开关控制",
    "private_format": "私聊格式，常驻",
}


def _safe_prompt_path(name: str) -> Path:
    """只允许 skills 目录下的 .md，或 EXTRA_FILES 里登记的少数几个"""
    if "/" in name or "\\" in name or ".." in name:
        raise HTTPException(400, "非法文件名")
    if Path(name).name != name:
        raise HTTPException(400, "只允许文件名")

    if name in EXTRA_FILES:
        return EXTRA_FILES[name]

    if not name.endswith(".md"):
        raise HTTPException(400, "只允许 .md 文件")
    p = (SKILLS_DIR / name).resolve()
    base = SKILLS_DIR.resolve()
    if base not in p.parents:
        raise HTTPException(400, "路径越界")
    return p


def _outline(text: str) -> dict:
    """解析出这份提示词的结构，让人一眼看清它由什么构成。

    两种标记：
      `## 章节名`     —— 常规 markdown 标题
      `<section id="x">` —— 部分文件用的显式标记
    """
    lines = text.splitlines()
    sections: list[dict] = []
    cur: dict | None = None

    for i, line in enumerate(lines, 1):
        m = re.match(r"^(#{1,4})\s+(.+?)\s*$", line)
        m2 = re.match(r'^\s*<section\s+id="([^"]+)"', line)
        if m:
            if cur:
                cur["end"] = i - 1
                cur["lines"] = cur["end"] - cur["start"] + 1
                sections.append(cur)
            cur = {"level": len(m.group(1)), "title": m.group(2).strip(),
                   "start": i, "end": i, "lines": 1, "marker": "md"}
        elif m2:
            if cur:
                cur["end"] = i - 1
                cur["lines"] = cur["end"] - cur["start"] + 1
                sections.append(cur)
            cur = {"level": 0, "title": m2.group(1), "start": i,
                   "end": i, "lines": 1, "marker": "section"}
    if cur:
        cur["end"] = len(lines)
        cur["lines"] = cur["end"] - cur["start"] + 1
        sections.append(cur)

    return {
        "total_lines": len(lines),
        "total_chars": len(text),
        "sections": sections,
    }


@router.get("", summary="提示词文件列表")
async def list_prompts():
    out = []
    if SKILLS_DIR.is_dir():
        for f in sorted(SKILLS_DIR.glob("*.md")):
            try:
                st = f.stat()
            except Exception:
                continue
            out.append({
                "name": f.name,
                "group": "skills",
                "size": st.st_size,
                "mtime": int(st.st_mtime),
            })
    for name, p in EXTRA_FILES.items():
        if p.is_file():
            try:
                st = p.stat()
            except Exception:
                continue
            out.append({
                "name": name,
                "group": "extra",
                "size": st.st_size,
                "mtime": int(st.st_mtime),
            })
    total = sum(x["size"] for x in out)
    return {"ok": True, "count": len(out), "total_size": total, "files": out}


@router.get("/{name}", summary="读提示词（含结构解析）")
async def read_prompt(name: str):
    p = _safe_prompt_path(name)
    if not p.is_file():
        raise HTTPException(404, f"文件不存在：{name}")
    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        raise HTTPException(500, f"读取失败：{e}")

    return {
        "ok": True,
        "name": name,
        "size": len(text.encode("utf-8")),
        "content": text,
        "outline": _outline(text),
        "hot_reload": name.startswith("60_") or name in EXTRA_FILES,
        "hint": OPTIONAL_HINT.get(name.replace(".md", ""), ""),
    }


# ── 写操作 ────────────────────────────────────────────────

class PromptWriteReq(BaseModel):
    content: str = Field(max_length=500_000)
    confirm: str = Field("", max_length=80)


@router.put("/{name}", summary="写提示词（备份 + 可回滚）")
async def write_prompt(
    name: str,
    body: PromptWriteReq,
    user: auth.CurrentUser = Depends(auth.require_user),
):
    p = _safe_prompt_path(name)
    if not p.is_file():
        raise HTTPException(404, f"文件不存在：{name}")

    if body.confirm != name:
        raise HTTPException(
            400, f"改提示词需确认：请在 confirm 里原样填入文件名 {name}"
        )

    if not body.content.strip():
        raise HTTPException(400, "内容不能为空 —— 空提示词会让 bot 行为失控")

    old_size = p.stat().st_size
    bak = security.tracked_write_text(
        p, body.content, kind="write_prompt", note=f"面板改提示词 {name}"
    )
    auth.audit(user, "prompt_write", name,
               f"{old_size} → {len(body.content.encode('utf-8'))} 字节")

    return {
        "ok": True,
        "name": name,
        "old_size": old_size,
        "new_size": len(body.content.encode("utf-8")),
        "backup": bak.name if bak else "",
        "need_restart": not name.startswith("60_"),
        "note": (
            "skills 目录是运行时读取的，改完通常立即生效；"
            "但如果该章节受实验开关控制且开关是关的，改了不会注入。"
        ),
    }


class PromptCreateReq(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    content: str = Field("", max_length=500_000)
    confirm: str = Field("", max_length=20)


@router.post("", summary="新建提示词章节")
async def create_prompt(
    body: PromptCreateReq,
    user: auth.CurrentUser = Depends(auth.require_user),
):
    """新建一个提示词文件。

    命名建议：`<两位数字>_<名字>.md`，数字决定注入顺序
    （已有 00_core / 10_format_group / 11_format_private / 20_command_tools …）。
    不带数字也能建，但顺序会排在带数字的后面。
    """
    if body.confirm != "CREATE":
        raise HTTPException(400, "新建需确认：请在 confirm 里填入 CREATE")

    nm = body.name
    if not nm.endswith(".md"):
        nm += ".md"
    if "/" in nm or "\\" in nm or ".." in nm or Path(nm).name != nm:
        raise HTTPException(400, "非法文件名")
    if not re.match(r"^[A-Za-z0-9_\u4e00-\u9fff\-]{1,60}\.md$", nm):
        raise HTTPException(400, "文件名只允许中英文数字下划线与短横")

    p = SKILLS_DIR / nm
    if p.exists():
        raise HTTPException(409, f"{nm} 已存在")

    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    security.tracked_write_text(
        p, body.content or f"# {nm[:-3]}\n\n", kind="create_prompt",
        note=f"新建提示词 {nm}",
    )
    auth.audit(user, "prompt_create", nm, f"{len(body.content)} 字符")
    return {"ok": True, "name": nm,
            "hint": "新建后需在 services/llm.py 的章节配置里登记才会被注入"}


class PromptDeleteReq(BaseModel):
    confirm: str = Field("", max_length=20)


@router.delete("/{name}", summary="删除提示词（移入回收目录）")
async def delete_prompt(
    name: str,
    body: PromptDeleteReq,
    user: auth.CurrentUser = Depends(auth.require_user),
):
    """删除提示词。同样不真删 —— 移入 data/panel_trash/。

    删提示词比删图片危险得多（bot 会突然少一块行为约束，
    而且你往往要过很久才察觉），所以这里强制二次确认。
    """
    if body.confirm != "DELETE":
        raise HTTPException(400, "删除需确认：请在 confirm 里填入 DELETE")

    p = _safe_prompt_path(name)
    if not p.is_file():
        raise HTTPException(404, f"文件不存在：{name}")

    # self_knowledge 是自认知的核心，不允许删
    if name in EXTRA_FILES:
        raise HTTPException(403, f"{name} 属于核心文件，不允许删除（可编辑）")

    import shutil
    import time
    trash = DATA_DIR / "panel_trash" / f"prompt_{time.strftime('%Y%m%d_%H%M%S')}"
    trash.mkdir(parents=True, exist_ok=True)
    shutil.move(str(p), str(trash / p.name))

    auth.audit(user, "prompt_delete", name, f"移入 {trash.name}")
    return {"ok": True, "name": name,
            "trash": trash.relative_to(ROOT).as_posix()}


@router.get("/{name}/backups", summary="提示词的历史备份")
async def prompt_backups(name: str, limit: int = Query(30, ge=1, le=200)):
    p = _safe_prompt_path(name)
    out = []
    for f in p.parent.glob(f"{p.name}.bak*"):
        try:
            st = f.stat()
        except Exception:
            continue
        out.append({"file": f.name, "size": st.st_size,
                    "mtime": int(st.st_mtime)})
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return {"ok": True, "count": len(out), "backups": out[:limit]}


class PromptRestoreReq(BaseModel):
    backup: str = Field(max_length=200)
    confirm: str = Field("", max_length=80)


@router.post("/{name}/restore", summary="从备份还原提示词")
async def restore_prompt(
    name: str,
    body: PromptRestoreReq,
    user: auth.CurrentUser = Depends(auth.require_user),
):
    if body.confirm != name:
        raise HTTPException(
            400, f"还原需确认：请在 confirm 里原样填入文件名 {name}"
        )
    p = _safe_prompt_path(name)
    if "/" in body.backup or "\\" in body.backup or ".." in body.backup:
        raise HTTPException(400, "非法备份文件名")
    if not body.backup.startswith(p.name):
        raise HTTPException(400, "备份文件名不匹配")

    bp = p.parent / body.backup
    if not bp.is_file():
        raise HTTPException(404, f"备份不存在：{body.backup}")

    content = bp.read_text(encoding="utf-8", errors="ignore")
    bak = security.tracked_write_text(
        p, content, kind="restore_prompt",
        note=f"从 {body.backup} 还原 {name}",
    )
    auth.audit(user, "prompt_restore", name, f"来源 {body.backup}")
    return {"ok": True, "name": name, "from": body.backup,
            "backup": bak.name if bak else ""}

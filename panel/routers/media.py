"""分类图片管理：表情库 / 生图产物 / 临时图 / 撤回留图

这块的存在理由是真实的：`data/recall_images` 已经涨到 3.8G / 12800+ 张，
而 `modules/recall.py` 里**没有任何清理逻辑** —— 它只管下载不管回收。
`_RECALL_TTL` 管的是消息记录的 14 天，图片是永久堆积的。
磁盘 30G 可用时看着没事，但它只增不减。

所以这里提供的是"看得见 + 能筛 + 能删"：
  - 按目录分类列出，显示每类总大小与数量
  - 支持按大小/时间排序，方便"找出占地方的大文件"
  - 删除走二次确认 + 移入回收目录（不直接 unlink，留后悔余地）

四个目录的语义：
  data/faces            表情库，122 张，**bot 在用的**，删了表情功能就哑了
  data/agnes_output     生图产物，可清
  data/img_temp         临时图，可清
  data/recall_images    聊天抓图，可清（但清了之后撤回就复现不出原图）
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from panel import auth, security
from panel.config import DATA_DIR, ROOT

router = APIRouter(
    prefix="/media", tags=["图片管理"],
    dependencies=[Depends(auth.require_user)],
)

# 分类定义：目录 + 语义 + 是否允许删
CATEGORIES: dict[str, dict] = {
    "faces": {
        "dir": DATA_DIR / "faces",
        "label": "表情库",
        "desc": "bot 正在使用的表情包。删除会导致该情绪词失效。",
        "deletable": True,
        "warn": "这些是 bot 在用的表情，删掉后对应情绪词会发不出图。",
    },
    "agnes": {
        "dir": DATA_DIR / "agnes_output",
        "label": "生图产物",
        "desc": "AI 生成的图片，留档用，可随时清理。",
        "deletable": True,
        "warn": "",
    },
    "img_temp": {
        "dir": DATA_DIR / "img_temp",
        "label": "临时图",
        "desc": "处理过程中的中间文件，可随时清理。",
        "deletable": True,
        "warn": "",
    },
    "recall_images": {
        "dir": DATA_DIR / "recall_images",
        "label": "撤回留图",
        "desc": "群里被撤回消息的原图。清理后撤回记录只能显示文字。",
        "deletable": True,
        "warn": "删掉后，旧的撤回记录无法再复现原图（文字记录仍在）。",
    },
}

# 删除不是真删 —— 先移到这里，一周后自己确认再清
_TRASH = DATA_DIR / "panel_trash"

_IMG_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".apng"}


def _cat(name: str) -> dict:
    c = CATEGORIES.get(name)
    if not c:
        raise HTTPException(404, f"未知分类 {name}，可选：{list(CATEGORIES)}")
    return c


def _dir_stats(d: Path) -> dict:
    """统计一个目录的数量与总大小。不递归 —— 这些目录都是平的。"""
    if not d.is_dir():
        return {"exists": False, "count": 0, "size": 0}
    count = 0
    size = 0
    try:
        for f in d.iterdir():
            if f.is_file():
                count += 1
                try:
                    size += f.stat().st_size
                except Exception:
                    pass
    except Exception:
        pass
    return {"exists": True, "count": count, "size": size}


@router.get("/categories", summary="图片分类总览（数量 + 占用）")
async def categories():
    out = []
    total_size = 0
    total_count = 0
    for key, c in CATEGORIES.items():
        st = _dir_stats(c["dir"])
        total_size += st["size"]
        total_count += st["count"]
        out.append({
            "key": key,
            "label": c["label"],
            "desc": c["desc"],
            "deletable": c["deletable"],
            "warn": c["warn"],
            **st,
        })
    out.sort(key=lambda x: x["size"], reverse=True)
    return {"ok": True, "total_size": total_size, "total_count": total_count,
            "categories": out}


@router.get("/{category}", summary="某分类的图片列表")
async def list_images(
    category: str,
    sort: str = Query("size", pattern="^(size|mtime|name)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    ext: str = Query("", max_length=20, description="按扩展名筛，如 png"),
    min_kb: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
):
    c = _cat(category)
    d: Path = c["dir"]
    if not d.is_dir():
        return {"ok": True, "category": category, "count": 0, "total": 0, "images": []}

    rows = []
    try:
        for f in d.iterdir():
            if not f.is_file():
                continue
            if ext and f.suffix.lower().lstrip(".") != ext.lower().lstrip("."):
                continue
            try:
                st = f.stat()
            except Exception:
                continue
            if min_kb and st.st_size < min_kb * 1024:
                continue
            rows.append({
                "name": f.name,
                "size": st.st_size,
                "mtime": int(st.st_mtime),
                "ext": f.suffix.lower().lstrip("."),
            })
    except Exception as e:
        raise HTTPException(500, f"读取目录失败：{e}")

    key = {"size": "size", "mtime": "mtime", "name": "name"}[sort]
    rows.sort(key=lambda x: x[key], reverse=(order == "desc"))
    total = len(rows)

    return {
        "ok": True,
        "category": category,
        "label": c["label"],
        "total": total,
        "count": len(rows[offset:offset + limit]),
        "total_size": sum(r["size"] for r in rows),
        "images": rows[offset:offset + limit],
    }


@router.get("/{category}/thumb/{name}", summary="取单张图片（前端缩略图/预览）")
async def get_image(category: str, name: str):
    """直接返回图片字节，让前端 <img> 能显示。

    只允许取分类目录下的**单层文件名** —— 路径穿越在这里是最容易被
    忽视的口子（`name` 是完全可控的用户输入）。
    """
    from fastapi.responses import FileResponse

    c = _cat(category)
    if "/" in name or "\\" in name or ".." in name:
        raise HTTPException(400, "非法文件名")
    if Path(name).name != name:
        raise HTTPException(400, "只允许文件名，不允许路径")
    if Path(name).suffix.lower() not in _IMG_EXT:
        raise HTTPException(400, "不是图片文件")

    p = c["dir"] / name
    p = p.resolve()
    # 双保险：解析后必须仍在分类目录内
    try:
        base = c["dir"].resolve()
    except Exception:
        base = c["dir"]
    if p != base and base not in p.parents:
        raise HTTPException(400, "路径越界")
    if not p.is_file():
        raise HTTPException(404, "图片不存在")

    return FileResponse(p)


# ── 写操作 ────────────────────────────────────────────────

class DeleteReq(BaseModel):
    category: str = Field(max_length=40)
    names: list[str] = Field(min_length=1, max_length=500)
    confirm: str = Field("", max_length=40)


@router.post("/delete", summary="删除图片（移入回收目录，非真删）")
async def delete_images(
    body: DeleteReq,
    user: auth.CurrentUser = Depends(auth.require_user),
):
    """删除策略：移入 data/panel_trash/，不直接 unlink。

    理由：图片管理是最容易手滑的地方（尤其"清空这一页"），
    真删了就找不回来。移走的话随时能捞。
    """
    if body.confirm != "DELETE":
        raise HTTPException(400, "删除需确认：请在 confirm 里填入 DELETE")

    c = _cat(body.category)
    if not c["deletable"]:
        raise HTTPException(403, f"{c['label']} 不允许删除")

    src_dir: Path = c["dir"]
    stamp = time.strftime("%Y%m%d_%H%M%S")
    trash = _TRASH / f"{body.category}_{stamp}"
    moved: list[str] = []
    failed: list[dict] = []

    for name in body.names:
        if "/" in name or "\\" in name or Path(name).name != name:
            failed.append({"name": name, "why": "非法文件名"})
            continue
        p = src_dir / name
        if not p.is_file():
            failed.append({"name": name, "why": "不存在"})
            continue
        try:
            trash.mkdir(parents=True, exist_ok=True)
            target = trash / name
            if target.exists():
                target = trash / f"{int(time.time())}_{name}"
            shutil.move(str(p), str(target))
            moved.append(name)
        except Exception as e:
            failed.append({"name": name, "why": str(e)[:80]})

    freed = 0
    for name in moved:
        try:
            freed += (trash / name).stat().st_size
        except Exception:
            pass

    auth.audit(
        user, "media_delete", body.category,
        f"删除 {len(moved)} 个，失败 {len(failed)} 个，回收目录 {trash.name}",
    )
    return {
        "ok": True,
        "deleted": len(moved),
        "failed": failed,
        "freed": freed,
        "trash_dir": str(trash.relative_to(ROOT)) if trash.exists() else "",
    }


@router.get("/trash/list", summary="回收目录里有什么")
async def trash_list():
    if not _TRASH.is_dir():
        return {"ok": True, "count": 0, "size": 0, "batches": []}
    batches = []
    total = 0
    for d in sorted(_TRASH.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        st = _dir_stats(d)
        total += st["size"]
        try:
            mt = int(d.stat().st_mtime)
        except Exception:
            mt = 0
        batches.append({"name": d.name, "count": st["count"],
                        "size": st["size"], "mtime": mt})
    return {"ok": True, "count": len(batches), "size": total, "batches": batches}


class PurgeReq(BaseModel):
    batch: str = Field(max_length=120)
    confirm: str = Field("", max_length=40)


@router.post("/trash/purge", summary="彻底清空某批回收文件")
async def trash_purge(
    body: PurgeReq,
    user: auth.CurrentUser = Depends(auth.require_user),
):
    if body.confirm != "PURGE":
        raise HTTPException(400, "彻底删除需确认：请在 confirm 里填入 PURGE")
    if "/" in body.batch or "\\" in body.batch or ".." in body.batch:
        raise HTTPException(400, "非法批次名")
    d = _TRASH / body.batch
    if not d.is_dir():
        raise HTTPException(404, "批次不存在")
    d = d.resolve()
    try:
        base = _TRASH.resolve()
    except Exception:
        base = _TRASH
    if base not in d.parents:
        raise HTTPException(400, "路径越界")

    n = 0
    for f in d.iterdir():
        if f.is_file():
            f.unlink()
            n += 1
    try:
        d.rmdir()
    except Exception:
        pass
    auth.audit(user, "media_purge", body.batch, f"彻底删除 {n} 个")
    return {"ok": True, "purged": n}

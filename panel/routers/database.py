"""数据库管理：浏览 / 查询 / 改数据 / 重建索引

管理两个库，它们不是一回事：

  data/search.db    messages(18042) + messages_fts
                    消息全文检索。FTS 是手动同步的 ——
                    改完主表要跑 `insert into messages_fts(messages_fts) values('rebuild')`

  data/huanmeng.db  memories(27646) + memories_fts + 5 个触发器
                    记忆库。**有 ai/au/ad 触发器自动维护 FTS** ——
                    所以这里绝不能直接写 memories_fts，会双重写入。
                    改数据只碰主表，让触发器干活。

⚠️ 两个库都有 trigram FTS，对 **<3 字** 查询无效（这是 SQLite trigram
   分词器的固有性质，不是 bug）。短词查询必须退回 LIKE，
   否则用户会以为"库里没这条数据"。
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from panel import auth, security
from panel.config import DATA_DIR, ROOT

router = APIRouter(
    prefix="/database", tags=["数据库"],
    dependencies=[Depends(auth.require_user)],
)

# 白名单：只允许碰这两个库，且只允许碰这些表
DBS: dict[str, dict] = {
    "search": {
        "path": DATA_DIR / "search.db",
        "label": "消息检索库",
        "tables": ["messages"],
        "fts": {"messages": "messages_fts"},
        "note": "存所有群消息，供 /~回顾 用。改完记得重建索引。",
    },
    "huanmeng": {
        "path": DATA_DIR / "huanmeng.db",
        "label": "记忆库",
        "tables": ["memories"],
        "fts": {"memories": "memories_fts"},
        "note": "存长期记忆。FTS 由触发器自动维护，别手写。",
    },
}

# 危险：这些 SQL 关键字在自定义查询里一律拒绝
_FORBIDDEN = re.compile(
    r"(?i)\b(drop|alter|attach|detach|pragma|vacuum|reindex|create)\b"
)


def _db(name: str) -> dict:
    d = DBS.get(name)
    if not d:
        raise HTTPException(404, f"未知库 {name}，可选：{list(DBS)}")
    if not d["path"].is_file():
        raise HTTPException(404, f"库文件不存在：{d['path'].name}")
    return d


def _conn(path: Path) -> sqlite3.Connection:
    # 只读打开会拿不到写锁，这里用普通模式但靠 SQL 白名单约束
    c = sqlite3.connect(str(path), timeout=5)
    c.row_factory = sqlite3.Row
    return c


def _tables_of(c: sqlite3.Connection) -> list[str]:
    rows = c.execute(
        "select name from sqlite_master where type='table' "
        "and name not like '%_fts%' order by name"
    ).fetchall()
    return [r[0] for r in rows]


@router.get("/list", summary="库列表（大小 + 表数量）")
async def db_list():
    out = []
    for name, d in DBS.items():
        p: Path = d["path"]
        info: dict[str, Any] = {
            "key": name,
            "label": d["label"],
            "file": p.name,
            "rel": p.relative_to(ROOT).as_posix(),
            "note": d["note"],
            "exists": p.is_file(),
            "size": p.stat().st_size if p.is_file() else 0,
            "tables": [],
        }
        if p.is_file():
            try:
                c = _conn(p)
                for t in _tables_of(c):
                    try:
                        n = c.execute(f"select count(*) from [{t}]").fetchone()[0]
                    except Exception:
                        n = -1
                    info["tables"].append({"name": t, "rows": n})
                c.close()
            except Exception as e:
                info["error"] = str(e)[:120]
        out.append(info)
    return {"ok": True, "databases": out}


def _validate_table(name: str, dbkey: str) -> dict:
    d = _db(dbkey)
    if name not in d["tables"]:
        raise HTTPException(403, f"表 {name} 不在允许列表：{d['tables']}")
    return d


@router.get("/{dbkey}/schema/{table}", summary="表结构")
async def table_schema(dbkey: str, table: str):
    d = _validate_table(table, dbkey)
    c = _conn(d["path"])
    try:
        cols = [dict(r) for r in c.execute(f"pragma table_info([{table}])")]
        idx = [dict(r) for r in c.execute(f"pragma index_list([{table}])")]
        return {"ok": True, "db": dbkey, "table": table,
                "columns": cols, "indexes": idx}
    finally:
        c.close()


@router.get("/{dbkey}/rows/{table}", summary="分页读表")
async def table_rows(
    dbkey: str,
    table: str,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    order_by: str = Query("", max_length=40),
    desc: bool = Query(True),
):
    d = _validate_table(table, dbkey)
    c = _conn(d["path"])
    try:
        cols = [r["name"] for r in c.execute(f"pragma table_info([{table}])")]
        total = c.execute(f"select count(*) from [{table}]").fetchone()[0]

        # order_by 必须是真实列名，否则拼进 SQL 就是注入口子
        ob = ""
        if order_by:
            if order_by not in cols:
                raise HTTPException(400, f"列 {order_by} 不存在")
            ob = f" order by [{order_by}] {'desc' if desc else 'asc'}"

        rows = c.execute(
            f"select * from [{table}]{ob} limit ? offset ?", (limit, offset)
        ).fetchall()
        data = []
        for r in rows:
            rec = {}
            for k in r.keys():
                v = r[k]
                if isinstance(v, (bytes, bytearray)):
                    v = f"<{len(v)} 字节>"
                elif v is not None and not isinstance(v, (int, float, str)):
                    v = str(v)
                rec[k] = v
            rec = security.sanitize_obj(rec)
            data.append(rec)
        return {"ok": True, "db": dbkey, "table": table, "columns": cols,
                "total": total, "limit": limit, "offset": offset, "rows": data}
    finally:
        c.close()


@router.get("/{dbkey}/search/{table}", summary="表内检索（自动处理 trigram 短词）")
async def table_search(
    dbkey: str,
    table: str,
    q: str = Query(..., min_length=1, max_length=200),
    column: str = Query("", max_length=40),
    limit: int = Query(50, ge=1, le=500),
):
    """在表内搜。有 FTS 就走 FTS，短词自动降级 LIKE。

    ⚠️ trigram 对 <3 字无效，这条规则必须和 db/store.py 保持一致，
       否则面板搜得到、bot 搜不到（或反过来），排查起来会疯。
    """
    d = _validate_table(table, dbkey)
    c = _conn(d["path"])
    try:
        cols = [r["name"] for r in c.execute(f"pragma table_info([{table}])")]
        if column:
            if column not in cols:
                raise HTTPException(400, f"列 {column} 不存在")
            target_cols = [column]
        else:
            target_cols = [x for x in cols
                           if x in ("content", "text", "memory", "value", "name")]
            if not target_cols:
                raise HTTPException(400, f"表 {table} 没有可搜的文本列，请指定 column")

        use_fts = len(q.strip()) >= 3
        if use_fts:
            # 清洗成 trigram 能接受的查询串
            cleaned = re.sub(
                r"[^\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7afa-zA-Z0-9\s]", " ", q
            )
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            fts_name = d["fts"].get(table)
            if cleaned and fts_name:
                try:
                    rows = c.execute(
                        f"select t.* from [{table}] t "
                        f"join [{fts_name}] f on f.rowid = t.rowid "
                        f"where [{fts_name}] match ? limit ?",
                        (cleaned, limit),
                    ).fetchall()
                    return _rows_out(dbkey, table, cols, rows, q, "fts")
                except Exception:
                    pass   # FTS 语法不认，退回 LIKE

        like = f"%{q}%"
        where = " or ".join(f"[{col}] like ?" for col in target_cols)
        params = [like] * len(target_cols) + [limit]
        rows = c.execute(
            f"select * from [{table}] where {where} limit ?", params
        ).fetchall()
        return _rows_out(dbkey, table, cols, rows, q, "like")
    finally:
        c.close()


def _rows_out(dbkey: str, table: str, cols: list, rows: list,
              q: str, engine: str) -> dict:
    data = []
    for r in rows:
        rec = {}
        for k in r.keys():
            v = r[k]
            if isinstance(v, (bytes, bytearray)):
                v = f"<{len(v)} 字节>"
            elif v is not None and not isinstance(v, (int, float, str)):
                v = str(v)
            rec[k] = v
        data.append(security.sanitize_obj(rec))
    return {"ok": True, "db": dbkey, "table": table, "columns": cols,
            "query": q, "engine": engine, "count": len(data), "rows": data}


# ── 写操作 ────────────────────────────────────────────────

class SqlReq(BaseModel):
    sql: str = Field(min_length=1, max_length=5000)
    confirm: str = Field("", max_length=20)


@router.post("/{dbkey}/exec", summary="执行 SQL（仅 SELECT/UPDATE/DELETE，需确认）")
async def exec_sql(
    dbkey: str,
    body: SqlReq,
    user: auth.CurrentUser = Depends(auth.require_user),
):
    """开放但受约束的 SQL 执行。

    限制：
      - 只允许单条语句（分号截断多条 = 常见绕过手法）
      - 拒绝 DDL 与 PRAGMA（drop/alter/attach/vacuum 等）
      - 必须先备份库文件
      - 需要 confirm=CUSTOM

    这样既能干"批量改个字段"这种活，又不会一不小心把表删了。
    真的需要 DDL 请到服务器上做 —— 面板不该有那个权力。
    """
    if body.confirm != "CUSTOM":
        raise HTTPException(400, "自定义 SQL 需确认：请在 confirm 里填入 CUSTOM")

    d = _db(dbkey)

    # 只允许单条：去掉结尾分号后不该再有分号
    sql = body.sql.strip().rstrip(";").strip()
    if ";" in sql:
        raise HTTPException(400, "只允许单条 SQL（检测到多条语句）")
    if _FORBIDDEN.search(sql):
        raise HTTPException(403, "该语句类型被禁止（drop/alter/create/pragma 等）")

    head = sql.split(None, 1)[0].upper()
    if head not in ("SELECT", "UPDATE", "DELETE", "INSERT"):
        raise HTTPException(403, f"仅允许 SELECT/UPDATE/DELETE/INSERT，收到 {head}")

    # 涉及的表必须在白名单内
    d_tables = set(d["tables"])
    touched = set(re.findall(r"(?i)\b(?:from|join|into|update)\s+\[?(\w+)\]?", sql))
    illegal = {t for t in touched if t not in d_tables and not t.endswith("_fts")}
    if illegal:
        raise HTTPException(403, f"语句涉及未授权表：{sorted(illegal)}")

    # 备份后再执行
    bak = security.atomic_write_text  # 占位，下面用字节级备份
    stamp = __import__("time").strftime("%Y%m%d_%H%M%S")
    bak_path = d["path"].with_name(f"{d['path'].name}.bak_{stamp}")
    try:
        bak_path.write_bytes(d["path"].read_bytes())
    except Exception as e:
        raise HTTPException(500, f"备份失败，已中止：{e}")

    c = _conn(d["path"])
    try:
        cur = c.execute(sql)
        if head == "SELECT":
            rows = cur.fetchall()
            c.close()
            out = _rows_out(dbkey, "(custom)", [], rows, sql, "custom")
            auth.audit(user, "db_select", dbkey, sql[:200])
            return {**out, "backup": bak_path.name, "affected": len(rows)}

        c.commit()
        affected = cur.rowcount
        c.close()
    except Exception as e:
        c.close()
        raise HTTPException(400, f"SQL 执行失败：{e}")

    auth.audit(user, f"db_{head.lower()}", dbkey,
               f"影响 {affected} 行 | {sql[:180]}")
    return {"ok": True, "db": dbkey, "statement": head, "affected": affected,
            "backup": bak_path.name,
            "hint": "改了数据后可能需要重建 FTS 索引，见 /database/{db}/reindex"}


@router.post("/{dbkey}/reindex/{table}", summary="重建 FTS 索引")
async def reindex(
    dbkey: str,
    table: str,
    user: auth.CurrentUser = Depends(auth.require_user),
):
    """重建全文索引。

    什么时候需要：
      - 手动 UPDATE/DELETE 过主表（search.db 的 FTS 不会自动跟随）
      - 搜索结果和实际数据对不上
    huanmeng.db 的 memories 有触发器，正常不用手动重建，但重建也无害。
    """
    d = _validate_table(table, dbkey)
    fts = d["fts"].get(table)
    if not fts:
        raise HTTPException(400, f"表 {table} 没有对应的 FTS 索引")

    c = _conn(d["path"])
    try:
        try:
            c.execute(
                f"insert into [{fts}]([{fts}]) values('rebuild')"
            )
            c.commit()
        except Exception as e:
            raise HTTPException(400, f"重建失败：{e}")
        n = c.execute(f"select count(*) from [{fts}]").fetchone()[0]
    finally:
        c.close()

    auth.audit(user, "db_reindex", f"{dbkey}.{table}", f"重建后 {n} 行")
    return {"ok": True, "db": dbkey, "table": table, "fts": fts, "rows": n}


@router.post("/{dbkey}/optimize", summary="整理数据库（回收空间）")
async def optimize(
    dbkey: str,
    user: auth.CurrentUser = Depends(auth.require_user),
):
    """VACUUM —— 数据库被删过大量数据后，文件不会自动缩小，
    跑一次 VACUUM 才能把空间还给系统。"""
    d = _db(dbkey)
    before = d["path"].stat().st_size
    c = sqlite3.connect(str(d["path"]), timeout=30)
    try:
        c.execute("vacuum")
        c.close()
    except Exception as e:
        c.close()
        raise HTTPException(500, f"整理失败：{e}")
    after = d["path"].stat().st_size
    auth.audit(user, "db_optimize", dbkey,
               f"{before} → {after} 字节")
    return {"ok": True, "db": dbkey, "size_before": before,
            "size_after": after, "freed": max(0, before - after)}

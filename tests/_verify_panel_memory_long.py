#!/usr/bin/env python3
"""面板「长期记忆（分块）」后端接口验收（2026-09-30）

不调 LLM，纯 HTTP + 直接读 DB/md 校验。

覆盖：
  1. 总览 / 分块列表 / 块内分页 / 跨会话检索（FTS 与 LIKE 两条路）
  2. **写往返自测**（唯一能证明"没写坏记忆"的手段）：
     新增 → 编辑 → 删除，每一步都断言 **DB 与 md 两边同步**，最后清理干净
  3. 时间戳按**毫秒**解析（不是秒 —— 按秒解析会显示成 58716 年）
  4. 路由顺序：静态 /long/search 没被 /long/blocks/{chat_id} 吃掉

⚠️ 往返测试一律用**测试专用 conversation_id**（999999999），
   在真实 data/ 目录下建一个独立的 memory_999999999.md，跑完删掉，
   绝不碰任何真实会话的记忆。

用法（服务器）: python3 tests/_verify_panel_memory_long.py
"""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

API = "http://127.0.0.1:59300/api"
PWD = "HuanmengPanel@2026"
TEST_CHAT = 999999999          # 测试专用块，不会与真实会话撞
DB = ROOT / "data" / "huanmeng.db"
MD = ROOT / "data" / ("memory_%d.md" % TEST_CHAT)
REAL_CHAT = 1058782600         # 只读抽查用

PASS = 0
FAIL = 0


def ok(msg):
    global PASS
    PASS += 1
    print("  [OK] %s" % msg)


def bad(msg):
    global FAIL
    FAIL += 1
    print("  [FAIL] %s" % msg)


def db_rows(sql, args=()):
    c = sqlite3.connect(str(DB))
    c.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in c.execute(sql, args).fetchall()]
    finally:
        c.close()


def main():
    c = httpx.Client(timeout=30)
    r = c.post(API + "/auth/login", json={"username": "admin", "password": PWD})
    if r.status_code != 200:
        print("登录失败: %s %s" % (r.status_code, r.text[:200]))
        sys.exit(1)
    tk = r.json()["token"]
    H = {"Authorization": "Bearer " + tk}
    print("登录 OK")

    print()
    print("=== 1. 总览 /long/stats ===")
    d = c.get(API + "/memory/long/stats", headers=H).json()
    print("     %s" % json.dumps(d, ensure_ascii=False))
    if d.get("ok") and d.get("total", 0) > 0:
        ok("总览可用：%d 条 / %d 块（live=%d backfill=%d）"
           % (d["total"], d["blocks"], d["live"], d["backfill"]))
    else:
        bad("总览异常: %s" % d)

    print()
    print("=== 2. 分块列表 /long/blocks ===")
    d = c.get(API + "/memory/long/blocks", headers=H).json()
    blocks = d.get("blocks") or []
    if blocks:
        ok("返回 %d 个会话块" % len(blocks))
        b0 = blocks[0]
        print("     最大块: chat=%s name=%r count=%d live=%d last_time=%s"
              % (b0["conversation_id"], b0["name"], b0["count"],
                 b0["live_count"], b0["last_time"]))
        if b0["count"] > 0 and b0["last_time"]:
            ok("块内条数与最新时间都有值")
        else:
            bad("块字段缺失: %s" % b0)
        # 时间戳必须是毫秒解析（按秒会变成 58716 年）
        if b0["last_time"].startswith(("2025-", "2026-")):
            ok("时间按毫秒解析正确（%s，不是 58716 年）" % b0["last_time"])
        else:
            bad("时间解析可疑: %s" % b0["last_time"])
    else:
        bad("块列表为空: %s" % d)

    print()
    print("=== 3. 块内分页 /long/blocks/%d ===" % REAL_CHAT)
    d = c.get(API + "/memory/long/blocks/%d" % REAL_CHAT,
              headers=H, params={"page": 1, "size": 5}).json()
    items = d.get("items") or []
    if d.get("ok") and items:
        ok("取到 %d 条（该块共 %d 条）" % (len(items), d["total"]))
        print("     首条: %s" % (items[0]["content"] or "")[:80])
        if items[0].get("source"):
            ok("带 source 字段（前端可标注时间可信度）")
        else:
            bad("缺 source")
        # 分页：第 2 页与第 1 页不应重复
        d2 = c.get(API + "/memory/long/blocks/%d" % REAL_CHAT,
                   headers=H, params={"page": 2, "size": 5}).json()
        ids1 = {x["id"] for x in items}
        ids2 = {x["id"] for x in (d2.get("items") or [])}
        if ids1 and ids2 and not (ids1 & ids2):
            ok("分页正确（第 1/2 页无重叠）")
        else:
            bad("分页重叠或第 2 页空: %s / %s" % (sorted(ids1)[:3], sorted(ids2)[:3]))
    else:
        bad("块内分页异常: %s" % d)

    print()
    print("=== 4. 跨会话检索 /long/search（静态路由没被动态吃掉）===")
    d = c.get(API + "/memory/long/search", headers=H, params={"q": "披风", "limit": 10}).json()
    if d.get("ok"):
        ok("检索可用（mode=%s，命中 %d 条）" % (d.get("mode"), d.get("count", 0)))
        if d.get("items"):
            it = d["items"][0]
            if "conversation_id" in it and "name" in it:
                ok("命中项带所属会话（chat=%s name=%r）"
                   % (it["conversation_id"], it.get("name")))
            else:
                bad("命中项缺会话信息: %s" % list(it))
    else:
        bad("检索失败: %s" % d)
    # 短词（<3 字）必须走 LIKE 而不是 FTS（trigram 对短词无效）
    d2 = c.get(API + "/memory/long/search", headers=H, params={"q": "喵", "limit": 5}).json()
    if d2.get("ok") and d2.get("mode") == "like":
        ok("短词自动降级 LIKE（q=喵 → mode=like）")
    elif d2.get("ok"):
        bad("短词没降级: mode=%s" % d2.get("mode"))
    else:
        bad("短词检索失败")

    # ── 以下为写往返：作用域严格限制在 TEST_CHAT ──
    print()
    print("=== 5. 写往返自测（新增 → 编辑 → 删除，两边必须同步）===")
    MD.unlink(missing_ok=True)
    base = c.get(API + "/memory/long/blocks/%d" % TEST_CHAT, headers=H).json()
    if base.get("total") == 0 and not MD.exists():
        ok("起点干净（测试块 chat=%d 无数据、无 md）" % TEST_CHAT)
    else:
        bad("测试块起点不干净：total=%s md=%s" % (base.get("total"), MD.exists()))

    line = "- [验收] 面板写往返测试，可安全删除"
    r = c.post(API + "/memory/long/blocks/%d/lines" % TEST_CHAT, headers=H,
               json={"content": line, "confirm": "CONFIRM"})
    if r.status_code != 200:
        bad("新增失败: %s %s" % (r.status_code, r.text[:200]))
    else:
        mid = r.json()["id"]
        ok("新增成功 id=%d" % mid)
        # DB 侧
        rows = db_rows("SELECT content, source FROM memories WHERE id=?", (mid,))
        if rows and rows[0]["content"] == line:
            ok("DB 侧内容正确（source=%s）" % rows[0]["source"])
        else:
            bad("DB 侧没写进去: %s" % rows)
        # md 侧
        if MD.is_file() and line in MD.read_text(encoding="utf-8"):
            ok("md 侧同步写入（memory_%d.md）" % TEST_CHAT)
        else:
            bad("md 侧没写进去")

        # 编辑
        new_line = "- [验收] 已编辑的内容 v2"
        r2 = c.put(API + "/memory/long/lines/%d" % mid, headers=H,
                   json={"content": new_line, "confirm": "CONFIRM"})
        if r2.status_code == 200 and r2.json().get("md_updated"):
            ok("编辑成功且 md 同步（md_updated=True）")
        else:
            bad("编辑异常: %s" % (r2.text[:200] if r2.status_code != 200 else r2.json()))
        rows = db_rows("SELECT content FROM memories WHERE id=?", (mid,))
        md_txt = MD.read_text(encoding="utf-8") if MD.is_file() else ""
        if rows and rows[0]["content"] == new_line and new_line in md_txt:
            ok("编辑后 DB 与 md 都是新内容")
        else:
            bad("编辑后不一致：db=%r md含=%s"
                % (rows[0]["content"] if rows else None, new_line in md_txt))

        # 删除
        r3 = c.request("DELETE", API + "/memory/long/lines/%d" % mid, headers=H)
        if r3.status_code == 200 and r3.json().get("md_updated"):
            ok("删除成功且 md 同步")
        else:
            bad("删除异常: %s" % (r3.text[:200] if r3.status_code != 200 else r3.json()))
        rows = db_rows("SELECT id FROM memories WHERE id=?", (mid,))
        md_txt = MD.read_text(encoding="utf-8") if MD.is_file() else ""
        if not rows and new_line not in md_txt:
            ok("删除后 DB 与 md 都不含该条")
        else:
            bad("删除不彻底：db=%s md含=%s" % (rows, new_line in md_txt))

    print()
    print("=== 6. 缺二次确认必须拒绝 ===")
    r = c.post(API + "/memory/long/blocks/%d/lines" % TEST_CHAT, headers=H,
               json={"content": "- 不该写进去"})
    if r.status_code == 400:
        ok("没传 confirm=CONFIRM → 400 拒绝")
    else:
        bad("缺确认也放行了: %s" % r.status_code)
        # 兜底清理
        for row in db_rows("SELECT id FROM memories WHERE content LIKE '%%不该写进去%%'"):
            c.request("DELETE", API + "/memory/long/lines/%d" % row["id"], headers=H)

    print()
    print("=== 7. 清理测试痕迹 ===")
    left = db_rows("SELECT COUNT(*) n FROM memories WHERE conversation_id=?", (TEST_CHAT,))
    if left and left[0]["n"] == 0:
        ok("测试块的 DB 记录已清空")
    else:
        bad("测试块还有 %s 条残留" % (left[0]["n"] if left else "?"))
    if MD.exists():
        MD.unlink()
        ok("测试 md 文件已删除")
    else:
        ok("没有残留 md 文件")

    print()
    print("结果: %d passed, %d failed" % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()

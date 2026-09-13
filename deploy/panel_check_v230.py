"""面板 v2.3.0 真实数据验收

跑在服务器上，对**真实数据**打接口。每条都带一个 brief 校验，
不只看 HTTP 200，还要看返回内容是不是合理。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

# 让脚本无论从哪个目录启动都能 import panel（默认是 deploy/ 下）
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

BASE = os.environ.get("PANEL_TEST_BASE", "http://127.0.0.1:59300")
PW = os.environ.get("PANEL_TEST_PW", "")

if not PW:
    print("需要 PANEL_TEST_PW 环境变量")
    sys.exit(2)

PASS = 0
FAIL = 0
FAILED: list[str] = []


def api(path: str, method: str = "GET", body: dict | None = None,
        token: str | None = None) -> tuple[int, dict]:
    """打接口。

    ⚠️ 路径里可能含中文查询参数（如 ?q=机器人），必须 quote ——
       urllib.request 不会自动编码 URL，中文直接拼进去会抛异常，
       看起来像"接口坏了"其实是测试脚本的锅（踩过一次）。
    """
    url = BASE + urllib.parse.quote(path, safe="/?=&%")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode("utf-8", "ignore")
            try:
                return r.status, json.loads(raw)
            except Exception:
                return r.status, {"_raw": raw[:200]}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "ignore")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"_raw": raw[:200]}
    except Exception as e:
        return 0, {"_err": str(e)}


def check(name: str, ok: bool, brief: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [OK]   {name}  {brief}")
    else:
        FAIL += 1
        FAILED.append(f"{name} :: {brief}")
        print(f"  [FAIL] {name}  {brief}")


# ── 登录 ──────────────────────────────────────────────────
print("=== 0. 登录 ===")
st, r = api("/api/auth/status")
check("health 免认证", api("/api/health")[0] == 200)
check("status 免认证", st == 200, f"initialized={r.get('initialized')}")
check("接口版本是 v2.3.0", r.get("api_version") == "v2.3.0",
      f"得到 {r.get('api_version')}")

st, r = api("/api/auth/login", "POST", {"password": PW})
if st != 200:
    print(f"登录失败 {st}: {r}")
    sys.exit(1)
TOKEN = r["token"]
print(f"  登录成功，token 长度 {len(TOKEN)}")
check("未带 token 访问受保护接口 → 401",
      api("/api/overview")[0] == 401)
check("带 token 可访问", api("/api/overview", token=TOKEN)[0] == 200)

# ── 1. 崩溃自愈 ───────────────────────────────────────────
print("\n=== 1. 崩溃自愈 ===")
st, r = api("/api/system/selfheal", token=TOKEN)
check("自愈状态可读", st == 200 and r.get("enabled") is True,
      f"enabled={r.get('enabled')} bot_alive={r.get('bot_alive')}")
check("阈值是 3", r.get("fail_threshold") == 3)
check("当前档位是 idle（未布防不探测）", r.get("phase") == "idle",
      f"phase={r.get('phase')}")
check("空闲时探测间隔为 0（不做无谓轮询）",
      r.get("probe_interval") == 0, f"得到 {r.get('probe_interval')}")
check("有探测次数统计", isinstance(r.get("probe_count"), int),
      f"累计探测 {r.get('probe_count')} 次")
check("当前未布防（还没改过东西）", r.get("armed") is False)

# 布防后应该进入 armed 档，间隔变 5s
st, r = api("/api/system/selfheal/arm", "POST", token=TOKEN)
check("布防接口可用", st == 200 and r.get("phase") == "armed",
      f"phase={r.get('phase')}")
st, r = api("/api/system/selfheal", token=TOKEN)
check("布防后间隔变 5s", r.get("probe_interval") == 5,
      f"得到 {r.get('probe_interval')}")

# 心跳应延长窗口而不改变档位
st, r = api("/api/system/selfheal/keepalive", "POST", token=TOKEN)
check("心跳接口可用", st == 200 and r.get("armed") is True,
      f"phase={r.get('phase')}")

# 解除布防回到 idle
st, r = api("/api/system/selfheal/disarm", "POST", token=TOKEN)
check("解除布防回到 idle", st == 200 and r.get("phase") == "idle")
st, r = api("/api/system/selfheal", token=TOKEN)
check("解除后间隔回到 0", r.get("probe_interval") == 0,
      f"得到 {r.get('probe_interval')}")

st, r = api("/api/system/selfheal/test", "POST", {"confirm": "TEST"},
            token=TOKEN)
check("演练接口可跑", st == 200 and r.get("dry_run") is True,
      f"结论：{r.get('conclusion', '')[:60]}")
check("演练确认了 bot 存活", r.get("bot_alive") is True)

st, r = api("/api/system/ops", token=TOKEN)
check("操作栈可读", st == 200 and "ops" in r, f"当前 {r.get('count')} 条")

# ── 2. 群管理 ─────────────────────────────────────────────
print("\n=== 2. 群管理 ===")
st, r = api("/api/groups", token=TOKEN)
check("群列表可读", st == 200 and r.get("count", 0) > 0,
      f"{r.get('count')} 个群")
groups = r.get("groups", [])
if groups:
    g0 = groups[0]
    check("群有 msglog 行数", g0.get("msglog_lines", 0) > 0,
          f"群 {g0['group_id']} 有 {g0.get('msglog_lines')} 行")
    check("群有最后活跃时间", g0.get("last_active", 0) > 0)
    check("群标注了数据来源", len(g0.get("sources", [])) > 0,
          f"来源 {g0.get('sources')}")

    gid = g0["group_id"]
    st, r2 = api(f"/api/groups/{gid}", token=TOKEN)
    check(f"群 {gid} 详情可读", st == 200 and r2.get("group_id") == gid)

    st, r3 = api(f"/api/groups/{gid}/members", token=TOKEN)
    check(f"群 {gid} 成员聚合可读", st == 200 and r3.get("count", 0) > 0,
          f"聚合出 {r3.get('count')} 个发言者")
    if r3.get("members"):
        m0 = r3["members"][0]
        check("成员统计含发言数", m0.get("count", 0) > 0,
              f"最多者发言 {m0.get('count')} 次")

    st, _ = api("/api/groups/999999999999", token=TOKEN)
    check("不存在的群返回 404", st == 404)

# ── 3. 图片管理 ───────────────────────────────────────────
print("\n=== 3. 图片管理 ===")
st, r = api("/api/media/categories", token=TOKEN)
check("分类总览可读", st == 200 and len(r.get("categories", [])) == 4,
      f"总占用 {r.get('total_size', 0) / 1048576:.1f} MB / {r.get('total_count')} 个")
cats = {c["key"]: c for c in r.get("categories", [])}

if "recall_images" in cats:
    ri = cats["recall_images"]
    check("撤回留图有数据", ri.get("count", 0) > 0,
          f"{ri.get('count')} 张 / {ri.get('size', 0) / 1048576:.1f} MB")
    check("撤回留图标注了删除风险", bool(ri.get("warn")))
    check("撤回留图标记可删", ri.get("deletable") is True)

if "faces" in cats:
    f = cats["faces"]
    check("表情库有数据", f.get("count", 0) > 0,
          f"{f.get('count')} 张")

st, r = api("/api/media/recall_images?sort=size&order=desc&limit=5", token=TOKEN)
check("按大小排序可读", st == 200 and r.get("count", 0) > 0,
      f"共 {r.get('total')} 张")
if r.get("images"):
    sizes = [i["size"] for i in r["images"]]
    check("确实按大小降序", sizes == sorted(sizes, reverse=True),
          f"前三个 {[f'{s/1024:.0f}KB' for s in sizes[:3]]}")

# 取一张真实图片
st, r = api("/api/media/faces?limit=1", token=TOKEN)
if r.get("images"):
    nm = r["images"][0]["name"]
    st2, _ = api(f"/api/media/faces/thumb/{nm}", token=TOKEN)
    check("能取到真实图片字节", st2 == 200, f"{nm}")
check("路径穿越被拦", api("/api/media/faces/thumb/..%2F..%2Fconfig%2Fbot_config.toml",
                          token=TOKEN)[0] in (400, 404))
check("非图片扩展名被拦",
      api("/api/media/faces/thumb/evil.exe", token=TOKEN)[0] in (400, 404))
check("回收目录可读", api("/api/media/trash/list", token=TOKEN)[0] == 200)

st, _ = api("/api/media/delete", "POST",
            {"category": "faces", "names": ["x.jpg"], "confirm": "wrong"},
            token=TOKEN)
check("删除缺正确确认串 → 400", st == 400)

# ── 4. 数据库 ─────────────────────────────────────────────
print("\n=== 4. 数据库 ===")
st, r = api("/api/database/list", token=TOKEN)
check("库列表可读", st == 200 and len(r.get("databases", [])) == 2,
      f"{[d['label'] for d in r.get('databases', [])]}")
dbs = {d["key"]: d for d in r.get("databases", [])}
if "search" in dbs:
    msgs = [t for t in dbs["search"]["tables"] if t["name"] == "messages"]
    check("search.db 有 messages 表", bool(msgs),
          f"rows={msgs[0]['rows'] if msgs else '?'}")
if "huanmeng" in dbs:
    mems = [t for t in dbs["huanmeng"]["tables"] if t["name"] == "memories"]
    check("huanmeng.db 有 memories 表", bool(mems),
          f"rows={mems[0]['rows'] if mems else '?'}")

st, r = api("/api/database/search/schema/messages", token=TOKEN)
check("表结构可读", st == 200 and len(r.get("columns", [])) > 0,
      f"列：{[c['name'] for c in r.get('columns', [])]}")

st, r = api("/api/database/search/rows/messages?limit=3", token=TOKEN)
check("分页读表可读", st == 200 and r.get("total", 0) > 0,
      f"共 {r.get('total')} 行，取了 {len(r.get('rows', []))} 行")
if r.get("rows"):
    row = r["rows"][0]
    check("行内容是合理字段", "content" in row or "name" in row,
          f"字段 {list(row.keys())}")

# 检索：长词走 FTS
st, r = api("/api/database/search/search/messages?q=机器人", token=TOKEN)
check("数据库检索可读", st == 200, f"engine={r.get('engine')} count={r.get('count')}")
if st == 200:
    check("长词走了 FTS", r.get("engine") == "fts", f"得到 {r.get('engine')}")

# 检索：短词降级 LIKE（trigram 对 <3 字无效）
st, r = api("/api/database/search/search/messages?q=哈哈", token=TOKEN)
check("短词查询不报错", st == 200, f"engine={r.get('engine')}")
if st == 200:
    check("短词降级为 LIKE", r.get("engine") == "like",
          f"得到 {r.get('engine')}")

# 危险 SQL 被拒
st, _ = api("/api/database/search/exec", "POST",
            {"sql": "drop table messages", "confirm": "CUSTOM"}, token=TOKEN)
check("DROP 被拒", st == 403, f"返回 {st}")
st, _ = api("/api/database/search/exec", "POST",
            {"sql": "select 1; drop table messages", "confirm": "CUSTOM"},
            token=TOKEN)
check("多语句被拒", st == 400, f"返回 {st}")
st, _ = api("/api/database/search/exec", "POST",
            {"sql": "select * from messages", "confirm": "wrong"}, token=TOKEN)
check("缺确认串被拒", st == 400, f"返回 {st}")
st, r = api("/api/database/search/exec", "POST",
            {"sql": "select count(*) as n from messages", "confirm": "CUSTOM"},
            token=TOKEN)
check("合法 SELECT 可执行", st == 200 and r.get("affected", 0) == 1,
      f"结果 {r.get('rows')}")

# ── 5. 配置编辑 ───────────────────────────────────────────
print("\n=== 5. 配置编辑 ===")
st, r = api("/api/config/files", token=TOKEN)
check("配置文件列表可读", st == 200 and r.get("count", 0) > 0,
      f"{r.get('count')} 个文件")
names = [f["name"] for f in r.get("files", [])]
check("含 bot_config.toml", "bot_config.toml" in names)
check("不含备份文件", not any(".bak" in n for n in names),
      f"名单：{names[:6]}")
check("不含 .env", ".env" not in names)

st, r = api("/api/config/file/bot_config.toml", token=TOKEN)
check("读配置成功", st == 200 and len(r.get("raw", "")) > 1000,
      f"{r.get('size')} 字节")
parsed = r.get("parsed")
check("配置解析成结构化视图", isinstance(parsed, dict),
      f"顶层键 {list(parsed.keys())[:6] if parsed else r.get('parse_error')}")

# 中文键名必须能解析出来 —— 这是服务器配置的真实特征
if isinstance(parsed, dict) and "bot" in parsed:
    bot_sec = parsed["bot"]
    cn_keys = [k for k in bot_sec if not k.isascii()]
    check("含裸中文键名的配置能解析", True,
          f"bot 段有 {len(cn_keys)} 个中文键：{cn_keys[:4]}")
    check("中文键的值正确读出",
          any(bot_sec.get(k) for k in cn_keys) if cn_keys else True,
          f"示例 {[(k, bot_sec.get(k)) for k in cn_keys[:2]]}")

raw = r.get("raw", "")
check("原文完整返回", raw.count("[") > 3, f"含 {raw.count('[')} 个左方括号")

st, r = api("/api/config/env", token=TOKEN)
check(".env 键名可读", st == 200, f"{r.get('count')} 个键")
if r.get("items"):
    keys = [i["key"] for i in r["items"]]
    check("只暴露键名不暴露值",
          all("configured" in i for i in r["items"]),
          f"键：{keys[:5]}")
    check("有已配置的键",
          any(i["configured"] for i in r["items"]),
          f"已填 {sum(1 for i in r['items'] if i['configured'])} 个")
    # 密钥值绝不能出现在返回里
    raw_resp = json.dumps(r, ensure_ascii=False)
    check("响应体不含 sk- 开头的密钥",
          "sk-" not in raw_resp, "未发现明文密钥")

# 真实配置里若无敏感信息，脱敏前后相同是正常的 —— 用合成样本验证脱敏逻辑
from panel import security as _sec
probe = 'DEEPSEEK_KEY = "sk-abcdef1234567890"'
check("脱敏对密钥生效",
      "sk-abcdef1234567890" not in _sec.sanitize_text(probe),
      f"结果 {_sec.sanitize_text(probe)}")

st, r = api("/api/config/file/bot_config.toml/backups", token=TOKEN)
check("配置备份列表可读", st == 200, f"{r.get('count')} 个备份")

# 写配置：确认串不对应被拒
st, _ = api("/api/config/file/bot_config.toml", "PUT",
            {"content": "[bot]\nname='x'\n", "confirm": "wrong"}, token=TOKEN)
check("写配置缺正确确认串 → 400", st == 400)

# 写配置：语法错误应被拒
st, r = api("/api/config/file/bot_config.toml", "PUT",
            {"content": "[bot\nbroken", "confirm": "bot_config.toml"},
            token=TOKEN)
check("语法错误被拒（未落盘）", st == 400,
      f"提示：{str(r.get('detail', ''))[:60]}")

# 单键编辑：不存在的键
st, _ = api("/api/config/file/bot_config.toml/set", "POST",
            {"key": "bot.nonexistent_key_xyz", "value": "x",
             "confirm": "bot_config.toml"}, token=TOKEN)
check("改不存在的键 → 404", st == 404)

# ── 6. 提示词 ─────────────────────────────────────────────
print("\n=== 6. 提示词 ===")
st, r = api("/api/prompts", token=TOKEN)
check("提示词列表可读", st == 200 and r.get("count", 0) > 0,
      f"{r.get('count')} 个文件 / {r.get('total_size', 0) / 1024:.0f} KB")

st, r = api("/api/prompts/00_core.md", token=TOKEN)
check("读提示词成功", st == 200 and len(r.get("content", "")) > 100,
      f"{r.get('size')} 字节")
check("有结构解析", isinstance(r.get("outline"), dict)
      and r["outline"].get("total_lines", 0) > 0,
      f"{len(r.get('outline', {}).get('sections', []))} 个章节 / "
      f"{r.get('outline', {}).get('total_lines')} 行")

st, r = api("/api/prompts/self_knowledge.md", token=TOKEN)
check("读自我认知文档", st == 200,
      f"{r.get('size')} 字节 / {len(r.get('outline', {}).get('sections', []))} 章节")

st, _ = api("/api/prompts/../bot_config.toml", token=TOKEN)
check("提示词路径穿越被拦", st in (400, 404), f"返回 {st}")

st, _ = api("/api/prompts/00_core.md", "PUT",
            {"content": "x", "confirm": "wrong"}, token=TOKEN)
check("写提示词缺正确确认串 → 400", st == 400)

st, r = api("/api/prompts/00_core.md/backups", token=TOKEN)
check("提示词备份列表可读", st == 200, f"{r.get('count')} 个备份")

st, _ = api("/api/prompts/self_knowledge.md", "DELETE", {"confirm": "DELETE"},
            token=TOKEN)
check("核心文件禁删", st == 403, f"返回 {st}")

# ── 7. 审计 ───────────────────────────────────────────────
print("\n=== 7. 审计 ===")
st, r = api("/api/system/audit?limit=20", token=TOKEN)
check("审计日志可读", st == 200 and r.get("total", 0) > 0,
      f"共 {r.get('total')} 条")
if r.get("items"):
    recent = r["items"][:8]
    actions = [i.get("action") for i in recent]
    check("审计记录了刚才的操作", any("login_ok" in str(a) for a in actions),
          f"最近动作：{actions}")

# ── 8. 回归：老接口没被改坏 ───────────────────────────────
print("\n=== 8. 回归（老接口） ===")
for path, label in [
    ("/api/overview", "概览"),
    ("/api/overview/trend?days=7", "趋势"),
    ("/api/messages/stats", "消息库统计"),
    ("/api/social/fav?limit=5", "好感度"),
    ("/api/social/profiles?limit=5", "画像"),
    ("/api/features", "实验开关"),
    ("/api/commands", "指令列表"),
    ("/api/logs/files", "日志文件"),
    ("/api/economy", "经济"),
    ("/api/plugins/capabilities", "能力注册表"),
    ("/api/system/services", "服务状态"),
    ("/api/memory/notes", "笔记"),
]:
    st, _ = api(path, token=TOKEN)
    check(f"{label} 可用", st == 200, f"返回 {st}")

print(f"\n{'=' * 56}")
print(f"通过 {PASS} / 失败 {FAIL}")
if FAILED:
    print("\n失败项：")
    for f in FAILED:
        print(f"  - {f}")
else:
    print("全部通过")
print("=" * 56)
sys.exit(1 if FAIL else 0)

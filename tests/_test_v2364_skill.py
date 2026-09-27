"""v2.3.64 skill 检索改造 回归测试

验证：
  1. @skill: 元数据行被解析并**从正文剔除**（不会泄漏给模型）
  2. get_skill_index() 只列可按需加载的 skill；内部管道章节/常驻章节/黑话词典都不列
  3. get_skill_content() 能按英文名、大小写、中文标题取到正文
  4. 关键词热加载生效（老规则靠章节名，命中率≈0）
  5. needs 与关键词两条路径**不重复注入**
  6. 内部章节（writing_system / session_summary / reply_reminder_core）不会被注入聊天
  7. core.tools 的 load_skill 工具已注册且描述里带技能索引
  8. execute_tool('load_skill', ...) 命中与未命中两条路径

用法（本地）:
  python tests/_test_v2364_skill.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PASS = 0
FAIL = 0


def ok(msg: str) -> None:
    global PASS
    PASS += 1
    print(f"  \033[32mOK\033[0m   {msg}")


def bad(msg: str) -> None:
    global FAIL
    FAIL += 1
    print(f"  \033[31mFAIL\033[0m {msg}")


def main() -> None:
    from services import llm
    from core import tools

    sec = llm._load_skill_sections()
    meta = llm._skill_meta

    print("=== 1. @skill: 元数据解析与剔除 ===")
    for k in ("command_table", "play_mode", "face_lib", "deep_explain"):
        if k not in sec:
            bad(f"章节缺失: {k}")
            continue
        if "@skill:" in sec[k]:
            bad(f"{k} 正文里仍残留 @skill: 行（会泄漏给模型）")
        elif k not in meta:
            bad(f"{k} 未解析出元数据")
        else:
            ok(f"{k}: title={meta[k]['title']!r} kw={len(meta[k]['keywords'])}个，正文已剔除元数据行")

    print("\n=== 2. get_skill_index() 内容边界 ===")
    idx = llm.get_skill_index()
    if not idx:
        bad("技能索引为空")
    else:
        ok(f"索引 {len(idx.splitlines())} 行")
    must_have = ["command_table", "play_mode", "face_lib", "deep_explain"]
    must_not = ["writing_system", "writing_followup", "session_summary",
                "reply_reminder_core", "slang_dict", "group_format",
                "fav_format", "command_tools", "private_tone"]
    for k in must_have:
        (ok if k in idx else bad)(f"索引包含 {k}")
    for k in must_not:
        (ok if k not in idx else bad)(f"索引不包含内部/常驻章节 {k}")

    print("\n=== 3. get_skill_content() 取值容错 ===")
    for name, want in [
        ("deep_explain", "deep_explain"),
        ("DEEP_EXPLAIN", "deep_explain"),
        ("深度讲解规范", "deep_explain"),
        ("face_lib", "face_lib"),
    ]:
        got = llm.get_skill_content(name)
        if got and want in sec and got == sec[want]:
            ok(f"{name!r} → {want} 正文（{len(got)}字）")
        else:
            bad(f"{name!r} 取值失败/不匹配")
    if llm.get_skill_content("不存在的技能xyz") is None:
        ok("不存在的技能返回 None")
    else:
        bad("不存在的技能竟然返回了内容")
    if llm.get_skill_content("") is None:
        ok("空名返回 None")
    else:
        bad("空名竟然返回了内容")

    print("\n=== 4. 关键词热加载（新规则） ===")
    cases = [
        # (消息, 期望注入的章节, 不应注入的章节)
        ("bot 有哪些指令表啊", "command_table", "deep_explain"),
        ("你现在是终端，我来输命令", "play_mode", None),
        ("给我配个表情包", "face_lib", None),
        ("详细讲讲 mysql 索引的原理", "deep_explain", None),
        ("今天天气不错啊", None, "deep_explain"),
    ]
    for msg, want_hit, want_miss in cases:
        needs = llm._detect_skill_needs(msg, True)
        refs = llm._build_skill_refs(needs, True, msg)
        if want_hit:
            hit = (sec.get(want_hit, "")[:40] in refs) if sec.get(want_hit) else False
            (ok if hit else bad)(f"{msg!r} → 注入 {want_hit}")
        if want_miss:
            leaked = (sec.get(want_miss, "")[:40] in refs) if sec.get(want_miss) else False
            (ok if not leaked else bad)(f"{msg!r} → 未注入 {want_miss}")

    print("\n=== 5. needs 与关键词不重复注入 ===")
    msg = "详细讲讲什么是分布式锁"
    needs = llm._detect_skill_needs(msg, True)
    if "deep" in needs:
        refs = llm._build_skill_refs(needs, True, msg)
        probe = sec.get("deep_explain", "")[:40]
        cnt = refs.count(probe) if probe else 0
        (ok if cnt == 1 else bad)(f"deep_explain 只注入 1 次（实际 {cnt} 次）")
    else:
        bad("_detect_skill_needs 未判出 deep need（测试前提不成立）")

    print("\n=== 6. 内部章节绝不注入聊天 ===")
    for msg in ("帮我写个会话摘要", "writing_system 是什么", "reply_reminder_core 讲讲"):
        refs = llm._build_skill_refs(llm._detect_skill_needs(msg, True), True, msg)
        leaked = [k for k in ("writing_system", "writing_followup", "session_summary",
                              "reply_reminder_core")
                  if sec.get(k) and sec[k][:30] in refs]
        (ok if not leaked else bad)(f"{msg!r} → 未泄漏内部章节 {leaked or ''}")

    print("\n=== 7. load_skill 工具注册 ===")
    schemas = tools.get_tool_schemas()
    names = [t.get("function", {}).get("name") for t in schemas]
    if "load_skill" in names:
        ok("get_tool_schemas() 含 load_skill")
    else:
        bad("get_tool_schemas() 缺少 load_skill")
    if names.count("load_skill") == 1:
        ok("load_skill 无重名（重名会被 API 直接拒）")
    else:
        bad(f"load_skill 出现 {names.count('load_skill')} 次")
    ls = next((t for t in schemas if t.get("function", {}).get("name") == "load_skill"), None)
    desc = (ls or {}).get("function", {}).get("description", "")
    if "可用技能" in desc and "deep_explain" in desc:
        ok("load_skill 描述里动态拼入了技能索引")
    else:
        bad(f"load_skill 描述未含技能索引: {desc[:80]!r}")
    # 不能污染模块级 TOOLS
    raw = next((t for t in tools.TOOLS if t.get("function", {}).get("name") == "load_skill"), None)
    if raw and "可用技能" not in raw.get("function", {}).get("description", ""):
        ok("模块级 TOOLS 未被动态描述污染")
    else:
        bad("schmemas 深拷贝失败，TOOLS 被污染")

    print("\n=== 8. execute_tool('load_skill') 两条路径 ===")
    async def _run():
        hit = await tools.execute_tool("load_skill", {"name": "deep_explain"},
                                       1, 1, "t", True, 1)
        miss = await tools.execute_tool("load_skill", {"name": "根本没这个skill"},
                                        1, 1, "t", True, 1)
        return hit, miss
    hit, miss = asyncio.run(_run())
    if hit and "【技能 deep_explain】" in hit and "深度讲解" in hit:
        ok(f"命中路径返回正文（{len(hit)}字）")
    else:
        bad(f"命中路径异常: {(hit or '')[:80]!r}")
    if miss and "没有名为" in miss:
        ok("未命中路径给出可用清单提示")
    else:
        bad(f"未命中路径异常: {(miss or '')[:80]!r}")

    print(f"\n=== 结果: {PASS} passed, {FAIL} failed ===")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()

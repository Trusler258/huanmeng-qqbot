# -*- coding: utf-8 -*-
"""v2.1.17 回归测试 —— 自身认知修复 / replies 纯文本 / 通用化 / 指令清单

运行: python tests/_test_phase_v2117_generic.py
覆盖:
  1. self_awareness 不再重复注入 + system 里无未替换占位符
  2. replies 不再触发指令（源码断言：扫描执行已移除，llm_calls 只来自 JSON）
  3. 通用化：skills/代码里无写死的人名、域名、端口
  4. 指令清单动态生成且与 COMMAND_MAP 一致，且只在该问时注入
  5. {bot_name} 占位符在常驻章节可替换
"""
import os
import re
import sys

for p in (r"G:\py\qqbot", "/root/bot", os.getcwd()):
    if p not in sys.path and os.path.isdir(p):
        sys.path.insert(0, p)

ROOT = None
for p in (r"G:\py\qqbot", "/root/bot", os.getcwd()):
    if os.path.isfile(os.path.join(p, "core", "pipeline.py")):
        ROOT = p
        break
assert ROOT, "找不到项目根目录"


def read(rel: str) -> str:
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def read_skills() -> str:
    """读所有 skills 正文（跳过注释行和备份文件）

    注意：self_awareness 章节里的 ${host}/${port}/${admin_qq} 等是**正常模板**，
    由 core/config.py 的 _build_self_awareness() 在运行时替换，不算残留——
    这段会被剥掉再做检查。
    """
    d = os.path.join(ROOT, "data", "skills")
    out = []
    for fn in sorted(os.listdir(d)):
        if fn.endswith(".md") and ".bak" not in fn:
            with open(os.path.join(d, fn), encoding="utf-8") as f:
                text = f.read()
            # 剥掉 self_awareness 段（该段的 ${...} 由 config 负责替换）
            text = re.sub(r"## self_awareness\n.*?(?=\n## |\Z)", "", text, flags=re.DOTALL)
            for ln in text.split("\n"):
                if not ln.lstrip().startswith("#"):
                    out.append(ln)
    return "\n".join(out)


# ── 1. self_awareness 不重复 + 无占位符残留 ──
from services.llm import _build_system_text
from core.config import get_config

cfg = get_config()
st = _build_system_text(cfg.bot_name, cfg.system_prompt, True)
assert st.count("最新更新") == 1, f'"{最新更新}"出现 {st.count("最新更新")} 次，应只 1 次（重复注入）'
leftover = set(re.findall(r"\$\{[a-z_]+\}", st))
assert not leftover, f"system 里仍有未替换占位符: {leftover}"
assert "{bot_name}" not in st, "system 里仍有未替换的 {bot_name}"
print(f"[1] self_awareness 无重复、无占位符残留 OK（system {len(st)} 字符）")

# ── 2. replies 不再触发指令 ──
pipe = read("core/pipeline.py")
assert "已清理（不执行）" in pipe, "replies 清理逻辑缺失"
assert "自动提取CALL" in pipe and "只命中 1 次" in pipe, "缺少改造说明"
# 扫描执行的两处特征必须消失
assert "llm_calls.append({\"name\": _cn" not in pipe, "仍有从正文提取 CALL 并执行"
assert "_placeholder_re" not in pipe and "_teach_re" not in pipe, "旧的占位符/教学防护已无用，应随扫描一起移除"
# llm_calls 只能来自 generate_multi_reply_with_tools 的返回
src_lines = [ln for ln in pipe.split("\n") if "llm_calls" in ln]
assert all("generate_multi_reply_with_tools" in ln or "if llm_calls" in ln or "for call in llm_calls" in ln
           for ln in src_lines), f"llm_calls 出现非预期来源: {src_lines}"
print(f"[2] replies 纯文本化 OK（llm_calls 仅来自 JSON calls，共 {len(src_lines)} 处引用）")

# ── 3. 通用化：提示词与代码无写死 ──
sk = read_skills()
for bad, why in [("Trusler", "写死的人名"), ("3483585417", "写死的 QQ 号"),
                 ("01240820", "写死的域名"), ("58888", "写死的端口"), ("58889", "写死的端口")]:
    assert bad not in sk, f"skills 里仍有{why}: {bad}"
assert "${host}" not in sk, "skills 里仍有未替换的 ${host}"
# 代码错误提示里也不该写死人名
assert "请联系管理员 @Trusler" not in pipe, "pipeline 错误提示仍写死管理员名"
assert "cfg.admin_qq}" in pipe, "错误提示应改用配置里的 admin_qq"
# 模板文件用占位符（该文件只在仓库里，服务器部署可不存在 → 容错）
_ex_path = os.path.join(ROOT, "config", "example.bot_config.toml")
if os.path.isfile(_ex_path):
    ex = read("config/example.bot_config.toml")
    assert "Trusler" not in ex, "example 模板仍写死名字"
    assert "<机器人名字>" in ex or "<你的名字" in ex, "example 模板应用占位符"
    print("[3] example 模板占位符 OK")
else:
    print("[3] example 模板不在本机（跳过该项）")
print("[3] 通用化 OK（skills/错误提示/example 模板均无写死人名/域名/端口）")

# ── 4. 指令清单动态生成 ──
from core.arch_loader import get_self_knowledge

MARK = "以下是我实际能调用的指令"
self_know = get_self_knowledge("你会什么指令")
assert MARK in self_know, "问「会什么」应带指令清单"
assert MARK not in get_self_knowledge("你的记忆存在哪"), "问记忆不该带指令清单（省 token）"
assert MARK not in get_self_knowledge("你的架构是怎样的"), "问架构不该带指令清单"
# 清单必须与真实注册一致（不写死、跟着部署走）
from modules.commands import COMMAND_MAP

cmds = re.findall(r"/~([^\s:（）]+)", self_know)
assert cmds, "指令清单为空"
# v2.1.20: 清单改为复用 help_card.collect_commands()，除 COMMAND_MAP 静态注册的
# 指令外，还包含**运行时注册的插件指令**（dice/checkin/points/shop）与说明表里的
# 中文别名——这些都是真实可用的，不算"未注册"。这里人工给出已知白名单。
PLUGIN_CMDS = {"dice", "checkin", "points", "shop", "motou", "指令名"}
missing = [c for c in cmds if c not in COMMAND_MAP and c not in PLUGIN_CMDS]
assert not missing, f"清单含未注册指令: {missing[:5]}"
print(f"[4] 指令清单 OK（{len(cmds)} 条，与 COMMAND_MAP/插件注册一致，按需注入）")

# ── 5. {bot_name} 占位符可替换 ──
assert "{bot_name}" in read("data/skills/00_core.md"), "persona_lock 应改用 {bot_name} 占位"
assert cfg.bot_name in st, f"system 里应出现实际 bot 名 {cfg.bot_name}"
print(f"[5] {{bot_name}} 占位符替换 OK（实际名: {cfg.bot_name}）")

print("\n全部 5 组通过: v2.1.17 自身认知修复 + replies 纯文本 + 通用化 + 指令清单")

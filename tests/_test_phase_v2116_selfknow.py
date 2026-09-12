# -*- coding: utf-8 -*-
"""v2.1.16 自身认知 + 脱敏 回归测试

运行: python tests/_test_phase_v2116_selfknow.py
覆盖:
  1. 按需取章节（记忆/架构/能力/模型各只给相关章节，不整份塞）
  2. 泛问兜底（无关键词 → 全量章节）
  3. 内容准确性（记忆路径、模型名与实际配置一致）
  4. 脱敏兜底（路径/IP/域名/密钥/凭据 五类）
  5. pipeline 触发词与自认知接线（源码断言）
  6. self_knowledge.md 源头干净（不含绝对路径/域名/密钥）
"""
import os
import re
import sys

for p in (r"G:\py\qqbot", "/root/bot", os.getcwd()):
    if p not in sys.path and os.path.isdir(p):
        sys.path.insert(0, p)

ROOT = None
for p in (r"G:\py\qqbot", "/root/bot", os.getcwd()):
    if os.path.isfile(os.path.join(p, "core", "arch_loader.py")):
        ROOT = p
        break
assert ROOT, "找不到项目根目录"

from core.arch_loader import get_self_knowledge, sanitize


def read(rel: str) -> str:
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def sections_of(text: str) -> list:
    return [ln[3:].strip() for ln in text.split("\n") if ln.startswith("## ")]


# ── 1. 按需取章节 ──
cases = [
    ("你的记忆是存在哪里的", {"数据存储", "记忆机制"}, "问记忆"),
    ("你的架构是怎样的", {"模块架构"}, "问架构"),
    ("你会什么", {"能力清单"}, "问能力"),
    ("你用的什么模型", {"模型与依赖"}, "问模型"),
]
for msg, want, desc in cases:
    got = set(sections_of(get_self_knowledge(msg)))
    assert got == want, f"{desc}: 期望 {want}，实得 {got}"
    print(f"[1] {desc} → {sorted(got)} OK（{len(get_self_knowledge(msg))} 字符）")

# ── 2. 泛问兜底 ──
allk = get_self_knowledge("你好呀")
got_all = set(sections_of(allk))
expect_all = {"模块架构", "数据存储", "记忆机制", "模型与依赖", "能力清单"}
assert got_all == expect_all, f"泛问应给全量，实得 {got_all}"
print(f"[2] 泛问兜底 → {len(got_all)} 章节（{len(allk)} 字符）OK")

# ── 3. 内容准确性 ──
mem = get_self_knowledge("你的记忆存在哪")
for key, why in [
    ("data/notes/", "笔记本路径"),
    ("data/memory_", "长期记忆路径"),
    ("data/stm/", "短期记忆路径"),
    ("data/context_cache.json", "对话上下文"),
    ("data/huanmeng.db", "消息检索库"),
    ("笔记本", "笔记本机制说明"),
]:
    assert key in mem, f"数据存储章节应含 {why}（{key}）"
mdl = get_self_knowledge("你用的什么模型")
assert "deepseek-flash" in mdl, "模型章节应写实际使用的 deepseek-flash"
print("[3] 内容准确性 OK（路径完整 + 模型名与实际配置一致）")

# ── 4. 脱敏兜底 ──
sensitive_cases = [
    ("/root/bot/data/notes", "[路径]", "Linux 绝对路径"),
    ("/home/user/xx", "[路径]", "home 路径"),
    ("C:\\Users\\Huang\\bot", "[路径]", "Windows 盘符路径"),
    ("123.163.121.224:20015", "[IP]", "公网 IP+端口"),
    ("01240820.xyz:20015", "[域名]", "域名+端口"),
    ("sk-abcdef123456ghij", "[密钥]", "API 密钥"),
    ("token=supersecret123", "[凭据]", "token 赋值"),
    ("Authorization: Bearer abc123def456", "Bearer [凭据]", "Bearer 头"),
]
for src, want, desc in sensitive_cases:
    out = sanitize(src)
    assert want in out, f"{desc}: {src!r} → {out!r}，期望含 {want}"
    print(f"[4] {desc} 已脱敏 OK → {out!r}")

# 正常相对路径**不能**被误伤
for safe in ["data/notes/123.md", "data/image_repo.jsonl", "config/bot_config.toml", "data/huanmeng.db"]:
    out = sanitize(safe)
    assert out == safe, f"安全路径被误伤: {safe!r} → {out!r}"
print("[4] 相对路径未被误伤 OK")

# ── 5. pipeline 接线 ──
pipe = read("core/pipeline.py")
assert "get_self_knowledge" in pipe, "pipeline 未接自认知"
assert "自身认知注入" in pipe, "缺少自认知注入说明"
for kw in ("记忆", "存哪", "存储", "笔记", "架构", "能力", "模型"):
    assert f'"{kw}"' in pipe, f"触发词缺少 {kw}"
assert "get_architecture_context" in pipe, "应保留 mermaid 兜底"
# 触发必须早于 extra_info 组装
assert pipe.index("自身认知注入") < pipe.index("extra_info_parts = []"), "触发应在 extra_info 组装之前"
print("[5] pipeline 接线 OK（含 mermaid 回退）")

# ── 6. 源头干净（self_knowledge.md 不能含敏感信息）──
src = read("data/self_knowledge.md")
# 去掉注释行再检查正文
body = "\n".join(ln for ln in src.split("\n") if not ln.lstrip().startswith("#"))
leaks = []
if re.search(r"/(?:root|home|opt|var|www)/", body):
    leaks.append("绝对路径")
if re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", body):
    leaks.append("IP")
if re.search(r"\b[\w\-]+\.(?:com|cn|net|org|xyz|top|io)\b", body):
    leaks.append("域名")
if re.search(r"\bsk-[A-Za-z0-9]{8,}", body):
    leaks.append("密钥")
assert not leaks, f"self_knowledge.md 正文含敏感信息: {leaks}"
print(f"[6] 源头干净 OK（正文 {len(body)} 字符，无路径/IP/域名/密钥）")

# ── 7. 版本号与 changelog 一致性（今天踩的坑：新日志被追加到文件末尾 →
#      "取第一条"的旧逻辑拿到旧版本，bot 自报的「最新更新」错位）──
from core.config import get_config
cfg = get_config()
log_text = read("data/update_log.md")
m = re.search(r"## (v[\d.]+ .+?)(?=\n## |\Z)", log_text, re.DOTALL)
assert m, "update_log 里找不到版本条目"
first_ver = m.group(1).strip().split("\n")[0].strip("#- ").split(" ")[0]
assert cfg.version, "cfg.version 为空"
assert first_ver == cfg.version, (
    f"update_log 首条版本({first_ver}) 与 version.toml({cfg.version}) 不一致——"
    f"通常是把新日志追加到文件末尾了（约定：最新在最上面）"
)
# changelog 必须包含本次版本的一句话总结（说明精确匹配生效）
assert cfg.version in cfg.system_prompt, f"system_prompt 应含版本号 {cfg.version}"
assert "一句话总结" in cfg.system_prompt, "注入的 changelog 应含一句话总结"
print(f"[7] 版本一致性 OK（{cfg.version}，且 system_prompt 含一句话总结）")

print("\n全部 7 组通过: v2.1.16 自身认知 + 脱敏")

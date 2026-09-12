"""架构与自我认知加载（按需注入，不进常驻 system）

两个来源：
  - data/architecture.mermaid    → 模块级架构图（给人看的图，提取成节点列表）
  - data/self_knowledge.md       → 自我认知文档（模型友好，含"数据存储/记忆机制"等）

v2.1.16: 新增 self_knowledge 按需取章节 + 统一脱敏兜底。
bot 被问"你的记忆存在哪""你的架构是怎样的"时必须答得准，但绝不能漏出
服务器绝对路径 / IP / 域名 / 密钥 —— 源头靠 self_knowledge.md 的自律，
这里再加一层正则兜底，双保险。
"""

import re
from pathlib import Path

_DATA = Path(__file__).resolve().parent.parent / "data"

# ── 脱敏规则 ────────────────────────────────────────────────
# 顺序重要：先处理路径（可能含域名样式），再 IP/域名，最后密钥
_SENSITIVE_RULES = (
    # 绝对路径：/root/xx、/home/xx、/opt/xx、/var/xx、/www/xx
    (re.compile(r"/(?:root|home|opt|var|www|srv|etc|usr)/[\w./\-]*"), "[路径]"),
    # Windows 盘符路径
    (re.compile(r"\b[A-Za-z]:[\\/][\w.\\/\-]*"), "[路径]"),
    # IPv4（含端口）
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d{1,5})?\b"), "[IP]"),
    # 域名（只认常见 TLD，避免误伤 data/xx.json 这类文件名）
    (re.compile(r"\b[\w\-]+(?:\.[\w\-]+)*\.(?:com|cn|net|org|xyz|top|io|dev|me|cc|site|online)\b(?::\d{1,5})?"), "[域名]"),
    # API 密钥样式
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"), "[密钥]"),
    (re.compile(r"(?i)\b(?:api[_\-]?key|token|password|secret)\s*[:=]\s*\S+"), "[凭据]"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{8,}"), "Bearer [凭据]"),
)


def sanitize(text: str) -> str:
    """脱敏兜底：清掉绝对路径 / IP / 域名 / 密钥样式的内容。

    源头（self_knowledge.md）已要求只写相对路径，这里是第二道防线——
    万一以后有人往里写了敏感信息，也不会经提示词外泄。
    """
    if not text:
        return ""
    for pattern, repl in _SENSITIVE_RULES:
        text = pattern.sub(repl, text)
    return text


def _read_self_knowledge() -> dict[str, str]:
    """把 self_knowledge.md 按 '## 章节名' 解析成 {章节名: 正文}"""
    path = _DATA / "self_knowledge.md"
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return {}
    sections: dict[str, str] = {}
    current = None
    buf: list[str] = []
    for line in text.split("\n"):
        if line.startswith("## "):
            if current:
                sections[current] = "\n".join(buf).strip()
            current = line[3:].strip()
            buf = []
        elif current is not None:
            # 跳过以 # 开头的注释行
            if not line.lstrip().startswith("#"):
                buf.append(line)
    if current:
        sections[current] = "\n".join(buf).strip()
    return sections


# 关键词 → 需要注入的章节（按需，省 token）
_SECTION_HINTS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (
        ("记忆", "存哪", "存在哪", "存储", "存到", "数据放", "笔记", "笔记本",
         "数据库", "memory", "回顾", "遗忘"),
        ("数据存储", "记忆机制"),
    ),
    (
        ("架构", "模块", "结构", "分层", "组成", "怎么实现", "工程"),
        ("模块架构",),
    ),
    (
        ("什么模型", "用的什么", "哪个模型", "什么ai", "依赖", "接入"),
        ("模型与依赖",),
    ),
    (
        ("会什么", "能做什么", "能力", "功能", "会干啥", "会干什么", "能干啥"),
        ("能力清单",),
    ),
)


def _command_list() -> str:
    """动态生成「可调用指令清单」——从真实注册的 COMMAND_MAP 取，部署时自动跟随。

    v2.1.17: 用户反馈"你没有指令清单，他怎么知道要调用什么指令"。
    手写清单写死了就不通用（换部署/加指令都得改提示词），所以这里运行时生成：
    指令名与说明都由 modules.commands.COMMAND_MAP + services.llm._CMD_DESC 提供。
    延迟导入避免 core↔services 循环依赖（arch_loader 被 core.config 导入，
    而 services.llm 又导入 core.config），调用发生在运行期、模块早已加载完毕。
    """
    try:
        from services.llm import _build_dynamic_command_list
        out = _build_dynamic_command_list()
        if out:
            return out
    except Exception:
        pass
    # 兜底：至少把指令名列出来（缺中文说明也比没有强）
    try:
        from modules.commands import COMMAND_MAP
        names = sorted(set(COMMAND_MAP))
        if names:
            return "【全部可调用指令】\n" + "、".join(f"/~{n}" for n in names)
    except Exception:
        pass
    return ""


def get_self_knowledge(msg: str = "") -> str:
    """按消息内容取自我认知章节；未命中关键词 → 返回全部（泛问"介绍一下你自己"）。

    Args:
        msg: 用户当前消息（用于关键词匹配）

    Returns:
        组装好的自我认知文本（已脱敏），无内容时返回 ""
    """
    sections = _read_self_knowledge()
    if not sections:
        return ""

    low = (msg or "").lower()
    picked: list[str] = []
    for keywords, want in _SECTION_HINTS:
        if any(k in low for k in keywords):
            for name in want:
                if sections.get(name) and name not in picked:
                    picked.append(name)

    if not picked:
        for name in ("模块架构", "数据存储", "记忆机制", "模型与依赖", "能力清单"):
            if name in sections:
                picked.append(name)

    parts = []
    for name in picked:
        body = sections[name]
        # v2.1.17: 「能力清单」追加真实指令表——被问"会什么/能调什么指令"时
        # 得能报出具体指令名，而不是只说"能查天气"这种笼统描述
        if name == "能力清单":
            _cmds = _command_list()
            if _cmds:
                body = f"{body}\n\n以下是我实际能调用的指令（都是真的，回答「会什么」时可以照这个说）：\n{_cmds}"
        parts.append(f"## {name}\n{body}")

    if not parts:
        return ""
    return sanitize("# 关于我自己\n" + "\n\n".join(parts))


def get_architecture_context() -> str:
    """按需加载完整架构（token消耗高，仅需要时调用）"""
    arch_path = _DATA / "architecture.mermaid"
    if not arch_path.exists():
        return ""
    try:
        arch_text = arch_path.read_text(encoding="utf-8")
        clean = re.sub(r"<br/>", " | ", arch_text)
        nodes = re.findall(r'\[([^\[\]]+)\]', clean)
        return sanitize("# 完整架构\n" + "\n".join(f"- {n.strip()}" for n in nodes))
    except Exception:
        return ""

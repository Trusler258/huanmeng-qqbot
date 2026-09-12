"""
LLM 调用服务（原 send_message.py 中的 LLM 部分 + judge/if_module）
- 统一 OpenAI 兼容接口封装
- ✅ 支持并行调用多个模型（asyncio.gather）减少串行延迟
- 多句回复生成 + 好感度提取
- 兴趣度判断（三级判断模型调用）
- 搜索判断模型调用
- v1.1.2: 提示词统一管理 → data/main_skill.md，群聊/私聊分离
"""

from __future__ import annotations

import asyncio
import json
import time as _time
from pathlib import Path

# ── 回复 JSON schema ──────────────────────────────────────
_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "config" / "reply_schema.json"
_REPLY_SCHEMA: dict | None = None


def _load_reply_schema() -> dict:
    global _REPLY_SCHEMA
    if _REPLY_SCHEMA is None:
        _REPLY_SCHEMA = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    return _REPLY_SCHEMA


def _normalize_reply_json(data: dict) -> dict:
    """按 schema 自动补缺、clamp 范围、校验类型"""
    s = _load_reply_schema().get("fields", {})
    out = {}
    for key, field in s.items():
        val = data.get(key)
        dtype = field.get("type", "str")

        # null 处理
        is_nullable = "null" in dtype
        if val is None and is_nullable:
            out[key] = None
            continue
        if val is None:
            val = field.get("default")

        # 类型转换
        if dtype.startswith("str"):
            out[key] = str(val) if val else field.get("default", "")
            max_len = field.get("max_len")
            if max_len and len(out[key]) > max_len:
                out[key] = out[key][:max_len]
        elif dtype.startswith("int"):
            try:
                out[key] = int(val)
            except (ValueError, TypeError):
                out[key] = int(field.get("default", 0))
            mn, mx = field.get("min"), field.get("max")
            if mn is not None and out[key] < mn:
                out[key] = mn
            if mx is not None and out[key] > mx:
                out[key] = mx
            if is_nullable and not val:
                out[key] = None
        elif dtype.startswith("list"):
            out[key] = val if isinstance(val, list) else field.get("default", [])
        elif dtype.startswith("dict"):
            if isinstance(val, dict):
                sub = {}
                for sk, sf in field.get("keys", {}).items():
                    sv = val.get(sk, sf.get("default"))
                    if sf.get("type") == "str":
                        sub[sk] = str(sv) if sv else ""
                    else:
                        try:
                            sub[sk] = int(sv)
                        except (ValueError, TypeError):
                            sub[sk] = 0
                out[key] = sub
            elif is_nullable:
                out[key] = None
            else:
                out[key] = field.get("default", {})
        elif dtype.startswith("int|null"):
            if val is None:
                out[key] = None
            else:
                try:
                    out[key] = int(val)
                except (ValueError, TypeError):
                    out[key] = None
        elif dtype.startswith("str|null"):
            out[key] = str(val) if val else None
        else:
            out[key] = val if val is not None else field.get("default")
    return out
import re
import random
from pathlib import Path
from typing import Optional

from openai import OpenAI

from core.logger import get_logger
from core.config import BotConfig, ModelConfig, get_config
from utils.format_lang import format_lang

logger = get_logger("llm")

# ── 多句回复提示词（从 main_skill.md 加载）──────────────────
# 设计原则：system 消息只放不随对话变化的内容（人设+格式规则），
# 动态内容（历史/记忆/搜索）放在 user/assistant 多轮消息中。
# 这样 API 端的 KV cache 在连续对话中命中率最高。

# ★ 锚点消息：插入在 system 和对话历史之间。内容永远不变，
#    确保即使 FIFO 裁剪历史消息，system+锚点这段前缀始终缓存命中。
_MULTI_REPLY_ANCHOR = "【以下是最新的聊天记录，请结合你的人设和上述规则参与对话】"

# ── 提示词加载（data/skills/*.md 为唯一落点）────────────────

_skill_sections: dict[str, str] = {}
_skill_loaded = False


def _load_skill_sections() -> dict[str, str]:
    """加载提示词章节：data/skills/*.md，叠加历史遗留的 data/main_skill.md（可选）。

    v2.2.4：提示词已按「加载角色」拆分到 data/skills/，见各文件头部注释：
      00_ 常驻核心 / 10,11_ 格式 / 20_ 常驻指令规则 / 21_ 按需指令表 /
      30_ 好感度 / 40_ 提醒模板 / 50_ 按需玩梗 / 60_ 按需表情库

    ★ main_skill.md 缺失时**必须继续合并 skills 目录**，不能提前 return——
      旧实现直接 return，会导致拆分后所有提示词静默丢失（真 bug，已修）。
    """
    global _skill_sections, _skill_loaded
    if _skill_loaded:
        return _skill_sections

    _base = Path(__file__).resolve().parent.parent / "data"
    sections: dict[str, str] = {}

    # 1) 历史遗留单文件（可选，splits 后已不再维护）
    legacy = _base / "main_skill.md"
    if legacy.exists():
        try:
            sections = _parse_md_sections(legacy.read_text(encoding="utf-8"))
            logger.info("main_skill.md 已解析: %d 个章节（遗留文件）", len(sections))
        except Exception as e:
            logger.error("读取 main_skill.md 失败: %s", e)

    # 2) 叠加 data/skills/*.md —— 提示词的唯一落点（同名章节覆盖 legacy）
    _merge_skills_dir(sections)

    _skill_sections = sections
    _skill_loaded = True
    logger.info("提示词已加载: %d 个章节（data/skills/ + 遗留 main_skill.md）", len(sections))
    return sections


_SKILLS_DIR = Path(__file__).resolve().parent.parent / "data" / "skills"


def _parse_md_sections(text: str) -> dict[str, str]:
    """按 ## 节名切割 markdown，返回 {节名: 内容}（与 main_skill.md 同解析规则）。"""
    sections: dict[str, str] = {}
    current_key = ""
    current_lines: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped == "" or (stripped.startswith("#") and not stripped.startswith("## ")):
            continue
        if stripped.startswith("## "):
            if current_key:
                sections[current_key] = "\n".join(current_lines).strip()
            current_key = stripped[3:].strip()
            current_lines = []
        elif current_key:
            current_lines.append(line)
    if current_key:
        sections[current_key] = "\n".join(current_lines).strip()
    return sections


def _merge_skills_dir(sections: dict[str, str]) -> None:
    """扫描 data/skills/*.md，把每个文件解析为章节并并入 sections（同名覆盖）。"""
    if not _SKILLS_DIR.exists():
        return
    try:
        files = sorted(_SKILLS_DIR.glob("*.md"))
    except Exception as e:
        logger.warning("扫描 skills 目录失败: %s", e)
        return
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("读取 skill 文件失败 %s: %s", f.name, e)
            continue
        parsed = _parse_md_sections(text)
        if parsed:
            # 文件内含 ## 分节：逐节并入
            for k, v in parsed.items():
                sections[k] = v
        else:
            # 整个文件作为一个章节，key 为文件名（去扩展名）
            sections[f.stem] = text.strip()
        logger.info("已叠加 skill 文件: %s", f.name)


def reload_skill_cache():
    """清除技能文件缓存（reload 时调用）"""
    global _skill_sections, _skill_loaded
    _skill_sections = {}
    _skill_loaded = False


def _build_system_text(bot_name: str, personality: str, is_group: bool, custom_persona: dict | None = None) -> str:
    """组装 system prompt —— 只放稳定核心（人设 / 格式 / 核心规则）

    v2.1.9 分层重构：
    - system 只留常驻核心，每轮完全一致 → DeepSeek 前缀缓存命中率最高
    - 指令表格、指令列表、表情库、玩梗模式等参考资料改由 _build_skill_refs() 注入 user 消息
    - data/skills/*.md 的额外章节按关键词热匹配（见 _build_skill_refs）
    """
    sec = _load_skill_sections()

    if custom_persona:
        # per-user persona：用三段构造 header
        parts = []
        core = custom_persona.get("core", "")
        side = custom_persona.get("side", "")
        identity = custom_persona.get("identity", "")
        if core:
            parts.append(f"# 核心人格\n{core}")
        if side:
            parts.append(f"# 侧面人格\n{side}")
        if identity:
            parts.append(f"# 固定身份\n{identity}")
        header = "\n---\n".join(parts) if parts else f"# 角色设定\n{personality}"
    else:
        header = sec.get("prompt_header", "你是{bot_name}").format(
            bot_name=bot_name, personality=personality
        )

    fmt_key = "group_format" if is_group else "private_format"
    # v2.1.17: self_awareness 只在 custom_persona 分支需要单独补。
    #   常规分支的 header 已通过 personality(=cfg.system_prompt) 带入**替换好变量**的版本；
    #   这里若再取一次 skills 原始模板，会导致：
    #     ① 重复注入（实测"最新更新"在 system 里出现两次）
    #     ② 残留 ${bot_name}/${version}/${changelog}/${architecture}/${admin_qq} 等
    #        字面占位符——白烧 ~400 token，还让模型看到一堆废信息
    _self_aware = ""
    if custom_persona:
        try:
            _self_aware = get_config()._build_self_awareness()
        except Exception:
            _self_aware = ""
    core_parts = [
        header,
        sec.get("persona_lock", "") if not custom_persona else "",
        sec.get(fmt_key, ""),
        sec.get("command_tools", ""),
        sec.get("fav_format", ""),
        sec.get("fav_tiers", ""),
        _self_aware,
        sec.get("anti_repeat", ""),
        sec.get("private_tone", "") if (not is_group and not custom_persona) else "",
    ]
    # v2.1.17: 允许在任意常驻章节里用 {bot_name} 占位——换名字部署时只改配置，
    # 不用逐条改提示词。prompt_header 已由 .format 处理，这里的 replace 对它是空操作。
    core_parts = [p.replace("{bot_name}", bot_name) for p in core_parts]
    return "\n\n".join(p for p in core_parts if p)


# 常驻 system 的章节名（不参与按需注入）
_SYSTEM_SECTIONS = frozenset((
    "prompt_header", "persona_lock", "group_format", "private_format",
    "command_tools", "fav_format", "fav_tiers", "self_awareness",
    "anti_repeat", "private_tone",
))
# 已单独处理的可选章节（既不进 system 常驻，也不参与 _build_skill_refs 的关键词兜底注入）
_OPTIONAL_SECTIONS = frozenset((
    "command_table", "face_lib", "play_mode",
    # 提示词模板章节：由 _build_reminder() 按调用路径显式读取（见 data/skills/40_reminders.md）
    "reply_reminder", "voice_reminder", "jsonraw_reminder", "plain_text_rule",
    # 长回答规范：命中知识/技术/原理类意图才注入
    "deep_explain",
))

# 工具/指令意图触发词（宽松匹配：宁可多带，漏带会导致不会调指令）
_TOOL_HINTS = (
    "查", "搜", "帮我", "画", "唱", "天气", "地震", "新闻", "战绩", "积分", "签到",
    "提醒", "倒计时", "余额", "快递", "翻译", "抽", "谱面", "棋", "卡片", "读",
    "网页", "更新", "状态", "统计", "记忆", "昵称", "备注", "下载", "谱", "tuf",
)

# 深度讲解意图触发词（v2.2.5：把长回答规范从常驻 system 改为按需注入，日常闲聊省 ~870 tokens）
# 宽松匹配：多带只是多花 token，漏带会让知识类回答被常驻的"闲聊 1~3 句"规则压短
_DEEP_HINTS = (
    "详细", "详解", "展开", "细说", "讲讲", "讲一下", "解释", "说明", "科普", "介绍",
    "原理", "为什么", "为啥", "怎么", "如何", "什么是", "是什么", "啥是", "是啥",
    "啥意思", "什么意思", "指的是", "区别", "差异", "对比", "比较", "步骤", "流程",
    "怎么做", "教程", "分析", "总结", "举例", "例子", "冷知识", "历史", "背景",
    "起源", "典故", "多少", "多久",
)


def _detect_skill_needs(msg: str, is_group: bool) -> set:
    """判断本轮需要哪些参考资料（宽松触发）"""
    needs: set = set()
    m = (msg or "").lower()
    if any(w in m for w in _TOOL_HINTS) or "~" in m:
        needs.add("tools")
    if any(w in m for w in ("?", "？", "吗", "怎么", "什么", "为什么", "如何", "哪")):
        needs.add("tools")
    if any(w in m for w in ("表情", "emoji", "颜文字", "表情包")):
        needs.add("face")
    if any(w in m for w in ("扮演", "演个", "来一个", "角色", "设定", "梗")):
        needs.add("play")
    # 深度讲解模式：知识/技术/原理/概念/对比/步骤类提问 → 才加载长回答规范
    # （宽松触发：宁可多带多花点 token，漏带的代价是知识类回答被"闲聊短句"规则压短）
    if any(w in m for w in _DEEP_HINTS):
        needs.add("deep")
    elif len(m) >= 8 and ("？" in m or "?" in m):
        needs.add("deep")
    return needs


def _build_skill_refs(needs: set, is_group: bool, msg: str = "") -> str:
    """组装按需参考资料（注入 user 消息，不污染 system 缓存）

    v2.1.9：system 保持稳定，把"参考资料"类内容（指令表/指令列表/表情库/玩梗）
    挪到这里按需注入。data/skills/*.md 的自定义章节按章节名热匹配才加载。
    """
    sec = _load_skill_sections()
    parts: list[str] = []

    if "tools" in needs:
        t = sec.get("command_table", "")
        if t:
            parts.append(t)
        cl = _build_dynamic_command_list()
        if cl:
            parts.append(cl)
    if "face" in needs:
        f = sec.get("face_lib", "")
        if f:
            parts.append(f)
    if "play" in needs:
        pm = sec.get("play_mode", "")
        if pm:
            parts.append(pm)
    if "deep" in needs:
        de = sec.get("deep_explain", "")
        if de:
            parts.append(de)

    # skills/*.md 拖进来的自定义章节：章节名出现在消息里才热加载
    low = (msg or "").lower()
    for k, v in sec.items():
        if k in _SYSTEM_SECTIONS or k in _OPTIONAL_SECTIONS or not v:
            continue
        if k.lower() in low:
            parts.append(v)

    if not parts:
        return ""
    return "【本轮参考资料】\n" + "\n\n".join(parts)


# ── 格式提醒：统一从提示词文件读取（v2.2.2）────────────────────────
# 背景：这些提醒原先硬编码在本文件的 _build_messages / generate_multi_reply，
#       另有一份副本散落在 core/ctx_usage.py 和 modules/commands.py。
#       改一处忘一处 → 行为漂移 + /~ctx 统计失真。现统一放 data/skills/40_reminders.md。
# 模板缺失时的最小兜底：JSON 约束不能丢，否则 LLM 会输出纯文本导致全量解析失败。
_REMINDER_FALLBACK = (
    "★★★ 最重要规则：你的全部回复必须是 JSON 格式，绝不允许输出纯文本 ★★★\n"
    '回复格式（必填只有这两个）: {"replies":["回复内容"],"fav":0}\n'
    "严格按上面的 JSON 格式输出（replies 和 fav 必须有，其他按需）。"
)


def _recent_bot_snippets(history: list, bot_name: str, limit: int = 3) -> str:
    """抽取 bot 最近的回复片段，生成「防复读」提醒文本（空则返回 ""）。

    v2.1.15 背景：用户实测同一个梗连续 4 轮出现（"薯片味" 12:37/12:44/12:44/13:02，
    "KD 0.37" 3 轮），说明只写在 system 里的 anti_repeat 被长提示词稀释、没生效。
    这里把 bot 自己刚说过的话摘出来，放到 fmt_reminder（最后一条 user 消息、
    注意力最高处）再明确点一次，属于代码层的硬提醒。

    history 兼容两种形态：
      - 扁平上下文行：["幻梦: 文本", "群友: 文本", ...]
      - 已解析的多轮消息：[{"role": "assistant", "content": "文本"}, ...]
    """
    picked: list[str] = []
    for item in reversed(history or []):
        if isinstance(item, dict):
            if item.get("role") != "assistant":
                continue
            txt = str(item.get("content") or "")
            # 去掉可能残留的 "幻梦: " 前缀
            for sep in (f"{bot_name}: ", f"{bot_name}:", f"{bot_name}：", f"{bot_name}:"):
                if txt.startswith(sep):
                    txt = txt[len(sep):]
                    break
        else:
            # 扁平上下文：只有 "{bot_name}: xxx" 开头的才是 bot 自己的话，
            # 其余是别人发言 → 必须跳过（否则会把群友的话当自己的复读内容）
            raw = str(item or "")
            for sep in (f"{bot_name}: ", f"{bot_name}:", f"{bot_name}：", f"{bot_name}:"):
                if raw.startswith(sep):
                    txt = raw[len(sep):]
                    break
            else:
                continue
        txt = re.sub(r'\s+', ' ', txt).strip()
        if not txt or txt in picked:
            continue
        picked.append(txt[:50])
        if len(picked) >= limit:
            break
    if not picked:
        return ""
    quoted = " ／ ".join(f"「{t}」" for t in picked)
    return f"你最近说过：{quoted} —— 这些话和用过的梗不要再重复，换个说法或换话题。"


def _build_reminder(name: str, **vars) -> str:
    """按章节名取格式提醒模板并插值。

    - 模板位置：data/skills/40_reminders.md（经 _load_skill_sections 自动叠加）
    - ${xxx} 为变量占位符，由 vars 传入替换
    - plain_text_rule（禁用 Markdown）自动追加到末尾 —— 越靠近当前消息注意力越高
    - 章节缺失 → 打 ERROR 日志 + 回退最小 JSON 兜底，保证回复链路不崩
    - v2.1.15: 未传值的占位符清空 + 告警，插值后变空的行删除 ——
      可选段落（如防复读）没内容时不会留"【防复读】"空壳行，也不会把 ${no_repeat}
      字面量塞给模型
    """
    sec = _load_skill_sections()
    tpl = (sec.get(name) or "").strip()
    if not tpl:
        logger.error("提示词章节缺失: %s —— 已回退兜底提醒（检查 data/skills/40_reminders.md）", name)
        tpl = _REMINDER_FALLBACK
    for k, v in vars.items():
        tpl = tpl.replace("${" + k + "}", str(v))
    # 未赋值的占位符：告警 + 清掉，避免字面量进入提示词
    _leftover = re.findall(r"\$\{(\w+)\}", tpl)
    if _leftover:
        logger.warning("提醒模板 %s 存在未赋值占位符: %s", name, _leftover)
        tpl = re.sub(r"\$\{\w+\}", "", tpl)
    plain = (sec.get("plain_text_rule") or "").strip()
    if plain:
        tpl = tpl + "\n" + plain
    # 插值后变空的行（可选段落未启用）直接丢弃，别留空行/空壳标题
    tpl = "\n".join(ln for ln in tpl.split("\n") if ln.strip())
    return tpl


def _build_dynamic_command_list() -> str:
    """从 COMMAND_MAP 动态生成全部指令用法列表（含中文说明）"""
    try:
        from modules.commands import COMMAND_MAP
    except ImportError:
        return ""
    lines = ["【全部可调用指令】"]
    for name in sorted(set(COMMAND_MAP)):
        desc = _CMD_DESC.get(name)
        if desc:
            lines.append(f"  /~{name}: {desc}")
        else:
            lines.append(f"  /~{name}")
    return "\n".join(lines)

# ── 指令说明（精简，面向 LLM）─────────────────────────────
_CMD_DESC = {
    # 数据查询
    "balance": "查 DeepSeek API 余额（还剩多少钱）",
    "cost":    "查今日 Token 消耗统计（调了多少次、花了多少钱）",
    "tokens":  "查今日各模型 Token 用量明细",
    "ctx":     "查当前对话的上下文用量统计（system/参考资料/历史/注入各占多少token）",
    "say":     "代发消息到指定群或指定私聊（仅管理员）。用法: /~say g<群号> <内容> 或 /~say u<QQ号> <内容>，纯数字默认群号；内容里的 @123456 会变成真的@",
    "stats":   "查自身统计（回复次数/好感度/被@次数）",
    "setstats":"设置自身统计数据（主人用）",
    "unstats": "管理员用",
    # PC状态
    "sys":     "查看主人电脑状态（当前窗口、在听什么歌、歌词）",
    "pc":      "同 sys，查看主人电脑状态",
    # 好感度 / 关系
    "favlist": "查看本群好感度排行榜",
    "resetfav":"重置某人的好感度（管理员）",
    "添加关系":"添加用户的预设身份（管理员）",
    # 天气 / 地震 / 新闻
    "weather": "查指定城市天气",
    "天气":    "同 weather，查天气",
    "eq":      "查最近地震信息",
    "地震":    "同 eq，查地震",
    "wzq":     "五子棋对战。用法: /~wzq duel @某人 [nofb] 发起挑战(加nofb为无禁手) | /~wzq ai 新手/普通/困难/专家 [nofb] 人机 | /~wzq accept 接受 | /~wzq <坐标> 落子(H8) | /~wzq board 看棋盘。nofb必须跟在duel/ai后面，不能单独用",
    # 搜索 / 阅读
    "search":  "搜索互联网获取信息，返回总结",
    "read":    "阅读网页内容，返回总结",
    # 记忆 / 上下文
    "memory":  "显示当前群聊的记忆（管理员）",
    "recall":  "召回历史聊天中与当前话题相关的记忆",
    # 经济系统
    "points":  "查看自己的积分和全服排行榜",
    "sign":    "每日签到领取随机积分",
    "gift":    "把自己的积分转赠给其他用户",
    "shop":    "查看积分商店里的权益物品",
    "buy":     "用积分购买权益物品",
    "bag":     "查看自己的权益背包",
    "use":     "使用背包里的权益（如好感券）",
    "persona": "查看机器人的性格描述/adoptable persona",
    # 群管理
    "op":      "移交特权/管理员",
    "owner":   "移交拥有者权限",
    "leave":   "退出群聊（管理员）",
    "nickname":"给群友设置备注名",
    # 模式 / 预设
    "preset":  "切换群聊 preset（管理员）",
    "sleep":   "切换群聊到休眠模式",
    "叙事":    "切换群聊到叙事模式",
    "主人":    "切换群聊到主人互动模式",
    "含蓄":    "切换群聊到含蓄模式",
    # 提醒 / 倒计时 / 抽签
    "remind":  "设置定时提醒",
    "countdown":"设置倒计时",
    "倒计时":   "同 countdown",
    "luck":    "抽签/运势占卜",
    "抽":      "同 luck，抽签",
    # 信息 / 更新
    "info":    "查看个人信息/群信息",
    "updateinfo":"更新个人备注信息",
    "up":      "同 updateinfo",
    "reload":  "热重载配置（管理员）",
    "update":  "从 git 拉取最新代码并更新 bot",
    "upd":     "同 update",
    "gh":      "从 git 拉取指定分支代码更新 bot",
    "md":      "用 LLM 渲染 Markdown 为图片卡片",
    # 绘画 / 视频 / 语音
    "draw":    "AI 绘画生成图片",
    "绘画":    "同 draw",
    "video":   "AI 生成视频",
    "视频":    "同 video",
    "img2video":"图片转视频",
    "图生视频": "同 img2video",
    "voice":   "文本转语音播报",
    "语音":    "同 voice",
    "box":     "查看或加入小游戏盒子",
    # 五子棋 / 象棋 / 翻译
    "五子棋":   "同 wzq，五子棋对战（duel/ai 可加 nofb 无禁手）",
    "象棋":    "发起象棋对战",
    "tr":      "翻译文本到指定语言",
    "翻译":    "同 tr，翻译文本",
    "xq":      "查看大群在线信息",
    # 战绩 / TUFD
    "wdsj":    "查我的数据",
    "tufd":    "查 TUFD 信息",
    "tuflevel":"查 TUFD 难度信息",
    "tufpage":"查看 TUFD 关卡页面",
    "tufsearch":"搜索 TUFD 谱面",
    "tuf谱面":  "同 tufsearch",
    "analyze": "分析谱面数据",
    "calc":    "执行Python代码进行数学计算（方程/方程组/计算题），用代码精确求解",
    # 系统
    "help":    "显示帮助信息",
    "ping":    "检查机器人是否在线",
    "restart": "重启机器人（管理员）",
    "reload":  "热重载配置文件（管理员）",
    # 调试/测试
    "jsonraw": "查看原始 LLM 输出 JSON（调试用）",
    "testok":  "测试用",
    "testsys": "测试用",
}


# ── 底层调用：同步 OpenAI → 异步执行器 ────────────────────

# ══ 模型级熔断（Circuit Breaker，v2.0.4r）══
# 背景：2026-09-02 22:45 事故 —— SiliconFlow(Qwen2.5-7B) 故障，judge 阶段每条消息
#       都白等 15s 超时，per-group 串行队列从 2 积压到 8，连 /~restart 指令都被拖 45s。
# 设计：按 model.name 独立计数，连续 3 次失败(且 5 分钟内) → 熔断冷却 180s，
#       重复熔断指数退避(×2，封顶 600s)；任意一次成功即清零复位。
#       熔断期间 judge 类调用跳过模型直接走本地规则兜底，毫秒级出队不再拖队列。
_CIRCUIT: dict[str, dict] = {}          # model.name -> {fails, first_fail_ts, open_until, open_count}
_CIRCUIT_MAX_FAILS = 3                  # 连续失败 N 次打开
_CIRCUIT_WINDOW = 300                   # N 次失败须发生在该秒数内（防零散失败误开）
_CIRCUIT_COOLDOWN = 180                 # 首次熔断冷却秒数
_CIRCUIT_MAX_COOLDOWN = 600             # 指数退避封顶

# 当前调用场景标记（record_usage 用于命中率归因）
# call_llm 是底层入口，各调用方在调用前设置此变量
_current_call_scene: str = ""


def _circuit_key(model_cfg) -> str:
    return (getattr(model_cfg, "name", "") or "?").split("/")[-1][:24]


def _record_call_success(model_cfg) -> None:
    """调用成功 → 清空该模型熔断状态"""
    k = _circuit_key(model_cfg)
    st = _CIRCUIT.pop(k, None)
    if st is not None:
        logger.info("熔断[%s] 调用成功，熔断状态已复位", k)


def _record_call_failure(model_cfg) -> None:
    """调用失败(超时/连接错误) → 累计失败计数，达阈值打开熔断"""
    k = _circuit_key(model_cfg)
    now = _time.time()
    st = _CIRCUIT.setdefault(k, {"fails": 0, "first_fail_ts": 0.0, "open_until": 0.0, "open_count": 0})
    # 冷却期内的失败只续期，不重复计数
    if st["open_until"] > now:
        return
    st["fails"] += 1
    if st["first_fail_ts"] == 0.0:
        st["first_fail_ts"] = now
    if st["fails"] >= _CIRCUIT_MAX_FAILS:
        if now - st["first_fail_ts"] <= _CIRCUIT_WINDOW:
            base = _CIRCUIT_COOLDOWN * (2 ** min(st["open_count"], 3))
            st["open_until"] = now + min(base, _CIRCUIT_MAX_COOLDOWN)
            st["open_count"] += 1
            logger.error("熔断[%s] 连续%d次失败 → 冷却%.0fs(第%d次熔断)，期间走规则兜底",
                         k, st["fails"], st["open_until"] - now, st["open_count"])
        # 失败窗口太长 → 视为零散失败，重新起算
        st["fails"] = 0
        st["first_fail_ts"] = 0.0


def is_model_circuit_open(model_cfg) -> bool:
    """该模型是否处于熔断冷却期"""
    k = _circuit_key(model_cfg)
    st = _CIRCUIT.get(k)
    return bool(st and st["open_until"] > _time.time())


def circuit_status() -> dict:
    """熔断状态快照（供 /~status 之类排查）"""
    return {k: dict(v) for k, v in _CIRCUIT.items()}


def _create_client(model_cfg: ModelConfig, timeout: float = 60.0) -> OpenAI:
    """创建 OpenAI 客户端实例。
    ★ v2.0.4r: timeout 不再写死 60s。call_llm 外层 wait_for 超时后，
    executor 线程里阻塞的同步调用若底层 timeout 更大，会继续占用线程池
    （默认池 ≤32 线程），多条超时消息即可饿死线程池，拖垮所有 LLM 调用。
    底层超时对齐到 调用超时+5s，保证超时后线程 5s 内自动释放。
    """
    return OpenAI(base_url=model_cfg.url, api_key=model_cfg.key, timeout=max(timeout, 10.0))


def _hint_json_output(messages: list[dict]) -> list[dict]:
    """严格 JSON 重试辅助：追加强制 JSON 提示行。

    DeepSeek json_object 模式要求 prompt 中出现 "json" 字样，否则可能返回空；
    空返回重试时用它给模型最后一次明确指令。
    """
    out = [dict(m) for m in messages]
    out.append({"role": "user", "content": "只输出一个合法 JSON 对象，不要输出任何其他文字、解释或代码块。"})
    return out


async def call_llm(
    model_cfg: ModelConfig,
    messages: list[dict],
    max_tokens: int | None = None,
    temperature: float = 0.7,
    timeout: float = 60.0,
    json_mode: bool = False,
    _json_retries: int = 0,
    scene: str = "",
    thinking: bool | None = False,
) -> str:
    """
    调用 LLM 并返回原始文本内容。
    内部使用 run_in_executor 避免阻塞事件循环。

    Args:
        model_cfg: 模型配置（含 url / key / name）
        messages: 对话消息列表 [{"role":"system","content":...}, ...]
        max_tokens: 最大生成 token 数
        temperature: 温度参数
        timeout: 超时时间（秒）
        scene: 调用场景标识（reply/judge/search/tools等），用于 token 命中率归因
        thinking: v2.1.10 思考模式开关。默认 False（本函数多为短输出判断调用，
          思考会吃掉小输出预算导致空返回；长文本生成场景调用方显式传 True）。

    Returns:
        模型返回的文本内容；出错返回空字符串
    """
    client = _create_client(model_cfg, timeout=timeout + 5.0)
    loop = asyncio.get_running_loop()
    
    # ★ max_tokens<=0 视为不设上限（DeepSeek 对 max_tokens=0 报 400 Invalid）
    if max_tokens is not None and max_tokens <= 0:
        max_tokens = None

    logger.info("调用LLM [%s] url=%s tokens=%s temp=%.1f timeout=%.1fs",
                 model_cfg.name[:20], model_cfg.url.split('/')[-2] if '/' in model_cfg.url else model_cfg.url[:20],
                 str(max_tokens), temperature, timeout)
    
    start_time = loop.time()
    try:
        # ★ max_tokens=None 时不传递该参数，让 API 用默认值
        req_params = {
            "model": model_cfg.model if hasattr(model_cfg, 'model') else model_cfg.name,
            "temperature": temperature,
            "messages": messages,
        }
        if max_tokens is not None:
            req_params["max_tokens"] = max_tokens
        if json_mode:
            req_params["response_format"] = {"type": "json_object"}
        # v2.1.10: 思考模式显式控制。本函数默认 False（短判断调用防思考吃光输出预算）
        if thinking is not None:
            req_params["extra_body"] = {"thinking": {"type": "enabled" if thinking else "disabled"}}
        
        completion = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: client.chat.completions.create(**req_params)),
            timeout=timeout,
        )
        elapsed = loop.time() - start_time
        text = completion.choices[0].message.content
        text = text.strip() if text else ""
        finish = completion.choices[0].finish_reason or "unknown"
        
        if not text:
            logger.warning("LLM [%s] 返回空内容 | finish_reason=%s | 耗时%.1fs",
                         model_cfg.name[:20], finish, elapsed)
            if json_mode and finish == "stop":
                # ★ v2.0.4w: 严格 JSON —— DeepSeek json_object 偶发空返回。
                #   不再立刻降级普通模式（降级=放弃严格JSON，容易再吐纯文本），
                #   保持 json_mode 重试最多 2 次（追加 json 提示行），仍空才普通兜底。
                if _json_retries < 2:
                    logger.warning("JSON模式空返回，保持 json_mode 重试(%d/2)...", _json_retries + 1)
                    return await call_llm(
                        model_cfg, _hint_json_output(messages),
                        max_tokens, temperature, timeout,
                        json_mode=True, _json_retries=_json_retries + 1,
                    )
                logger.warning("json_mode 连续空返回，降级普通模式兜底一次")
                return await call_llm(model_cfg, messages, max_tokens, temperature, timeout, json_mode=False)
            if finish == "length" and (max_tokens or 0) < 2000:
                boosted = min(max(max_tokens or 0, 1000) * 2, 2000)
                logger.warning("finish_reason=length, 扩大上限 %d→%d 重试...", max_tokens, boosted)
                return await call_llm(model_cfg, messages, boosted, temperature, timeout, json_mode)

        # 记录 token 消耗
        try:
            usage = completion.usage
            if usage:
                from core.token_tracker import record_usage
                cached = getattr(usage, 'prompt_tokens_details', None)
                cached_tokens = getattr(cached, 'cached_tokens', 0) or 0
                cwrite = getattr(cached, 'cache_write_tokens', 0) or 0
                record_usage(
                    model=model_cfg.name,
                    prompt_tokens=usage.prompt_tokens,
                    completion_tokens=usage.completion_tokens,
                    cached_tokens=cached_tokens,
                    cache_write_tokens=cwrite,
                    scene=scene or _current_call_scene,
                )
        except Exception:
            pass
        
        preview = text[:50] + ("..." if len(text) > 50 else "")
        logger.info("LLM [%s] 返回 %d字符 耗时%.1fs → \"%s\"",
                   model_cfg.name[:20], len(text), elapsed, preview.replace("\n", "\\n"))
        _record_call_success(model_cfg)
        return text
    except asyncio.TimeoutError:
        elapsed = loop.time() - start_time
        logger.error("LLM [%s] 调用超时 (%.1fs/%.1fs)", model_cfg.name[:20], elapsed, timeout)
        _record_call_failure(model_cfg)
        return ""
    except Exception as e:
        elapsed = loop.time() - start_time
        logger.error("LLM [%s] 调用失败 (%.1fs): %s", model_cfg.name[:20], elapsed, e)
        _record_call_failure(model_cfg)
        return ""


# ── Function Calling ─────────────────────────────────────

class ToolCallResult:
    """工具调用结果"""
    def __init__(self, content: str, tool_calls: list[dict] | None, reasoning: str = "", reasoning_duration: float = 0.0):
        self.content = content or ""
        self.tool_calls = tool_calls or []
        # v2.1.10: 思考模式思维链（deepseek-flash reasoning_content），
        # 带 tools 的请求必须回传给 API（否则 400），存这里方便拼接
        self.reasoning = reasoning or ""
        # v2.1.12: reasoning 非空时记录本轮耗时（思考时长秒），供 [已思考N秒] 标注
        self.reasoning_duration = reasoning_duration or 0.0

async def call_llm_with_tools(
    model_cfg: "ModelConfig",
    messages: list[dict],
    tools: list[dict],
    max_tokens: int | None = None,
    temperature: float = 0.7,
    timeout: float = 60.0,
    scene: str = "",
    thinking: bool | None = None,
) -> ToolCallResult:
    """
    调用 LLM，支持 Function Calling。
    返回 ToolCallResult，包含可能的 tool_calls。
    scene: 调用场景标识，用于 token 命中率归因

    thinking: 思考模式开关（DeepSeek-flash 默认开启思考）。
      - True  → 显式启用 (extra_body thinking.enabled)
      - False → 显式关闭（闲聊更快更省；思考 token 不计入输出）
      - None  → 跟随 API 默认（不传 extra_body）
    v2.1.10: deepseek-flash 自带 thinking；带 tools 的请求必须回传 reasoning_content
    否则 API 400（DeepSeek 官方要求），故 ToolCallResult 新增 reasoning 字段承载。

    v2.1.12: reasoning 有值时记录本轮耗时（思考时长），供 [已思考N秒] 前缀标注。
    """

    client = _create_client(model_cfg, timeout=timeout + 5.0)
    loop = asyncio.get_running_loop()

    # ★ max_tokens<=0 视为不设上限（DeepSeek 对 max_tokens=0 报 400 Invalid）
    if max_tokens is not None and max_tokens <= 0:
        max_tokens = None

    req_params = {
        "model": model_cfg.model if hasattr(model_cfg, 'model') else model_cfg.name,
        "temperature": temperature,
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
    }
    if max_tokens is not None:
        req_params["max_tokens"] = max_tokens
    # v2.1.10: 思考模式显式控制（DeepSeek-flash；reasoning_effort 走模型默认 high）
    if thinking is not None:
        req_params["extra_body"] = {"thinking": {"type": "enabled" if thinking else "disabled"}}

    _t_start = loop.time()
    try:
        completion = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: client.chat.completions.create(**req_params)),
            timeout=timeout,
        )
        msg = completion.choices[0].message
        content = msg.content or ""
        reasoning = getattr(msg, "reasoning_content", None) or ""
        reasoning_duration = (loop.time() - _t_start) if reasoning else 0.0
        tc_list: list[dict] = []

        if msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}
                tc_list.append({
                    "id": tc.id,
                    "name": tc.function.name,
                    "arguments": args,
                })
            logger.info("LLM [%s] → tool_calls: %s",
                       model_cfg.name[:20],
                       [(t["name"], str(t["arguments"])[:60]) for t in tc_list])

        # 记录 token 消耗（FC 轮同样记录，命中率归因用）
        try:
            usage = getattr(completion, "usage", None)
            if usage:
                from core.token_tracker import record_usage
                cached = getattr(usage, "prompt_tokens_details", None)
                record_usage(
                    model=model_cfg.name,
                    prompt_tokens=usage.prompt_tokens,
                    completion_tokens=usage.completion_tokens,
                    cached_tokens=getattr(cached, "cached_tokens", 0) or 0,
                    cache_write_tokens=getattr(cached, "cache_write_tokens", 0) or 0,
                    scene=scene or _current_call_scene or "tools",
                )
        except Exception:
            pass

        _record_call_success(model_cfg)
        return ToolCallResult(content=content.strip(), tool_calls=tc_list, reasoning=reasoning, reasoning_duration=reasoning_duration)

    except asyncio.TimeoutError:
        logger.error("LLM FC [%s] 超时", model_cfg.name[:20])
        _record_call_failure(model_cfg)
        return ToolCallResult(content="", tool_calls=[])
    except Exception as e:
        logger.error("LLM FC [%s] 失败: %s", model_cfg.name[:20], e)
        _record_call_failure(model_cfg)
        return ToolCallResult(content="", tool_calls=[])


# ── 高级功能：多句回复生成 ─────────────────────────────────

def _extract_fav_change(raw: str) -> tuple[str, int]:
    """
    从模型输出中提取好感度变化值和清洗后的文本。
    
    Returns:
        (清洗后的原始文本, fav变化值)
    """
    fav_change = 0
    fav_match = re.search(r'[\[［]fav:\s*([+-]?\d+)[\]］]', raw)
    if fav_match:
        try:
            fav_change = int(fav_match.group(1))
            fav_change = max(-5, min(5, fav_change))
            raw = raw.replace(fav_match.group(0), "")
        except Exception:
            pass
    return raw, fav_change


def _clean_sentences(raw: str) -> list[str]:
    """
    清洗模型输出的原始文本为句子列表。
    - 按 ||| 分割
    - 如果没有 ||| 分隔符，尝试按空行段落分割
    - 移除残留竖线和角色标签
    - 过滤过短的句子
    - 上限 5 句
    """
    # 先检查是否有 ||| 分隔符
    if "|||" in raw:
        raw = raw.replace("\n", " ").replace("\r", " ")
        sentences = [s.strip() for s in raw.split("|||") if s.strip()]
    elif "||" in raw:
        raw = raw.replace("\n", " ").replace("\r", " ")
        sentences = [s.strip() for s in raw.split("||") if s.strip()]
    else:
        # 没有分隔符 → 按双换行段落切分（适配长文回复如错误报告分析）
        paragraphs = [p.strip() for p in re.split(r'\n\s*\n', raw) if p.strip()]
        if len(paragraphs) <= 1:
            sentences = [raw.strip()]
        else:
            sentences = paragraphs
        # 不合并换行，保留段落格式
        sentences = [s.replace("\r", "") for s in sentences]
        return [s for s in sentences if len(s) >= 2][:5]
    
    # 清理残留
    sentences = [
        s.replace("||", "").replace("|", "").strip()
        for s in sentences
    ]
    sentences = [
        re.sub(r'[\[［](admin|friend|群友)[\]］]', '', s)
        for s in sentences
    ]
    sentences = [s for s in sentences if len(s) >= 2]
    return sentences[:5]


def _build_history_messages(msg_history: list[str], bot_name: str) -> list[dict]:
    """
    将扁平上下文列表拆分为多轮 user/assistant 消息。

    拆分规则：
    - 以 "{bot_name}:" 或 "{bot_name}：" 开头的行 → assistant
    - 其他所有行 → user
    - 连续相同角色的消息合并为一条（减少轮次）
    - assistant 消息中的回复分隔符保持原样（不做替换），确保 prefix 稳定
    """
    if not msg_history:
        return []

    turns: list[dict] = []

    for line in msg_history:
        line = line.strip()
        if not line:
            continue

        is_bot = False
        content = line

        # 检测是否为 bot 回复（兼容半角/全角冒号）
        for sep in [": ", "："]:
            if line.startswith(f"{bot_name}{sep}"):
                is_bot = True
                content = line[len(bot_name) + len(sep):].strip()
                break

        role = "assistant" if is_bot else "user"

        # ★ 每条消息独立成 turn，不合并——让 LLM 清楚区分每个人的发言
        turns.append({"role": role, "content": content})

    return turns


# ── Function Calling 增强版生成 ──────────────────────────

async def generate_multi_reply_with_tools(
    msg_history: list[str],
    speaker_name: str,
    current_msg: str,
    bot_name: str,
    system_prompt: str,
    reply_model: "ModelConfig",
    is_group: bool = True,
    extra_info: str = "",
    max_tokens: int = 3000,
    user_id: int = 0,
    group_id: int = 0,
    bot_qq: int = 0,
    interim_cb=None,
    thinking: bool | None = None,
    thought_shown_cb=None,
) -> tuple[list[str], int, list, str, str, list | None, str, int | None, str | None, str, dict]:
    """
    跟 generate_multi_reply 一样，但先走 FC 工具调用。
    如果 LLM 选择调用工具，执行后把结果喂回去，再生成最终回复。

    interim_cb: 可选 async 回调 (text)->None。FC 轮1 LLM 在发起工具调用前
    写的自然语先导语（如"帮你搜搜看吧"）会经它先发给用户，避免 13s+ 干等。
    由调用方（pipeline）注入发送通道；不传则维持旧行为（先导语丢弃）。

    thinking: v2.1.10 思考模式开关，透传给每轮 FC 调用（True=启用/False=关闭/None=默认）

    thought_shown_cb: 可选 async 回调 (duration_sec:int)->None，v2.1.12。
    思考模式启用且模型产出 reasoning 时，在"给用户的第一条可见文本发出前"调用一次，
    由发送端决定是否/如何展示 [已思考N秒] 标记（前缀、过滤由 pipeline 负责）。
    - 轮1带工具调用：先导语先行 → duration=该轮 reasoning 耗时，附在先导语上
    - 纯思考无工具：最终回复生成 → duration=最后有 reasoning 的轮次的耗时
    - 思考关闭 / reasoning 为空 / 耗时过短：不调用（由 pipeline 侧过滤）
    """
    from core.tools import get_tool_schemas, execute_tool

    tools = get_tool_schemas()
    msgs = _build_messages(msg_history, speaker_name, current_msg, bot_name, system_prompt, is_group, extra_info)

    # v2.1.12: 记录"最后一条有思考"的轮次耗时，最终回复生成时按它标注 [已思考N秒]
    _last_reasoning_dur: float | None = None

    # 长消息（题目/长文/网页阅读）→ 扩大输出 token
    has_long_context = len(current_msg) > 2000
    if max_tokens and has_long_context:
        max_tokens = max(max_tokens, 8000)

    # 多轮 FC Agent 循环：LLM 可连续调多个工具；errors/data/action 结果出现即提前退出
    # （移植 kook 5fcab40：轮数由 LLM 决定，MAX_ROUNDS 仅作防死循环保险上限）
    MAX_ROUNDS = 6
    errors = []
    data_results = []
    action_results = []
    _prev_call_set: frozenset[str] | None = None  # 防死循环：连续两轮相同调用集则停

    for round_idx in range(MAX_ROUNDS):
        result = await call_llm_with_tools(reply_model, msgs, tools, max_tokens=max_tokens, temperature=0.4, scene="reply_tools", thinking=thinking)
        raw_preview = (result.content or "")[:200].replace("\n", "\\n")
        logger.info("LLM原始输出 [轮%d]: content=%s | tool_calls=%d | reasoning=%d字",
                    round_idx + 1, raw_preview, len(result.tool_calls), len(result.reasoning))
        # v2.1.12: 记录"最后一条有思考"的轮次耗时（轮1先导语分支在下方就近回调）
        if result.reasoning and result.reasoning_duration > 0:
            _last_reasoning_dur = result.reasoning_duration

        if not result.tool_calls:
            if not (result.content or "").strip():
                if round_idx == 0:
                    logger.warning("LLM 返回空内容，重试...")
                    continue
                # 第二轮仍空 → json_mode 兜底（FC 路径未开 json_mode，这里补一次）
                logger.warning("LLM 连续返回空内容，用 json_mode 兜底...")
                json_raw = await call_llm(
                    reply_model, msgs,
                    max_tokens=max(max_tokens or 0, 4000),  # v2.0.4w: 长 JSON 不再被 800 焊死
                    temperature=0.3, json_mode=True,
                )
                if json_raw and json_raw.strip():
                    result.content = json_raw
                    logger.info("json_mode 兜底成功: %s...", json_raw[:80])
                else:
                    logger.error("json_mode 兜底仍失败，放弃")
                    break
            # 非 JSON 且首次 → 强制 json_mode 重试一次
            raw = (result.content or "").strip()
            if round_idx == 0 and raw and not raw.startswith("{"):
                # 先尝试从混合文本中提取 JSON（LLM 有时会先吐自然语言再吐 JSON）
                json_pos = raw.find('{"replies"') if '"replies"' in raw else raw.find('{"')
                if json_pos >= 0:
                    extracted = raw[json_pos:]
                    logger.info("从混合文本提取 JSON: pos=%d", json_pos)
                    result.content = extracted
                    # 从 JSON 解析 calls 转为原生 tool_calls 格式（触发FC循环执行）
                    # 兼容 tool/name、arguments/args 多形态（移植 kook a101954）
                    try:
                        parsed = json.loads(extracted)
                        calls = parsed.get("calls", [])
                        if calls and isinstance(calls, list):
                            result.tool_calls = []
                            for i, c in enumerate(calls):
                                if not isinstance(c, dict):
                                    continue
                                name = str(c.get("name") or c.get("tool") or "").strip()
                                if not name:
                                    continue
                                args_raw = c.get("args", c.get("arguments", {}))
                                if isinstance(args_raw, str):
                                    try:
                                        args_raw = json.loads(args_raw)
                                    except Exception:
                                        args_raw = {"args": args_raw}
                                if not isinstance(args_raw, dict):
                                    args_raw = {"args": args_raw}
                                result.tool_calls.append(
                                    {"id": f"call_{i}", "name": name, "arguments": args_raw}
                                )
                            logger.info("混合文本中提取到 %d 个工具调用", len(result.tool_calls))
                    except Exception:
                        pass
                    # 提取成功但无 calls → json_mode 确保 JSON 完整
                    if not result.tool_calls:
                        json_raw = await call_llm(reply_model, msgs, max_tokens=max(max_tokens or 0, 4000), temperature=0.4, json_mode=True)
                        if json_raw and json_raw.startswith("{"):
                            result.content = json_raw
                else:
                    logger.info("LLM 输出非 JSON，强制重试...")
                    json_raw = await call_llm(reply_model, msgs, max_tokens=max(max_tokens or 0, 4000), temperature=0.3, json_mode=True)  # v2.0.4w
                    if json_raw and json_raw.startswith("{"):
                        result.content = json_raw
                        logger.info("json_mode 重试成功: %s...", json_raw[:80])
            break

        # ★ v2.0.4y(2026-09-04): 轮1有工具调用且 content 是短自然语 → 先发先导语。
        #   现象：LLM 写"帮你搜搜看吧"却整段被吞，工具执行完(13s+)才一次性出正文，
        #   期间用户以为 bot 没反应，且铺垫丢失后正文开头突兀。
        #   只发第一轮，后续轮 content 丢弃（防刷屏，最终回复会覆盖）。
        if (
            round_idx == 0 and result.tool_calls and interim_cb
            and (result.content or "").strip()
        ):
            lead = result.content.strip()
            # 过滤：只发"值得先说的话"——短自然语；JSON/长文/代码块/指令文本不发
            if (
                1 < len(lead) <= 120
                and not lead.startswith("{")
                and "```" not in lead
                and "/~" not in lead
            ):
                # v2.1.12: 先导语是"用户看到的第一条文本"——思考了就把 [已思考N秒] 挂这里
                if thought_shown_cb is not None and result.reasoning and result.reasoning_duration > 0:
                    try:
                        await thought_shown_cb(round(result.reasoning_duration))
                    except Exception:
                        logger.warning("FC: 思考标记回调失败: %s", exc_info=True)
                logger.info("FC: 轮1先导语先发 (%d字): %s", len(lead), lead[:60])
                try:
                    await interim_cb(lead)
                except Exception:
                    logger.warning("FC: 先导语发送失败: %s...", lead[:30], exc_info=True)

        logger.info("FC: 轮%d 检测到 %d 个工具调用", round_idx + 1, len(result.tool_calls))
        msgs.append({
            "role": "assistant",
            # ★ 保留轮 content：让后续轮/最终轮 LLM 看到自己已说过的先导语，
            #   最终回复才不会重复"我查查/稍等"等动手前用语（仅保留短内容防污染）
            "content": (result.content or None) if result.content and len(result.content) <= 300 else None,
            # v2.1.10: 思考模式启用时，带 tools 请求必须回传 reasoning_content（否则 400）
            "reasoning_content": result.reasoning or None,
            "tool_calls": [
                {"id": tc["id"], "type": "function", "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"], ensure_ascii=False)}}
                for tc in result.tool_calls
            ],
        })

        # 并行执行本轮所有工具
        async def run_one(tc):
            # 单工具超时（移植 kook 67dd501：工具级超时表，防止慢工具拖死整轮）
            try:
                from core.tools import get_tool_timeout
                timeout = get_tool_timeout(tc["name"])
            except Exception:
                timeout = 60.0
            try:
                r = await asyncio.wait_for(
                    execute_tool(
                        tc["name"], tc["arguments"],
                        user_id=user_id, group_id=group_id,
                        sender_name=speaker_name, is_group=is_group, bot_qq=bot_qq,
                        original_msg=current_msg,
                    ),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                logger.warning("FC: 工具 %s 执行超时(%.0fs)", tc["name"], timeout)
                r = f"[超时] 工具 {tc['name']} 执行超过 {timeout:.0f} 秒"
            return tc, r or ""

        tool_results = await asyncio.gather(*(run_one(tc) for tc in result.tool_calls))
        for tc, tool_text in tool_results:
            msgs.append({"role": "tool", "tool_call_id": tc["id"], "content": tool_text})
            logger.info("FC: 工具 %s 返回 %d 字符", tc["name"], len(tool_text))
            # 工具返回长内容时扩大 max_tokens
            if len(tool_text) > 500:
                max_tokens = max(max_tokens or 0, 8000)

            if "未绑定" in tool_text or "失败" in tool_text or "出错" in tool_text:
                errors.append(tool_text)
            elif tc["name"] in ("calc", "wdsj_query", "weather", "search_web", "earthquake", "sys", "pc"):
                data_results.append(tool_text)
            elif tool_text:
                action_results.append(tool_text)

        # 遇到错误或数据结果 → 停止循环
        if errors or data_results:
            break
        # 已执行过工具 → 跳出，走 json_mode 生成最终回复
        if action_results:
            break

        # 防死循环：连续两轮发起相同的工具调用集则停（移植 kook LoopDetector 思路）
        cur_set = frozenset((tc["name"], json.dumps(tc.get("arguments", {}), ensure_ascii=False, sort_keys=True)) for tc in result.tool_calls)
        if cur_set and cur_set == _prev_call_set:
            logger.warning("FC: 检测到连续两轮相同工具调用，终止循环防止死循环")
            break
        _prev_call_set = cur_set

    # ── 如果工具已执行，强制 json_mode 回复 ──
    if errors or data_results or action_results:
        # v2.1.12: 工具链最终回复前，若此前思考过（轮1先导语未附标记时）补标一次
        if thought_shown_cb is not None and _last_reasoning_dur:
            try:
                await thought_shown_cb(round(_last_reasoning_dur))
            except Exception:
                logger.warning("FC: 思考标记回调失败: %s", exc_info=True)
            _last_reasoning_dur = None
        # ★ v2.0.4u(2026-09-04): 工具结果已返回 → 给最终回复轮注入"诚实归因"提醒。
        #   现象：search_web 查完，LLM 却说"哦这个我知道喵"（装成本来就会），
        #   与上一句"让我查查"自相矛盾。此处插一条 system 提醒，配合 main_skill.md 规则10。
        _final_msgs = list(msgs)
        _final_msgs.insert(1, {
            "role": "system",
            "content": (
                "你刚才调用了工具查询/搜索，现在请基于工具返回的结果回答。"
                "回复必须如实体现信息是刚查到的：开头可用'我刚查了下''搜到啦''查到啦'等，"
                "禁止说'这个我知道''我记得''早就知道'这类装作自己本来就会的话。"
            ),
        })
        json_raw = await call_llm(
            reply_model, _final_msgs,
            max_tokens=max(max_tokens or 0, 8000),
            temperature=0.4, json_mode=True,
        )
        if json_raw and json_raw.strip():
            return _parse_reply(json_raw, speaker_name)

    # ── 处理结果 ──
    if errors:
        return _parse_reply(
            json.dumps({"replies": [errors[0].rstrip("。") + "喵"], "fav": 0, "calls": [], "face": None, "mood": "无奈", "action": "", "at": None, "mode": None, "origin": "user", "actor": {}}),
            speaker_name,
        )
    if data_results:
        wrap_msgs = [
            {"role": "system", "content": "你是幻梦。用自然语气回复，别句句挂喵。简单数据一句话即可；如果是知识性/原理性内容，就多说几句讲清楚，不限字数。"},
            {"role": "user", "content": f"用户说「{current_msg}」\n查到数据：{data_results[0]}\n请自然回复——简单数据一两句；知识性内容该展开就展开，讲透为止。"},
        ]
        raw = await call_llm(reply_model, wrap_msgs, max_tokens=2000, temperature=0.4)
        if raw:
            return _parse_reply(
                json.dumps({"replies": [raw.strip()], "fav": 0, "calls": [], "face": None, "mood": "好奇", "action": "", "at": None, "mode": None, "origin": "user", "actor": {}}, ensure_ascii=False),
                speaker_name,
            )
        return _parse_reply(
            json.dumps({"replies": [data_results[0]], "fav": 0, "calls": [], "face": None, "mood": "好奇", "action": "", "at": None, "mode": None, "origin": "user", "actor": {}}, ensure_ascii=False),
            speaker_name,
        )

    if action_results:
        reply_text = action_results[0]
        return _parse_reply(
            json.dumps({"replies": [reply_text], "fav": 0, "calls": [], "face": None, "mood": "开心", "action": "", "at": None, "mode": None, "origin": "user", "actor": {}}),
            speaker_name,
        )

    # 无工具调用 → 优先尝试 JSON，失败则按纯文本处理
    raw = result.content or ""
    if not raw.strip():
        return [], 0, [], "", "", None, "", None, None, "user", {}, None

    # v2.1.12: 纯思考路径（无工具）→ 生成完毕，发出前补一次 [已思考N秒] 回调
    #   （前置判断开了 thinking 且模型真思考了才会到这里；时长取最后有思考的轮次）
    if thought_shown_cb is not None and _last_reasoning_dur:
        try:
            await thought_shown_cb(round(_last_reasoning_dur))
        except Exception:
            logger.warning("FC: 思考标记回调失败: %s", exc_info=True)
        _last_reasoning_dur = None

    # 尝试 JSON 解析（LLM 有时返回 JSON 但不调工具）
    parsed = _parse_reply(raw, speaker_name, quiet=True)
    if parsed[0]:
        return parsed

    # 纯文本：按句号拆开发送
    sentences = [s.strip() + "。" for s in raw.replace("\n", " ").split("。") if s.strip()]
    if not sentences:
        sentences = [raw.strip()[:120]]
    return sentences, 0, [], "", "", None, "", None, None, "user", {}, None



def _parse_reply(
    raw: str,
    speaker_name: str = "",
    quiet: bool = False,
) -> tuple:
    """解析 LLM JSON 回复，返回标准 11 元组。quiet=True 时 JSON 失败不告警"""
    if not raw:
        return [], 0, [], "", "", None, "", None, None, "user", {}, None

    try:
        raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.IGNORECASE)
        raw = re.sub(r'\s*```$', '', raw)
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            raw = raw[start:end + 1]
        raw = re.sub(r'//[^\n]*', '', raw)
        data = json.loads(raw)
        data = _normalize_reply_json(data)
        replies = data.get("replies", [])
        if not isinstance(replies, list) or not replies:
            raw_cleaned, fav_change = _extract_fav_change(raw)
            replies = _clean_sentences(raw_cleaned)
            return replies, fav_change, [], "", "", None, "", None, None, "user", {}, None
        if isinstance(replies[0], list):
            replies = [str(r) for r in replies[0]]
        else:
            replies = [str(r) for r in replies]

        fav_change = data.get("fav", 0)
        calls = data.get("calls", [])
        face_cq = ""
        face = data.get("face")
        if face:
            try:
                from modules.face_lib import get_face, make_cq
                fp = get_face(str(face).strip())
                if fp:
                    face_cq = make_cq(fp)
            except Exception:
                pass

        mood = data.get("mood", "")
        mood_detail = data.get("mood_detail")
        action = data.get("action", "")
        at_qq = data.get("at")
        mode_switch = data.get("mode")
        origin = data.get("origin", "user")
        actor = data.get("actor") or {}
        instructs = data.get("instructs")

        logger.info("JSON回复解析: %d句 fav=%+d calls=%d mood=%s",
                   len(replies), fav_change, len(calls), mood)
        return replies, fav_change, calls, face_cq, mood, mood_detail, action, at_qq, mode_switch, origin, actor, instructs

    except json.JSONDecodeError:
        if not quiet:
            logger.warning("JSON解析失败，尝试修复: %s...", raw[:80])
        try:
            # 先尝试补全截断的 JSON（加缺失的 }]）
            fixed = raw.rstrip()
            # 找到最后一个完整的字符串结尾
            last_complete = fixed.rfind('",')
            if last_complete > 0:
                fixed = fixed[:last_complete] + '"],"fav":0,"calls":[]}'
                try:
                    data = json.loads(fixed)
                    replies = data.get("replies", [])
                    if isinstance(replies, list) and replies:
                        fav_change = safe_fav(data.get("fav", 0))
                        calls = data.get("calls") or []
                        face_cq = data.get("face")
                        mood = data.get("mood", "")
                        mood_detail = data.get("mood_detail")
                        action = data.get("action", "")
                        at_qq = data.get("at")
                        mode_switch = data.get("mode")
                        origin = data.get("origin", "user")
                        actor = data.get("actor") or {}
                        instructs = data.get("instructs")
                        logger.info("JSON截断修复: %d句", len(replies))
                        return replies, fav_change, calls, face_cq, mood, mood_detail, action, at_qq, mode_switch, origin, actor, instructs
                except Exception:
                    pass

            m = re.search(r'"replies"\s*:\s*\[', raw)
            if m:
                inner = raw[m.end():]
                depth = 0
                end_pos = -1
                for i, ch in enumerate(inner):
                    if ch == '[': depth += 1
                    elif ch == ']':
                        if depth == 0:
                            end_pos = i
                            break
                        depth -= 1
                if end_pos >= 0:
                    parts = []
                    in_str = False
                    cur = ""
                    repl_inner = inner[:end_pos]
                    for i, ch in enumerate(repl_inner):
                        if ch == '"':
                            in_str = not in_str
                        elif ch == ',' and not in_str:
                            parts.append(cur.strip().strip('"').strip("'"))
                            cur = ""
                            continue
                        cur += ch
                    if cur.strip():
                        parts.append(cur.strip().strip('"').strip("'"))
                    parts = [p for p in parts if p]
                    if parts:
                        fm = re.search(r'"fav"\s*:\s*(-?\d+)', raw)
                        fv = int(fm.group(1)) if fm else 0
                        return parts, fv, [], "", "", None, "", None, None, "user", {}, None
        except Exception:
            pass
        return [], 0, [], "", "", None, "", None, None, "user", {}, None


def _build_messages(
    msg_history: list[str],
    speaker_name: str,
    current_msg: str,
    bot_name: str,
    system_prompt: str,
    is_group: bool,
    extra_info: str,
) -> list[dict]:
    """构建 messages 列表（与 generate_multi_reply 相同的格式）"""
    # 拆 PERSONA:::{json}:::{原system} 标记（与 generate_multi_reply 一致）
    _custom_persona = None
    _personality = system_prompt
    if system_prompt.startswith("PERSONA:::"):
        parts = system_prompt.split(":::", 2)
        if len(parts) == 3:
            try:
                _custom_persona = json.loads(parts[1])
            except Exception:
                _custom_persona = None
            _personality = parts[2]
    msgs = [{"role": "system", "content": _build_system_text(bot_name, _personality, is_group, custom_persona=_custom_persona)}]
    # 按 bot_name: 前缀判断角色（与 _build_history_messages 一致）
    # 群聊中多个用户连续发言时，奇偶索引会把第 2/4/6 条 user 消息错误标为 assistant，
    # 导致 LLM 看到混乱的对话历史，返回空内容或非 JSON 输出
    for line in msg_history:
        line = line.strip()
        if not line:
            continue
        is_bot = False
        content = line
        for sep in [": ", "："]:
            if line.startswith(f"{bot_name}{sep}"):
                is_bot = True
                content = line[len(bot_name) + len(sep):].strip()
                break
        role = "assistant" if is_bot else "user"
        msgs.append({"role": role, "content": content})

    user_parts = []
    # ★ v2.1.9: 按需参考资料（指令表/指令列表/表情库）注入 user，不污染 system 缓存
    _refs = _build_skill_refs(_detect_skill_needs(current_msg, is_group), is_group, current_msg)
    if _refs:
        user_parts.append(_refs)
    # 大消息（题目/长文）→ 去记忆，给 LLM 省上下文
    is_long = len(current_msg) > 1500
    if extra_info and not is_long:
        user_parts.append(f"【上下文】\n{extra_info}")
        ctx_hint = "优先用上下文+自身知识回答，上下文够用就别搜。"
    else:
        ctx_hint = "如果你不了解，可以调用搜索工具查一下。"
    max_chars = "40" if is_group else "20"
    # ★ v2.2.2: 提醒模板统一放 data/skills/40_reminders.md，此处不再硬编码
    # ★ v2.1.15: 防复读——把 bot 最近说过的话摘出来，在注意力最高处再点一次
    no_repeat = _recent_bot_snippets(msg_history, bot_name)
    fmt_reminder = _build_reminder("reply_reminder", ctx_hint=ctx_hint, max_chars=max_chars, no_repeat=no_repeat)
    user_parts.append(fmt_reminder)
    # 长消息截断：保留前 2500 字（够题目描述+要求），防止 flash 模型吃不下
    msg_text = current_msg[:2500] + ("…[截断]" if len(current_msg) > 2500 else "")
    user_parts.append(f"【当前对话者】{speaker_name}\n{speaker_name} 发消息：「{msg_text}」")
    msgs.append({"role": "user", "content": "\n".join(user_parts)})
    return msgs


async def generate_multi_reply(
    msg_history: list[str],
    speaker_name: str,
    current_msg: str,
    bot_name: str,
    system_prompt: str,
    reply_model: ModelConfig,
    is_group: bool = True,
    extra_info: str = "",
    max_tokens: int = 3000,
) -> tuple[list[str], int]:
    """
    调用主回复模型，生成多句回复 + 好感度变化。

    采用多轮对话格式，利用 API 前缀缓存：
    - system: 静态人设 + 格式规则（每次相同，缓存命中率最高）
    - 历史: user/assistant 多轮（前面不变的部分也能命中缓存）
    - 最后一条 user: 当前消息 + 动态记忆/搜索信息（变化部分集中在末尾）

    v1.1.2: 群聊/私聊使用不同格式规则。群聊最多5句、允许颜文字；
            私聊最多8句、简短、只用文字表情(QAQ/OuO/QwQ)、用你我他称呼。

    Args:
        msg_history: 上下文历史消息列表
        speaker_name: 说话者名称
        current_msg: 当前消息文本
        bot_name: 机器人名字
        system_prompt: 系统提示词（人设）
        reply_model: 回复模型配置
        is_group: 是否群聊
        extra_info: 额外信息（记忆/搜索结果等）
        max_tokens: 最大 token 数

    Returns:
        (句子列表, 好感度变化值, CALL列表, FACE_CQ码)
    """
    # 1. 从 main_skill.md 组装 system 消息（群聊/私聊不同）
    #    system_prompt 形如 "PERSONA:::{json}:::{原system}" 时拆出 custom_persona dict 注入
    _custom_persona = None
    _personality = system_prompt
    if system_prompt.startswith("PERSONA:::"):
        parts = system_prompt.split(":::", 2)
        if len(parts) == 3:
            try:
                _custom_persona = json.loads(parts[1])
            except Exception:
                _custom_persona = None
            _personality = parts[2]

    system_text = _build_system_text(
        bot_name=bot_name,
        personality=_personality,
        is_group=is_group,
        custom_persona=_custom_persona,
    )

    # 2. 解析历史为多轮 user/assistant 消息（排除最后一条，因为它就是当前消息，
    #    会作为独立的最后一条 user 消息追加）
    history = msg_history[:-1] if msg_history else []
    turns = _build_history_messages(history, bot_name)

    # 3. 构造最后一条 user 消息：动态信息 + 格式提醒 + 当前发言
    user_parts = []
    # ★ v2.1.9: 按需参考资料注入 user
    _refs = _build_skill_refs(_detect_skill_needs(current_msg, is_group), is_group, current_msg)
    if _refs:
        user_parts.append(_refs)
    if extra_info:
        user_parts.append(f"【当前可用的搜索/记忆信息】\n{extra_info}\n请参考以上信息回答，如果信息不相关可忽略。")
    # ★ 格式提醒：JSON 输出
    max_chars = "40" if is_group else "20"
    # ★ v2.2.2: 提醒模板统一放 data/skills/40_reminders.md，此处不再硬编码
    # ★ v2.1.15: 防复读（语音路径同样需要，turns 已是解析好的多轮消息）
    no_repeat = _recent_bot_snippets(turns, bot_name)
    fmt_reminder = _build_reminder("voice_reminder", max_chars=max_chars, no_repeat=no_repeat)
    user_parts.append(fmt_reminder)
    user_parts.append(f"{speaker_name}说：{current_msg}")
    turns.append({"role": "user", "content": "\n\n".join(user_parts)})

    # 4. 组装 messages：system + 固定锚点 + 多轮历史 + 当前消息
    #    锚点消息永远不变，保证前缀缓存命中（即使历史被 FIFO 裁剪）
    messages = [
        {"role": "system", "content": system_text},
        {"role": "user", "content": _MULTI_REPLY_ANCHOR},
    ]
    # 上下文超 60 轮时裁到最近 40，防止 JSON 模式拒答
    if len(turns) > 60:
        logger.warning("上下文过长(%d轮)，裁剪至最近40轮", len(turns))
        turns = turns[-40:]
    messages.extend(turns)

    logger.info("开始多句回复生成 | speaker=%s | history_turns=%d | extra=%d字",
               speaker_name, len(turns) - 1, len(extra_info))

    raw = await call_llm(reply_model, messages, max_tokens=max_tokens, temperature=0.4, json_mode=True, scene="reply")
    if not raw:
        logger.warning("多句回复生成为空，将使用 fallback")
        return [], 0, [], "", "", "", None, None, "user", {}, [], []

    logger.debug("多句生成原始输出: %s", raw[:200])

    # ------JSON解析（含防幻觉清洗）------
    try:
        # 1. 去掉 markdown 代码块
        raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.IGNORECASE)
        raw = re.sub(r'\s*```$', '', raw)
        # 2. 截取第一个 { 到最后一个 }，扔掉前后垃圾
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            raw = raw[start:end + 1]
        # 3. 去掉行尾 // 注释
        raw = re.sub(r'//[^\n]*', '', raw)
        data = json.loads(raw)
        # ── schema 校验 + 自动补缺 ──
        data = _normalize_reply_json(data)
        replies = data.get("replies", [])
        if not isinstance(replies, list) or not replies:
            raw_cleaned, fav_change = _extract_fav_change(raw)
            replies = _clean_sentences(raw_cleaned)
            logger.info("回退旧格式: %d句", len(replies))
            return replies, fav_change, [], "", "", "", None, None, None, "user", {}, []
        if isinstance(replies[0], list):
            replies = [str(r) for r in replies[0]]
        else:
            replies = [str(r) for r in replies]

        fav_change = data.get("fav", 0)
        calls = data.get("calls", [])

        face_cq = ""
        face = data.get("face")
        if face:
            try:
                from modules.face_lib import get_face, make_cq
                fp = get_face(str(face).strip())
                if fp:
                    face_cq = make_cq(fp)
            except Exception:
                pass

        mood = data.get("mood", "")
        mood_detail = data.get("mood_detail")  # 每句情绪数组
        instructs = data.get("instructs") or []  # 每句语气指令（语音模式）
        action = data.get("action", "")
        at_qq = data.get("at")
        mode_switch = data.get("mode")
        origin = data.get("origin", "user")
        actor = data.get("actor") or {}

        logger.info("JSON回复解析: %d句 fav=%+d calls=%d mood=%s action=%s",
                   len(replies), fav_change, len(calls), mood, action or "-")
        return replies, fav_change, calls, face_cq, mood, mood_detail, action, at_qq, mode_switch, origin, actor, instructs
    except json.JSONDecodeError:
        logger.warning("JSON解析失败，尝试修复: %s...", raw[:80])
        # 修复 JSON 中最常见错误：replies 数组里未转义的双引号
        try:
            # 1. 用正则定位 replies 数组
            m = re.search(r'"replies"\s*:\s*\[', raw)
            if m:
                bracket_start = m.end() - 1  # [
                # 找到对应的 ]
                depth = 0
                bracket_end = -1
                for i in range(bracket_start, len(raw)):
                    if raw[i] == '[':
                        depth += 1
                    elif raw[i] == ']':
                        depth -= 1
                        if depth == 0:
                            bracket_end = i
                            break
                    if bracket_end > bracket_start:
                        arr_text = raw[bracket_start + 1:bracket_end]
                        # 按 "," 切分（JSON 数组元素分隔符）
                        # 这样内部的引号不会被误切
                        entries = arr_text.split('","')
                        parts = []
                        for i, e in enumerate(entries):
                            if i == 0:
                                e = e.lstrip('"')
                            if i == len(entries) - 1:
                                e = e.rstrip('"')
                            # 去掉首尾可能残留的引号
                            e = e.strip().strip('"').strip()
                            if e:
                                parts.append(e)
                        if parts:
                            fav_m = re.search(r'"fav"\s*:\s*(-?\d+)', raw)
                            fav_change = max(-5, min(5, int(fav_m.group(1)) if fav_m else 0))
                            logger.info("修复提取: %d句 fav=%+d", len(parts), fav_change)
                            return parts, fav_change, [], "", "", None, "", None, None, "user", {}
        except Exception:
            pass
        logger.warning("修复也失败，回退旧格式: %s...", raw[:80])
        raw = raw.replace("\\n", "\n")
        raw_cleaned, fav_change = _extract_fav_change(raw)
        sentences = _clean_sentences(raw_cleaned)
        logger.info("多句回复生成完成(旧格式): %d句 fav=%+d", len(sentences), fav_change)
        return sentences, fav_change, [], "", "", None, "", None, None, "user", {}


# ── 判断模型调用 ────────────────────────────────────────────

async def judge_interest(
    msg: str,
    sender_name: str,
    context_str: str,
    judge_model: ModelConfig,
    personality_core: str,
) -> int:
    """
    调用精细兴趣度判断模型（第三级），返回 0~10 的兴趣度分数。
    """
    system = (
        f"你叫{get_config().bot_name}，消息记录:{context_str} "
        f"请输出你对'{msg}'的感兴趣的程度(0~10)，只输出数字，不能带有其他内容。"
        f"你的人设：{personality_core}，如果消息与你无关可输出2，非常相关输出10。"
        "如果消息是纯表情、无意义或私人对话，不应回复（可输出0）。"
    )
    result = await call_llm(
        judge_model,
        [{"role": "system", "content": system}],
        max_tokens=2,
        temperature=0.5,
        timeout=5.0,   # v2.0.4r: judge 只输出数字，5s 足够；15s 在故障期拖死队列
        scene="judge",
    )
    
    digits = "".join(c for c in result if c.isdigit())
    if not digits:
        return 0
    interest = int(digits)
    interest = min(10, max(0, interest))
    logger.debug("精细兴趣度判断: msg='%s...' → %d/10", msg[:30], interest)
    return interest


async def judge_should_reply_cheap(
    msg: str,
    context_str: str,
    cheap_model: ModelConfig,
) -> bool:
    """
    调用廉价判断模型（第二级），返回是否应该回复（bool）。
    """
    bot_name = get_config().bot_name
    prompt = (
        f"你是群聊管家。机器人叫{bot_name}。根据聊天记录判断机器人是否应回复新消息。\n"
        f"【最近对话】\n{context_str}\n"
        f"【新消息】{msg}\n"
        "如果机器人需要回应（被直接提及、问题可解答、话题高度相关），输出1；否则输出0。只输出数字。"
    )
    result = await call_llm(
        cheap_model,
        [{"role": "user", "content": prompt}],
        max_tokens=1,
        temperature=0,
        timeout=5.0,   # v2.0.4r
        scene="judge",
    )
    should = result.strip() == "1"
    logger.debug("廉价判断模型: msg='%s...' → %s", msg[:30], "REPLY" if should else "SKIP")
    return should


async def judge_need_search(
    msg: str,
    context_str: str,
    cheap_model: ModelConfig,
) -> bool:
    """判断消息是否需要联网搜索"""
    bot_name = get_config().bot_name
    prompt = (
        f"你是一个搜索判断助手。机器人叫{bot_name}，正在群聊中。\n"
        f"最近对话：{context_str}\n"
        f"新消息：{msg}\n"
        "这条消息是否需要联网查询实时信息或未知知识才能准确回答？"
        "如果需要，输出1；否则输出0。只输出数字。"
    )
    result = await call_llm(
        cheap_model,
        [{"role": "user", "content": prompt}],
        max_tokens=1,
        temperature=0,
        timeout=5.0,   # v2.0.4r
        scene="judge_search",
    )
    need = result.strip() == "1"
    logger.debug("搜索判断: msg='%s...' → %s", msg[:30], "SEARCH" if need else "NO_SEARCH")
    return need


# ── 并行调用工具 ────────────────────────────────────────────

async def call_judgment_pipeline(
    msg: str,
    sender_name: str,
    context_str: str,
    personality: str,
    cheap_model: ModelConfig | None,
    judge_model: ModelConfig | None,
) -> tuple[int, bool, bool]:
    """
    判断管道 — 相同模型时合并为一次调用
    Returns: (兴趣度分数 0~10, 廉价判断 bool, 是否在问架构 bool)
    """
    # 相同模型 → 合并为一次调用
    has_cheap = cheap_model and cheap_model.url and cheap_model.key and cheap_model.name
    has_judge = judge_model and judge_model.url and judge_model.key and judge_model.name

    # ★ v2.0.4r 熔断短路：上游判断模型故障期跳过模型调用，本地规则毫秒级判定，
    #   防止"每条群消息白等超时秒数"把 per-group 串行队列堵死（指令也被拖）。
    if has_cheap and is_model_circuit_open(cheap_model):
        _interest, _cheap, _arch = _fallback_judge(msg, personality)
        logger.warning("熔断[%s] 生效 → 规则兜底: msg='%s...' cheap=%s interest=%d (剩%.0fs)",
                       cheap_model.name.split("/")[-1][:24], msg[:20], _cheap, _interest,
                       max(0.0, _CIRCUIT.get(_circuit_key(cheap_model), {}).get("open_until", 0) - _time.time()))
        return _interest, _cheap, _arch

    if has_cheap and has_judge and cheap_model.name == judge_model.name:
        return await _judge_combined(msg, sender_name, context_str, cheap_model, personality)

    # 不同模型或仅一个 → 并行调用
    tasks = {}
    if has_cheap:
        tasks["cheap"] = asyncio.create_task(judge_should_reply_cheap(msg, context_str, cheap_model))
    if has_judge:
        tasks["interest"] = asyncio.create_task(judge_interest(msg, sender_name, context_str, judge_model, personality))

    interest_score = 0
    should_reply_cheap = False
    is_arch = False
    for name, task in tasks.items():
        try:
            result = await task
            if name == "cheap":
                should_reply_cheap = bool(result)
            elif name == "interest":
                interest_score = int(result) if result else 0
        except Exception as e:
            logger.warning("判断 '%s' 出错: %s", name, e)

    logger.debug("并行判断完成: cheap=%s interest=%d arch=N/A", should_reply_cheap, interest_score)
    return interest_score, should_reply_cheap, is_arch


# 熔断期间规则兜底：只在消息明显需要 bot 时放行（宁缺勿滥，快速出队不打扰）
_FALLBACK_ASK_RE = re.compile(
    r'(怎么|如何|什么|为什么|谁|哪|几岁|多少|是不是|会不会|能不能|可不可以|'
    r'帮我|给我|介绍一下|讲讲|说说|说一下|来一个|来张|来份|搜|查|找|'
    r'天气|几点|时间|新闻|@|？|\?)'
)


def _fallback_judge(msg: str, personality_core: str) -> tuple[int, bool, bool]:
    """上游判断模型熔断时的本地规则判定。
    返回 (兴趣度, 是否回复, 是否在问架构) —— 与 call_judgment_pipeline 契约一致。
    """
    text = (msg or "").strip()
    bot_name = get_config().bot_name
    if not text:
        return 0, False, False
    # 明确点名 bot / @bot → 必回（给满分，确保过 judge.py 的阈值判断）
    if bot_name and (bot_name in text):
        return 10, True, False
    # 明确提问 / 请求 → 回（让主模型接住；给 9 确保 > reply_interest=8 阈值）
    if _FALLBACK_ASK_RE.search(text[-60:]):
        return 9, True, False
    # 其余一律不回（无模型判断时不打扰群聊）
    return 0, False, False


async def _judge_combined(
    msg: str, sender_name: str, context_str: str,
    model: ModelConfig, personality_core: str,
) -> tuple[int, bool, bool]:
    """合并判断：一次 API 返回 cheap+interest+arch"""
    bot_name = get_config().bot_name
    prompt = (
        f"你是群聊助手 {bot_name}。回答三个问题，只输出 X|Y|Z：\n"
        f"X=1 机器人应回复 / 0 不应回复\n"
        f"Y=对消息的兴趣度 0~10\n"
        f"Z=1 消息在问机器人的架构/版本/模型/能力 / 0 不是\n\n"
        f"【人设】{personality_core}\n"
        f"【上下文】{context_str}\n"
        f"【新消息】{msg}\n"
    )
    result = await call_llm(model, [{"role": "user", "content": prompt}], max_tokens=20, temperature=0.4, timeout=5.0)  # v2.0.4r: 15s→5s
    result = result.strip()
    logger.debug("合并判断: '%s...' → '%s'", msg[:30], result)
    # ★ v2.0.4r: call_llm 失败返回 ""（与模型说"0|0|0"同形）。这里区分：
    #   空串/无法解析 = 调用失败 → 走规则兜底并返回，避免误判"不该回"而静默
    if not result:
        logger.warning("合并判断调用失败(空返回) → 规则兜底")
        return _fallback_judge(msg, personality_core)
    try:
        parts = result.replace(" ", "").split("|")
        cheap = parts[0].strip() == "1" if len(parts) >= 1 else False
        interest = int("".join(c for c in parts[1] if c.isdigit())) if len(parts) >= 2 else 0
        is_arch = parts[2].strip() == "1" if len(parts) >= 3 else False
        return min(10, max(0, interest)), cheap, is_arch
    except Exception:
        return _fallback_judge(msg, personality_core)


async def call_summary_model(
    messages: list[str],
    summary_model: ModelConfig,
    max_tokens: int = 80,
) -> str:
    """
    调用摘要模型总结对话记录。
    """
    from utils.format_lang import format_lang
    
    prompt = format_lang("memory.summarize_prompt", conversation="\n".join(messages[-30:]))
    
    result = await call_llm(
        summary_model,
        [{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=0.4,
        timeout=30.0,
    )
    
    if result:
        logger.info("对话摘要生成: %s...", result[:50])
    else:
        logger.warning("对话摘要生成为空")
    
    return result or ""

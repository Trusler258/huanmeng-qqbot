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

# ── main_skill.md 加载 ────────────────────────────────────

_skill_sections: dict[str, str] = {}
_skill_loaded = False


def _load_skill_sections() -> dict[str, str]:
    """解析 data/main_skill.md，按 ## 节名 切割返回 {节名: 内容}"""
    global _skill_sections, _skill_loaded
    if _skill_loaded:
        return _skill_sections

    skill_path = Path(__file__).resolve().parent.parent / "data" / "main_skill.md"
    if not skill_path.exists():
        logger.warning("main_skill.md 不存在: %s，使用内置回退", skill_path)
        _skill_loaded = True
        return _skill_sections

    try:
        text = skill_path.read_text(encoding="utf-8")
    except Exception as e:
        logger.error("读取 main_skill.md 失败: %s", e)
        _skill_loaded = True
        return _skill_sections

    sections: dict[str, str] = {}
    current_key = ""
    current_lines: list[str] = []

    for line in text.split("\n"):
        stripped = line.strip()
        # 纯注释行（# 开头但不是 ## 开头）和空行跳过
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

    _skill_sections = sections
    _skill_loaded = True
    logger.info("main_skill.md 已加载: %d 个章节", len(sections))
    return sections


def reload_skill_cache():
    """清除技能文件缓存（reload 时调用）"""
    global _skill_sections, _skill_loaded
    _skill_sections = {}
    _skill_loaded = False


def _build_system_text(bot_name: str, personality: str, is_group: bool) -> str:
    """根据群聊/私聊组装 system prompt"""
    sec = _load_skill_sections()

    header = sec.get("prompt_header", "你是{bot_name}").format(
        bot_name=bot_name, personality=personality
    )

    fmt_key = "group_format" if is_group else "private_format"
    format_rules = sec.get(fmt_key, "")

    fav_format = sec.get("fav_format", "")
    fav_tiers = sec.get("fav_tiers", "")
    anti_repeat = sec.get("anti_repeat", "")
    play_mode = sec.get("play_mode", "")
    command_tools = sec.get("command_tools", "")
    face_lib = sec.get("face_lib", "")
    private_tone = sec.get("private_tone", "") if not is_group else ""

    # 动态注入 COMMAND_MAP 全部指令
    cmd_list = _build_dynamic_command_list()

    return "\n\n".join(
        p for p in [header, format_rules, command_tools, cmd_list,
                     face_lib, private_tone, anti_repeat, fav_format, fav_tiers, play_mode]
        if p
    )


def _build_dynamic_command_list() -> str:
    """从 COMMAND_MAP 动态生成全部指令用法列表"""
    try:
        from modules.commands import COMMAND_MAP
    except ImportError:
        return ""
    seen = set()
    lines = ["【全部可调用指令（用法示例）】"]
    for name in sorted(COMMAND_MAP):
        if name in seen:
            continue
        seen.add(name)
        usage = _get_cmd_desc(name)
        if usage:
            lines.append(f"  {usage}")
        else:
            lines.append(f"  /~{name}")
    return "\n".join(lines)


def _get_cmd_desc(name: str) -> str:
    """从 lang.toml 提取指令用法的第一行示例"""
    try:
        from pathlib import Path
        lang_path = Path(__file__).resolve().parent.parent / "config" / "lang.toml"
        if lang_path.exists():
            import re
            text = lang_path.read_text(encoding="utf-8")
            pat = rf'\[help\.detail\.{re.escape(name)}\](.*?)(?=\n\[|\Z)'
            m = re.search(pat, text, re.DOTALL)
            if m:
                block = m.group(1)
                for line in block.strip().split("\n"):
                    line = line.strip()
                    if line.startswith("#") or "=" not in line:
                        continue
                    # 取 = 后面的值
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    # 展开 \n，取第一行用法示例
                    parts = val.split("\\n")
                    # 找第一行以 /~ 开头的示例
                    for p in parts:
                        p = p.strip()
                        if p.startswith("/~"):
                            return p.replace("【", "").replace("】", "")
                    # 没有 /~ 行就用第一行
                    first = parts[0].strip().replace("【", "").replace("】", "")
                    return first if first else ""
    except Exception:
        pass
    return ""


# ── 底层调用：同步 OpenAI → 异步执行器 ────────────────────

def _create_client(model_cfg: ModelConfig) -> OpenAI:
    """创建 OpenAI 客户端实例"""
    return OpenAI(base_url=model_cfg.url, api_key=model_cfg.key, timeout=60.0)


async def call_llm(
    model_cfg: ModelConfig,
    messages: list[dict],
    max_tokens: int | None = None,
    temperature: float = 0.7,
    timeout: float = 60.0,
    json_mode: bool = False,
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
        
    Returns:
        模型返回的文本内容；出错返回空字符串
    """
    client = _create_client(model_cfg)
    loop = asyncio.get_running_loop()
    
    logger.debug("调用LLM [%s] url=%s tokens=%s temp=%.1f timeout=%.1fs",
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
                logger.warning("JSON模式返回空，关闭json_mode重试...")
                return await call_llm(model_cfg, messages, max_tokens, temperature, timeout, json_mode=False)
            if finish == "length":
                boosted = min(max_tokens * 2, 8000)
                logger.warning("finish_reason=length, 扩大上限 %d→%d 重试...", max_tokens, boosted)
                return await call_llm(model_cfg, messages, boosted, temperature, timeout, json_mode)

        # 记录 token 消耗
        try:
            usage = completion.usage
            if usage:
                from core.token_tracker import record_usage
                cached = getattr(usage, 'prompt_tokens_details', None)
                cached_tokens = cached.cached_tokens if cached else 0
                record_usage(
                    model=model_cfg.name,
                    prompt_tokens=usage.prompt_tokens,
                    completion_tokens=usage.completion_tokens,
                    cached_tokens=cached_tokens,
                )
        except Exception:
            pass
        
        preview = text[:50] + ("..." if len(text) > 50 else "")
        logger.info("LLM [%s] 返回 %d字符 耗时%.1fs → \"%s\"",
                   model_cfg.name[:20], len(text), elapsed, preview.replace("\n", "\\n"))
        return text
    except asyncio.TimeoutError:
        elapsed = loop.time() - start_time
        logger.error("LLM [%s] 调用超时 (%.1fs/%.1fs)", model_cfg.name[:20], elapsed, timeout)
        return ""
    except Exception as e:
        elapsed = loop.time() - start_time
        logger.error("LLM [%s] 调用失败 (%.1fs): %s", model_cfg.name[:20], elapsed, e)
        return ""


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
    system_text = _build_system_text(
        bot_name=bot_name,
        personality=system_prompt,
        is_group=is_group,
    )

    # 2. 解析历史为多轮 user/assistant 消息（排除最后一条，因为它就是当前消息，
    #    会作为独立的最后一条 user 消息追加）
    history = msg_history[:-1] if msg_history else []
    turns = _build_history_messages(history, bot_name)

    # 3. 构造最后一条 user 消息：动态信息 + 格式提醒 + 当前发言
    user_parts = []
    if extra_info:
        user_parts.append(f"【当前可用的搜索/记忆信息】\n{extra_info}\n请参考以上信息回答，如果信息不相关可忽略。")
    # ★ 格式提醒：JSON 输出
    max_chars = "40" if is_group else "12"
    fmt_reminder = (
        "【格式规则：严格输出 JSON，不要任何额外文字】"
        "\n"
        '{"replies":["完整的第一句话","自然的第二句话"],"mood_detail":["开心","好奇"],"fav":2,"calls":[],"face":null,"mood":"开心","action":"摇了摇尾巴","at":null,"mode":null,"origin":"user","actor":{"name":"当前发言者","qq":发言人QQ号}}'
        "\n"
        f"日常回复 1~3 句，每句≤{max_chars}字。复杂问题 3~6 句，每句≤150 字，展开说透。fav -5~+5。"
            "\n"
                        "mood: 当前整体情绪。mood_detail: 每句话对应情绪数组(和replies一一对应)。action: 动作描写。at: @的QQ号，不@就null。mode: 模式切换。face: 极少用，通常null。"
            "\n"
            "origin: 谁发起操作(user/bot)。actor: 替谁执行({name,qq})，bot发起时actor=null。"
            "\n"
            '【origin和actor必须填！每句JSON都要包含这两个字段，origin不填默认当user，actor填发言者名字和QQ】'
            "\n"
            '不要输出残缺URL（如单独的"https:"），不知道完整链接就说不知道！'
            "\n"
            '【指令调用规则：如果有人要求你执行一个操作（如发战报/查天气/搜百度/发群统计等），必须通过calls执行对应指令，不要只回文字假装做了。calls里填指令名和参数，replies里说一句简短的"好的喵~"即可。】'
            "\n"
            '【致命规则：replies 内必须用标准 JSON，英文引号必须转义为 \\" ，或用中文引号「」替代！】'
            "\n"
            "【禁止：JSON之后严禁加任何注释、说明、//、/*、```、换行文字！】"
        )
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

    raw = await call_llm(reply_model, messages, max_tokens=max_tokens, temperature=0.8, json_mode=True)
    if not raw:
        logger.warning("多句回复生成为空，将使用 fallback")
        return [], 0, [], "", "", "", None, None, "user", {}

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
            return replies, fav_change, [], "", "", "", None, None, "user", {}
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
        action = data.get("action", "")
        at_qq = data.get("at")
        mode_switch = data.get("mode")
        origin = data.get("origin", "user")
        actor = data.get("actor") or {}

        logger.info("JSON回复解析: %d句 fav=%+d calls=%d mood=%s action=%s",
                   len(replies), fav_change, len(calls), mood)
        return replies, fav_change, calls, face_cq, mood, mood_detail, action, at_qq, mode_switch, origin, actor
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
                            return parts, fav_change, [], "", "", "", None, None, "user", {}
        except Exception:
            pass
        logger.warning("修复也失败，回退旧格式: %s...", raw[:80])
        raw = raw.replace("\\n", "\n")
        raw_cleaned, fav_change = _extract_fav_change(raw)
        sentences = _clean_sentences(raw_cleaned)
        logger.info("多句回复生成完成(旧格式): %d句 fav=%+d", len(sentences), fav_change)
        return sentences, fav_change, [], "", "", "", None, None, "user", {}


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
        timeout=15.0,
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
        timeout=10.0,
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
        timeout=10.0,
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
    result = await call_llm(model, [{"role": "user", "content": prompt}], max_tokens=500, temperature=0.3, timeout=15.0)
    result = result.strip()
    logger.debug("合并判断: '%s...' → '%s'", msg[:30], result)
    try:
        parts = result.replace(" ", "").split("|")
        cheap = parts[0].strip() == "1" if len(parts) >= 1 else False
        interest = int("".join(c for c in parts[1] if c.isdigit())) if len(parts) >= 2 else 0
        is_arch = parts[2].strip() == "1" if len(parts) >= 3 else False
        return min(10, max(0, interest)), cheap, is_arch
    except Exception:
        return 0, False, False


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
        temperature=0.3,
        timeout=30.0,
    )
    
    if result:
        logger.info("对话摘要生成: %s...", result[:50])
    else:
        logger.warning("对话摘要生成为空")
    
    return result or ""

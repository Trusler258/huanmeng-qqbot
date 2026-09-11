"""
上下文用量统计（/~ctx）
- 展示每条消息构建给 LLM 的上下文都装了啥：system / 参考资料 / 历史 / 注入 / 格式提醒 / 当前消息
- token 计算：优先用本地 DeepSeek tokenizer（data/tokenizer），失败回退字符估算
- 数据源与 services/llm.py 的真实构建路径保持一致（_build_system_text / _build_skill_refs / fmt_reminder）

用法: /~ctx [群号]
不带参数统计当前对话；带参数统计指定群（管理员）。
"""

from __future__ import annotations

import re

from core.logger import get_logger

logger = get_logger("ctx")

# 上下文窗口上限（DeepSeek 64K，留安全余量按 60K 计）
CTX_WINDOW = 60_000

# 超长注入的内容要截断，避免 /~ctx 自己也把上下文撑爆
_MAX_PREVIEW = 60


def _estimate_tokens(text: str) -> int:
    """无 tokenizer 时的字符估算：中文≈0.7 tok/字，英文/符号≈0.25 tok/字符"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = len(text) - cjk
    return max(1, int(cjk * 0.7 + other * 0.25))


def _tok_count(text: str) -> int | None:
    """真实 token 数；tokenizer 不可用/不可靠时返回 None。

    DeepSeek V3 tokenizer 在无 PyTorch 环境下 BPE 退化：encode 长文本返回
    严重偏小的值（12745 字符只算出 1236，正常应 ~6600）。检测规则：
    估算值(中文≈0.7/字) 与真实值差距 > 2.5 倍 → 判定不可靠，回退估算。
    """
    try:
        from core.token_tracker import _token_count
        n = _token_count(text)
        if n is None or n == 0:
            return None
        est = _estimate_tokens(text)
        # BPE 退化检测：真实值远小于估算值 → 不可靠
        if est > 100 and n < est / 2.5:
            return None
        return n
    except Exception:
        return None


def count_tokens(text: str) -> int:
    """对外统一的 token 计数：优先真实 tokenizer，回退估算"""
    n = _tok_count(text)
    return n if n is not None else _estimate_tokens(text)


def fmt_tokens(n: int) -> str:
    """格式化 token 数：1234 → 1.2K"""
    if n >= 1000:
        return f"{n / 1000:.1f}K"
    return str(n)


def fmt_pct(part: int, total: int) -> str:
    """百分比，最多 2 位小数"""
    if total <= 0:
        return "0.0%"
    return f"{part / total * 100:.1f}%"


def bar(part: int, total: int, width: int = 16) -> str:
    """进度条：▓ 已用 / ░ 剩余"""
    if total <= 0:
        return "░" * width
    filled = int(part / total * width)
    filled = max(0, min(width, filled))
    return "▓" * filled + "░" * (width - filled)


def _preview(text: str) -> str:
    """单行预览（截断）"""
    if not text:
        return ""
    line = text.replace("\n", " ").strip()
    if len(line) > _MAX_PREVIEW:
        return line[:_MAX_PREVIEW] + "…"
    return line


def build_ctx_report(chat_id: int, user_id: int = 0, is_group: bool = True, sample_msg: str = "") -> str:
    """统计某对话的上下文构成，返回展示文本。

    与 pipeline 主回复路径一致地重建 system / 参考资料 / 历史 / 注入 / 格式提醒 / 当前消息，
    逐项计算 token 数与占比。

    sample_msg: 用于判断"参考资料"热注入的当前消息。为空时用典型工具意图样例
    （/~ctx 本身不注入参考资料，这里展示的是正常工具请求时的最大参考占用）。
    """
    from core.config import get_config
    cfg = get_config()

    from core.context_manager import get_context_mgr
    ctx = get_context_mgr()

    history = ctx.get_context(chat_id)
    max_lines = cfg.context_length or 20

    # ── 1. system（与 pipeline 传入 _build_messages 的 cfg.system_prompt 一致）─
    #    _build_system_text 内部再用 main_skill 的 header/persona_lock/format 等
    #    叠一层，这里直接调它拿完整 system（与真实请求一致）
    system_text = ""
    try:
        from services.llm import _build_system_text
        system_text = _build_system_text(
            bot_name=cfg.bot_name,
            personality=cfg.system_prompt,
            is_group=is_group,
        )
    except Exception as e:
        logger.warning("ctx: system 构建失败 %s", e)
        system_text = ""

    # ── 2. 参考资料（热注入，按 sample_msg 意图判断）────────────
    refs_text = ""
    try:
        from services.llm import _build_skill_refs, _detect_skill_needs
        if sample_msg:
            needs = _detect_skill_needs(sample_msg, is_group)
        else:
            needs = {"tools"}  # /~ctx 无当前消息，展示典型工具请求的最大参考占用
        refs_text = _build_skill_refs(needs, is_group, sample_msg or "帮我查一下天气")
    except Exception as e:
        logger.warning("ctx: 参考资料构建失败 %s", e)
        refs_text = ""

    # ── 3. 历史消息（Conversation）──────────────────────────
    conv_text = "\n".join(history[-max_lines:])

    # ── 4. 注入信息（extra_info，与 pipeline 对齐）──────────
    extra_text = ""
    try:
        extra_parts = []
        from datetime import datetime
        now = datetime.now()
        now_str = now.strftime("%Y年%m月%d日 %H:%M:%S")
        weekdays = "日一二三四五六"
        now_str += f" 周{weekdays[int(now.strftime('%w'))]}"
        extra_parts.append(f"当前时间：{now_str}")

        try:
            from core.bot_notes import load_notes
            _n = load_notes(chat_id)
            if _n:
                extra_parts.append(_n)
        except Exception:
            pass
        extra_text = "\n".join(extra_parts)
    except Exception as e:
        logger.warning("ctx: 注入信息构建失败 %s", e)
        extra_text = ""

    # ── 5. 格式提醒 fmt_reminder（固定段，与 _build_messages 完全一致）──
    max_chars = "40" if is_group else "20"
    reminder_text = (
        "★★★ 最重要规则：你的全部回复必须是 JSON 格式，绝不允许输出纯文本 ★★★\n"
        "如果你不了解，可以调用搜索工具查一下。\n"
        "用户让你写代码/做游戏/做网页/写脚本时，必须调用 write_code 工具，不要口头承诺。"
        "出题/写文章/答疑等直接文字回答。"
        "数学题/方程/方程组/计算题必须调用 calc 工具用代码精确求解，不要心算。"
        "★ 搜索规则：闲聊、寒暄、接梗、聊已知日常话题时不要搜。但名词/概念类提问"
        "（XX是啥/是什么/什么意思/这词哪来的）、你不确定的、涉及时效或数据的问题，"
        "必须先用 search 查证再答——禁止凭印象瞎猜，禁止反问'从哪看到的'打发；拿不准就查，别硬答。"
        "不用工具就直接输出 JSON。\n"
        "★ 笔记本规则（必须真记，出现即调）：①群内关系 ②称呼/黑话 ③某人身份/昵称/性别/特征 "
        "④有人明确说'记一下/记住'。碰到任一类，立即用 calls 调 note 记录，写清'谁=什么信息'。"
        "判定要点：只要群里的人这样称呼/这样认为就照记——哪怕是知名角色或公众人物，"
        "你记的是「这个群的事实」，不是百科。发现之前记错了或过时了 → 用 calls 调 note 传 "
        "'fix <序号> <新内容>' 改正，别重记一条。只在回复里说'记下了'而没有实际调用，是骗人，绝对禁止。\n"
        '回复格式（必填只有这两个）: {"replies":["回复内容"],"fav":0}\n'
        "★ 动态字段——只在真的需要时才带上，不需要就【整个省略这个 key】，绝对不要写 null 占位（省 token）：\n"
        '  · "mood":"开心" —— 当前情绪，一般带上\n'
        '  · "calls":[{"name":"指令名","args":"参数"}] —— 需要调指令时才加\n'
        '  · "action":"动作描写" —— 需要肢体动作才加（少用）\n'
        '  · "at":对方QQ号 —— 需要@某人时才加\n'
        '  · "face":"表情关键词" —— 极少用\n'
        '  · "origin":"user","actor":{"name":"发言人","qq":0} —— 只在带 calls 时一起加\n'
        f"回复长度随场景：闲聊/接梗 1~3 句、每句≤{max_chars}字；知识/技术/原理/概念/步骤/对比/追问 "
        f"→ 3~10 句、不限字数，讲透为止。fav -5~+5。"
        "严格按上面的 JSON 格式输出（replies 和 fav 必须有，其他按需）。"
    )

    # ── 6. 当前消息示例 ─────────────────────────────────────
    cur_text = "（示例）帮我查一下明天北京的天气"

    # 逐项 token
    parts = [
        ("System Prompt", system_text),
        ("参考资料", refs_text),
        ("Conversation", conv_text),
        ("注入信息", extra_text),
        ("格式提醒", reminder_text),
        ("当前消息", cur_text),
    ]
    tokens = {name: count_tokens(t) for name, t in parts}
    total = sum(tokens.values())
    pct = total / CTX_WINDOW * 100

    lines = []
    lines.append(f"上下文用量 {pct:.1f}% · {fmt_tokens(total)} / {fmt_tokens(CTX_WINDOW)}")
    lines.append(f"`{bar(total, CTX_WINDOW)}`")
    lines.append("")
    # 固定部分
    lines.append(f"System Prompt  ~{fmt_tokens(tokens['System Prompt'])}   {fmt_pct(tokens['System Prompt'], CTX_WINDOW)}")
    lines.append(f"参考资料     ~{fmt_tokens(tokens['参考资料'])}   {fmt_pct(tokens['参考资料'], CTX_WINDOW)}")
    lines.append("")
    lines.append(f"Conversation   ~{fmt_tokens(tokens['Conversation'])}   {fmt_pct(tokens['Conversation'], CTX_WINDOW)}  ({len(history[-max_lines:])}条/上限{max_lines}条)")
    lines.append(f"注入信息     ~{fmt_tokens(tokens['注入信息'])}   {fmt_pct(tokens['注入信息'], CTX_WINDOW)}")
    lines.append(f"格式提醒     ~{fmt_tokens(tokens['格式提醒'])}   {fmt_pct(tokens['格式提醒'], CTX_WINDOW)}")
    lines.append(f"当前消息     ~{fmt_tokens(tokens['当前消息'])}   {fmt_pct(tokens['当前消息'], CTX_WINDOW)}")
    lines.append("")
    # 超长项预警
    for name, t in tokens.items():
        if t > 5000 and name in ("Conversation", "注入信息"):
            lines.append(f"⚠ {name} 已超 5K tokens，建议清理或调低上下文长度")
    return "\n".join(lines)


async def cmd_ctx(args, user_id, group_id, sender_name, is_group, bot_qq):
    """查看上下文用量 /~ctx"""
    from core.config import get_config
    cfg = get_config()

    # 支持指定群号（管理员）
    target = group_id
    if args and args[0].strip().lstrip("-").isdigit():
        q = int(args[0].strip())
        if not cfg.is_admin(user_id, group_id):
            return "只有管理员才能查看指定群的上下文喵~"
        target = q

    try:
        return build_ctx_report(target, user_id=user_id, is_group=is_group)
    except Exception as e:
        logger.exception("ctx 指令失败")
        return f"上下文统计失败: {e}"
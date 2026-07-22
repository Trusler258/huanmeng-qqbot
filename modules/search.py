"""
搜索模块（从 commands.py 抽离）
- 本地多源搜索 + 缓存集成
- 关键词触发 + 实时查询检测
- 三数据源：百度百科 / 百度网页 / 必应
"""

from __future__ import annotations

import asyncio
import re
from typing import Optional

import httpx

from core.logger import get_logger
from modules.judge import get_cached_search, set_search_cache, is_realtime_query, keyword_need_search
from modules.memory import save_search_memory
from utils.format_lang import format_lang

logger = get_logger("search")


async def perform_search(
    query: str,
    sender_name: str = "",
    user_id: int = 0,
    chat_id: int = 0,
    limit: int = 4,
    source: str = "all",
    is_group: bool = False,
) -> Optional[str]:
    """
    执行搜索：缓存 → DDG → 格式化 → 写缓存/记忆。
    
    Args:
        query: 搜索关键词
        sender_name: 触发搜索的用户名
        user_id: 用户 QQ 号
        chat_id: 对话 ID
        is_group: 是否群聊（用于发送进度提示）
        
    Returns:
        搜索结果文本；无结果或失败返回 None
    """
    # ── Step 1: 缓存命中检查 ──
    cached = get_cached_search(query)
    if cached:
        logger.info("搜索缓存命中: '%s...'", query[:30])
        return cached

    # ★ 缓存未命中 → 真正要搜索了，发进度提示（异步不阻塞）
    from services.sender import send_by_chat_type
    search_tip = "🔍 正在搜索中喵~" if is_group else "🔍 正在搜喵~"
    asyncio.create_task(send_by_chat_type(
        search_tip, chat_id, is_group,
        user_id=user_id if not is_group else None
    ))

    # ── Step 2: 执行搜索（本地多源：百科→百度→必应）──
    logger.info("执行搜索: '%s...' (user=%s)", query[:40], sender_name)
    try:
        from modules.local_search import get_searcher
        loop = asyncio.get_running_loop()
        searcher = get_searcher()
        result_text = await loop.run_in_executor(
            None,
            lambda: searcher.run_search(query, source=source, limit=limit),
        )
    except Exception as e:
        logger.error("搜索异常: %s", e)
        return None

    if not result_text:
        logger.info("搜索无结果: '%s...'", query[:30])
        return None

    # 截断（按请求条数弹性调整）
    max_len = min(limit * 250, 2000)
    if len(result_text) > max_len:
        result_text = result_text[:max_len] + "..."

    result_text = f"【搜索结果】\n{result_text}"
    logger.info("搜索完成: '%s...' → %d字符", query[:30], len(result_text))

    # ── Step 4: 写入缓存 + 记忆 ──
    realtime = is_realtime_query(query)
    set_search_cache(query, result_text, realtime=realtime)

    if sender_name and chat_id:
        try:
            await asyncio.to_thread(
                save_search_memory, query, result_text, sender_name, user_id, chat_id,
            )
        except Exception as e:
            logger.warning("写入搜索记忆失败: %s", e)

    return result_text


async def auto_search_if_needed(
    msg: str,
    sender_name: str,
    user_id: int,
    chat_id: int,
    is_group: bool = False,
) -> Optional[str]:
    """
    自动搜索：根据关键词和实时性判断是否需要搜索。
    仅对非 @ 消息生效。
    
    Returns:
        搜索结果文本；不需要搜索时返回 None
    """
    from modules.judge import needs_search as _needs_search_judge
    
    msg_lower = msg.lower()

    # ★ 预检：用户拒搜 → 跳过（与 core/tools.py 保持一致）
    no_search_patterns = [
        r'不[要需用]搜', r'别搜', r'不[要需用]查', r'不用搜索',
        r'不[是要能].*[搜查找]', r'别[去再].*[搜查找]',
        r'你[自己]的(?:回答|想法|看法|意见|思考)',
        r'(?:不要|不想|不需要).*搜索', r'(?:而)?不是.*搜索',
        r'搜[索到]了?[什么啥]', r'不要.*[网上去]?找',
    ]
    if any(re.search(p, msg_lower) for p in no_search_patterns):
        logger.info("自动搜索跳过（用户拒搜）: '%s...'", msg[:30])
        return None

    # 快速关键词匹配
    if keyword_need_search(msg):
        logger.info("自动搜索触发（关键词）: '%s...'", msg[:30])
        return await perform_search(msg, sender_name=sender_name, user_id=user_id, chat_id=chat_id, is_group=is_group)

    # 实时话题判断
    if is_realtime_query(msg):
        logger.info("自动搜索触发（实时话题）: '%s...'", msg[:30])
        return await perform_search(msg, sender_name=sender_name, user_id=user_id, chat_id=chat_id, is_group=is_group)

    # 回退到模型判断（较慢，仅前面都没命中时）
    context_str = ""  # 调用方应传入上下文
    need_model = await _needs_search_judge(msg, context_str)
    if need_model:
        logger.info("自动搜索触发（模型判断）: '%s...'", msg[:30])
        return await perform_search(msg, sender_name=sender_name, user_id=user_id, chat_id=chat_id, is_group=is_group)

    return None

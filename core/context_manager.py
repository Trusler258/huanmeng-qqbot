"""
上下文管理器
- 管理每个对话的上下文消息列表（v2.3.10 起改为**分块结构**）
- 管理每个对话的记忆缓冲区
- 管理活跃的发送任务（支持取消旧任务）
- 持久化到 data/context_cache.json，重启不丢瞬时记忆

【会话分块结构（v2.3.10，用户方案）】
拼给 LLM 的顺序：
    SYSTEM → 长期记忆 → 会话摘要(可多条) → BLOCK 1..N（已冻结）→ 当前块（增长中）→ 当前消息
设计要点：
  · 每 BLOCK_SIZE 条消息**封闭**成一个 block，封闭后内容永不再改；
  · 新消息只往"当前块"末尾追加 —— 于是整条历史是"只追加、老块不动"的序列，
    DeepSeek 前缀缓存（按消息序列逐 token 匹配）几乎全命中（v2.3.9 前的
    history[-200:] 每轮前移一位，会把历史前缀全部打碎）；
  · 未压缩块数 > MAX_BLOCKS 时，把最早的 COMPRESS_BATCH 块交给便宜模型压成
    一段会话摘要（**只追加、不回改**），块随之移除 —— 低频操作，前缀依然稳定；
  · 容量：BLOCK_SIZE(200) × (MAX_BLOCKS(20)+1) ≈ 4200 条消息，
    按中文约 30~50 token/条估算 ≈ 130K~210K tokens，在 1M 窗口内很安全。
"""

from __future__ import annotations

import asyncio
import json
import time as _time
from pathlib import Path
from typing import Optional

from core.logger import get_logger
from core.config import get_config

logger = get_logger("context")

# ── 分块参数 ─────────────────────────────────────────────
# 单块条数默认 200；实际优先取配置里的「消息记录长度」(context_length)，
# 这样换部署时改 toml 即可，不用动代码。
BLOCK_SIZE = 200
MAX_BLOCKS = 20        # 未压缩块上限，超过就压缩
COMPRESS_BATCH = 10    # 一次压掉最早的几块（压完还剩 MAX-BATCH 块，低频操作）

_CACHE_FILE = Path(__file__).resolve().parent.parent / "data" / "context_cache.json"
# 磁盘上最多保留多少个已封闭块（摘要另存，不受此限）——控制文件体积
_KEEP_BLOCKS_ON_DISK = MAX_BLOCKS + 2


class ContextManager:
    """
    全局对话上下文管理。
    
    每个群/私聊维护独立的：
    - group_context: 供 LLM 参考的消息历史（带角色标签）
    - memory_buffer: 供记忆系统使用的原始消息（不带标签）
    - active_send_tasks: 当前活跃的发送任务
    """

    def __init__(self):
        # group_context[chat_id] = 当前**正在填充**的块（未封闭）
        self.group_context: dict[int, list[str]] = {}
        # closed_blocks[chat_id] = [{"id": 1, "lines": [...]}] 已封闭的块（冻结，不再改）
        self.closed_blocks: dict[int, list[dict]] = {}
        # summaries[chat_id] = ["【会话摘要】...", ...] 压缩产物，**只追加、不回改**
        self.summaries: dict[int, list[str]] = {}
        self.memory_buffer: dict[int, list[str]] = {}
        self.active_send_tasks: dict[int, asyncio.Task] = {}
        self._dirty: set[int] = set()
        self._load_from_disk()

    # ── 分块参数 ─────────────────────────────────────────

    def _block_size(self) -> int:
        """单块条数：优先取配置里的「消息记录长度」，异常时回退默认值。"""
        try:
            n = int(get_config().context_length)
            return n if n > 0 else BLOCK_SIZE
        except Exception:
            return BLOCK_SIZE

    def _next_block_id(self, chat_id: int) -> int:
        return len(self.closed_blocks.get(chat_id, [])) + 1

    # ── 持久化 ──────────────────────────────────────────

    def _load_from_disk(self):
        """从文件恢复上下文（兼容 v2.3.9 及以前的纯列表格式）"""
        try:
            if not _CACHE_FILE.exists():
                return
            with open(_CACHE_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            for k, v in raw.items():
                cid = int(k)
                if isinstance(v, dict):
                    # 新格式：{"current": [...], "blocks": [...], "summaries": [...]}
                    self.group_context[cid] = list(v.get("current") or [])
                    self.closed_blocks[cid] = list(v.get("blocks") or [])
                    self.summaries[cid] = list(v.get("summaries") or [])
                elif isinstance(v, list):
                    # 旧格式：纯列表 → 按整块切分进 blocks，余数留作当前块
                    # 注意：整块数用 len//size，余数 = lines[n_blocks*size:]，
                    # 不能写成 lines[-size:]（会与最后一块重叠、总数翻倍）。
                    size = self._block_size()
                    lines = [str(x) for x in v]
                    n_blocks = len(lines) // size
                    if n_blocks:
                        self.closed_blocks[cid] = [
                            {"id": i + 1, "lines": lines[i * size:(i + 1) * size]}
                            for i in range(n_blocks)
                        ]
                    self.group_context[cid] = lines[n_blocks * size:]
            logger.info("上下文已从磁盘恢复: %d 个对话（分块结构）", len(raw))
        except Exception as e:
            logger.warning("上下文恢复失败: %s", e)

    def _save_to_disk(self):
        """持久化上下文（分块结构；磁盘只留最近若干块，摘要全留）"""
        try:
            _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            compact = {}
            for cid, cur in self.group_context.items():
                blocks = self.closed_blocks.get(cid, [])[-_KEEP_BLOCKS_ON_DISK:]
                sums = self.summaries.get(cid, [])
                if not cur and not blocks and not sums:
                    continue
                compact[str(cid)] = {
                    "current": cur,
                    "blocks": blocks,
                    "summaries": sums,
                }
            with open(_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(compact, f, ensure_ascii=False)
        except Exception as e:
            logger.warning("上下文持久化失败: %s", e)

    # ── 上下文操作 ───────────────────────────────────────

    def append_to_context(self, chat_id: int, line: str):
        """追加一条消息到上下文历史"""
        if chat_id not in self.group_context:
            self.group_context[chat_id] = []
        
        self.group_context[chat_id].append(line)

        # 当前块填满 → 封存（此后这一块内容永不再改，前缀稳定可缓存）
        size = self._block_size()
        if len(self.group_context[chat_id]) >= size:
            blocks = self.closed_blocks.setdefault(chat_id, [])
            blocks.append({"id": len(blocks) + 1, "lines": list(self.group_context[chat_id])})
            self.group_context[chat_id] = []
            logger.info("上下文分块封存 [%d]: block #%d（%d 条），待压缩块数 %d/%d",
                        chat_id, len(blocks), size, len(blocks), MAX_BLOCKS)

        # 每 3 条写一次磁盘
        if len(self.group_context[chat_id]) % 3 == 0:
            self._save_to_disk()

    def get_context(self, chat_id: int) -> list[str]:
        """获取某对话的完整上下文（摘要 + 已封存块 + 当前块，按时间顺序展开）

        调用方（pipeline / ctx_usage）拿到的仍是**线性列表**，接口不变；
        分块只影响"前缀稳定性"和"压缩粒度"。
        """
        out: list[str] = []
        out.extend(self.summaries.get(chat_id, []))
        for b in self.closed_blocks.get(chat_id, []):
            out.extend(b.get("lines", []))
        out.extend(self.group_context.get(chat_id, []))
        return out

    # ── 会话摘要压缩 ─────────────────────────────────────

    async def maybe_compress(self, chat_id: int) -> bool:
        """未压缩块数超过上限时，把最早的若干块压成一段会话摘要。

        返回 True 表示真的压缩了。设计上：
          · 摘要**只追加**（加在 summaries 末尾），老摘要不回改 → 前缀依旧稳定；
          · 块数低于阈值时直接返回，不做任何事（绝大多数调用走这条路径）；
          · 压缩交给便宜模型，异步调用，不阻塞用户回复。
        """
        blocks = self.closed_blocks.get(chat_id, [])
        if len(blocks) <= MAX_BLOCKS:
            return False

        batch = blocks[:COMPRESS_BATCH]
        text = "\n".join(ln for b in batch for ln in b.get("lines", []))
        if not text.strip():
            self.closed_blocks[chat_id] = blocks[COMPRESS_BATCH:]
            return False

        summary = await self._summarize(text)
        if not summary:
            logger.warning("会话压缩失败（模型无输出），保留原块 [%d]", chat_id)
            return False

        idx = len(self.summaries.get(chat_id, [])) + 1
        self.summaries.setdefault(chat_id, []).append(f"【会话摘要 {idx}】{summary}")
        self.closed_blocks[chat_id] = blocks[COMPRESS_BATCH:]
        self._save_to_disk()
        logger.info("会话压缩完成 [%d]: %d 块 → 摘要 %d 字，剩余待压缩块 %d",
                    chat_id, len(batch), len(summary), len(self.closed_blocks[chat_id]))
        return True

    async def _summarize(self, text: str) -> str:
        """调便宜模型把若干块压成摘要（提示词见 data/skills/90_summary.md）"""
        try:
            from services.llm import call_llm, _load_skill_sections
            cfg = get_config()
            sec = _load_skill_sections()
            prompt = (sec.get("session_summary") or "").strip() or (
                "把下面的群聊记录压缩成简洁的要点摘要。保留：人物关系与称呼、"
                "长期有效的事实（偏好/身份/约定）、正在进行的话题脉络、重要的情绪事件。"
                "丢弃：寒暄、重复、一次性闲聊。第三人称陈述，不要加评论，控制在 300 字内。"
            )
            model = cfg.cheap_model if (cfg.cheap_model.url and cfg.cheap_model.key) else cfg.reply_model
            out = await call_llm(
                model,
                [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": text[:20000]},
                ],
                max_tokens=600, temperature=0.3, timeout=60.0, scene="session_summary",
            )
            return (out or "").strip()[:800]
        except Exception as e:
            logger.warning("会话摘要生成异常: %s", e)
            return ""

    # ── 分块统计（/~ctx 与排查用）─────────────────────────

    def block_stats(self, chat_id: int) -> dict:
        return {
            "current": len(self.group_context.get(chat_id, [])),
            "closed_blocks": len(self.closed_blocks.get(chat_id, [])),
            "summaries": len(self.summaries.get(chat_id, [])),
            "total_lines": len(self.get_context(chat_id)),
        }

    # ── 记忆缓冲区操作 ───────────────────────────────────

    def append_to_buffer(self, chat_id: int, line: str):
        """追加一条消息到记忆缓冲区"""
        if chat_id not in self.memory_buffer:
            self.memory_buffer[chat_id] = []
        self.memory_buffer[chat_id].append(line)

    def get_buffer(self, chat_id: int) -> list[str]:
        """获取某对话的记忆缓冲区"""
        return self.memory_buffer.get(chat_id, [])

    def clear_buffer(self, chat_id: int):
        """清空缓冲区"""
        if chat_id in self.memory_buffer:
            self.memory_buffer[chat_id].clear()

    # ── 发送任务管理 ─────────────────────────────────────

    def set_active_send_task(self, chat_id: int, task: asyncio.Task):
        """设置活跃发送任务（会取消旧任务）"""
        old_task = self.active_send_tasks.get(chat_id)
        if old_task is not None and not old_task.done():
            logger.debug("取消旧发送任务 [%d]", chat_id)
            old_task.cancel()
        
        self.active_send_tasks[chat_id] = task
        
        # 注册完成回调以清理引用
        task.add_done_callback(lambda t: self._on_task_done(chat_id, t))

    def cancel_old_task(self, chat_id: int) -> Optional[asyncio.Task]:
        """
        取消旧的发送任务并返回它。
        新任务应该在调用此方法后通过 set_active_send_task 设置。
        """
        return self.active_send_tasks.get(chat_id)

    def _on_task_done(self, chat_id: int, task: asyncio.Task):
        """任务完成后的清理回调"""
        if chat_id in self.active_send_tasks and self.active_send_tasks[chat_id] is task:
            del self.active_send_tasks[chat_id]
            logger.debug("发送任务已清理 [%d]", chat_id)

    # ── 统计与调试 ───────────────────────────────────────

    def get_stats(self) -> dict:
        """获取管理器状态统计"""
        return {
            "active_chats": len(self.group_context),
            "total_context_lines": sum(len(v) for v in self.group_context.values()),
            "total_buffer_lines": sum(len(v) for v in self.memory_buffer.values()),
            "active_tasks": len(self.active_send_tasks),
        }

    def cleanup_inactive(self, max_idle_seconds: float = 3600.0):
        """清理长时间不活动的对话上下文（可选，定时调用）"""
        import time
        now = time.time()
        to_remove = []
        for chat_id in list(self.group_context.keys()):
            ctx = self.group_context[chat_id]
            if not ctx:
                continue
            # 简单启发：如果最后一条消息时间戳... 实际上我们没记录时间戳
            # 这里用条数过少的作为不活跃标记（简单实现）
            pass  # TODO: 添加时间戳追踪后实现真正的过期清理


# ── 全局单例 ────────────────────────────────────────────────
_global_ctx_mgr: Optional[ContextManager] = None


def get_context_mgr() -> ContextManager:
    global _global_ctx_mgr
    if _global_ctx_mgr is None:
        _global_ctx_mgr = ContextManager()
    return _global_ctx_mgr


def init_context():
    """初始化全局上下文管理器"""
    global _global_ctx_mgr
    _global_ctx_mgr = ContextManager()
    logger.info("上下文管理器已初始化（含磁盘持久化）")


def save_context():
    """SAFELY persist all context to disk (call on shutdown)"""
    mgr = get_context_mgr()
    # Transfer all group_context data before saving
    try:
        mgr._save_to_disk()
        logger.info("上下文已写入磁盘")
    except Exception as e:
        logger.warning("上下文写入失败: %s", e)

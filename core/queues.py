"""
per-group 异步消息队列 + 渲染队列
- 每个群/私聊独立一个 asyncio.PriorityQueue + worker，互不阻塞
- ★ v2.0.4r: 指令消息(priority=0)永远插到普通消息(priority=1)前。
  事故背景 2026-09-02: 普通消息 judge 阶段每条白等 15s 超时，串行队列积压
  到 8，/ ~restart 指令排后面被拖 45s。指令不依赖 LLM 判断，必须优先响应。
- 图片渲染走独立队列，信号量=1，不阻塞聊天
"""

from __future__ import annotations

import asyncio
import itertools
import time as _time
from core.logger import get_logger

logger = get_logger("queues")

# 单条消息处理总超时（秒）。超过视为卡死（如 2026-09-02 双进程事故中 worker
# 卡在单条消息上 10 分钟，队列积压 17 条无人消费），超时强制跳过继续出队。
_GROUP_MSG_TIMEOUT = 200.0

# ── 消息队列 ──
_group_queues: dict[int, asyncio.PriorityQueue] = {}
_group_tasks: dict[int, asyncio.Task] = {}
_seq_counter = itertools.count(1)   # 全局递增序号：保证 (priority, seq) 唯一可比

# ── 渲染队列（串行化，避免抢 Chromium）──
_render_queue: asyncio.Queue | None = None
_render_task: asyncio.Task | None = None
_render_semaphore = asyncio.Semaphore(2)  # 最多 2 个并发渲染


def _get_or_create_queue(chat_id: int) -> asyncio.PriorityQueue:
    """获取或创建某个对话的独立队列"""
    if chat_id not in _group_queues:
        q = asyncio.PriorityQueue()
        _group_queues[chat_id] = q
        task = asyncio.ensure_future(_group_worker(chat_id, q))
        _group_tasks[chat_id] = task
        logger.info("[队列] 创建群%d 的独立消息队列", chat_id)
    else:
        # ★ v2.0.4r 看门狗：worker 意外死亡(Cancel/崩溃)时自动重建，杜绝队列无人消费
        task = _group_tasks.get(chat_id)
        if task is None or task.done():
            q = _group_queues[chat_id]
            task = asyncio.ensure_future(_group_worker(chat_id, q))
            _group_tasks[chat_id] = task
            logger.warning("[队列] 群%d worker 已死亡(done=%s)，自动重建，队列残留%d条",
                           chat_id, task.done(), q.qsize())
    return _group_queues[chat_id]


async def _group_worker(chat_id: int, queue: asyncio.PriorityQueue):
    """某个群的 worker，串行处理该群消息。
    ★ v2.0.4r 加固：
      - 单条消息总超时 _GROUP_MSG_TIMEOUT（wait_for），超时打日志后跳过继续出队，
        单条卡死不再拖死整个群队列
      - 任何意外异常 → 记录后自动续命重跑，worker 永不静默死亡
    """
    from core.pipeline import process_message
    while True:
        try:
            # ★ v2.0.4r bug修复: 入队是 (priority, seq, kwargs) 三元组，这里必须三元解包！
            #   2026-09-02 事故: 漏改此处为二元解包 → 每条消息取出即抛
            #   ValueError('too many values to unpack') → worker 死亡/死循环，队列只进不出
            _priority, _seq, kwargs = await queue.get()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("[队列] 群%d worker 取消息异常: %s → 重试", chat_id, e)
            await asyncio.sleep(1)
            continue
        _t0 = _time.monotonic()
        try:
            await asyncio.wait_for(process_message(**kwargs), timeout=_GROUP_MSG_TIMEOUT)
            _dt = _time.monotonic() - _t0
            if _dt > 5:
                logger.info("[队列] 群%d 消息处理完成 耗时%.1fs: '%s...'",
                            chat_id, _dt, str(kwargs.get("msg_content", ""))[:30])
        except asyncio.TimeoutError:
            logger.error("[队列] 群%d 消息处理超时(>%.0fs)已强制跳过: '%s...' → 继续下一条",
                         chat_id, _GROUP_MSG_TIMEOUT, str(kwargs.get("msg_content", ""))[:40])
        except asyncio.CancelledError:
            queue.task_done()
            break
        except Exception as e:
            logger.error("[队列] 群%d 处理异常: %s", chat_id, e)
        finally:
            queue.task_done()


async def enqueue_message(**kwargs):
    """将消息投入对应群的队列，不阻塞调用方。
    ★ is_command=True 的消息 priority=0 插队（指令不需 LLM 判断，不能被
    卡在 judge 阶段的普通消息堵住）。
    """
    chat_id = kwargs.get("chat_id", 0)
    priority = 0 if kwargs.pop("is_command", False) else 1
    seq = next(_seq_counter)
    q = _get_or_create_queue(chat_id)
    await q.put((priority, seq, kwargs))
    logger.debug("[队列] 群%d 消息已入队 (prio=%d 队长度=%d)", chat_id, priority, q.qsize())


# ── 渲染队列 ──

async def _render_worker(queue: asyncio.Queue):
    """渲染 worker，串行处理图片生成请求"""
    while True:
        try:
            future, render_fn, args, kwargs = await queue.get()
            async with _render_semaphore:
                try:
                    result = await render_fn(*args, **kwargs)
                    future.set_result(result)
                except Exception as e:
                    future.set_exception(e)
        except asyncio.CancelledError:
            break
        finally:
            queue.task_done()


def start_render_queue():
    """启动渲染队列"""
    global _render_queue, _render_task
    if _render_task is None:
        _render_queue = asyncio.Queue()
        _render_task = asyncio.ensure_future(_render_worker(_render_queue))
        logger.info("[队列] 渲染队列已启动")


async def submit_render(render_fn, *args, **kwargs):
    """提交渲染任务，返回结果（异步等待）"""
    if _render_queue is None:
        start_render_queue()
    future = asyncio.Future()
    await _render_queue.put((future, render_fn, args, kwargs))
    return await future


async def shutdown_queues():
    """关闭所有队列和 worker"""
    for task in list(_group_tasks.values()):
        task.cancel()
    if _render_task:
        _render_task.cancel()
    await asyncio.gather(*_group_tasks.values(), return_exceptions=True)
    logger.info("[队列] 所有队列已关闭")

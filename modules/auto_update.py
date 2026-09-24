"""
自动更新模块 v2 — Git Patch 行级增量合并（安全代码级更新）

/~update         手动触发增量更新（安全流水线：
                 Fetch→Diff→分析→评估→快照→Staging→测试→Health→生产→回滚）
/~update check   只检查不下载（只读，零风险）
/~update force   强制全量对比（跳过 SHA 缓存）
/~upd            同上（短别名）
/~update test    公开连通性测试（无需权限）

移植自 KOOK 端的 modules/_auto_update（Phase 16 安全更新流水线），适配 QQ bot：
- 去掉 KOOK 的 notify_system / 卡片渲染（QQ bot 没有通知系统），结果直接文本返回
- 进度推送走 services.sender.send_by_chat_type
- 事件走 core.eventbus 的 EVENT_UPDATE_STARTED / EVENT_UPDATE_COMPLETED
- 不支持 KOOK 的 resend / approve / deny 子命令（依赖通知系统）
"""

from __future__ import annotations

import asyncio
import os

import httpx

from core.logger import get_logger
from modules._auto_update.engine import (GITHUB_API, GITHUB_BRANCH, GITHUB_REPO,
                                         _gh_headers)
from modules._auto_update.safe_update import safe_check_and_update

logger = get_logger("auto_update")


async def _restart_after(delay: float):
    """延迟后强退进程。依赖 systemd Restart=always 自动拉起新进程加载新代码。"""
    try:
        await asyncio.sleep(delay)
    except asyncio.CancelledError:
        logger.warning("自动重启任务被取消，请手动重启使更新生效")
        return
    except Exception:
        logger.exception("自动重启任务异常")
        return
    logger.info("更新完成，自动重启进程...")
    os._exit(0)


def _schedule_restart(delay: float = 5.0) -> bool:
    """安排延迟自动重启；失败返回 False（调用方回退为仅提示手动重启）。"""
    try:
        asyncio.create_task(_restart_after(delay))
        return True
    except Exception:
        logger.exception("安排自动重启失败")
        return False


def _parse_update_args(args):
    """解析 /~update 的 bash 风格参数。

    支持 -y/--yes（已废弃，保留兼容）、-q/--quiet（静默，只提示完成+重启），
    以及子命令 check / force / test。
    返回 (yes, quiet, sub)。
    """
    yes = quiet = False
    sub = None
    for a in (args or []):
        al = str(a).lower()
        if al in ("-y", "--yes", "-yes", "yes"):
            yes = True
        elif al in ("-q", "--quiet", "-quiet", "quiet"):
            quiet = True
        elif sub is None and al in ("check", "force", "test"):
            sub = al
    return yes, quiet, sub


async def cmd_update(args, user_id, group_id, sender_name, is_group, bot_qq):
    """/~update [check|force|test] [-q]"""
    yes, quiet, sub = _parse_update_args(args)

    # test 模式无需权限，任何人可用
    if sub == "test":
        return await _test_connectivity()

    try:
        from core.config import load_roles_config
        roles = load_roles_config()
        admin_qq = roles.get("admin_qq", 0)
        if admin_qq and user_id != admin_qq:
            return "权限不足喵~"
    except Exception:
        pass

    check_only = sub == "check"
    force = sub == "force"

    # Phase 16 安全流水线（含 Snapshot / Test / Health / Rollback）
    bus = None
    try:
        from core.eventbus import (EVENT_UPDATE_COMPLETED, EVENT_UPDATE_STARTED,
                                   get_event_bus)
        bus = get_event_bus()
        bus.emit(EVENT_UPDATE_STARTED, {
            "user_id": user_id, "check_only": check_only, "force": force,
            "yes": yes, "quiet": quiet,
        })
    except Exception:
        logger.debug("事件总线不可用，跳过", exc_info=True)

    # 进度回调：应用更新期间实时上报；-q 静默或只检查时不推送
    from services.sender import send_by_chat_type

    async def _progress(msg: str):
        try:
            await send_by_chat_type(f"[更新进度] {msg}", group_id, is_group,
                                    user_id=None if is_group else user_id)
        except Exception:
            logger.debug("更新进度推送失败", exc_info=True)

    result = await safe_check_and_update(
        check_only=check_only, force=force, require_approval=False,
        progress=None if (check_only or quiet) else _progress,
    )

    if bus is not None:
        try:
            bus.emit(EVENT_UPDATE_COMPLETED, {
                "user_id": user_id, "check_only": check_only, "force": force,
                "yes": yes, "quiet": quiet, "result": (result or "")[:200],
            })
        except Exception:
            logger.debug("事件总线不可用，跳过", exc_info=True)

    if check_only:
        return result

    if result and ("已更新" in result or "已安全更新" in result) and "个文件" in result:
        # 更新成功 → 延迟自动重启（上层先发出本条回复，再由 systemd 拉起新进程）
        if _schedule_restart(5.0):
            logger.info("更新成功，已安排 5 秒后自动重启")
            if quiet:
                return "更新完成，正在重启…"
            result += "\n\n更新成功，5 秒后自动重启，请稍候…"
        else:
            result += "\n\n建议重启 bot 使更新生效"
    return result


async def _test_connectivity() -> str:
    """公开测试：检查 GitHub 连通性 + 仓库可达性"""
    import time
    t0 = time.time()
    lines = ["【自动更新连通性测试】\n"]

    # 1. DNS / 直连
    try:
        async with httpx.AsyncClient(timeout=8, verify=False) as c:
            # ★ 必须带 token 头（v2.3.54）：匿名配额仅 60 次/小时，而"连通性测试"
            #   恰恰是用来排查限流的，不能自己反过来撞在限流上（否则配了 token
            #   也显示"触发限流"，误判成没配好）。
            r = await c.get(f"{GITHUB_API}/commits/{GITHUB_BRANCH}",
                            headers=_gh_headers())
        ms = int((time.time() - t0) * 1000)
        if r.status_code == 200:
            data = r.json()
            sha = data.get("sha", "?")[:7]
            msg = (data.get("commit", {}).get("message", "?").split("\n")[0])[:40]
            lines.append(f"GitHub 连通: OK ({ms}ms)")
            lines.append(f"仓库: {GITHUB_REPO}@{GITHUB_BRANCH}")
            lines.append(f"最新提交: {sha} — {msg}")
        elif r.status_code in (403, 429):
            lines.append(f"GitHub 连通: OK ({ms}ms) 但触发限流，稍后再试")
        else:
            lines.append(f"GitHub 响应异常: HTTP {r.status_code} ({ms}ms)")
    except Exception as e:
        ms = int((time.time() - t0) * 1000)
        lines.append(f"GitHub 不通 ({ms}ms): {str(e)[:100]}")

    # 2. 本地追踪状态
    try:
        from pathlib import Path
        state = Path("data/update_state.json")
        if state.exists():
            import json
            d = json.loads(state.read_text(encoding="utf-8"))
            n = len(d.get("files", {})) if isinstance(d, dict) else len(d)
            lines.append(f"本地追踪文件: {n} 个")
        else:
            lines.append("本地状态: 无缓存（首次更新将全量对比）")
    except Exception:
        lines.append("本地状态: 读取失败")

    lines.append("\n结论: 自动更新系统可正常工作" if "OK" in lines[1]
                 else "\n结论: 网络不通，检查服务器防火墙或代理")
    return "\n".join(lines)

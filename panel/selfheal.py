"""崩溃自愈：bot 起不来时自动回滚最后一次写操作

为什么这件事能做：面板和 bot 是**两个独立进程**（见 deploy/panel.service 的注释
—— 有意没有写 Requires/BindsTo）。bot 崩了面板还活着，所以面板能当那个
"在外面看着的人"。如果面板挂在 bot 的 event loop 里，这套机制根本不可能存在。

## 探测策略：按需探测，不是一直扫

不做无条件轮询。理由：
  - 没改过任何东西时，bot 崩了也回滚不了（栈是空的），盯着毫无意义
  - 常态下每 5 秒跑一次 systemctl 是纯浪费 —— 面板要跟着常驻几个月
  - 真正的风险窗口只有"刚写完文件、还没验证"那几分钟

所以分三档：
  IDLE      未布防 → **完全不探测**，只在被 arm() 或收到前端心跳时唤醒
  ARMED     布防后 N 分钟内 → 高频探测（5s），这是危险窗口
  DEGRADED  布防超时但仍未确认 → 低频探测（60s），万一你改完忘了确认

一旦 bot 恢复或自愈完成就回到 IDLE，重新静默。

这也意味着：如果你在服务器上用 vim 手改了文件然后把 bot 弄崩了，
自愈不会管 —— 这是**刻意的**。手改的文件不归面板管，回滚它等于抹掉你的操作。
手改崩了请 ssh 上去处理。

## ⚠️ 为什么不能只看 is-active（2026-09-13 实测踩坑）

`bot.service` 里是：
    Restart=always
    RestartSec=2

意思是 bot 崩了 systemd 会**每 2 秒拉起来一次，永远拉**。实测把 roles.toml
改坏之后：

    t= 0.3s  ActiveState=active      MainPID=1501070  NRestarts=0
    t= 0.8s  ActiveState=activating  MainPID=0        NRestarts=0
    t= 2.9s  ActiveState=active      MainPID=1501229  NRestarts=1
    t= 3.4s  ActiveState=activating  MainPID=0        NRestarts=1
    ...一路涨到 NRestarts=6

`is-active` **一直返回 active** —— 因为它永远抓在"刚被拉起来、还没崩"的那一瞬。
第一版探测就是看 `is-active`，结论是"bot 活得好好的"，自愈在最该出手的场景下
完全瞎了。这个坑不实测根本发现不了，因为逻辑上"看服务活没活"听起来天经地义。

正确信号是**重启计数器**：读 `NRestarts` + `MainPID`，稳定一小段时间不变才算真活。
所以探测改成分两步：
    1. 先读快照，跟上次比。MainPID 变了或 NRestarts 涨了 → 立刻判定不健康，
       不用等，这就是崩了
    2. 数值没变 → 再等 `STABLE_WINDOW` 秒复读一次，两次都一样才认定稳定
       （防止刚好卡在"起来了但马上要崩"的瞬间）

`is-active` 降级为辅助信息，只用来在告警里说明当前状态，不再作为判据。
"""

from __future__ import annotations

import asyncio
import subprocess
import time
from datetime import datetime
from typing import Any

from core.logger import get_logger

from panel import security

logger = get_logger("panel.selfheal")

# 布防后的高频探测间隔（危险窗口）
PROBE_INTERVAL = 5

# 布防多久后退化为低频探测（秒）。10 分钟够走完"改完→重启→看日志→确认"了
ARM_WINDOW = 600

# 退化后的低频探测间隔（秒）
DEGRADED_INTERVAL = 60

# 布防多久后彻底放弃（不再探测）。2 小时 —— 到这时候还没确认，
# 要么你忘了，要么已经在别处确认过了
ARM_EXPIRE = 7200

# 连续失败多少次判定为"改崩了"。3 次 ≈ 15 秒
FAIL_THRESHOLD = 3

# 复读间隔：MainPID/NRestarts 没变化时，等这么久再读一次确认真的稳住了
STABLE_WINDOW = 3

# 自愈后冷却多久才允许下一次自愈，防止连环倒栈
HEAL_COOLDOWN = 180

# 崩溃循环判定：窗口内重启次数超过这个值 → 认定在反复崩
# RestartSec=2，正常重启一次约 2.5s；30 秒内重启 ≥3 次说明一直在挂
CRASH_LOOP_WINDOW = 30
CRASH_LOOP_LIMIT = 3


class SelfHealState:
    """自愈运行态。面板进程内单例。

    探测是**按需唤醒**的：空闲时 loop 睡在 Event 上，
    只有 arm() 或前端心跳才会把它叫醒。没人改东西时，
    面板不会去碰 systemctl。
    """

    def __init__(self) -> None:
        self.enabled = True
        self.consecutive_fail = 0
        self.last_probe_ok: bool | None = None
        self.last_heal_at: float = 0.0
        self.events: list[dict] = []
        self._armed_op_ts: str | None = None
        self._armed_at: float = 0.0
        self._task: asyncio.Task | None = None
        # 按需唤醒：空闲时 loop 阻塞在这里，不轮询
        self._wake: asyncio.Event | None = None
        self.probe_count = 0        # 累计探测次数，便于观察开销
        # ── 上次读到的 unit 状态快照（判断"变没变"的基线）──
        self._last_snap: dict | None = None
        # 最近几次重启的时间戳，用来识别崩溃循环
        self._restart_marks: list[float] = []

    # ── 状态机 ──────────────────────────────────────────

    def phase(self) -> str:
        """当前处于哪个探测档位。

        idle      未布防，完全不探测
        armed     布防中且在高频窗口内，5s 一次
        degraded  布防超时未确认，60s 一次
        expired   布防太久，已放弃
        """
        if not self.enabled or self._armed_op_ts is None:
            return "idle"
        age = time.time() - self._armed_at
        if age > ARM_EXPIRE:
            return "expired"
        if age > ARM_WINDOW:
            return "degraded"
        return "armed"

    def _interval(self) -> int:
        p = self.phase()
        if p == "armed":
            return PROBE_INTERVAL
        if p == "degraded":
            return DEGRADED_INTERVAL
        return 0

    # ── 探测 ────────────────────────────────────────────

    @staticmethod
    def _unit_snapshot(service: str = "bot.service") -> dict:
        """一次 systemctl show 拿到判定所需全部字段。

        比连开好几个 systemctl 子进程快，也保证这些值来自同一瞬间
        （分开读的话可能读到"重启前"和"重启后"，自相矛盾）。
        """
        fields = ("ActiveState", "SubState", "MainPID", "NRestarts", "ExecMainStatus")
        try:
            r = subprocess.run(
                ["systemctl", "show", service,
                 "-p", ",".join(fields), "--no-pager"],
                capture_output=True, text=True, timeout=8,
            )
            out: dict[str, Any] = {}
            for line in (r.stdout or "").splitlines():
                if "=" in line:
                    k, _, v = line.partition("=")
                    out[k.strip()] = v.strip()
            try:
                out["_pid"] = int(out.get("MainPID") or 0)
            except ValueError:
                out["_pid"] = 0
            try:
                out["_n"] = int(out.get("NRestarts") or 0)
            except ValueError:
                out["_n"] = 0
            out["_at"] = time.time()
            return out
        except Exception as e:
            logger.debug("探测 %s 失败: %s", service, e)
            return {"_err": str(e), "_pid": 0, "_n": 0, "_at": time.time()}

    @staticmethod
    def _unit_failed(service: str = "bot.service") -> bool:
        """是否处于 failed 状态（辅助信息，Restart=always 时基本不会为真）"""
        try:
            r = subprocess.run(
                ["systemctl", "is-failed", service],
                capture_output=True, text=True, timeout=8,
            )
            return r.stdout.strip() == "failed"
        except Exception:
            return False

    def _judge(self, snap: dict) -> tuple[bool, str]:
        """根据快照判断 bot 是否健康。返回 (健康, 原因)。

        这是整个自愈最核心的一段 —— 判据错了后面全错。
        """
        if snap.get("_err"):
            # 探测本身失败（systemctl 不可用等）→ 不判定为崩，避免误回滚
            return True, f"探测失败已忽略: {snap['_err'][:80]}"

        state = snap.get("ActiveState", "")
        pid = snap.get("_pid", 0)
        n = snap.get("_n", 0)
        prev = self._last_snap

        if prev is None:
            self._last_snap = snap
            # 首次没有基线。active + 有 PID 就当作健康，避免面板刚启动就误判
            return (state == "active" and pid > 0), "首次探测，建立基线"

        # ★ 关键判据 1：重启计数涨了 → 刚刚崩过重启，直接判不健康
        if n > prev.get("_n", 0):
            self._last_snap = snap
            self._restart_marks.append(snap["_at"])
            return False, f"检测到重启（NRestarts {prev.get('_n')} → {n}）"

        # ★ 关键判据 2：主进程号变了 → systemd 重新拉起了进程
        if pid > 0 and prev.get("_pid", 0) > 0 and pid != prev.get("_pid"):
            self._last_snap = snap
            self._restart_marks.append(snap["_at"])
            return False, f"主进程已更换（PID {prev.get('_pid')} → {pid}）"

        # 数值没变。可能刚好卡在"起来了但马上要崩"的瞬间，
        # 所以隔一小段复读一次，两次都一样才认稳定。
        time.sleep(STABLE_WINDOW)
        again = self._unit_snapshot()
        self._last_snap = again
        if again.get("_n", 0) > n or again.get("_pid", 0) != pid:
            self._restart_marks.append(again["_at"])
            return False, (f"复读期间又重启（NRestarts {n} → {again.get('_n')}，"
                           f"PID {pid} → {again.get('_pid')}）")

        ok = again.get("ActiveState") == "active" and again.get("_pid", 0) > 0
        return ok, ("稳定运行中" if ok else f"状态异常: {again.get('ActiveState')}")

    def _in_crash_loop(self) -> bool:
        """最近 30 秒内重启 ≥3 次 → 崩溃循环。用于告警文案。"""
        now = time.time()
        recent = [t for t in self._restart_marks if now - t <= CRASH_LOOP_WINDOW]
        self._restart_marks = recent[-20:]
        return len(recent) >= CRASH_LOOP_LIMIT

    @staticmethod
    def _restart(service: str = "bot.service") -> tuple[bool, str]:
        try:
            r = subprocess.run(
                ["systemctl", "restart", service],
                capture_output=True, text=True, timeout=40,
            )
            ok = r.returncode == 0
            return ok, (r.stdout + r.stderr).strip()[:300]
        except Exception as e:
            return False, str(e)[:300]

    @staticmethod
    def _recent_journal(service: str = "bot.service", lines: int = 25) -> str:
        """抓 bot 最近日志 —— 自愈告警里带上崩溃原因，否则用户一脸懵"""
        try:
            r = subprocess.run(
                ["journalctl", "-u", service, "-n", str(lines), "--no-pager"],
                capture_output=True, text=True, timeout=10,
            )
            return (r.stdout or "")[-2000:]
        except Exception as e:
            return f"(取日志失败: {e})"

    # ── 操作栈联动 ──────────────────────────────────────

    def arm(self, op: dict | None = None) -> None:
        """布防：接下来这段时间盯紧 bot。

        两种情况会调用：
          1. 面板写了个关键文件（写操作自带）
          2. 前端心跳，表示"我刚改完，正在等 bot 重启"

        只有布防期间才探测 —— 空闲时面板不碰 systemctl。
        """
        # 优先记 id（唯一），退化到 ts（兼容旧数据）。见 security.pop_op_by_id
        _op = op or {}
        self._armed_op_ts = _op.get("id") or _op.get("ts") or "__manual__"
        self._armed_at = time.time()
        self.consecutive_fail = 0
        # 重置基线：布防前可能刚重启过（比如用户点了重启按钮），
        # 那个重启不算在"改崩了"头上
        self._last_snap = None
        self._restart_marks.clear()
        self._wake_up()
        logger.info("自愈布防 | 目标=%s | 高频窗口 %ds",
                    (op or {}).get("target", "(手动)"), ARM_WINDOW)

    def keep_alive(self) -> None:
        """前端心跳：用户还在页面上盯着，延长高频窗口。

        为什么需要：你改完配置、点重启、盯着日志看 bot 有没有起来 ——
        这个过程可能超过 10 分钟。心跳能保证这段时间探测不掉档。
        """
        if self._armed_op_ts is None:
            return
        self._armed_at = time.time()      # 刷新窗口起点
        self._wake_up()

    def disarm(self) -> None:
        """解除布防（确认改动静好），回到 idle 不再探测"""
        if self._armed_op_ts is not None:
            logger.info("自愈解除布防，回到静默")
        self._armed_op_ts = None
        self._armed_at = 0.0
        self.consecutive_fail = 0

    def _wake_up(self) -> None:
        try:
            if self._wake is not None:
                self._wake.set()
        except Exception:
            pass

    # ── 主循环（事件驱动） ──────────────────────────────

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._wake = asyncio.Event()
            self._task = asyncio.create_task(self._loop())
            logger.info(
                "崩溃自愈已就绪 | 按需探测：布防后 %ds 一次，"
                "超 %d 分钟降为 %ds 一次 | 空闲时不探测 | "
                "判据=NRestarts/MainPID 变化（不依赖 is-active，"
                "因为 Restart=always 下它恒为 active）",
                PROBE_INTERVAL, ARM_WINDOW // 60, DEGRADED_INTERVAL,
            )

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        """事件驱动主循环。

        空闲时阻塞在 Event 上（不消耗 CPU、不跑 systemctl）；
        布防期间按间隔醒来探测；布防过期后回到阻塞。
        """
        while True:
            try:
                interval = self._interval()

                if interval == 0:
                    # 空闲：等到被唤醒为止
                    if self._wake is not None:
                        self._wake.clear()
                        # 等唤醒，同时兜一个较长的超时，
                        # 防止 Event 丢失导致永久睡死
                        try:
                            await asyncio.wait_for(self._wake.wait(), timeout=300)
                        except asyncio.TimeoutError:
                            pass
                    else:
                        await asyncio.sleep(60)
                    continue

                # 布防中：等一小段，但随时可被心跳打断
                if self._wake is not None:
                    self._wake.clear()
                    try:
                        await asyncio.wait_for(self._wake.wait(),
                                               timeout=interval)
                        continue        # 被唤醒 → 立刻重新算间隔
                    except asyncio.TimeoutError:
                        pass
                else:
                    await asyncio.sleep(interval)

                # 到期了，跑一次探测（放线程池，别阻塞 event loop）
                await asyncio.to_thread(self._tick)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                # 自愈自己不能把面板搞崩
                logger.warning("自愈 tick 异常（已忽略）: %s", e)
                await asyncio.sleep(5)

    def _tick(self) -> None:
        self.probe_count += 1
        snap = self._unit_snapshot()
        healthy, reason = self._judge(snap)
        self.last_probe_ok = healthy

        if healthy:
            if self.consecutive_fail:
                logger.info("bot 已恢复，失败计数清零（此前 %d 次）", self.consecutive_fail)
            self.consecutive_fail = 0
            # bot 活得好好的 → 解除布防，回到静默
            if self._armed_op_ts is not None:
                self.disarm()
            return

        # bot 不健康 —— 只有布防状态才计数
        if not self.enabled or self._armed_op_ts is None:
            return

        self.consecutive_fail += 1
        loop_hint = "（疑似崩溃循环）" if self._in_crash_loop() else ""
        logger.warning("bot 探测失败 %d/%d 次 | 档位=%s | %s%s",
                       self.consecutive_fail, FAIL_THRESHOLD, self.phase(),
                       reason, loop_hint)

        if self.consecutive_fail < FAIL_THRESHOLD:
            return
        if self._is_cooling():
            return

        self._heal(trigger=reason)

    def _is_cooling(self) -> bool:
        gap = time.time() - self.last_heal_at
        if self.last_heal_at and gap < HEAL_COOLDOWN:
            logger.warning("自愈冷却中，还剩 %ds", int(HEAL_COOLDOWN - gap))
            return True
        return False

    # ── 自愈动作 ────────────────────────────────────────

    def _heal(self, trigger: str = "") -> None:
        self.last_heal_at = time.time()
        loop_hint = "\n\n⚠️ 检测到**崩溃循环**：bot 反复启动失败，" \
                    "systemd 每 2 秒重试一次（Restart=always）。" \
                    "服务状态一直显示 active，但实际根本没跑起来。" \
                    if self._in_crash_loop() else ""

        rec = security.peek_ops(limit=1)
        if not rec:
            self._record(
                ok=False,
                action="无操作可回滚",
                detail=("bot 起不来，但操作栈是空的 —— 崩溃原因不在面板改动。"
                        "请查日志手动处理。"
                        + (f"\n触发原因：{trigger}" if trigger else "")
                        + loop_hint),
                journal=self._recent_journal(),
            )
            self._armed_op_ts = None
            return

        # ★ 只回滚"布防时记住的那条操作"，不是无脑弹栈顶。
        # 原因见 security.pop_op_by_id 的注释：栈顶未必是导致这次崩溃的写入。
        armed = self._armed_op_ts
        popped = None
        if armed and armed != "__manual__":
            popped = security.pop_op_by_id(armed)
        if popped is None:
            # 手动布防（没有具体操作）或记录已被清掉 → 退化为弹栈顶
            target_hint = security.peek_ops(limit=1)
            if target_hint:
                logger.warning("未找到布防时的那条操作，退化为回滚栈顶：%s",
                               target_hint[0].get("target"))
            popped = security.pop_op()
        if not popped:
            return
        ok, msg = security.rollback_op(popped)
        logger.warning("自愈回滚 [%s] %s → %s", popped.get("kind"),
                       popped.get("target"), msg)

        if not ok:
            self._record(
                ok=False,
                action=f"回滚失败：{popped.get('target')}",
                detail=msg + (f"\n触发原因：{trigger}" if trigger else "") + loop_hint,
                journal=self._recent_journal(),
            )
            self._armed_op_ts = None
            return

        # 回滚完重启，验证是否真的救回来
        r_ok, r_out = self._restart()

        self._record(
            ok=r_ok,
            action=f"已回滚 {popped.get('kind')}：{popped.get('target')}",
            detail=("bot 已重启，自愈完成。"
                    if r_ok else f"重启失败：{r_out}")
                   + (f"\n触发原因：{trigger}" if trigger else "")
                   + loop_hint,
            journal=self._recent_journal(),
        )

        # 不管成功失败都解除布防：成功则该改动已被撤销，
        # 失败则说明问题更深，继续自动倒栈会把好改动毁掉
        self._armed_op_ts = None
        self.consecutive_fail = 0
        self._last_snap = None
        self._restart_marks.clear()

    def _record(self, *, ok: bool, action: str, detail: str,
                journal: str = "") -> None:
        ev = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "ok": ok,
            "action": action,
            "detail": detail,
            "journal": journal,
        }
        self.events.append(ev)
        del self.events[:-20]          # 只留最近 20 条
        logger.warning("自愈事件 | ok=%s | %s | %s", ok, action, detail)

    # ── 给接口用的视图 ──────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        p = self.phase()
        age = int(time.time() - self._armed_at) if self._armed_at else 0
        snap = self._last_snap or {}
        return {
            "enabled": self.enabled,
            "phase": p,
            "armed": self._armed_op_ts is not None,
            "armed_op_ts": self._armed_op_ts,
            "armed_age": age,
            "consecutive_fail": self.consecutive_fail,
            "fail_threshold": FAIL_THRESHOLD,
            "probe_interval": self._interval(),
            "probe_count": self.probe_count,
            "arm_window": ARM_WINDOW,
            "arm_expire": ARM_EXPIRE,
            "degraded_interval": DEGRADED_INTERVAL,
            "heal_cooldown": HEAL_COOLDOWN,
            "stable_window": STABLE_WINDOW,
            "crash_loop": self._in_crash_loop(),
            "bot_alive": self.last_probe_ok,
            # ── 探测看到的原始信号，排查时用 ──
            "unit_active_state": snap.get("ActiveState"),
            "unit_sub_state": snap.get("SubState"),
            "unit_main_pid": snap.get("_pid"),
            "unit_n_restarts": snap.get("_n"),
            "unit_failed": self._unit_failed(),
            "events": list(reversed(self.events)),
        }


_state = SelfHealState()


def get_state() -> SelfHealState:
    return _state

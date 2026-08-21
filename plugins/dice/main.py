"""示例插件：骰子。

演示能力：
- register_command：注册 /~dice <面数> 指令（自动挂进 COMMAND_MAP）
- register_tool：注册 roll_dice 常驻工具（LLM 普通聊天也能调用）
- ctx.economy：掷骰奖励积分（演示经济系统联动）
- ctx.config：读取 manifest.config
- 生命周期钩子：on_load / on_enable / on_disable / on_unload
"""
from __future__ import annotations

import random
import time

from core.plugin.api import PluginContext


class Plugin:
    def __init__(self, ctx: PluginContext):
        self.ctx = ctx
        self._rolls = 0

    # ── 生命周期 ──────────────────────────────────────
    async def on_load(self):
        self.ctx.logger.info("[dice] 插件加载中...")
        self.ctx.capability.register_command(
            "dice", "掷骰子，格式: /~dice [面数]，默认 6 面，掷骰奖励 1 积分",
            self._cmd_dice,
        )
        self.ctx.capability.register_tool(
            name="roll_dice",
            description="掷骰子。用户说掷骰子/扔骰子/摇骰子时调用，返回点数。",
            schema={
                "type": "function",
                "function": {
                    "name": "roll_dice",
                    "description": "掷骰子。用户说掷骰子/扔骰子/摇骰子时调用，返回点数。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "sides": {"type": "integer", "description": "骰子面数，默认 6"},
                        },
                        "required": [],
                    },
                },
            },
            handler=self._tool_roll,
            always_on=True,
        )
        self.ctx.logger.info("[dice] 指令 /~dice 与工具 roll_dice 已注册")

    async def on_enable(self):
        self.ctx.logger.info("[dice] 插件启用")

    async def on_disable(self):
        self.ctx.logger.info("[dice] 插件禁用")

    async def on_unload(self):
        self.ctx.logger.info("[dice] 插件卸载，共掷骰 %d 次", self._rolls)

    # ── 指令 handler（qqbot 指令签名）─────────────────
    async def _cmd_dice(self, args, user_id, group_id, sender_name, is_group, bot_qq):
        max_sides = int(self.ctx.config("max_sides", 100))
        sides = 6
        if args:
            try:
                sides = int(args[0])
            except (TypeError, ValueError):
                pass
        sides = max(1, min(sides, max_sides))
        points = await self._roll(user_id, sides, sender_name)
        return f"🎲 {sender_name} 掷出了 {points} 点（{sides}面骰），获得 1 积分！"

    # ── 工具 handler（FC 签名）─────────────────────────
    async def _tool_roll(self, arguments, user_id, group_id, sender_name, is_group, bot_qq):
        max_sides = int(self.ctx.config("max_sides", 100))
        try:
            sides = int((arguments or {}).get("sides", 6))
        except (TypeError, ValueError):
            sides = 6
        sides = max(1, min(sides, max_sides))
        points = await self._roll(user_id, sides, sender_name)
        return f"{sender_name} 掷出了 {points} 点（{sides}面骰），获得 1 积分"

    # ── 公共逻辑 ──────────────────────────────────────
    async def _roll(self, user_id, sides: int, sender_name: str) -> int:
        points = random.randint(1, sides)
        self._rolls += 1
        try:
            # 演示经济系统联动：掷骰 +1 积分（优雅降级，经济模块缺失不报错）
            reward = int(self.ctx.config("point_reward", 1))
            self.ctx.economy.add_points(user_id, reward)
        except Exception as e:
            self.ctx.logger.warning("[dice] 积分奖励失败(忽略): %s", e)
        return points

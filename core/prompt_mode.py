"""渠道级能力策略（唯一判定处）。

★ 2026-11-09：官方渠道（织梦）转为公共服务后，定位收窄为「聊天 + 搜索」：

  - 不给模型任何工具/指令能力（FC 工具集为空，指令表不注入提示词）
  - skills 里与指令、工具、agent 相关的章节跳过不加载
  - 允许 Markdown（官方 msg_type=2 渲染，NapCat 端不渲染 —— 这是分渠道的根源）
  - 长期记忆独立到 data/official_mem/，与 NapCat 侧的 data/memory_*.md 完全隔离

NapCat 渠道（BOT_CHANNEL 未设或为 napcat）保持原样，不受本模块影响。

⚠️ 本模块只读环境变量，不依赖 core.config，避免循环导入；也不要在这里 import
   services/llm（llm 会 import 这里）。
"""

from __future__ import annotations

import os

CHANNEL: str = (os.environ.get("BOT_CHANNEL", "") or "").strip().lower()

# 官方「织梦」渠道
IS_OFFICIAL: bool = CHANNEL == "official"

# 是否允许模型使用工具（FC）。官方渠道：不允许。
ALLOW_TOOLS: bool = not IS_OFFICIAL

# 是否允许 Markdown 输出。官方渠道：允许（平台侧渲染）；NapCat：纯文本。
ALLOW_MARKDOWN: bool = IS_OFFICIAL

# 长期记忆子目录（相对 data/）。空串 = 直接用 data/
MEMORY_SUBDIR: str = "official_mem" if IS_OFFICIAL else ""

# 官方渠道独立的技能/人设目录（data/skills_official/），与 NapCat 的 data/skills/ 隔离
SKILLS_SUBDIR: str = "skills_official" if IS_OFFICIAL else ""

# 官方渠道不加载的技能章节（写死在 skills/*.md 里的 ## 名称）
#   command_tools     : 告诉模型「有哪些指令可以调」
#   command_table     : 指令清单（按需注入）
#   reply_reminder    : 指令穿插顺序规则（{"cmd": ...} 用法）
#   reply_reminder_core 保留 —— 它只管语气/长度，不含指令
DISABLED_SKILL_SECTIONS: frozenset[str] = (
    frozenset({"command_tools", "command_table", "reply_reminder"})
    if IS_OFFICIAL else frozenset()
)



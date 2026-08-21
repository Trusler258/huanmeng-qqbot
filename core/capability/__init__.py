"""
Capability System（移植自 huanmeng-kook-bot core/capability/__init__.py）

统一 Skill / Command / Tool / Plugin 为 Capability 抽象：
- metadata：id / name / description / tags / category / permissions / runtime
- registry：CapabilityRegistry（发现 + 登记 + 查询）
- router：CapabilityRouter（query + intent → 相关能力，metadata 级）
- loader：CapabilityLoader（确认后按需加载 Tool Schema / 指令用法）
"""
from core.capability.metadata import (
    Capability,
    CATEGORY_TOOL, CATEGORY_COMMAND, CATEGORY_SKILL, CATEGORY_PLUGIN,
    RUNTIME_FC, RUNTIME_COMMAND, RUNTIME_SKILL, RUNTIME_PYTHON, RUNTIME_LUA,
)
from core.capability.registry import get_capability_registry
from core.capability.router import get_capability_router
from core.capability.loader import get_capability_loader, load_fc_schemas

__all__ = [
    "Capability",
    "CATEGORY_TOOL", "CATEGORY_COMMAND", "CATEGORY_SKILL", "CATEGORY_PLUGIN",
    "RUNTIME_FC", "RUNTIME_COMMAND", "RUNTIME_SKILL", "RUNTIME_PYTHON", "RUNTIME_LUA",
    "get_capability_registry", "get_capability_router", "get_capability_loader",
    "load_fc_schemas",
]

"""
统一配置管理器
- 一次性加载所有 .toml / .env 配置
- 提供 BotConfig @dataclass 封装所有运行时参数
- 支持 reload（重新加载所有配置文件）
- 所有模块通过 BotConfig 单例访问配置，避免重复读盘
"""

from __future__ import annotations
from core.arch_loader import get_architecture_context

import os
import toml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(path): pass  # noqa: E701,E301


# ── 项目根目录 ──────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_DIR = _PROJECT_ROOT / "config"


@dataclass
class ModelConfig:
    """单个模型配置"""
    name: str = ""
    provider: str = ""
    url: str = ""
    key: str = ""
    maxtoken: int = 300
    switch: bool = False


@dataclass
class BotConfig:
    """全量配置 — 所有运行时参数集中在此"""
    
    # ── 连接信息 ──
    host: str = "localhost"
    port: int = 8099
    
    # ── 机器人身份 ──
    bot_name: str = "幻梦"
    bot_qq: int = 0
    reply_interest: int = 10         # 回复兴趣阈值
    context_length: int = 20         # 消息上下文最大条数
    enable_private: bool = False     # 允许私聊
    debug_mode: bool = False         # 调试开关
    
    # ── 角色权限 ──
    admin_qq: int = 0
    friend_qqs: list[int] = field(default_factory=list)
    qq_name_map: dict[str, str] = field(default_factory=dict)
    op_qqs: list[int] = field(default_factory=list)           # OP（次级管理员）QQ 列表
    group_owners: dict[int, list[int]] = field(default_factory=dict) # 群主权限指派: {group_id: [op_qq, ...]}
    
    # ── 模型配置 ──
    reply_model: ModelConfig = field(default_factory=ModelConfig)
    judge_model: ModelConfig = field(default_factory=ModelConfig)
    cheap_model: ModelConfig = field(default_factory=ModelConfig)
    image_model: ModelConfig = field(default_factory=ModelConfig)
    
    # ── 人设 ──
    personality_core: str = ""
    personality_side: str = ""
    identity: str = ""
    system_prompt: str = ""           # 组装后的完整提示词
    
    # ── 群白名单 ──
    group_list: list[int] = field(default_factory=list)
    # ── 私聊白名单 ──
    private_whitelist: list[int] = field(default_factory=list)
    # ── 群自定义回复策略 ──
    # {group_id: {"reply_threshold": 8, "at_only": False}}
    group_settings: dict[int, dict] = field(default_factory=dict)
    
    # ── 文本替换 ──
    replace_words: list[str] = field(default_factory=list)
    be_replaced_words: list[str] = field(default_factory=list)
    
    # ── 判断关键词 ──
    search_trigger_words: list[str] = field(default_factory=list)
    realtime_words: list[str] = field(default_factory=list)

    # ── 反刷屏禁言 ──
    spam_threshold: int = 8           # 触发禁言的重复@次数
    mute_duration: int = 1800         # 禁言时长（秒），0=仅警告
    
    # ── 构建完整系统提示词 ──
    def build_system_prompt(self) -> str:
        self.system_prompt = (
            f"# 核心人格\n{self.personality_core}\n"
            f"---\n"
            f"# 侧面人格\n{self.personality_side}\n"
            f"---\n"
            f"# 固定身份\n{self.identity}\n"
            f"---\n"
            f"{self._build_self_awareness()}"
        )
        return self.system_prompt

    def _build_self_awareness(self) -> str:
        """构建自我认知：从 main_skill.md 加载模板并填入动态信息"""
        import re

        # 读取模板
        skill_path = Path(__file__).resolve().parent.parent / "data" / "main_skill.md"
        template = ""
        if skill_path.exists():
            try:
                text = skill_path.read_text(encoding="utf-8")
                # 提取 ## self_awareness 段
                m = re.search(r'## self_awareness\n(.*?)(?=\n## |\Z)', text, re.DOTALL)
                if m:
                    template = m.group(1).strip()
            except Exception:
                pass

        # 从更新日志提取最新版本
        version = "v0.9.8 Pro"
        changelog_lines = ""
        log_path = Path(__file__).resolve().parent.parent / "data" / "update_log.md"
        if log_path.exists():
            try:
                ltext = log_path.read_text(encoding="utf-8")
                vm = re.search(r"## (v[\d.]+ .+?)(?=\n## |\Z)", ltext, re.DOTALL)
                if vm:
                    version = vm.group(1).strip().split("\n")[0].strip("#- ")
                    lines = vm.group(1).strip().split("\n")
                    lines = [l.strip("- # ").strip() for l in lines if l.strip() and not l.startswith("|") and not l.startswith("###")]
                    lines = [l for l in lines[:6] if l]
                    changelog_lines = "\n".join(f"- {l}" for l in lines)
            except Exception:
                pass

        # 架构
        arch_lines = ""
        arch_path = Path(__file__).resolve().parent.parent / "data" / "architecture.mermaid"
        if arch_path.exists():
            try:
                arch_text = arch_path.read_text(encoding="utf-8")
                clean = re.sub(r'<br/>', ' · ', arch_text)
                clean = re.sub(r'[\\"]', '', clean)
                nodes = re.findall(r'\[([^\]]+)\]', clean)
                arch_lines = "\n".join(f"- {n.strip()}" for n in nodes)
            except Exception:
                pass

        model_info = "DeepSeek(回复) + Zhipu(视觉) + DuckDuckGo(搜索)"

        if template:
            # 使用模板填入变量
            result = template.replace("${bot_name}", self.bot_name)
            result = result.replace("${version}", version)
            result = result.replace("${changelog}", changelog_lines)
            result = result.replace("${architecture}", arch_lines)
            result = result.replace("${reply_interest}", str(self.reply_interest))
            result = result.replace("${context_length}", str(self.context_length))
            result = result.replace("${model_info}", model_info)
            result = result.replace("${admin_qq}", str(self.admin_qq))
            result = result.replace("${host}", self.host)
            result = result.replace("${port}", str(self.port))
            return result

        # 回退：硬编码模板（模板文件缺失时）
        parts = []
        parts.append(f"# 自我认知\n你是{self.bot_name} {version}。")
        if changelog_lines:
            parts.append(f"最新更新：\n{changelog_lines}")
        if arch_lines:
            parts.append(f"\n完整架构:\n{arch_lines}")
        parts.append(f"\n当前设置: 兴趣度阈值={self.reply_interest}, 上下文长度={self.context_length}条")
        parts.append(f"已接入: {model_info}")
        parts.append(f"主人 QQ: {self.admin_qq}, 运行: {self.host}:{self.port}")
        return "\n".join(parts)
    
    def get_user_tag(self, user_id: int, group_id: int = 0) -> str:
        """根据 QQ 号返回角色标签（支持分群 OP 判断）"""
        if user_id == self.admin_qq:
            return "admin"
        # 分群 OP：在指派的群内标签为 op，不混用 admin
        if group_id and user_id in self.group_owners.get(group_id, []):
            return "op"
        if user_id in self.friend_qqs:
            return "friend"
        return "群友"

    def is_op(self, user_id: int) -> bool:
        """是否为 OP（次级管理员）"""
        return user_id in self.op_qqs

    def is_admin(self, user_id: int, group_id: int = 0) -> bool:
        """检查用户是否有 admin 权限（主人 + 分群 OP 指派）"""
        if user_id == self.admin_qq:
            return True
        if group_id and user_id in self.group_owners.get(group_id, []):
            return True
        return False

    def get_group_owner(self, group_id: int) -> list[int]:
        """获取群的 OP 指派列表"""
        return self.group_owners.get(group_id, [])

    def get_display_name(self, user_id: int, group_id: int = 0) -> str:
        """获取用户显示名：分群昵称 > 全局昵称 > QQ 号"""
        uid = str(user_id)
        # 优先查分群昵称
        if group_id and group_id in self.group_settings:
            gs_nicks = self.group_settings[group_id].get("nicknames", {})
            if uid in gs_nicks:
                return gs_nicks[uid]
        # fallback 全局昵称
        return self.qq_name_map.get(uid, uid)


# ── 单例实例 ────────────────────────────────────────────────
_instance: Optional[BotConfig] = None


def _load_env_config() -> dict[str, dict]:
    """从 .env 加载 API 密钥和 URL"""
    env_path = _CONFIG_DIR / ".env"
    if not env_path.exists():
        return {}
    load_dotenv(env_path)
    
    providers: dict[str, dict] = {}
    for key, value in os.environ.items():
        if key.endswith("_URL"):
            provider = key[:-4].lower()
            if provider not in providers:
                providers[provider] = {}
            providers[provider]["url"] = value
        elif key.endswith("_KEY"):
            provider = key[:-4].lower()
            if provider not in providers:
                providers[provider] = {}
            providers[provider]["key"] = value
    return providers


def _make_model(bot_model_cfg: dict, env_providers: dict[str, dict]) -> ModelConfig:
    """从 bot_config.toml 中的模型段 + env 配置构建 ModelConfig"""
    mc = ModelConfig()
    mc.name = bot_model_cfg.get("name", "")
    mc.provider = bot_model_cfg.get("provider", "")
    mc.maxtoken = bot_model_cfg.get("maxtoken", 300)
    mc.switch = bot_model_cfg.get("开关", False)
    
    env_cfg = env_providers.get(mc.provider.lower(), {})
    mc.url = env_cfg.get("url", "")
    mc.key = env_cfg.get("key", "")
    
    return mc


def load_bot_config() -> BotConfig:
    """
    加载所有配置文件并构建 BotConfig 实例。
    这是唯一需要调用的加载函数。
    """
    global _instance
    
    cfg_path = _CONFIG_DIR / "bot_config.toml"
    adapter_path = _CONFIG_DIR / "adapter_config.toml"
    roles_path = _CONFIG_DIR / "roles.toml"
    
    # ── bot_config.toml ──
    if not cfg_path.exists():
        raise FileNotFoundError(f"主配置文件不存在: {cfg_path}")
    with open(cfg_path, "r", encoding="utf-8") as f:
        bot_toml = toml.load(f)
    
    bot_section = bot_toml.get("bot", {})
    judge_section = bot_toml.get("judge", {})
    personality = bot_toml.get("personality", {})
    models_section = bot_toml.get("model", {})
    
    # ── adapter_config.toml ──
    adapter = {"host": "localhost", "port": 8099}
    if adapter_path.exists():
        with open(adapter_path, "r", encoding="utf-8") as f:
            adapter_toml = toml.load(f)
        napcat = adapter_toml.get("napcat_server", {})
        adapter["host"] = napcat.get("host", adapter["host"])
        adapter["port"] = napcat.get("port", adapter["port"])
    
    # ── roles.toml ──
    admin_qq = 0
    friend_qqs: list[int] = []
    qq_name_map: dict[str, str] = {}
    if roles_path.exists():
        with open(roles_path, "r", encoding="utf-8") as f:
            roles_toml = toml.load(f)
        admin_qq = roles_toml.get("admin_qq", 0)
        friend_qqs = roles_toml.get("friend_qqs", [])
        qq_name_map = roles_toml.get("qq_name_map", {})
        # ★ OP 次级管理员
        op_qqs_raw = roles_toml.get("op_qqs", [])
        op_qqs = [int(q) for q in op_qqs_raw]
        # ★ 群主权限指派: {group_id: [op_qq, ...]}
        group_owners_raw = roles_toml.get("group_owners", {})
        group_owners = {}
        for k, v in group_owners_raw.items():
            if isinstance(v, list):
                group_owners[int(k)] = [int(q) for q in v]
            else:
                group_owners[int(k)] = [int(v)]  # 兼容旧单个格式
    
    # ── .env ──
    env_providers = _load_env_config()
    
    # ── 构建 BotConfig ──
    # ── 私聊白名单 ──
    private_whitelist_raw = adapter_toml.get("chat", {}).get("private_whitelist", []) if adapter_path.exists() else []
    private_whitelist = [int(q) for q in private_whitelist_raw]

    # ── 群自定义设置 ──
    group_settings_raw = adapter_toml.get("group_settings", {}) if adapter_path.exists() else {}
    group_settings: dict[int, dict] = {}
    for k, v in group_settings_raw.items():
        gid = int(k)
        group_settings[gid] = {
            "reply_threshold": v.get("reply_threshold", None),
            "at_only": v.get("at_only", False),
            "welcome_msg": v.get("welcome_msg", "").strip() if isinstance(v.get("welcome_msg"), str) else "",
            "cmd_whitelist": v.get("cmd_whitelist", None),
            # ★ 分群昵称：{"3483585417": "trusler", ...}
            "nicknames": {str(qq): name for qq, name in v.get("nicknames", {}).items()},
        }

    instance = BotConfig(
        host=adapter["host"],
        port=adapter["port"],
        bot_name=bot_section.get("bot的名字", "幻梦"),
        bot_qq=int(bot_section.get("bot的qq号", 0)),
        reply_interest=bot_section.get("回复兴趣", 10),
        context_length=bot_section.get("消息记录长度", 20),
        enable_private=bot_section.get("enable_private_chat", False),
        debug_mode=bool(bot_section.get("调试模式", False)),
        admin_qq=admin_qq,
        friend_qqs=[int(q) for q in friend_qqs],
        qq_name_map={str(k): v for k, v in qq_name_map.items()},
        op_qqs=op_qqs,
        group_owners=group_owners,
        group_list=adapter_toml.get("chat", {}).get("group_list", []) if adapter_path.exists() else [],
        private_whitelist=private_whitelist,
        group_settings=group_settings,
        replace_words=bot_section.get("替换词", []),
        be_replaced_words=bot_section.get("被替换词", []),
        search_trigger_words=judge_section.get("search_trigger_words", []),
        realtime_words=judge_section.get("realtime_words", []),
        spam_threshold=bot_section.get("spam_threshold", 8),
        mute_duration=bot_section.get("mute_duration", 1800),
        personality_core=personality.get("personality_core", ""),
        personality_side=personality.get("personality_side", ""),
        identity=personality.get("identity", ""),
    )
    
    # ── 模型配置 ──
    instance.reply_model = _make_model(models_section.get("replyer_1", {}), env_providers)
    instance.judge_model = _make_model(models_section.get("utils_small", {}), env_providers)
    instance.cheap_model = _make_model(models_section.get("judge_cheap", {}), env_providers)
    instance.image_model = _make_model(models_section.get("picture", {}), env_providers)
    
    # 组装提示词
    instance.build_system_prompt()
    
    _instance = instance
    return instance


def get_config() -> BotConfig:
    """获取当前 BotConfig 实例。未加载时自动加载。"""
    global _instance
    if _instance is None:
        return load_bot_config()
    return _instance


def reload_config() -> BotConfig:
    """重新加载所有配置文件，返回新实例。"""
    from core.logger import info, set_debug_mode
    new_cfg = load_bot_config()
    set_debug_mode(new_cfg.debug_mode)
    info("配置已重新加载 | bot=%s | host=%s:%d | debug=%s",
         new_cfg.bot_name, new_cfg.host, new_cfg.port, new_cfg.debug_mode)
    return new_cfg


def load_roles_config() -> dict:
    """加载 roles.toml（供指令系统使用）"""
    roles_path = _CONFIG_DIR / "roles.toml"
    if not roles_path.exists():
        return {}
    with open(roles_path, "r", encoding="utf-8") as f:
        return toml.load(f)


def save_roles_config(config_dict: dict):
    """保存 roles.toml（供指令系统使用）"""
    roles_path = _CONFIG_DIR / "roles.toml"
    content = toml.dumps(config_dict)
    with open(roles_path, "w", encoding="utf-8") as f:
        f.write(content)


def set_debug_mode(enabled: bool):
    """动态切换调试模式（外部调用，转发给 logger）"""
    from core.logger import set_debug_mode as _set_debug_mode
    _set_debug_mode(enabled)


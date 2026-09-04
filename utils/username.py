"""
用户名获取模块（原 data/read_the_username.py）
- 从 roles.toml 预设映射 → 本地缓存文件 → 在线 WS 查询 三级查找
- 替换消息中的 @QQ 为 @昵称
"""

from __future__ import annotations

import json
import os
import re
import websockets
from pathlib import Path
from typing import Dict, Optional, Union

from core.logger import get_logger
from core.config import get_config

logger = get_logger("username")

# ── 映射文件路径 ────────────────────────────────────────────
MAPPING_FILENAME = "Group_members_name.txt"
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _mapping_path() -> Path:
    return _DATA_DIR / MAPPING_FILENAME


def _load_mapping(file_path: Optional[Path] = None) -> Dict[str, str]:
    """从本地 txt 文件加载 QQ→昵称映射"""
    path = file_path or _mapping_path()
    mapping: Dict[str, str] = {}
    if not path.exists():
        return mapping
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                sep_index = line.find(":")
                if sep_index == -1:
                    sep_index = line.find("：")
                    if sep_index == -1:
                        continue
                left = line[:sep_index].strip()
                right = line[sep_index + 1:].strip()
                m = re.search(r"\d+", left)
                if not m:
                    continue
                qq = m.group(0)
                if qq and right:
                    mapping[qq] = right
    except Exception as e:
        logger.warning("读取映射文件失败 [%s]: %s", path, e)
    return mapping


def _append_mapping(qq_str: str, nick: str, file_path: Optional[Path] = None) -> None:
    """追加一条映射到本地文件"""
    path = file_path or _mapping_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{qq_str} : {nick}\n")
        logger.debug("已记录用户 %s → %s 到本地缓存", qq_str, nick)
    except Exception as e:
        logger.warning("写入映射文件失败 [%s]: %s", path, e)


async def get_or_resolve_username(
    qq: Union[int, str],
    host: str,
    port: int,
    group_id: Optional[int] = None,
) -> str:
    """
    三级查找用户名：
    1. 分群昵称（data/group_nicknames.json，仅群聊）
    2. roles.toml 全局预设
    3. 本地缓存文件
    4. 在线 WS 查询（NapCat API）

    Args:
        qq: QQ 号码
        host: NapCat WebSocket 地址
        port: NapCat WebSocket 端口
        group_id: 所在群号（用于群成员查询）

    Returns:
        用户昵称，查询失败则返回 QQ 号字符串
    """
    qq_str = str(qq)

    # 第一优先：分群昵称（nickname_sync 同步的群名片）
    cfg = get_config()
    if group_id:
        per = cfg.group_nicknames.get(str(group_id), {})
        nick = per.get(qq_str)
        if nick:
            logger.debug("用户 %s 映射到分群名称: %s (群 %s)", qq_str, nick, group_id)
            return nick

    # 第二优先：roles.toml 全局预设
    preset_name = cfg.qq_name_map.get(qq_str)
    if preset_name:
        logger.debug("用户 %s 映射到预设名称: %s (来源: roles.toml)", qq_str, preset_name)
        return preset_name
    
    # 第三优先：本地文件缓存
    local_mapping = _load_mapping()
    cached_nick = local_mapping.get(qq_str)
    if cached_nick:
        logger.debug("用户 %s 映射到缓存名称: %s (来源: 本地文件)", qq_str, cached_nick)
        return cached_nick
    
    # 第四优先：在线 WS 查询
    logger.info("开始在线查询用户 %s 的昵称 (group=%s)...", qq_str, group_id)
    try:
        nick = await _query_username_ws(int(qq_str), host, port, group_id=group_id)
    except Exception as e:
        logger.warning("通过 WS 查询昵称失败 [%s]: %s", qq_str, e)
        nick = None
    
    if nick:
        _append_mapping(qq_str, nick)
        return nick
    
    logger.debug("无法获取用户 %s 的昵称，使用 QQ 号作为名称", qq_str)
    return qq_str


async def replace_at_in_message(
    message: str,
    host: str,
    port: int,
    *,
    bot_qq: Optional[int] = None,
    bot_name: Optional[str] = None,
    group_id: Optional[int] = None,
) -> str:
    """
    替换消息中所有 @数字 为 @用户昵称。
    
    Args:
        message: 原始消息文本
        host/port/bot_qq/bot_name/group_id: 连接信息
    
    Returns:
        替换后的消息文本
    """
    pattern = re.compile(r"@(\d{5,12})")
    result_parts: list[str] = []
    last_idx = 0
    
    for m in pattern.finditer(message):
        result_parts.append(message[last_idx:m.start()])
        qq = m.group(1)
        rep = m.group(0)
        try:
            if bot_qq is not None and int(qq) == int(bot_qq):
                # ★ bot 自身的 @ 保持原始 QQ 号格式，不替换
                # 避免群名片不同导致 @检测失败
                rep = m.group(0)
                logger.debug("@%s → 保持 (机器人自身)", qq)
            else:
                nick = await get_or_resolve_username(qq, host, port, group_id=group_id)
                if nick and str(nick) != str(qq):
                    rep = f"@{nick}"
                    logger.debug("@%s → @%s (WS查询)", qq, nick)
        except Exception as e:
            logger.warning("替换 @ 用户名失败 [%s]: %s", qq, e)
        result_parts.append(rep)
        last_idx = m.end()
    
    result_parts.append(message[last_idx:])
    return "".join(result_parts)


# ── WS 底层 API 调用 ────────────────────────────────────────

async def _onebot_ws_action(
    action: str,
    params: dict,
    host: str,
    port: int,
    echo: str,
) -> Optional[dict]:
    """
    调用 OneBot WebSocket action 接口并等待响应。
    """
    uri = f"ws://{host}:{port}/"
    try:
        async with websockets.connect(uri) as ws:
            req = {
                "action": action,
                "params": params,
                "echo": echo,
            }
            await ws.send(json.dumps(req))
            while True:
                resp_text = await ws.recv()
                try:
                    data = json.loads(resp_text)
                except Exception:
                    continue
                if isinstance(data, dict) and data.get("echo") == echo:
                    return data
    except Exception as e:
        logger.warning("WS action '%s' 失败: %s", action, e)
    return None


async def _query_username_ws(
    qq: int,
    host: str,
    port: int,
    group_id: Optional[int] = None,
) -> Optional[str]:
    """
    通过 NapCat WS API 查询用户昵称。
    先尝试 get_group_member_info（需要群号），再回退到 get_stranger_info。
    """
    # 尝试群成员查询（更准确，有群名片）
    if group_id is not None:
        echo = f"get_group_member_info_{group_id}_{qq}"
        resp = await _onebot_ws_action(
            "get_group_member_info",
            {"group_id": int(group_id), "user_id": int(qq)},
            host, port, echo,
        )
        if resp and isinstance(resp, dict):
            payload = resp.get("data", resp)
            if isinstance(payload, dict):
                nick = payload.get("card") or payload.get("nickname")
                if nick:
                    logger.info("通过群(%s)查到 %s 昵称: %s", group_id, qq, nick)
                    return nick

    # 回退到陌生人查询
    echo = f"get_stranger_info_{qq}"
    resp = await _onebot_ws_action(
        "get_stranger_info",
        {"user_id": int(qq)},
        host, port, echo,
    )
    if resp and isinstance(resp, dict):
        payload = resp.get("data", resp)
        if isinstance(payload, dict):
            nick = (
                payload.get("nickname")
                or payload.get("name")
                or payload.get("user_displayname")
            )
            if nick:
                logger.info("通过陌生人接口查到 %s 昵称: %s", qq, nick)
                return nick
    
    logger.warning("WS 查询 %s 昵称失败（所有方式均未成功）", qq)
    return None

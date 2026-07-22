import os
import re
from typing import Dict, Optional, Union
import json
import websockets
from log import info, warning
from read_config import load_roles_config

MAPPING_FILENAME = "Group_members_name.txt"

def _mapping_path() -> str:
    return os.path.join(os.path.dirname(__file__), MAPPING_FILENAME)

def _load_mapping(file_path: Optional[str] = None) -> Dict[str, str]:
    path = file_path or _mapping_path()
    mapping: Dict[str, str] = {}
    if not os.path.exists(path):
        return mapping
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
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
        warning(f"读取 {path} 失败: {e}")
    return mapping

def _append_mapping(qq号: str, 昵称: str, file_path: Optional[str] = None) -> None:
    path = file_path or _mapping_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{qq号} : {昵称}\n")
        info(f"已记录用户 {qq号} -> {昵称}")
    except Exception as e:
        warning(f"写入 {path} 失败: {e}")

async def 获取或新增用户名(qq号: Union[int, str], napcat_host: str, napcat_port: int, 群号: Optional[int] = None) -> str:
    qq_str = str(qq号)

    # 优先从 roles.toml 预设映射获取
    roles = load_roles_config()
    name_map = roles.get('qq_name_map', {})
    if qq_str in name_map:
        return name_map[qq_str]

    # 原逻辑：查本地文件
    映射 = _load_mapping()
    if qq_str in 映射:
        return 映射[qq_str]

    # 在线查询
    try:
        昵称 = await 查询用户名_ws(int(qq_str), napcat_host, napcat_port, group_id=群号)
    except Exception as e:
        warning(f"通过 WS 查询昵称失败[{qq_str}]: {e}")
        昵称 = None

    if 昵称:
        _append_mapping(qq_str, 昵称)
        return 昵称
    return qq_str

async def 替换消息中的at(消息内容: str, napcat_host: str, napcat_port: int, *,
                          bot_qq: Optional[int] = None,
                          bot_name: Optional[str] = None,
                          群号: Optional[int] = None) -> str:
    pattern = re.compile(r"@(\d{5,12})")
    result_parts = []
    last_idx = 0
    for m in pattern.finditer(消息内容):
        result_parts.append(消息内容[last_idx:m.start()])
        qq = m.group(1)
        rep = m.group(0)
        try:
            if bot_qq is not None and bot_name and int(qq) == int(bot_qq):
                rep = f"@{bot_name}"
            else:
                昵称 = await 获取或新增用户名(qq, napcat_host, napcat_port, 群号=群号)
                if 昵称 and str(昵称) != str(qq):
                    rep = f"@{昵称}"
        except Exception as e:
            warning(f"替换 @ 用户名失败[{qq}]: {e}")
        result_parts.append(rep)
        last_idx = m.end()
    result_parts.append(消息内容[last_idx:])
    return "".join(result_parts)

async def _onebot_ws_action(action: str, params: dict, napcat_host: str, napcat_port: int, echo: str) -> Optional[dict]:
    uri = f"ws://{napcat_host}:{napcat_port}/"
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
        warning(f"WS action {action} 失败: {e}")
    return None

async def 查询用户名_ws(qq号: int, napcat_host: str, napcat_port: int, group_id: Optional[int] = None) -> Optional[str]:
    if group_id is not None:
        echo = f"get_group_member_info_{group_id}_{qq号}"
        resp = await _onebot_ws_action(
            "get_group_member_info",
            {"group_id": int(group_id), "user_id": int(qq号)},
            napcat_host, napcat_port, echo,
        )
        if resp and isinstance(resp, dict):
            payload = resp.get("data", resp)
            if isinstance(payload, dict) and (payload.get("card") or payload.get("nickname")):
                nick = payload.get("card") or payload.get("nickname")
                if nick:
                    info(f"通过群({group_id})查询到 {qq号} 的昵称: {nick}")
                    return nick
            if resp.get("status") == "ok" and isinstance(resp.get("data"), dict):
                data = resp["data"]
                nick = data.get("card") or data.get("nickname")
                if nick:
                    info(f"通过群({group_id})查询到 {qq号} 的昵称: {nick}")
                    return nick

    echo = f"get_stranger_info_{qq号}"
    resp = await _onebot_ws_action(
        "get_stranger_info",
        {"user_id": int(qq号)},
        napcat_host, napcat_port, echo,
    )
    if resp and isinstance(resp, dict):
        payload = resp.get("data", resp)
        if isinstance(payload, dict) and (payload.get("nickname") or payload.get("name") or payload.get("user_displayname")):
            nick = payload.get("nickname") or payload.get("name") or payload.get("user_displayname")
            return nick
        if resp.get("status") == "ok" and isinstance(resp.get("data"), dict):
            data = resp["data"]
            nick = data.get("nickname") or data.get("name") or data.get("user_displayname")
            return nick
    warning(f"WS 查询 {qq号} 昵称失败")
    return None
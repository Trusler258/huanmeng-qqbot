#!/usr/bin/env python3
c = open("G:/py/qqbot/modules/admin.py", "r", encoding="utf-8").read()

new_func = """

def wl_cmd_manage(args):
    data = _read_toml(_CONFIG_DIR / "adapter_config.toml")
    gs = data.setdefault("group_settings", {})
    if not args:
        return "用法: wl cmd add|remove|list <群号> [指令]"
    sub = args[0].lower()
    if sub in ("list", "show"):
        if len(args) < 2:
            active = [(g, s.get("cmd_whitelist", [])) for g, s in gs.items() if s.get("cmd_whitelist")]
            if not active: return "无分群指令白名单"
            lines = ["分群指令白名单:"]
            for gid, cmds in active: lines.append(f"  群{gid}: {', '.join(cmds)}")
            return "\\n".join(lines)
        gid = args[1]; cmds = gs.get(gid, {}).get("cmd_whitelist", [])
        if not cmds: return f"群{gid} 无指令白名单"
        return f"群{gid} 指令白名单: {', '.join(cmds)}"
    if len(args) < 3: return f"用法: wl cmd {sub} <群号> <指令名>"
    gid = args[1]; cmd_name = args[2].lower().lstrip("/~#")
    gs_group = gs.get(gid, {}); cmds = gs_group.get("cmd_whitelist", []) or []
    if sub == "add":
        if cmd_name in cmds: return f"群{gid} 已有指令 {cmd_name}"
        cmds.append(cmd_name)
    elif sub == "remove":
        if cmd_name not in cmds: return f"群{gid} 无指令 {cmd_name}"
        cmds.remove(cmd_name)
    else: return f"未知操作: {sub}"
    gs_group["cmd_whitelist"] = cmds; gs[gid] = gs_group
    data["group_settings"] = gs; _write_toml(_CONFIG_DIR / "adapter_config.toml", data)
    from core.config import reload_config; reload_config()
    return f"群{gid} 指令白名单已更新: {', '.join(cmds) if cmds else '(空=全可用)'}"

"""

idx = c.find('# \u6587\u4ef6\u67e5\u770b\uff08\u8fc7\u6ee4\u654f\u611f\u4fe1\u606f\uff09')
c = c[:idx] + new_func + c[idx:]
open("G:/py/qqbot/modules/admin.py", "w", encoding="utf-8").write(c)
import ast
ast.parse(c)
print("OK")

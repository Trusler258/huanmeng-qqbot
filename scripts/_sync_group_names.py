"""一次性：连 NapCat WS 拉群列表 → data/group_names.json

为什么需要它：bot 的 nickname_sync 每天 23:59 才自动跑一次，
而面板的「群管理 / 白名单管理」需要立刻有群名可读（否则全是"未命名群"）。
本脚本走同一个数据源（get_group_list）、产出同一格式，与 nickname_sync 兼容。

在服务器上跑：python3 scripts/_sync_group_names.py
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, "/root/bot")

import websockets  # noqa: E402

URI = "ws://127.0.0.1:8099/"
OUT = Path("/root/bot/data/group_names.json")


async def fetch_groups() -> list:
    async with websockets.connect(URI, max_size=None) as ws:
        await ws.send(json.dumps(
            {"action": "get_group_list", "params": {}, "echo": "gn"}, ensure_ascii=False
        ))
        for _ in range(50):
            raw = await asyncio.wait_for(ws.recv(), timeout=20)
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if msg.get("echo") != "gn":
                continue
            if msg.get("status") == "failed":
                raise RuntimeError(f"NapCat 返回失败: {msg.get('message')}")
            data = msg.get("data")
            return data if isinstance(data, list) else []
    raise RuntimeError("没等到 echo=gn 的响应")


async def main() -> int:
    groups = await fetch_groups()
    names = {}
    for g in groups:
        if not isinstance(g, dict):
            continue
        gid = str(g.get("group_id", 0) or 0)
        name = str(g.get("group_name", "") or "").strip()
        if gid and gid != "0" and name:
            names[gid] = name

    if not names:
        print("没拿到任何群名（NapCat 返回为空？）")
        return 1

    # 与旧文件合并，保留上次有、这次没拉到的
    old = {}
    if OUT.exists():
        try:
            raw = json.loads(OUT.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                old = {str(k): str(v) for k, v in raw.items()}
        except Exception:
            old = {}
    old.update(names)
    OUT.write_text(json.dumps(old, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"共 {len(groups)} 个群，写入 {len(names)} 条群名 → {OUT}")
    for gid, n in list(names.items())[:12]:
        print(f"  {gid}  {n}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

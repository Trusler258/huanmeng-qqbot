"""自更新模块自测（在服务器上跑）

用法：
    python3 scripts/_probe_autoupdate.py test    # 连通性（无需权限）
    python3 scripts/_probe_autoupdate.py check   # 只读对比（不写盘、不应用）
    python3 scripts/_probe_autoupdate.py         # 同上，等于 check

直接调用 cmd_update，等价于管理员在群里发 /~update <模式>。
"""
import asyncio
import sys

sys.path.insert(0, "/root/bot")

ADMIN = 3483585417


async def main():
    from modules.auto_update import cmd_update

    mode = sys.argv[1] if len(sys.argv) > 1 else "check"
    args = [mode] if mode in ("test", "check", "force") else []

    print(f"### 调用 cmd_update(args={args}) ...")
    try:
        r = await cmd_update(args, ADMIN, 0, "probe", False, ADMIN)
    except Exception:
        import traceback
        traceback.print_exc()
        return 1

    print("=" * 60)
    print(r)
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

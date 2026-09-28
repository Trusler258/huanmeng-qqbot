"""v2.3.69 /~steam who 参数分派 —— 真实走 _do_who 全路径的回归测试

⚠️ 为什么必须单独有这个文件（教训）：
   上一版（v2.3.68）我为"直接给 id 就能查"写的端到端探针
   `scripts/_probe_steam_identity_e2e.py` 是**直接调 S.resolve_steamid()**，
   绕过了 `_do_who` 开头的 `target = _qq_of(raw, group_id)` 这道拦截，于是显示
   "8 种输入全部通过" —— 假阳性。真实命令路径上：

       _parse_opponent 第一句 = re.search(r"(\\d{4,12})", s)
       → 任何 4~12 位数字都被当成 QQ 号：
            1467315295         → QQ 1467315295      → 回「还没绑定 Steam 喵~」
            76561199427581023  → 抠出前 12 位 765611994275 → 同样「还没绑定」
       结果：**所有数字形式的 Steam 标识全部失效**（用户实测报障才发现）。

   本文件因此打桩 _send_card，**真的调用 _do_who**，断言它交给渲染的 steamid。
   并且专门断言"数字输入不得触发绑定查询"（get_bind 调用次数=0）。

用法（本地）:
  python tests/_test_v2369_steam_who_dispatch.py
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS = 0
FAIL = 0


def ok(msg):
    global PASS
    PASS += 1
    print(f"  [OK] {msg}")


def bad(msg):
    global FAIL
    FAIL += 1
    print(f"  [FAIL] {msg}")


# ── 打桩设施 ──────────────────────────────────────────────────

class Env:
    """一次 _do_who 调用的记录器"""

    def __init__(self, binds=None, qq_of=None):
        self.sent = []          # _send_card 收到的 steamid
        self.bind_lookups = []  # get_bind 被查的 QQ
        self.binds = binds or {}
        self.qq_of = qq_of or {}

    def install(self, M, S):
        env = self

        async def fake_send_card(steamid, user_id, group_id, is_group):
            env.sent.append(steamid)
            return True

        async def fake_delayed(*a, **kw):
            return None

        async def fake_summary(sid):
            return {"name": "Trusler"}

        def fake_has_key():
            return True

        def fake_steam_key():
            return "FAKEKEY"

        def fake_get_bind(qq):
            env.bind_lookups.append(int(qq))
            return env.binds.get(int(qq), "")

        def fake_qq_of(text, group_id):
            # 模拟真实 _parse_opponent：CQ at 码 → 抠出 qq=xxx；否则查映射表
            t = text.strip()
            if t in env.qq_of:
                return env.qq_of[t]
            m = re.search(r"qq=(\d+)", t)
            if m:
                return int(m.group(1))
            return 0

        M._send_card = fake_send_card
        M._delayed_notify = fake_delayed
        M._qq_of = fake_qq_of
        S.player_summary = fake_summary
        S.has_key = fake_has_key
        S.steam_key = fake_steam_key
        S.get_bind = fake_get_bind


async def run_who(M, text, user_id=10001, group_id=20002,
                  is_group=True, env=None, binds=None, qq_of=None):
    env = env or Env(binds=binds, qq_of=qq_of)
    from services import steam_api as S
    env.install(M, S)
    args = text.split() if text else []
    reply = await M._do_who(args, user_id, group_id, is_group, "测试者")
    return reply, env


ACCOUNT = 1467315295
SID64 = str(76561197960265728 + ACCOUNT)   # 76561199427581023


def main() -> None:
    import modules.steam as M

    print("=== 1. 纯数字输入必须按 Steam 标识解析（旧版被 _qq_of 吃掉）===")
    for label, text in [
        ("account_id", str(ACCOUNT)),
        ("SteamID64", SID64),
        ("带前后空白的 account_id", f"  {ACCOUNT}  "),
    ]:
        reply, env = asyncio.run(run_who(M, text))
        if env.sent == [SID64]:
            ok(f"{label}「{text.strip()}」→ 交给渲染的 steamid 正确 {SID64}")
        else:
            bad(f"{label}「{text.strip()}」→ 拿到 {env.sent}，期望 [{SID64}]（reply={reply!r}）")
        if not env.bind_lookups:
            ok(f"{label} 未触发绑定查询（没被当成 QQ 号）")
        else:
            bad(f"{label} 却去查了绑定 QQ {env.bind_lookups} —— 说明还是被当 QQ 了")

    print()
    print("=== 2. 其余标识形式也走直查 ===")
    for label, text in [
        ("字母好友代码", "hkkhkghw"),
        ("带横线好友代码", "hkk-hkghw"),
        ("大写好友代码", "HKKHKGHW"),
        ("SteamID2", "STEAM_1:1:733657647"),
        ("SteamID3 [U:1:x]", f"[U:1:{ACCOUNT}]"),
        ("SteamID3 U:1:x", f"U:1:{ACCOUNT}"),
        ("资料链接", f"https://steamcommunity.com/profiles/{SID64}"),
        ("资料链接带斜杠", f"steamcommunity.com/profiles/{SID64}/"),
    ]:
        reply, env = asyncio.run(run_who(M, text))
        if env.sent == [SID64] and not env.bind_lookups:
            ok(f"{label}「{text}」→ {SID64}，且未查绑定")
        else:
            bad(f"{label}「{text}」→ sent={env.sent} binds={env.bind_lookups} reply={reply!r}")

    print()
    print("=== 3. 带 @ 才走绑定（用户明确要求）===")
    reply, env = asyncio.run(
        run_who(M, f"[CQ:at,qq=3483585417]", binds={3483585417: SID64}))
    if env.sent == [SID64] and env.bind_lookups == [3483585417]:
        ok("CQ @码 → 查绑定 3483585417 → 拿到其 steamid")
    else:
        bad(f"CQ @码 分支异常: sent={env.sent} binds={env.bind_lookups} reply={reply!r}")

    reply, env = asyncio.run(run_who(M, "@小黄", binds={3483585417: SID64},
                                     qq_of={"@小黄": 3483585417}))
    if env.sent == [SID64] and env.bind_lookups == [3483585417]:
        ok("@昵称 → 查绑定 → 拿到其 steamid")
    else:
        bad(f"@昵称 分支异常: sent={env.sent} binds={env.bind_lookups} reply={reply!r}")

    print()
    print("=== 4. @ 了但认不出 / 未绑定 的提示 ===")
    reply, env = asyncio.run(run_who(M, "@查无此人", qq_of={}))
    if not env.sent and reply and "没认出你 @ 的是谁" in reply:
        ok("认不出的 @ → 明确提示，不发卡")
    else:
        bad(f"认不出的 @ 处理异常: sent={env.sent} reply={reply!r}")

    reply, env = asyncio.run(run_who(M, "[CQ:at,qq=3483585417]", binds={}))
    if not env.sent and reply and "还没绑定" in reply:
        ok("已 @ 但对方未绑定 → 提示他去绑定，不发卡")
    else:
        bad(f"未绑定分支异常: sent={env.sent} reply={reply!r}")

    print()
    print("=== 5. 不带参数 = 查自己（仍走绑定）===")
    reply, env = asyncio.run(run_who(M, "", user_id=10001, binds={10001: SID64}))
    if env.sent == [SID64] and env.bind_lookups == [10001]:
        ok("空参数 → 查自己的绑定")
    else:
        bad(f"空参数异常: sent={env.sent} binds={env.bind_lookups} reply={reply!r}")

    print()
    print("=== 6. 非 @ 的昵称/中文 不会被当成 QQ ===")
    reply, env = asyncio.run(run_who(M, "小黄"))
    if not env.bind_lookups:
        ok("中文昵称「小黄」未触发绑定查询（旧版也不会，但需锁死）")
    else:
        bad(f"中文昵称被当 QQ 了: {env.bind_lookups}")
    if reply and "认不出" in reply:
        ok("中文昵称 → 走 resolve 路径并给出认不出的提示")
    else:
        bad(f"中文昵称回复异常: {reply!r}")

    print()
    print("=== 7. 钉死根因：_parse_opponent 对纯数字就是抠数字当 QQ ===")
    from modules.commands import _parse_opponent
    from core.config import get_config
    cfg = get_config()
    n_account = _parse_opponent(str(ACCOUNT), 20002, cfg)
    n_sid64 = _parse_opponent(SID64, 20002, cfg)
    if n_account == ACCOUNT:
        ok(f"_parse_opponent('{ACCOUNT}') = {ACCOUNT}（正因如此才必须先判 Steam 标识）")
    else:
        bad(f"_parse_opponent 行为变了: {n_account}")
    if n_sid64 is not None and n_sid64 != int(SID64):
        ok(f"_parse_opponent('{SID64}') = {n_sid64} —— 只抠前 12 位，SteamID64 必被误判")
    else:
        bad(f"_parse_opponent 对 17 位数字返回 {n_sid64}，与预期（截断）不同")

    print()
    print("=== 8. resolve_steamid 的提示文案不再把好友代码说成数字 ===")
    from services import steam_api as S
    msg = None
    try:
        msg, err = asyncio.run(S.resolve_steamid("这个肯定认不出喵"))
    except Exception as e:
        err = f"异常 {e}"
    # 直接读源码更稳（联网分支不打桩时可能不返回文案）
    src = Path(ROOT, "services/steam_api.py").read_text(encoding="utf-8")
    if "好友代码（Steam 好友列表里那串字母，如 hkkhkghw）" in src:
        ok("文案已改为「字母形式好友代码」")
    else:
        bad("提示文案未更新")
    if "好友代码（Steam 客户端「好友代码」那串数字，如 1467315295）" not in src:
        ok("旧的错误说法（好友代码=1467315295 那种数字）已删除")
    else:
        bad("旧错误文案仍在")

    print()
    print("=== 9. 端到端探针改用真实入口（防再次假阳性）===")
    probe = Path(ROOT, "scripts/_probe_steam_identity_e2e.py")
    if probe.exists():
        psrc = probe.read_text(encoding="utf-8")
        if "_do_who" in psrc:
            ok("探针已改为调 _do_who（走真实分派）")
        else:
            bad("探针仍在直接调 resolve_steamid —— 会再次漏掉 _do_who 的拦截")

    print()
    print(f"结果: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()

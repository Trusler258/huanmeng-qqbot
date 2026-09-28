"""v2.3.69 Steam 账号标识解析 + /~steam who 直接查 回归测试

用户要求：`/~steam who <steam id 或好友代码>` —— 不只有 @某人，有 id 就直接查对应的。

关键知识点（已实测验证）：
  Steam 客户端「好友代码」是 account_id 的**十六进制**再按固定表替换字符：
    0=b 1=c 2=d 3=f 4=g 5=h 6=j 7=k 8=m 9=n a=p b=q c=r d=t e=v f=w
  例：Trusler account_id 1467315295 → hex 5775745f → hkkhkghw
      取 steamcommunity.com/user/hkkhkghw 返回 <title>Steam Community :: Trusler</title>
  ⚠️ 第一版误以为好友代码 = account_id 十进制，拿 /user/1467315295 去试 → Steam 返回 Error 页。

用法（本地）:
  python tests/_test_v2369_steam_identity.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS = 0
FAIL = 0


def ok(msg):
    global PASS
    PASS += 1
    print(f"  \033[32mOK\033[0m   {msg}")


def bad(msg):
    global FAIL
    FAIL += 1
    print(f"  \033[31mFAIL\033[0m {msg}")


TRUSLER_SID = "76561199427581023"
TRUSLER_ACC = "1467315295"
TRUSLER_CODE = "hkkhkghw"


def main():
    from services import steam_api as S

    print("=== 1. 字母好友代码编解码（可逆）===")
    if S.friend_code_to_account_id(TRUSLER_CODE) == TRUSLER_ACC:
        ok(f"{TRUSLER_CODE} → account_id {TRUSLER_ACC}")
    else:
        bad(f"{TRUSLER_CODE} → {S.friend_code_to_account_id(TRUSLER_CODE)}")
    if S.account_id_to_friend_code(TRUSLER_ACC) == TRUSLER_CODE:
        ok(f"{TRUSLER_ACC} → 好友代码 {TRUSLER_CODE}")
    else:
        bad(f"{TRUSLER_ACC} → {S.account_id_to_friend_code(TRUSLER_ACC)}")
    # 多组回环（覆盖不同 hex 位）
    good = 0
    for acc in (1, 2, 15, 16, 12345, 39734272, 1467315295, 1556340897, 1580141030):
        code = S.account_id_to_friend_code(acc)
        if S.friend_code_to_account_id(code) == str(acc):
            good += 1
        else:
            bad(f"回环失败 acc={acc} code={code}")
    if good == 9:
        ok("9 组 account_id ↔ 好友代码 回环全部通过")
    ok("带横线/大写也能解: " + ("是" if S.friend_code_to_account_id("hkk-hkghw") == TRUSLER_ACC
                              and S.friend_code_to_account_id("HKKHKGHW") == TRUSLER_ACC else "否"))

    print("\n=== 2. 非法输入不能误认 ===")
    for junk in ("", "abc", "hello", "zzzz", "123456789012345678901"):
        r = S.friend_code_to_account_id(junk)
        if r == "":
            ok(f"{junk!r} → 拒绝")
        else:
            bad(f"{junk!r} → 竟然解出 {r}")

    print("\n=== 3. 8 种输入形式都指向同一账号 ===")
    forms = {
        "字母好友代码": TRUSLER_CODE,
        "带横线": "hkk-hkghw",
        "大写": "HKKHKGHW",
        "SteamID64": TRUSLER_SID,
        "数字(account_id)": TRUSLER_ACC,
        "SteamID2": "STEAM_1:1:733657647",
        "SteamID3": "[U:1:1467315295]",
        "资料链接": f"https://steamcommunity.com/profiles/{TRUSLER_SID}",
    }
    for name, inp in forms.items():
        sid, src = S.extract_identity(inp)
        if sid == TRUSLER_SID:
            ok(f"{name:16} {inp[:34]:36} → {sid}  ({src})")
        else:
            bad(f"{name:16} {inp!r} → {sid or '(空)'} 期望 {TRUSLER_SID}")
    if S.extract_identity("someone_random_name")[0] == "":
        ok("非账号形式的字符串不误判（交给 vanity 解析）")
    else:
        bad("把普通字符串误判成账号了")

    print("\n=== 4. SteamID2/3 公式 ===")
    # STEAM_1:1:733657647 → acc = 733657647*2+1 = 1467315295
    sid, _ = S.extract_identity("STEAM_1:1:733657647")
    (ok if sid == TRUSLER_SID else bad)(f"SteamID2 公式正确 → {sid}")
    sid, _ = S.extract_identity("[U:1:1467315295]")
    (ok if sid == TRUSLER_SID else bad)(f"SteamID3 公式正确 → {sid}")

    print("\n=== 5. /~steam who 直接给 id：不走绑定 ===")
    import modules.steam as st

    calls = {"bind": 0, "resolve": [], "card": []}

    async def fake_resolve(text):
        calls["resolve"].append(text)
        if text.strip() == "hkkhkghw":
            return TRUSLER_SID, ""
        return "", "认不出这是账号喵~"

    async def fake_card(steamid, uid, gid, is_group):
        calls["card"].append(steamid)
        return True

    async def fake_notify(*a, **kw):
        return None

    def fake_bind(qq):
        calls["bind"] += 1
        return ""          # 故意让绑定为空：直接查不应依赖它

    S.has_key = lambda: True
    S.get_bind = fake_bind
    S.resolve_steamid = fake_resolve
    st._send_card = fake_card
    st._delayed_notify = fake_notify
    st._qq_of = lambda text, gid: 0        # 参数里没有 @

    # ① 给好友代码 → 应直查、不读绑定
    calls["bind"] = 0
    r = asyncio.run(st._do_who(["hkkhkghw"], 111, 222, True, "某人"))
    if r is None and calls["card"] == [TRUSLER_SID] and calls["bind"] == 0:
        ok("who <好友代码> → 直接查该账号（card 收到正确 sid，未读绑定）")
    else:
        bad(f"who <好友代码> 异常: r={r!r} card={calls['card']} bind={calls['bind']}")

    # ② 给 SteamID64 → 同上
    calls["card"] = []
    async def fake_resolve2(text):
        calls["resolve"].append(text)
        return (TRUSLER_SID, "") if len(text) == 17 else ("", "认不出")
    S.resolve_steamid = fake_resolve2
    asyncio.run(st._do_who([TRUSLER_SID], 111, 222, True, "某人"))
    (ok if calls["card"] == [TRUSLER_SID] else bad)(
        f"who <SteamID64> → 直接查（card={calls['card']}）")

    # ③ 不带参数 → 查自己，需要绑定
    calls["card"] = []
    S.resolve_steamid = fake_resolve
    r = asyncio.run(st._do_who([], 111, 222, True, "某人"))
    if "还没绑定" in (r or ""):
        ok("who（无参数）→ 仍走绑定逻辑并提示未绑定")
    else:
        bad(f"who（无参数）异常: {r!r}")

    # ④ 认不出的参数 → 返回解析错误，不静默查自己
    r = asyncio.run(st._do_who(["hello_world_xyz"], 111, 222, True, "某人"))
    if "认不出" in (r or ""):
        ok("who <认不出> → 明确报错（不会误查自己）")
    else:
        bad(f"who <认不出> 异常: {r!r}")

    print(f"\n=== 结果: {PASS} passed, {FAIL} failed ===")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()

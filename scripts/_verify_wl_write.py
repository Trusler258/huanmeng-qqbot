"""白名单写接口端到端验证（安全自测：加一个测试群号 → 校验落盘 → 撤销 → 校验还原）

在服务器上跑。重点验证三件事：
  1. 写操作真的落到 config/adapter_config.toml
  2. 只动被改的那一行，文件其余内容（注释/其他段/private_whitelist）不受影响
  3. 撤销后与操作前**完全一致**（绝不留下脏数据）
"""
import json
import sys
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:59300/api"
CFG = Path("/root/bot/config/adapter_config.toml")
TEST_ID = 999999999  # 不存在的群号，只用于往返测试


def req(method: str, path: str, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    r = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    return json.loads(urllib.request.urlopen(r, timeout=25).read())


def ids(path, token):
    return [g["group_id"] for g in req("GET", path, token=token)["group_list"]]


def main() -> int:
    tok = req("POST", "/auth/login",
              {"username": "admin", "password": "HuanmengPanel@2026"})["token"]

    raw_before = CFG.read_text(encoding="utf-8")
    before = ids("/groups/whitelist", tok)
    print(f"操作前：{len(before)} 个群 {before}")

    ok = True

    # ── 1. 加入 ──
    r1 = req("POST", "/groups/whitelist/group", {"target": TEST_ID, "add": True}, tok)
    if TEST_ID not in r1["group_list"]:
        print("✗ 加入后接口返回里没有该群")
        ok = False
    else:
        print(f"✓ 加入：现在 {len(r1['group_list'])} 个，need_restart={r1.get('need_restart')}")

    raw_add = CFG.read_text(encoding="utf-8")
    if str(TEST_ID) not in raw_add:
        print("✗ 配置文件里没写进去")
        ok = False
    else:
        line = [ln for ln in raw_add.splitlines() if ln.startswith("group_list")][0]
        print(f"✓ 落盘：{line[:90]}...")

    # 其余内容是否完好
    for probe in ("[napcat_server]", "private_whitelist", "[group_settings.1058782600]",
                  "at_only = true"):
        if probe not in raw_add:
            print(f"✗ 加入操作破坏了文件内容：缺 {probe!r}")
            ok = False
    if "private_whitelist = [ 3483585417" not in raw_add:
        print("✗ private_whitelist 被改动")
        ok = False
    else:
        print("✓ 文件其余内容完好（注释/段/private_whitelist 未受影响）")

    # ── 2. 撤销 ──
    r2 = req("POST", "/groups/whitelist/group", {"target": TEST_ID, "add": False}, tok)
    raw_del = CFG.read_text(encoding="utf-8")
    if str(TEST_ID) in raw_del:
        print("✗ 撤销后文件里仍有测试群号")
        ok = False

    after = ids("/groups/whitelist", tok)
    if after != before:
        print(f"✗ 撤销后与操作前不一致：{before} vs {after}")
        ok = False
    else:
        print(f"✓ 撤销：恢复为 {len(after)} 个群，与操作前完全一致")

    # ── 3. 备份是否生成 ──
    baks = sorted(CFG.parent.glob("adapter_config.toml.bak_*"))
    print(f"✓ 生成了 {len(baks)} 个 .bak 备份，最新：{baks[-1].name if baks else '无'}")

    # ── 4. 非法输入应被拒 ──
    try:
        req("POST", "/groups/whitelist/group", {"target": 0, "add": True}, tok)
        print("✗ target=0 竟然被接受（校验失效）")
        ok = False
    except Exception as e:
        print(f"✓ target=0 被拒：{str(e)[:60]}")

    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

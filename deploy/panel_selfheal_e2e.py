"""崩溃自愈端到端实测

这是整轮改动里最需要实证的部分 —— 自愈是"出事才会跑"的代码，
真出事时第一次运行就是最后一次机会。所以这里真的把 bot 弄坏，看它能不能自己救回来。

⚠️ 实测踩出来的关键坑（已修）：`bot.service` 是 Restart=always + RestartSec=2。
配置改坏后 systemd 每 2 秒把 bot 拉起来一次，永远不会停在 failed，
`systemctl is-active` **一直返回 active**。所以判定"崩了"不能看 is-active，
要看 NRestarts / MainPID 是否变化。这个脚本的第一版就是被这件事骗了
（明明崩了却报"bot 竟然起来了"），改完才跑通。

测试步骤：
  1. 备份 bot 的真实配置
  2. 通过面板接口写一个**语法合法但会让 bot 启动失败**的配置
  3. 重启 bot，确认它真的起不来（用 NRestarts 判定，不是 is-active）
  4. 手动触发探测，确认自愈判定并回滚
  5. 验证配置文件已还原、bot 恢复
  6. 无论如何都要把原始配置还原（try/finally 保证）

⚠️ 这个脚本会短暂中断 bot 服务（约 30-60 秒）。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

BASE = os.environ.get("PANEL_TEST_BASE", "http://127.0.0.1:59300")
PW = os.environ.get("PANEL_TEST_PW", "")
CFG = Path(_ROOT) / "config" / "bot_config.toml"

if not PW:
    print("需要 PANEL_TEST_PW")
    sys.exit(2)

PASS = 0
FAIL = 0
FAILED: list[str] = []


def check(name: str, ok: bool, brief: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [OK]   {name}  {brief}")
    else:
        FAIL += 1
        FAILED.append(f"{name} :: {brief}")
        print(f"  [FAIL] {name}  {brief}")


def api(path, method="GET", body=None, token=None):
    url = BASE + urllib.parse.quote(path, safe="/?=&%")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode("utf-8", "ignore")
            try:
                return r.status, json.loads(raw)
            except Exception:
                return r.status, {"_raw": raw[:300]}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "ignore")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"_raw": raw[:300]}
    except Exception as e:
        return 0, {"_err": str(e)}


def sh(cmd: list[str], timeout: int = 60) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except Exception as e:
        return -1, str(e)


def bot_active() -> bool:
    """辅助信息。⚠️ 在 Restart=always 下这个值几乎恒为 active，不能当判据。"""
    return sh(["systemctl", "is-active", "bot.service"])[1].strip() == "active"


def unit_snap() -> dict:
    """读 systemd 的 NRestarts / MainPID —— 判定崩溃循环的正确信号"""
    out = sh(["systemctl", "show", "bot.service",
              "-p", "ActiveState,SubState,MainPID,NRestarts,ExecMainStatus",
              "--no-pager"])[1]
    d: dict = {}
    for line in out.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            d[k.strip()] = v.strip()
    for k in ("MainPID", "NRestarts"):
        try:
            d["_" + ("pid" if k == "MainPID" else "n")] = int(d.get(k) or 0)
        except ValueError:
            d["_" + ("pid" if k == "MainPID" else "n")] = 0
    d["_at"] = time.time()
    return d


def watch_crash(seconds: int = 14) -> dict:
    """观察一段时间，返回重启统计。用来确认"确实在崩"而不是看 is-active。"""
    start = unit_snap()
    time.sleep(seconds)
    end = unit_snap()
    return {
        "n_before": start["_n"], "n_after": end["_n"],
        "pid_before": start["_pid"], "pid_after": end["_pid"],
        "restarts": end["_n"] - start["_n"],
        "pid_changed": start["_pid"] != end["_pid"],
        "state": end.get("ActiveState"),
        "raw": end,
    }


def wait_stable(seconds: int = 40) -> dict:
    """等 bot 真正稳定（连续两次采样 MainPID/NRestarts 都不变）"""
    last = unit_snap()
    for _ in range(seconds):
        time.sleep(1)
        cur = unit_snap()
        if (cur["_pid"] == last["_pid"] and cur["_n"] == last["_n"]
                and cur["_pid"] > 0 and cur.get("ActiveState") == "active"):
            return cur
        last = cur
    return last


print("=== 自愈端到端实测 ===\n")

# ── 准备 ──
original = CFG.read_bytes()
backup_path = CFG.with_name(f"{CFG.name}.selfheal_test_orig")
backup_path.write_bytes(original)
print(f"已备份原始配置 ({len(original)} 字节) → {backup_path.name}")

# roles.toml 是本次真正要动的文件
ROLES = Path(_ROOT) / "config" / "roles.toml"
roles_orig_file = ROLES.with_name("roles.toml.selfheal_orig")
if ROLES.exists():
    roles_orig_file.write_bytes(ROLES.read_bytes())
    print(f"已备份 roles.toml ({ROLES.stat().st_size} 字节)")

TOKEN = None
try:
    # ── 0. 登录 ──
    print("\n--- 0. 准备 ---")
    st, r = api("/api/auth/login", "POST", {"password": PW})
    if st != 200:
        print(f"登录失败: {st} {r}")
        sys.exit(1)
    TOKEN = r["token"]
    check("登录成功", bool(TOKEN))

    st, r = api("/api/health")
    check("面板活着", st == 200)

    check("bot 当前正常运行", bot_active())

    # ── 1. 写一个语法合法但会让 bot 崩的配置 ──
    #
    # 思路：找一个 bot 加载时**没有 try 保护**的字段，改成类型不对的值。
    # 语法上完全是合法 TOML（语法校验会放行），但 bot 加载时抛异常 → 起不来。
    # 这正好模拟"手滑改错"的真实场景。
    #
    # 选 roles.toml 的 op_qqs：core/config.py 里是
    #     op_qqs = [int(q) for q in op_qqs_raw]
    # 没有异常保护，塞进非数字字符串必然 ValueError。
    #
    # ⚠️ 为什么不改 bot_config.toml：试过把 `bot的qq号` 改成字符串，
    #   bot 竟然正常起来了（那个字段容错好，不做强转）。所以这里选了
    #   真正会抛异常的那一处。
    #
    # ⚠️ 另一个坑：必须是**改已有键的值**，不能在段里新增同名键 ——
    #   toml 0.10.2 遇到中文键重复出现会解析失败，且报错行号完全误导
    #   （指向文件第 2 行）。踩过一次，记在这里。
    print("\n--- 1. 通过面板写入会致崩的配置 ---")
    roles_path = Path(_ROOT) / "config" / "roles.toml"
    roles_original = roles_path.read_bytes()
    roles_text = roles_original.decode("utf-8")
    print(f"  目标文件 roles.toml ({len(roles_original)} 字节)")

    # 清掉前几轮调试留下的陈旧操作栈记录。
    # 不清的话，自愈可能回滚到一个跟本次崩溃无关的文件 —— 早期版本正是
    # 弹栈顶，实测就撞上了这个（栈里躺着两条陈年 bot_config.toml 写入）。
    # 现在自愈已改为"只回滚布防时记住的那条"，这里清空是为了让断言更干净。
    st0, r0 = api("/api/system/ops", token=TOKEN)
    if r0.get("count"):
        api("/api/system/ops/clear", "POST", {"confirm": "CLEAR"}, token=TOKEN)
        st0, r0 = api("/api/system/ops", token=TOKEN)
        print(f"  已清空陈旧操作栈（原有 {r0.get('count')} 条剩余）")

    # 清掉历史自愈事件，否则断言会看到之前几轮调试留下的旧事件，
    # 明明这次没触发却以为触发了
    st_e, r_e = api("/api/system/selfheal", token=TOKEN)
    if r_e.get("events"):
        api("/api/system/selfheal/events/clear", "POST",
            {"confirm": "CLEAR"}, token=TOKEN)
        st_e, r_e = api("/api/system/selfheal", token=TOKEN)
        print(f"  已清空历史自愈事件（原有 {r_e.get('events') and len(r_e['events']) or 0} 条剩余）")

    if "op_qqs" not in roles_text:
        print("  roles.toml 里没有 op_qqs，无法构造测试。跳过。")
        raise SystemExit(0)

    # 把 op_qqs 数组里的第一个数字换成一个非数字字符串
    import re as _re
    m = _re.search(r"^op_qqs\s*=\s*\[([^\]]*)\]", roles_text, _re.M)
    if not m:
        print("  找不到 op_qqs 数组，跳过。")
        raise SystemExit(0)
    inner = m.group(1)
    if inner.strip():
        broken_roles = (roles_text[: m.start()]
                        + 'op_qqs = ["这不是QQ号"]'
                        + roles_text[m.end():])
    else:
        broken_roles = (roles_text[: m.start()]
                        + 'op_qqs = ["这不是QQ号"]'
                        + roles_text[m.end():])

    st, r = api("/api/config/file/roles.toml", "PUT",
                {"content": broken_roles, "confirm": "roles.toml"}, token=TOKEN)
    check("面板接受了这次写入（语法合法）", st == 200,
          f"返回 {st} {str(r)[:140]}")
    if st != 200:
        print("  ⚠️ 写入被拒，测试无法继续")
        raise SystemExit(1)
    check("接口要求布防", r.get("armed") is True, f"armed={r.get('armed')}")

    after_write = roles_path.read_text(encoding="utf-8")
    check("配置文件确实被改了", "这不是QQ号" in after_write)

    # ── 2. 确认自愈已布防 ──
    print("\n--- 2. 确认自愈状态 ---")
    st, r = api("/api/system/selfheal", token=TOKEN)
    check("自愈进入布防状态", r.get("armed") is True, f"phase={r.get('phase')}")
    check("布防后进入高频探测档", r.get("phase") == "armed",
          f"phase={r.get('phase')}")
    check("探测间隔为 5s", r.get("probe_interval") == 5,
          f"得到 {r.get('probe_interval')}")

    st, r = api("/api/system/ops", token=TOKEN)
    check("操作栈记录了这次写入", r.get("count", 0) >= 1,
          f"栈顶 = {r['ops'][0]['kind'] if r.get('ops') else '空'}")
    check("栈顶是配置写入",
          r.get("ops") and r["ops"][0].get("kind") == "write_config")

    # ── 3. 重启 bot，让它崩 ──
    print("\n--- 3. 重启 bot（预期起不来）---")
    print("  说明：bot.service 是 Restart=always + RestartSec=2，")
    print("       崩溃后 systemd 会反复拉起，is-active 看着还是 active。")
    print("       真正的判据是 NRestarts / MainPID 在变。")
    sh(["systemctl", "restart", "bot.service"])
    print("  观察 16 秒（布防后 5s 探测一次，期间面板可能已自行自愈）...")
    crash = watch_crash(16)
    print(f"    NRestarts: {crash['n_before']} → {crash['n_after']}"
          f"（+{crash['restarts']}）")
    print(f"    MainPID:   {crash['pid_before']} → {crash['pid_after']}")
    print(f"    ActiveState: {crash['state']}")

    crashed = crash["restarts"] > 0 or crash["pid_changed"]
    check("bot 确实在崩溃循环（NRestarts 增长 / PID 更换）", crashed,
          f"重启 {crash['restarts']} 次，PID {'变了' if crash['pid_changed'] else '没变'}")
    # 说明：这里不断言 ActiveState == "active"。崩溃循环里它会在
    # active / activating 之间跳（抓在哪一瞬看运气）。要看的是
    # "它明明在反复重启，但只要落在 active 那一瞬，is-active 就会说没事"。
    check("崩溃循环中 is-active 从不停在 failed（故必须看 NRestarts）",
          crash["state"] in ("active", "activating", "auto-restart"),
          f"ActiveState={crash['state']}，is-failed = "
          f"{sh(['systemctl', 'is-failed', 'bot.service'])[1].strip()}")
    if not crashed:
        print("  ⚠️ bot 没崩 —— 这个错法不够狠，测试无法继续")
        raise SystemExit(0)

    # ── 4. 观察自愈是否自行触发 ──
    #
    # ⚠️ 不要 import panel.selfheal 来调 state._tick()！
    # 这个脚本是**独立进程**，import 进来的 SelfHealState 是本进程里的另一个
    # 单例，跟真正跑在 panel.service 里的那个毫无关系。实测踩过：
    # 本地对象的 consecutive_fail 一直是 0，看起来"自愈没反应"，
    # 其实面板那边早就把 bot 救回来了。查这个 bug 花了半天。
    # 正确做法：全程走 HTTP 接口，既是黑盒测试，也验证了真实部署的代码。
    print("\n--- 4. 观察自愈是否自行触发 ---")
    st, r = api("/api/system/selfheal", token=TOKEN)
    armed_now = r.get("armed") is True
    healed = bool(r.get("events"))
    restart_marks = r.get("probe_count", 0)
    print(f"  面板侧状态：phase={r.get('phase')} armed={armed_now} "
          f"probe_count={restart_marks} crash_loop={r.get('crash_loop')}")
    print(f"  unit: NRestarts={r.get('unit_n_restarts')} "
          f"MainPID={r.get('unit_main_pid')} "
          f"ActiveState={r.get('unit_active_state')}")

    check("面板的探测循环确实在跑（probe_count > 0）", restart_marks > 0,
          f"probe_count={restart_marks}")
    check("自愈已自行触发（这是按需探测该有的样子）", healed,
          f"已有 {len(r.get('events', []))} 条自愈事件")

    # 如果后台还没触发（比如探测间隔没到），手动补一脚。
    # 走 HTTP /selfheal/arm 重新布防，让面板自己的 loop 去做 —— 而不是本地 tick。
    if not healed:
        print("  → 后台还没触发，重新布防等后台 loop 处理")
        api("/api/system/selfheal/arm", "POST", token=TOKEN)
        for _ in range(10):
            time.sleep(3)
            st, r = api("/api/system/selfheal", token=TOKEN)
            if r.get("events"):
                healed = True
                break
        check("重新布防后自愈触发", healed,
              f"phase={r.get('phase')} probe_count={r.get('probe_count')}")

    time.sleep(3)

    # ── 5. 验证自愈结果 ──
    print("\n--- 5. 验证自愈效果 ---")

    # ★ 先等自愈把文件写完。
    # 自愈是：回滚文件 → 重启 bot。回滚完还要等 bot 起来才知道成没成，
    # 这中间有十几秒窗口。不等就直接读文件会读到还没回滚的状态。
    print("  等待自愈完成（轮询操作栈清空 + 文件还原）...")
    healed_ok = False
    for i in range(20):
        time.sleep(2)
        st_ops, r_ops = api("/api/system/ops", token=TOKEN)
        cur = roles_path.read_text(encoding="utf-8")
        if r_ops.get("count") == 0 and "这不是QQ号" not in cur:
            healed_ok = True
            print(f"    第 {i+1} 次检查：操作栈已清空、文件已还原")
            break
        print(f"    第 {i+1} 次检查：栈剩 {r_ops.get('count')} 条，"
              f"文件{'仍含改动' if '这不是QQ号' in cur else '已还原'}")

    st, r = api("/api/system/selfheal", token=TOKEN)
    events = r.get("events", [])
    check("产生了自愈事件", len(events) > 0, f"{len(events)} 条")
    if events:
        ev = events[0]
        print(f"  事件：ok={ev.get('ok')} | {ev.get('action')}")
        print(f"  详情：{ev.get('detail', '')[:160]}")
        check("自愈事件标记成功", ev.get("ok") is True,
              f"action={ev.get('action')}")
        check("回滚目标是配置写入",
              "write_config" in str(ev.get("action", "")),
              f"action={ev.get('action')}")

    # ⚠️ 比字节而不是比字符串：roles.toml 里有大量 emoji 昵称，是 UTF-8 多字节。
    # 之前写成 len(restored)（字符数）对 len(roles_original)（字节数），
    # 5030 字符 vs 6137 字节，看着像回滚不完整，其实是拿两种单位在比。
    restored = roles_path.read_bytes()
    check("配置文件已回滚（不再含破坏内容）", "这不是QQ号".encode() not in restored,
          "改动已撤销" if "这不是QQ号".encode() not in restored else "改动仍在！")
    check("回滚后内容与原始**逐字节**一致",
          restored == roles_original,
          f"{len(restored)} 字节 vs 原始 {len(roles_original)} 字节"
          + ("（一致）" if restored == roles_original else "（不一致！）"))

    st, r = api("/api/system/ops", token=TOKEN)
    check("操作栈已弹出（不会重复回滚）", r.get("count") == 0,
          f"剩余 {r.get('count')} 条")

    st, r = api("/api/system/selfheal", token=TOKEN)
    check("自愈记录里回滚的就是本次改动的那条",
          any("roles.toml" in str(ev.get("action", ""))
              for ev in r.get("events", [])),
          "回滚了 roles.toml" if any(
              "roles.toml" in str(ev.get("action", ""))
              for ev in r.get("events", [])) else "回滚了别的文件！")

    # ── 6. 验证 bot 恢复 ──
    print("\n--- 6. 等待 bot 真正稳定（不是只看 is-active）---")
    final = wait_stable(45)
    print(f"    MainPID={final['_pid']}  NRestarts={final['_n']}"
          f"  ActiveState={final.get('ActiveState')}")
    check("bot 已稳定运行", final["_pid"] > 0
          and final.get("ActiveState") == "active",
          f"PID={final['_pid']}")

    check("自愈已自动解除布防（回到静默）",
          api("/api/system/selfheal", token=TOKEN)[1].get("phase") == "idle")

    # ── 7. 审计留档 ──
    print("\n--- 7. 审计留档 ---")
    st, r = api("/api/system/audit?limit=30", token=TOKEN)
    actions = [i.get("action") for i in r.get("items", [])]
    check("审计记录了配置写入", "config_write" in actions, f"{actions[:6]}")

finally:
    # ── 无论如何都还原 ──
    print("\n--- 清理 ---")
    # bot_config.toml（虽然本次没改它，但保险起见）
    try:
        cur = CFG.read_bytes()
        if cur != original:
            CFG.write_bytes(original)
            print("  已还原 bot_config.toml")
    except Exception as e:
        print(f"  ⚠️ bot_config.toml 还原失败：{e}")
        if backup_path.exists():
            shutil.copy(backup_path, CFG)

    # roles.toml（本次真正改动的）
    try:
        roles_path = Path(_ROOT) / "config" / "roles.toml"
        roles_backup = roles_path.with_name("roles.toml.selfheal_orig")
        src = roles_backup if roles_backup.exists() else None
        if src and src.read_bytes() != roles_path.read_bytes():
            shutil.copy(src, roles_path)
            print("  已还原 roles.toml")
        elif not src:
            print("  roles.toml 备份不存在，跳过")
    except Exception as e:
        print(f"  ⚠️ roles.toml 还原失败：{e}")

    # 确认 bot 真的活着（测试期间折腾过它）
    if not bot_active():
        print("  bot 未运行，执行重启...")
        sh(["systemctl", "restart", "bot.service"])
    final = wait_stable(45)
    print(f"  bot 最终：main_pid={final['_pid']} n_restarts={final['_n']}"
          f" state={final.get('ActiveState')}")
    if final["_pid"] <= 0:
        print("  ⚠️ bot 没起来，可能需要人工介入")

    try:
        backup_path.unlink()
    except Exception:
        pass
    try:
        (Path(_ROOT) / "config" / "roles.toml.selfheal_orig").unlink()
    except Exception:
        pass

    if TOKEN:
        try:
            api("/api/system/selfheal/disarm", "POST", token=TOKEN)
        except Exception:
            pass

print(f"\n{'=' * 56}")
print(f"通过 {PASS} / 失败 {FAIL}")
if FAILED:
    print("\n失败项：")
    for f in FAILED:
        print(f"  - {f}")
else:
    print("全部通过 —— 自愈机制经实测可用")
print("=" * 56)
sys.exit(1 if FAIL else 0)

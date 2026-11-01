#!/usr/bin/env python3
"""官方机器人（织梦）通道健康检查 + IP 白名单漂移检测。

为什么不能自动改白名单：
  QQ 开放平台没有公开的管理 API。管理端用的是内部 CGI
  `q.qq.com/cgi-bin/dev_info/update_white_ip_config`，必须带浏览器登录态
  （2026-11-01 实测：不带 cookie 直接 404）。脚本改不了，只能人工在网页上改。

那这个脚本干什么：
  家宽出口 IP 一漂，网关立刻返回 `close 4924 session ip forbidden`，机器人静默失效。
  以前要等群友喊「机器人似了」才知道。现在每 5 分钟查一次，5 分钟内就能定位，
  并且把「该往白名单里填哪个 IP」直接写清楚。

检测项：
  1. systemd 服务是否 active
  2. 出口 IPv4 是否与上次记录不同
  3. 最近日志里有没有 4924 / 会话失效

产物：
  data/official_ip_state.json   上次看到的出口 IP
  data/official_alert.json      异常时存在，恢复后自动删除
  config/official_alert.json    可选 {"webhook": "https://..."} 做外部推送

用法：
  python3 scripts/check_official_health.py            # 检查一次（给 timer 用）
  python3 scripts/check_official_health.py --status    # 只打印状态，不写文件
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
STATE_FILE = DATA / "official_ip_state.json"
ALERT_FILE = DATA / "official_alert.json"
WEBHOOK_CONF = ROOT / "config" / "official_alert.json"

SERVICE = "qq-official-listen"
IP_SOURCES = (
    "https://ifconfig.me/ip",
    "https://ipv4.icanhazip.com",
    "https://api.ipify.org",
)

# 本机有失效的 127.0.0.1:7890 代理环境变量，必须显式绕开，否则取 IP 全超时
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def fetch_ip(timeout: float = 8.0) -> str:
    for url in IP_SOURCES:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "curl/8.5"})
            with _OPENER.open(req, timeout=timeout) as r:
                ip = r.read(64).decode("utf-8", "replace").strip()
            parts = ip.split(".")
            if len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
                return ip
        except Exception:
            continue
    return ""


def service_active() -> bool:
    try:
        out = subprocess.run(
            ["systemctl", "is-active", SERVICE],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        return out == "active"
    except Exception:
        return False


def recent_log(minutes: int = 15) -> str:
    try:
        return subprocess.run(
            ["journalctl", "-u", SERVICE, "--since", f"-{minutes} min", "--no-pager"],
            capture_output=True, text=True, timeout=25,
        ).stdout
    except Exception:
        return ""


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, obj) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"写文件失败 {path.name}: {type(e).__name__} {e}", file=sys.stderr)


def notify(text: str) -> None:
    """可选外部推送。配了 config/official_alert.json 的 webhook 才发。"""
    url = str(load_json(WEBHOOK_CONF, {}).get("webhook", "")).strip()
    if not url:
        return
    try:
        body = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/json"},
        )
        with _OPENER.open(req, timeout=8) as r:
            r.read(200)
    except Exception as e:
        print(f"webhook 推送失败: {type(e).__name__} {e}", file=sys.stderr)


def main() -> int:
    status_only = "--status" in sys.argv
    now = int(time.time())
    ip = fetch_ip()
    state = load_json(STATE_FILE, {})
    prev_ip = str(state.get("ip", ""))
    active = service_active()
    log = recent_log()
    forbidden = ("4924" in log) or ("ip forbidden" in log)
    # ★ 光看 4924 不够：程序在 op=9 时直接 break，close code 会丢，
    #   日志里只剩「60s 后重连」。所以再加一条「卡在重连」的判定：
    #   10 分钟内反复重连且一次 READY 都没有，就是连不上。
    reconnects = log.count("后重连")
    readys = log.count("READY:")
    stuck = reconnects >= 2 and readys == 0
    moved = bool(prev_ip) and bool(ip) and prev_ip != ip

    issues: list[str] = []
    if not active:
        issues.append(f"服务 {SERVICE} 不是 active")
    if forbidden:
        issues.append("网关返回 4924（IP 白名单不匹配）")
    elif stuck:
        issues.append(f"10 分钟内重连 {reconnects} 次、没有一次 READY（连不上网关）")
    if moved and not forbidden:
        # IP 变了但没被拒：可能是清空白名单了，或者刚好还在有效期。记一笔不算故障。
        issues.append(f"出口 IP 变化 {prev_ip} → {ip}（暂未影响连接）")

    hard = [x for x in issues if "暂未影响" not in x]

    if status_only:
        print(f"服务: {'active' if active else 'inactive'}")
        print(f"出口 IP: {ip or '(取不到)'}   上次记录: {prev_ip or '(无)'}")
        print(f"4924: {'有' if forbidden else '无'}   重连 {reconnects} 次 / READY {readys} 次")
        return 0

    # 状态文件只在拿到 IP 时更新
    if ip:
        save_json(STATE_FILE, {"ip": ip, "ts": now, "prev_ip": prev_ip})

    if hard:
        alert = {
            "since": state.get("alert_since") or now,
            "ts": now,
            "ip": ip,
            "prev_ip": prev_ip,
            "issues": hard,
            "howto": (
                f"开放平台 → 机器人 → 开发设置 → IP 白名单，加 {ip}（旧的留着别删）"
                if ip else "先确认服务器能否上网"
            ),
        }
        prev_alert = load_json(ALERT_FILE, {})
        if not prev_alert:
            alert["since"] = now
        else:
            alert["since"] = prev_alert.get("since") or now
        save_json(ALERT_FILE, alert)
        msg = (
            f"[织梦] 官方通道异常\n"
            + "\n".join(f"· {x}" for x in hard)
            + f"\n当前出口: {ip or '未知'}\n上次记录: {prev_ip or '无'}\n{alert['howto']}"
        )
        print(msg)
        # 只在「新出现的故障」时推一次，避免每 5 分钟轰炸
        if not prev_alert or prev_alert.get("issues") != hard:
            notify(msg)
        return 1

    # 恢复
    if ALERT_FILE.exists():
        old = load_json(ALERT_FILE, {})
        ALERT_FILE.unlink(missing_ok=True)
        dur = max(0, now - int(old.get("since") or now))
        text = f"[织梦] 官方通道已恢复（持续 {dur // 60} 分钟）\n出口: {ip}"
        print(text)
        notify(text)
    else:
        print(f"[ok] 服务 active | 出口 {ip or '?'} | 无 4924")
    return 0


if __name__ == "__main__":
    sys.exit(main())

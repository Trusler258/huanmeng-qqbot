"""
ICANN RDAP 域名 WHOIS 查询
数据源: https://rdap.org/domain/<域名>（自动重定向到注册局 RDAP 服务器）
零依赖，纯 HTTPS + JSON
"""

from __future__ import annotations

import ssl
import json
import urllib.request
import urllib.parse
import re
from typing import Any

RDAP_BASE = "https://rdap.org/domain/"

# ★ v2.3.81b: 没有 RDAP 服务的 TLD（.cn 等）走传统 port-43 WHOIS
#   （CNNIC 不提供 RDAP，rdap.org 对 .cn 一律 404 → 会误报"未注册"）
_CUSTOM_WHOIS = {
    "cn": "whois.cnnic.cn", "com.cn": "whois.cnnic.cn", "net.cn": "whois.cnnic.cn",
    "org.cn": "whois.cnnic.cn", "edu.cn": "whois.cnnic.cn", "gov.cn": "whois.cnnic.cn",
    "top": "whois.nic.top", "xyz": "whois.nic.xyz", "vip": "whois.nic.vip",
    "club": "whois.nic.club", "shop": "whois.nic.shop", "online": "whois.nic.online",
    "site": "whois.nic.site", "work": "whois.nic.work", "icu": "whois.nic.icu",
    "cc": "cc.whois-servers.net", "tv": "tv.whois-servers.net",
}


def _raw_whois(domain: str, server: str, timeout: float = 15.0) -> str:
    """传统 WHOIS（port 43），用于没有 RDAP 的 TLD。返回原始文本，失败返回空串。"""
    import socket
    try:
        with socket.create_connection((server, 43), timeout=timeout) as s:
            s.sendall((domain + "\r\n").encode("ascii"))
            buf = b""
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
    except Exception:
        return ""
    # CNNIC 等中文注册局可能返回 GBK
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return buf.decode(enc)
        except UnicodeDecodeError:
            continue
    return buf.decode("utf-8", "replace")


def _format_raw_whois(domain: str, raw: str) -> str:
    """从 port-43 原始文本里挑关键字段（注册商/时间/NS/状态）。"""
    if not raw.strip():
        return ""
    fields = [
        ("注册商", r"(?:Registrar|Sponsoring Registrar|Registrar Name)\s*[::]\s*(.+)"),
        ("注册时间", r"(?:Registration Time|Creation Date|Created Date|Registered on)\s*[::]\s*(.+)"),
        ("到期时间", r"(?:Expiration Time|Expiry Date|Registry Expiry Date|Expires on)\s*[::]\s*(.+)"),
        ("更新时间", r"(?:Updated Date|Last Modified|Changed)\s*[::]\s*(.+)"),
        ("域名状态", r"(?:Domain Status|Status)\s*[::]\s*(.+)"),
    ]
    lines = [f"域名: {domain}"]
    for label, pat in fields:
        m = re.search(pat, raw, re.I)
        if m:
            lines.append(f"{label}: {m.group(1).strip()[:80]}")
    ns = re.findall(r"(?:Name Server|Nameserver|nserver)\s*[::]\s*(\S+)", raw, re.I)
    if ns:
        lines.append(f"NS: {', '.join(dict.fromkeys(ns))[:120]}")
    lines.append("(数据源: port-43 WHOIS，该后缀无 RDAP 服务)")
    return "\n".join(lines)


def _extract_domain(raw: str) -> str:
    """从 URL/带协议输入中提取裸域名"""
    raw = raw.strip().lower()
    # 去掉协议
    raw = re.sub(r'^https?://', '', raw)
    # 去掉路径/端口
    raw = raw.split('/')[0].split(':')[0]
    # 去掉 www. 前缀
    raw = re.sub(r'^www\.', '', raw)
    return raw.strip()


def _safe_get(d: dict, *keys: str, default: str = "未知") -> str:
    """安全从嵌套 dict 取值"""
    for k in keys:
        if isinstance(d, dict):
            d = d.get(k, {})
        else:
            return default
    return str(d) if d else default


def _format_date(raw: str) -> str:
    """格式化 ISO 日期"""
    if not raw or raw == "未知":
        return "未知"
    try:
        # ISO 8601 → YYYY-MM-DD
        return raw[:10]
    except Exception:
        return raw[:16] if len(raw) >= 10 else raw


def lookup_domain(domain: str) -> str:
    """
    查询域名 WHOIS 信息。
    返回格式化的纯文本字符串。
    """
    domain = _extract_domain(domain)
    if not domain:
        return "请输入有效域名，如 example.com"
    if '.' not in domain:
        return f"'{domain}' 不是有效域名格式，请包含顶级域（如 .com、.xyz）"

    url = f"{RDAP_BASE}{domain}"

    # 创建忽略 SSL 证书验证的 context（某些 RDAP 服务器证书可能过期）
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        # ★ v2.3.81b: RDAP 404/失败 → 该后缀没有 RDAP 时走 port-43 WHOIS 兜底，
        #   否则 .cn 这类会一律误报"未注册"（实测 updream.cn 已注册但 rdap.org 404）
        tld = domain.rsplit(".", 1)[-1] if "." in domain else ""
        server = _CUSTOM_WHOIS.get(tld) or _CUSTOM_WHOIS.get(
            ".".join(domain.rsplit(".", 2)[-2:]) if domain.count(".") >= 2 else "")
        if server:
            raw = _raw_whois(domain, server)
            if raw.strip():
                return _format_raw_whois(domain, raw)
            return f"域名 {domain} 的 WHOIS 服务器 {server} 无响应"
        if e.code == 404:
            return f"域名 {domain} 未注册或 RDAP 无数据"
        return f"查询失败: HTTP {e.code}"
    except Exception as e:
        tld = domain.rsplit(".", 1)[-1] if "." in domain else ""
        server = _CUSTOM_WHOIS.get(tld)
        if server:
            raw = _raw_whois(domain, server)
            if raw.strip():
                return _format_raw_whois(domain, raw)
        return f"查询失败: {e}"

    lines = [f"域名: {domain}"]

    # 注册商
    registrar = _safe_get(data, "entities", 0, "vcardArray", 1, 1, "text")
    if registrar and registrar != "未知":
        lines.append(f"注册商: {registrar}")

    # 状态
    statuses = data.get("status", [])
    if statuses:
        lines.append(f"状态: {', '.join(statuses)}")

    # 事件（注册/到期/修改时间）
    events = data.get("events", [])
    for ev in events:
        action = ev.get("eventAction", "")
        date = _format_date(ev.get("eventDate", ""))
        if action == "registration":
            lines.append(f"注册时间: {date}")
        elif action == "expiration":
            lines.append(f"到期时间: {date}")
        elif action == "last changed":
            lines.append(f"最后修改: {date}")

    # NS 服务器
    nameservers = data.get("nameservers", [])
    if nameservers:
        ns_list = []
        for ns in nameservers:
            name = ns.get("ldhName", ns.get("objectClassName", ""))
            if name:
                ns_list.append(name)
        if ns_list:
            lines.append(f"NS: {', '.join(ns_list)}")

    # DNSSEC
    dnssec = _safe_get(data, "secureDNS", "delegationSigned", default="")
    if dnssec and dnssec != "未知":
        signed = "已签名" if dnssec else "未签名"
        lines.append(f"DNSSEC: {signed}")

    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        print(lookup_domain(sys.argv[1]))
    else:
        print("用法: python whois_lookup.py <域名>")

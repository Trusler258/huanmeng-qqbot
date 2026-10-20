"""QQ 空间（Qzone）只读/评论 客户端 —— 与框架无关的可调用库。

设计目标（2026-10-20）：把逆向接口集中在这一层，插件/脚本随便调；腾讯改接口只改这里。
参考实现：znq19/KiraAI_qzone_plugin（Python）与 Gu-Heping/onebot-qzone（TS）的端点表。

【能力】
- `client.check_cookie()`        探活（返回 uin 或抛异常）
- `client.my_feeds(num)`         拉自己最新说说（含内嵌评论）
- `client.comment(feed_uin, tid, text)`             发一级评论
- `client.reply(feed_uin, tid, cid, cuin, text)`    楼中楼回复（自动带 @ 标记）

【认证】
Cookie 字符串，需含 `uin`（或 `p_uin`，形如 o123456789）、`skey`、`p_skey`。
`g_tk` 由 skey 按 QQ 空间经典算法算出（5381 位移累加 & 0x7fffffff）。

【已知限制（实测/上游一致）】
- PC 端 emotion_cgi_msglist_v6 常返回 -10000，列表优先走 feeds3（本库先试 PC，失败回退）
- 评论只内嵌"最近拉到的那批说说"，更早的拉不到
- cookie 会掉，接口返回 1401 / -3000 视为需要重登
"""
from __future__ import annotations

import json
import re
from typing import Any

import httpx

from core.logger import get_logger

logger = get_logger("qzone")

_BASE = "https://user.qzone.qq.com"
_EMOTION_LIST = f"{_BASE}/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msglist_v6"
_COMMENT_URL = f"{_BASE}/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_re_feeds"
_COMMENT_H5 = "https://h5.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_re_feeds"
_ZONE_LIST = f"{_BASE}/proxy/domain/ic2.qzone.qq.com/cgi-bin/feeds/feeds3_html_more"

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36")

# 需要重登的返回码（上游一致）
_AUTH_CODES = {-3, -100, -3000, -10001, -10006}


def calc_gtk(skey: str) -> int:
    """QQ 空间经典 g_tk 算法（即 gtk2）。"""
    h = 5381
    for ch in skey or "":
        h += (h << 5) + ord(ch)
    return h & 0x7FFFFFFF


def parse_cookie(cookie: str) -> dict:
    """把 cookie 字符串解析成 {uin:int, skey, p_skey, raw:dict}。"""
    raw: dict[str, str] = {}
    for part in (cookie or "").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        raw[k.strip()] = v.strip()
    uin_raw = raw.get("uin") or raw.get("p_uin") or ""
    if not uin_raw.startswith("o"):
        raise RuntimeError("Cookie 缺少合法 uin/p_uin（应形如 o123456789）")
    return {
        "uin": int(uin_raw[1:]),
        "skey": raw.get("skey", ""),
        "p_skey": raw.get("p_skey", ""),
        "raw": raw,
    }


def normalize_tid(tid: str) -> str:
    """规范化 tid：去 /mood/ 前缀、去 .311/.1 数字后缀、feeds3 复合 key 取最后一段。"""
    s = str(tid or "").strip()
    if "/mood/" in s:
        s = s.split("/mood/", 1)[1]
    s = s.split("?", 1)[0]
    if "." in s and s.rsplit(".", 1)[-1].isdigit():
        s = s.rsplit(".", 1)[0]
    if "_" in s:                      # feeds3 复合 key
        s = s.split("_")[-1]
    return s


class QzoneError(RuntimeError):
    """接口错误；auth=True 表示 cookie 失效需重登。"""

    def __init__(self, msg: str, *, auth: bool = False, code: Any = None):
        super().__init__(msg)
        self.auth = auth
        self.code = code


class QzoneClient:
    def __init__(self, cookie: str, *, timeout: float = 20.0):
        c = parse_cookie(cookie)
        self.uin: int = c["uin"]
        self.skey: str = c["skey"]
        self.p_skey: str = c["p_skey"]
        # ★ 2026-10-20：Qzone 接口的 g_tk 应按 **p_skey**（空间域的 bkn）计算；
        #   只有 p_skey 缺失时才回退 skey —— 此前一直用 skey，空间接口会判 -3000「请先登录空间」。
        self.gtk: int = calc_gtk(self.p_skey or self.skey)
        self.timeout = timeout
        self._cookie_header = cookie.strip()

    # ── 基础请求 ──────────────────────────────────────
    def _headers(self, referer: str | None = None) -> dict:
        return {
            "User-Agent": _UA,
            "Cookie": self._cookie_header,
            "Referer": referer or f"{_BASE}/{self.uin}",
            "Origin": _BASE,
        }

    @staticmethod
    def _jsonp_strip(text: str) -> str:
        """去掉 JSONP 外壳（_Callback(...) / _preloadCallback(...)）。"""
        t = (text or "").strip()
        m = re.match(r"^[\w$.]+\s*\((.*)\)\s*;?\s*$", t, re.S)
        return m.group(1) if m else t

    def _parse(self, resp: httpx.Response) -> dict:
        if resp.status_code != 200:
            raise QzoneError(f"HTTP {resp.status_code}")
        try:
            data = json.loads(self._jsonp_strip(resp.text))
        except Exception as e:
            raise QzoneError(f"响应非 JSON：{e}；前 200 字：{(resp.text or '')[:200]}")
        if isinstance(data, dict):
            code = data.get("code", data.get("ret"))
            try:
                code_i = int(code) if code is not None else None
            except Exception:
                code_i = None
            if code_i in _AUTH_CODES:
                raise QzoneError(f"cookie 失效（code={code_i}），请重新获取", auth=True, code=code_i)
        return data if isinstance(data, dict) else {}

    # ── 能力 ──────────────────────────────────────────
    async def check_cookie(self) -> int:
        """拉一次自己的说说列表，能解析即视为可用；返回 uin。"""
        await self.my_feeds(num=1)
        return self.uin

    async def my_feeds(self, num: int = 5) -> list[dict]:
        """自己最新说说（含 commentlist）。先试 PC msglist，失败回退 feeds3。"""
        params = {
            "g_tk": self.gtk, "uin": self.uin, "ftype": 0, "sort": 0,
            "pos": 0, "num": max(1, num), "replynum": 100,
            "callback": "_preloadCallback", "code_version": 1,
            "format": "json", "need_comment": 1, "need_private_comment": 1,
        }
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as c:
            r = await c.get(_EMOTION_LIST, params=params, headers=self._headers())
        data = self._parse(r)
        feeds = _extract_feeds(data)
        if feeds:
            return feeds
        logger.info("PC 列表无数据，回退 feeds3")
        return await self._feeds3(num)

    async def _feeds3(self, num: int = 5) -> list[dict]:
        params = {
            "uin": self.uin, "scope": 0, "view": 1, "filter": "all",
            "flag": 1, "applist": "all", "pagenum": 1,
            "aisortEndTime": 0, "aisortOffset": 0, "aisortBeginTime": 0,
            "begintime": 0, "format": "json", "g_tk": self.gtk,
        }
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as c:
            r = await c.get(_ZONE_LIST, params=params, headers=self._headers())
        data = self._parse(r)
        return _extract_feeds(data)

    async def comment(self, feed_uin: int, tid: str, text: str) -> bool:
        """发一级评论。优先 user 域，失败回退 H5。"""
        tid_n = normalize_tid(tid)
        form = {
            "hostUin": feed_uin,
            "topicId": f"{feed_uin}_{tid_n}",
            "content": text,
            "format": "json",
            "qzreferrer": f"{_BASE}/{feed_uin}/main",
        }
        ok = await self._post_form(_COMMENT_URL, form, referer=f"{_BASE}/{feed_uin}/main")
        if ok:
            return True
        form2 = {
            "topicId": f"{feed_uin}_{tid_n}__1", "uin": self.uin, "hostUin": feed_uin,
            "feedsType": 100, "inCharset": "utf-8", "outCharset": "utf-8",
            "plat": "qzone", "source": "ic", "isSignIn": "", "platformid": 50,
            "format": "fs", "ref": "feeds", "content": text,
            "richval": "", "richtype": "", "private": "0", "paramstr": "1",
            "qzreferrer": f"{_BASE}/{feed_uin}/main",
        }
        return await self._post_form(_COMMENT_H5, form2, referer=f"{_BASE}/{feed_uin}/main")

    async def reply(self, feed_uin: int, tid: str, root_cid: str,
                    root_uin: int, text: str) -> bool:
        """楼中楼回复（锚定主评论）。"""
        tid_n = normalize_tid(tid)
        form = {
            "topicId": f"{feed_uin}_{tid_n}__1", "uin": self.uin, "hostUin": feed_uin,
            "feedsType": 100, "inCharset": "utf-8", "outCharset": "utf-8",
            "plat": "qzone", "source": "ic", "platformid": 52, "format": "fs",
            "ref": "feeds", "content": text,
            "commentId": normalize_tid(root_cid), "commentUin": root_uin,
            "richval": "", "richtype": "", "private": "0", "paramstr": "2",
            "qzreferrer": f"{_BASE}/{self.uin}/main",
        }
        return await self._post_form(_COMMENT_H5, form, referer=f"{_BASE}/")

    async def _post_form(self, url: str, form: dict, *, referer: str) -> bool:
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as c:
            r = await c.post(f"{url}?g_tk={self.gtk}", data=form,
                             headers={**self._headers(referer),
                                      "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})
        data = self._parse(r)
        code = data.get("code", data.get("ret"))
        try:
            code_i = int(code) if code is not None else 0
        except Exception:
            code_i = 0
        ok = code_i == 0 or bool(data.get("data")) or bool(data.get("msglist"))
        if not ok:
            logger.warning("Qzone 写入返回非 0：%s", str(data)[:200])
        return ok


# ── 解析助手（防御式：不同接口字段名不一致）────────────────
def _to_int(v, default: int = 0) -> int:
    """容错转 int：接口的 createTime 有时是「前天15:06」这类相对时间串 / 带 o 前缀的 uin。"""
    if v is None:
        return default
    if isinstance(v, bool):
        return default
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    s = str(v).strip()
    if not s:
        return default
    if s[0] in "oO" and s[1:].isdigit():
        s = s[1:]
    elif s[0] == "-" and s[1:].isdigit():
        s = s[1:]
    elif s.isdigit():
        pass
    else:
        return default
    try:
        return int(s)
    except (TypeError, ValueError):
        return default


def _extract_feeds(data: dict) -> list[dict]:
    """从 PC msglist / feeds3 两种结构里抽统一列表。"""
    items: list = []
    for key in ("msglist", "data", "feedlist"):
        v = data.get(key)
        if isinstance(v, list):
            items = v
            break
        if isinstance(v, dict):
            for k2 in ("msglist", "feedlist", "vFeeds"):
                if isinstance(v.get(k2), list):
                    items = v[k2]
                    break
            if items:
                break
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        tid = it.get("tid") or it.get("cellid") or it.get("id") or ""
        if not tid:
            continue
        comments = []
        cl = it.get("commentlist") or it.get("comments") or []
        if isinstance(cl, list):
            for c in cl:
                if not isinstance(c, dict):
                    continue
                comments.append({
                    "cid": str(c.get("tid") or c.get("commentid") or c.get("id") or ""),
                    "uin": _to_int(c.get("uin") or c.get("commentuin")),
                    "nick": str(c.get("nickname") or c.get("name") or ""),
                    "content": str(c.get("content") or ""),
                    "time": _to_int(c.get("createTime") or c.get("time")),
                })
        out.append({
            "tid": str(tid),
            "uin": _to_int(it.get("uin")),
            "content": str(it.get("content") or ""),
            "time": _to_int(it.get("created_time") or it.get("createTime")),
            "comments": comments,
        })
    return out


def mention_uin_pattern(uin: int) -> re.Pattern:
    """QQ 空间原生 @ 标记：@{uin:123456,nick:xxx,who:1,auto:1}"""
    return re.compile(r"@\{uin:\s*%d\b[^}]*\}" % int(uin))

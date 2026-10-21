"""黑话查证服务（explain_slang 的底层实现）。

流程：本地词库 → 萌娘百科词条探测 → DeepSeek 联网搜索 → 结论落「待审队列」。

★ 为什么结论只进 pending 不直接进 slang.json：
  联网摘要常混营销号说法，直接入库会污染词库（一次错就永久错）。
  pending 需要人工（/~slang ok|no）或面板确认后才合并。

★ 为什么不用向量检索：梗是「词形 + 别名」匹配，消息里出现那两个字就能命中，
  向量只在「用户没说词、只描述意思」时有价值，群里是低频场景。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import httpx

from core.logger import get_logger

logger = get_logger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_SLANG_JSON = _DATA_DIR / "slang.json"
PENDING_PATH = _DATA_DIR / "slang_pending.json"

PENDING_CAP = 200          # 待审队列上限，超了丢最旧的
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# 词源类站点优先：百科/词典优先，营销号盘点文不算证据
_SOURCE_HINT = "网络用语 词源 意思 梗"
_GOOD_SITES = ("baike.baidu.com", "zh.wikipedia.org", "moegirl.org.cn",
               "dict.revised.moe.edu.tw", "zh.wiktionary.org", "hanyu.baidu.com")


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M")


# ══════════════════════════════════════════════════════════
# 本地词库
# ══════════════════════════════════════════════════════════

def _load_slang() -> dict:
    try:
        data = json.loads(_SLANG_JSON.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning("读取 slang.json 失败: %s", e)
        return {}


def find_local(term: str) -> dict | None:
    """本地词库精确/别名查找（忽略大小写）。"""
    low = (term or "").strip().lower()
    if not low:
        return None
    for e in _load_slang().get("entries") or []:
        names = [str(e.get("name") or "").lower()]
        names += [str(a).lower() for a in (e.get("alias") or [])]
        if low in names:
            return e
    return None


# ══════════════════════════════════════════════════════════
# 外部查证
# ══════════════════════════════════════════════════════════

async def moegirl_probe(term: str) -> dict:
    """萌娘百科只放行 opensearch（rest/页面被 Cloudflare 挡 403）。

    返回 {"exists": bool, "title": str, "url": str}。
    只能证明「这词有条目」，拿不到正文释义 —— 释义仍要靠搜索。
    """
    out = {"exists": False, "title": "", "url": ""}
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=True,
                                     headers={"User-Agent": _UA}) as c:
            r = await c.get(
                "https://zh.moegirl.org.cn/api.php",
                params={"action": "opensearch", "search": term,
                        "format": "json", "limit": 1},
            )
            if r.status_code != 200:
                return out
            arr = r.json()
            # 格式：[词, [标题...], [摘要...], [URL...]]
            if isinstance(arr, list) and len(arr) >= 4 and arr[1]:
                out["exists"] = True
                out["title"] = str(arr[1][0])
                out["url"] = str(arr[3][0]) if arr[3] else ""
    except Exception as e:
        logger.debug("萌娘探测失败（忽略）: %s", e)
    return out


def _authority_of(item: dict) -> int:
    url = (item.get("url") or "").lower()
    site = (item.get("site_name") or "").lower()
    try:
        auto = int(item.get("authority") or 0)
    except Exception:
        auto = 0
    if any(g in url for g in _GOOD_SITES):
        return max(auto, 3)
    if "baidu" in site or "wiki" in site or "百科" in site or "词典" in site:
        return max(auto, 2)
    return auto


async def web_probe(term: str) -> dict:
    """联网查词源。返回 {"text": 摘要, "hits": [...], "trusted": 可信源条数}。"""
    from services.ds_tools import ds_search
    query = f"「{term}」{_SOURCE_HINT}"
    try:
        results = await ds_search(query, count=6)
    except Exception as e:
        logger.warning("查黑话联网搜索失败: %s", e)
        return {"text": "", "hits": [], "trusted": 0}

    hits: list[dict] = []
    trusted = 0
    for r in results or []:
        title = (r.get("title") or "").strip()
        summary = (r.get("summary") or r.get("snippet") or "").strip()
        if not title and not summary:
            continue
        auth = _authority_of(r)
        if auth >= 3:          # 只认百科/词典类，论坛帖不算"独立权威源"
            trusted += 1
        hits.append({
            "title": title,
            "url": (r.get("url") or "").strip(),
            "site": (r.get("site_name") or "").strip(),
            "summary": summary[:220],
            "authority": auth,
        })
    # 可信源排前面给模型看
    hits.sort(key=lambda x: -x["authority"])
    text = "\n".join(
        f"[{i}] {h['title']}（{h['site'] or '未知站点'}）"
        + (f"\n    {h['summary']}" if h["summary"] else "")
        for i, h in enumerate(hits[:6], 1)
    )
    return {"text": text, "hits": hits, "trusted": trusted}


def _normalize(term: str) -> str:
    t = (term or "").strip()
    t = t.strip("「」『』\"'“”[]【】()（） ")
    return t


def _reject_reason(term: str) -> str:
    """哪些词根本不该进词库（不要浪费一次搜索）。"""
    t = _normalize(term)
    if not t:
        return "词为空"
    if len(t) < 2:
        return (f"「{t}」是单字，正常句子里到处都是，收进去会逢句必触发"
                "（例：给→gay 音译，但「我给你发」里也是给）。只收多字组合，如「给佬」")
    if len(t) > 16:
        return "太长了，不像黑话（黑话一般 2~8 字）"
    if re.fullmatch(r"[\d\s\W]+", t):
        return "没有有效文字"
    return ""


async def explain(term: str) -> dict:
    """完整查证：本地 → 萌娘 → 联网。返回结构化结论（不入库）。"""
    raw = _normalize(term)
    local = find_local(raw)
    if local:
        return {"status": "local", "term": raw, "entry": local}

    reason = _reject_reason(raw)
    if reason:
        return {"status": "reject", "term": raw, "reason": reason}

    import asyncio
    moe, web = await asyncio.gather(
        moegirl_probe(raw), web_probe(raw), return_exceptions=True)
    moe = moe if isinstance(moe, dict) else {"exists": False}
    web = web if isinstance(web, dict) else {"text": "", "hits": [], "trusted": 0}

    # 可信度：萌娘有条目 + 至少两个不同站点提到 → high
    # ★ 梗的源头常在论坛/贴吧，单帖不可信（同一个帖里都能自相矛盾），
    #   所以不数「权威站点」，数「互相独立的站点」。
    sites = {(h.get("site") or h.get("url") or "").strip()
             for h in (web.get("hits") or [])}
    sites.discard("")
    sources = 0
    if moe.get("exists"):
        sources += 1
    if len(sites) >= 2:
        sources += 1
    conf = "high" if sources >= 2 else ("mid" if sources == 1 else "low")

    return {
        "status": "found" if web.get("text") else "noresult",
        "term": raw,
        "moegirl": moe,
        "web": web,
        "sources": sources,
        "confidence": conf,
    }


# ══════════════════════════════════════════════════════════
# 待审队列
# ══════════════════════════════════════════════════════════

def _load_pending() -> dict:
    try:
        d = json.loads(PENDING_PATH.read_text(encoding="utf-8"))
        if isinstance(d, dict) and isinstance(d.get("items"), list):
            return d
    except Exception:
        pass
    return {"items": []}


def _save_pending(d: dict) -> bool:
    try:
        PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
        PENDING_PATH.write_text(json.dumps(d, ensure_ascii=False, indent=2),
                                encoding="utf-8")
        return True
    except Exception as e:
        logger.error("写 slang_pending.json 失败: %s", e, exc_info=True)
        return False


def add_pending(term: str, meaning: str, *, source: str = "",
                confidence: str = "low", evidence: str = "",
                circles: list[str] | None = None, risk: str = "safe") -> tuple[bool, str]:
    """把一条结论放进待审队列（同词覆盖，避免重复堆积）。"""
    t = _normalize(term)
    m = (meaning or "").strip()
    if not t or not m:
        return False, "词或释义为空"
    if find_local(t):
        return False, f"「{t}」词库里已有"
    _bad = _reject_reason(t)
    if _bad:
        return False, _bad

    d = _load_pending()
    items = [x for x in d["items"] if str(x.get("name", "")).lower() != t.lower()]
    items.append({
        "name": t,
        "meaning": m[:200],
        "alias": [],
        "circles": circles or ["通用"],
        "risk": risk if risk in ("safe", "mild") else "safe",
        "source": (source or "explain")[:40],
        "confidence": confidence,
        "evidence": (evidence or "")[:1200],
        "at": _now(),
        "status": "pending",
    })
    d["items"] = items[-PENDING_CAP:]
    if not _save_pending(d):
        return False, "写入待审队列失败"
    logger.info("黑话待审 +1: %s（可信度 %s）", t, confidence)
    return True, f"「{t}」已进待审队列"


def list_pending(limit: int = 10) -> list[dict]:
    return _load_pending()["items"][-max(1, limit):]


def pending_count() -> int:
    return len(_load_pending()["items"])


def _pop(index_no: int) -> tuple[dict | None, dict]:
    """按「/~slang 列表里的序号」（1 起，从最新往前）取出一条并移除。"""
    d = _load_pending()
    items = d["items"]
    if not items:
        return None, d
    # 展示顺序是倒序（最新在前），序号 1 = items[-1]
    rev = list(reversed(items))
    if not (1 <= index_no <= len(rev)):
        return None, d
    target = rev[index_no - 1]
    d["items"] = [x for x in items if x is not target]
    return target, d


def approve(index_no: int) -> tuple[bool, str]:
    """把待审第 N 条合并进 slang.json。"""
    from services import llm as _llm
    item, d = _pop(index_no)
    if not item:
        return False, f"没有第 {index_no} 条待审词"
    try:
        raw = json.loads(_SLANG_JSON.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raw = {}
    except Exception as e:
        logger.error("读 slang.json 失败，拒绝合并以免覆盖: %s", e, exc_info=True)
        return False, f"读词库失败: {e}"

    entries = raw.get("entries") or []
    if any(str(e.get("name", "")).lower() == str(item["name"]).lower() for e in entries):
        _save_pending(d)
        return False, f"「{item['name']}」已在词库中，已从待审移除"

    entries.append({
        "name": item["name"],
        "alias": item.get("alias") or [],
        "circles": item.get("circles") or ["通用"],
        "risk": item.get("risk", "safe"),
        "meaning": item.get("meaning", ""),
        "example": "",
        "source": item.get("source") or "explain",
        "last_seen": time.strftime("%Y-%m-%d"),
        "added_by": "explain",
    })
    raw["entries"] = entries
    raw.setdefault("_meta", {})["updated_at"] = time.strftime("%Y-%m-%d")
    try:
        _SLANG_JSON.write_text(json.dumps(raw, ensure_ascii=False, indent=2),
                               encoding="utf-8")
    except Exception as e:
        logger.error("写 slang.json 失败: %s", e, exc_info=True)
        return False, f"写词库失败: {e}"

    # 让 llm 的两级缓存立刻失效（mtime 变化本来也会失效，这里保险）
    _llm._slang_json_cache = None
    _llm._slang_terms_cache = None
    _save_pending(d)
    logger.info("黑话入库（人工审核）: %s", item["name"])
    return True, f"已收录「{item['name']}」进词库（现有 {len(entries)} 条）"


def reject(index_no: int) -> tuple[bool, str]:
    item, d = _pop(index_no)
    if not item:
        return False, f"没有第 {index_no} 条待审词"
    _save_pending(d)
    return True, f"已丢弃「{item['name']}」"


def format_pending(limit: int = 10) -> str:
    """给用户/模型看的纯文本列表（QQ 不渲染 Markdown）。"""
    items = list_pending(limit)
    if not items:
        return "待审黑话：空。用 /~slang 查词时，查到的结论会先放这里等你确认。"
    rev = list(reversed(items))
    lines = [f"待审黑话（{len(items)} 条，最新在前）"]
    for i, it in enumerate(rev, 1):
        conf = {"high": "可信", "mid": "存疑", "low": "不可靠"}.get(
            it.get("confidence", "low"), "?")
        lines.append(f"· {i}. {it['name']}  {it['meaning'][:40]}  [{conf}]")
    lines.append("收录：/~slang ok <序号>　　丢弃：/~slang no <序号>")
    return "\n".join(lines)


def format_explain(res: dict) -> str:
    """把 explain() 的结果转成给模型读的纯文本。"""
    status = res.get("status")
    term = res.get("term", "")

    if status == "local":
        e = res.get("entry") or {}
        tags = []
        if e.get("circles"):
            tags.append("/".join(str(c) for c in e["circles"]))
        if e.get("risk") == "mild":
            tags.append("慎用")
        tail = f"（{'，'.join(tags)}）" if tags else ""
        return f"「{term}」词库里已有：{e.get('meaning', '')}{tail}\n（拿去理解即可，不用跟对方解释）"

    if status == "reject":
        return f"「{term}」不适合当黑话收录：{res.get('reason', '')}"

    if status == "noresult":
        return f"「{term}」没查到可靠释义（本地无、萌娘无、联网也没找到词源）。别硬猜，直接问对方「这啥梗」。"

    moe = res.get("moegirl") or {}
    web = res.get("web") or {}
    conf = {"high": "多来源说法一致（萌娘词条 + 至少两个站点）",
            "mid": "只有一个来源，可能是二手说法",
            "low": "没找到能互证的来源，只能参考"}.get(res.get("confidence", "low"), "")

    parts = [f"「{term}」查询结果（{conf}）"]
    if moe.get("exists"):
        parts.append(f"萌娘百科有条目：{moe.get('title')} {moe.get('url')}")
    else:
        parts.append("萌娘百科没有这个词条")
    hits = web.get("hits") or []
    if hits:
        lines = []
        for i, h in enumerate(hits[:3], 1):
            sm = re.sub(r"\s+", " ", str(h.get("summary") or "")).strip()[:150]
            title = re.split(r"\s+-\s+|#", str(h.get("title") or ""))[0].strip()[:24]
            site = str(h.get("site") or "未知站点")[:14]
            lines.append(f"[{i}] {title}（{site}）{sm}")
        parts.append("联网检索（节选）：\n" + "\n".join(lines))
    parts.append("以上只是资料：用你自己的话概括一句就行，别复述来源；"
                 "若来源互相矛盾就说「不确定」，别编。")
    return "\n".join(parts)

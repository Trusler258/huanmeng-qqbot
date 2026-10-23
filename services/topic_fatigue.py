"""话题疲劳追踪：同一话题被反复拉扯时，让 bot 表现出不耐烦。

背景（2026-10-22 群聊实测）：一个群友整晚绕着「你是我婆娘 / 你主人不喜欢你 /
你爸爸」来回挑衅 20 多次，bot 每次都认真回 2~3 条，还带「你今晚绕了七八个弯，
这招我早看穿」这种元话语 —— 越认真越被动，观感很别扭。

这里做的是**统计**，判断交给提示词：命中越多次，注入的提示越强，
模型据此决定是「提一句别重复了」→「明显不耐烦」→「直接不聊 / 用 ignore_user 静默」。

设计取舍：
- 按 chat_id 追踪（群里此刻在聊什么），不按 user_id —— 多人一起追问同一话题也该算
- 相似度用字符 bigram 的 Jaccard，中文短句够用，不需要向量
- TTL 30 分钟：超过 30 分钟没有新消息 → 清零，第二天不会带着旧账
- 话题切换时不是清零而是衰减（hits - 2），避免来回横跳导致永远攒不起来
"""
import re
import time

TTL_SECONDS = 30 * 60      # 30 分钟
SIM_THRESHOLD = 0.30       # bigram 重合率
MIN_LEN = 4                # 太短的消息不参与（"嗯""好""？"）
MAX_CHATS = 200            # 防内存膨胀

# chat_id -> {"key": 代表文本, "hits": 次数, "first": 起始, "last": 最近, "top_user": 喊得最多的}
_state: dict = {}


def _clean(text: str) -> str:
    t = re.sub(r"\[CQ:[^\]]*\]", "", text or "")
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", t)


def _bigrams(text: str) -> set:
    t = _clean(text)
    if len(t) < 2:
        return {t} if t else set()
    return {t[i:i + 2] for i in range(len(t) - 1)}


def _similarity(a: str, b: str) -> float:
    A, B = _bigrams(a), _bigrams(b)
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)


def note(chat_id: int, text: str, user_id: int = 0) -> tuple[int, int, int]:
    """记录一条消息，返回 (level, hits, span_minutes)。

    level: 0 正常 / 1 略烦 / 2 明显烦 / 3 已经很烦（可以拒聊或静默）
    """
    if len(_clean(text)) < MIN_LEN:
        return _peek(chat_id)

    now = time.time()
    st = _state.get(int(chat_id))
    if st and (now - st["last"] > TTL_SECONDS):
        st = None

    if not st:
        if len(_state) >= MAX_CHATS:
            # 淘汰最久没动的
            oldest = min(_state.items(), key=lambda kv: kv[1]["last"])[0]
            _state.pop(oldest, None)
        _state[int(chat_id)] = {"key": text, "hits": 1, "first": now,
                                "last": now, "top_user": int(user_id)}
        return 0, 1, 0

    st["last"] = now
    if _similarity(st["key"], text) >= SIM_THRESHOLD:
        st["hits"] += 1
        if user_id:
            st["top_user"] = int(user_id)
    else:
        # 换话题：衰减而不是清零，来回横跳也能攒
        st["hits"] = max(0, st["hits"] - 2)
        if st["hits"] <= 0:
            st["hits"] = 1
            st["first"] = now
            st["key"] = text
        else:
            st["key"] = text
        st["top_user"] = int(user_id)

    return _peek(chat_id)


def _peek(chat_id: int) -> tuple[int, int, int]:
    st = _state.get(int(chat_id))
    if not st:
        return 0, 0, 0
    now = time.time()
    if now - st["last"] > TTL_SECONDS:
        _state.pop(int(chat_id), None)
        return 0, 0, 0
    span = int((st["last"] - st["first"]) // 60)
    hits = st["hits"]
    if hits >= 9 or (hits >= 7 and span >= 15):
        level = 3
    elif hits >= 6:
        level = 2
    elif hits >= 4:
        level = 1
    else:
        level = 0
    return level, hits, span


def advice(level: int, hits: int, span: int) -> str:
    """给模型的提示（注入上下文，不是发给用户的）。"""
    if level <= 0:
        return ""
    if level == 1:
        return (f"这话题已经来回 {hits} 次、{span} 分钟了。你有点腻，可以随口提一句"
                f"「这个刚说过吧」，但别翻脸。")
    if level == 2:
        return (f"同一话题第 {hits} 次、持续 {span} 分钟，你已经明显不耐烦了。"
                f"用一句话打断或换个话题，**不要**再认真重复一遍你的立场；"
                f"也不要分析「你绕来绕去」这种话。")
    return (f"这个话题纠缠了 {hits} 次、{span} 分钟，你已经很烦。"
            f"可以直接说「不聊这个了」「换个话题」或者撒娇抱怨一句；"
            f"对方还要继续逼的话，就调用 ignore_user 静默他 10 分钟。")


def reset(chat_id: int) -> None:
    _state.pop(int(chat_id), None)


def snapshot(chat_id: int) -> dict:
    return dict(_state.get(int(chat_id)) or {})

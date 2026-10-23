"""发言节奏：决定这一轮**长成什么样**，让人看不出固定长度。

背景（2026-10-23 线上日志实测）：
- 决策模型判的长度档位 short 出现 80/86 次（93%），回复恒为一两句；
- 事后「短回复合并」又把仅剩的 2 句拼成 1 条（当日 37 次）；
- 两条叠加 → 群里观感变成「永远一条、还只有一句话」。

真人聊天不是「每轮定长」，而是**有起伏的**：
  有时甩一句「嗯」；有时连发三条碎话；有时一条长消息把事讲完；
  也常常先甩一句短的起头、再补一句长的解释。

所以这里不判「字数档位」，而是抽「形态」(shape)：

  one    一条说完（可以只有两个字，也可以是一句三十来字的话）
  burst  2~3 条连着发（想到哪说到哪，每条都是完整的话）
  para   一条长消息（讲事/解释，几十到一百来字）
  mixed  一短一长两条（先起头，再补充）

形态 = 基础概率（看对方消息长短/是否提问）+ 上下文加权 + 反向惯性 + 抖动。

**反向惯性**是这里最关键的一环：真人不会连着五轮都一个长度。
- 上一轮已经是同一形态 → 该形态概率打 4.5 折
- 最近三轮总字数都很少 → 这一轮强行不许「一条短话」
- 上一轮刚发了一大段 → 这一轮倾向短

文案放 config/prompts.toml 的 [rhythm]（提示词不进 .py），本模块只负责算。
"""
import logging
import random
import re
import time

logger = logging.getLogger(__name__)

TTL_SECONDS = 30 * 60      # 30 分钟没消息 → 节奏状态清零
HISTORY = 6                # 记住最近几轮的形态
MAX_CHATS = 200

SHAPES = ("one", "burst", "para", "mixed")

# ── 基础概率：看对方这条消息是什么性质 ─────────────────────────
# tiny  极短消息（"嗯""1""wq"）→ 多半就回一句
# chat  普通闲聊 → 最常见是连发两条短话
# ask   提问/较长的消息 → 倾向一条讲完
# heavy 需要工具/深度解释 → 必须能展开
_BASE = {
    "tiny":  {"one": 0.55, "burst": 0.28, "para": 0.04, "mixed": 0.13},
    "chat":  {"one": 0.24, "burst": 0.42, "para": 0.12, "mixed": 0.22},
    "ask":   {"one": 0.12, "burst": 0.30, "para": 0.34, "mixed": 0.24},
    "heavy": {"one": 0.05, "burst": 0.20, "para": 0.55, "mixed": 0.20},
}

# chat_id -> {"shapes": [...], "chars": [...], "ts": 最近一次}
_state: dict = {}


def _clean(text: str) -> str:
    t = re.sub(r"\[CQ:[^\]]*\]", "", text or "")
    return t.strip()


def _reset_if_stale(chat_id: int) -> dict:
    now = time.time()
    st = _state.get(int(chat_id))
    if not st or now - st.get("ts", 0) > TTL_SECONDS:
        st = {"shapes": [], "chars": [], "ts": now}
        _state[int(chat_id)] = st
        if len(_state) > MAX_CHATS:
            oldest = sorted(_state.items(), key=lambda kv: kv[1].get("ts", 0))
            for k, _ in oldest[:len(_state) - MAX_CHATS]:
                _state.pop(k, None)
    st["ts"] = now
    return st


def _classify(msg: str, needs=None) -> str:
    """把对方这条消息归到 tiny / chat / ask / heavy。"""
    text = _clean(msg)
    n = len(text)
    needs = needs or set()
    if "tools" in needs or "deep" in needs or "search" in needs or "code" in needs:
        return "heavy"
    if n <= 6 and "?" not in text and "？" not in text:
        return "tiny"
    if n > 30 or "?" in text or "？" in text:
        return "ask"
    return "chat"


def _weigh(base: dict, hist_shapes: list, hist_chars: list) -> dict:
    """上下文加权 + 反向惯性。返回归一化后的概率表。"""
    w = dict(base)

    # ① 同形态连续 → 打折（真人不会连着一模一样的节奏）
    if hist_shapes:
        last = hist_shapes[-1]
        if last in w:
            w[last] *= 0.45

    # ② 最近三轮都太短 → 这轮不许再是一句短话，倾向讲长/连发
    if len(hist_chars) >= 3 and sum(hist_chars[-3:]) < 18:
        w["one"] *= 0.20
        w["para"] *= 2.60
        w["burst"] *= 1.30

    # ③ 上一轮刚发了一大段 → 这轮收着点
    if hist_chars and hist_chars[-1] >= 110:
        w["para"] *= 0.40
        w["one"] *= 1.40

    # ④ 连续两条 burst → 第三条容易腻，降低一点
    if len(hist_shapes) >= 2 and hist_shapes[-1] == hist_shapes[-2] == "burst":
        w["burst"] *= 0.60
        w["mixed"] *= 1.50

    total = sum(w.values()) or 1.0
    return {k: v / total for k, v in w.items()}


def _pick(prob: dict) -> str:
    keys = list(prob.keys())
    vals = [max(prob[k], 1e-6) for k in keys]
    return random.choices(keys, weights=vals, k=1)[0]


def _sentences_for(shape: str) -> int:
    if shape == "one" or shape == "para":
        return 1
    if shape == "mixed":
        return 2
    # burst：2 或 3 条，偶尔两条就够
    return random.choice((2, 2, 3))


def plan(chat_id: int, msg: str, needs=None, *, rng=None) -> dict:
    """规划本轮形态。

    返回 {"shape", "sentences", "hint", "kind", "anti_short"}：
      shape      one / burst / para / mixed
      sentences  建议条数上限（one/para=1，mixed=2，burst=2~3）
      hint       要注入 system 的节奏提示（来自 prompts.toml，已填好条数）
      anti_short 是否因为"连续太短"被强行纠正
    """
    st = _reset_if_stale(chat_id)
    kind = _classify(msg, needs)
    prob = _weigh(_BASE.get(kind, _BASE["chat"]), st["shapes"], st["chars"])
    shape = _pick(prob) if rng is None else rng.choices(
        list(prob.keys()), weights=[max(v, 1e-6) for v in prob.values()], k=1)[0]

    anti_short = (len(st["chars"]) >= 3 and sum(st["chars"][-3:]) < 18
                  and shape in ("one", "burst"))
    n = _sentences_for(shape)

    try:
        from utils.prompts import render
        hint = render("rhythm", shape, n=n, default="")
    except Exception as e:
        logger.warning("取节奏提示失败: %s", e)
        hint = ""

    logger.info("节奏规划: kind=%s shape=%s 条数=%d%s probs=%s", kind, shape, n,
                " (连续偏短已纠正)" if anti_short else "",
                {k: round(v, 3) for k, v in prob.items()})
    return {"shape": shape, "sentences": n, "hint": hint,
            "kind": kind, "anti_short": anti_short}


def record(chat_id: int, shape: str, total_chars: int) -> None:
    """发送后回填：这一轮实际是什么形态、多少字。"""
    try:
        st = _reset_if_stale(chat_id)
        st["shapes"].append(shape)
        st["chars"].append(int(total_chars or 0))
        del st["shapes"][:-HISTORY]
        del st["chars"][:-HISTORY]
    except Exception as e:
        logger.debug("节奏回填失败（忽略）: %s", e)

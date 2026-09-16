"""
按人用户画像系统
- JSON 文件存储: data/user_profiles.json
- 每次发言提取信息（增量更新）
- 生成回复时注入用户画像到上下文
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from core.logger import get_logger

logger = get_logger("profile")

_DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "user_profiles.json"

# 默认新用户画像
_DEFAULT_PROFILE: dict[str, Any] = {
    "name": "",
    "tags": [],          # 身份/特征标签: ["学生", "程序员", "夜猫子"]
    "interests": [],     # 兴趣: ["游戏", "编程", "音乐"]
    "dislikes": [],      # 雷点
    "tone": "",          # 偏好语气: 幽默/温柔/直接
    "events": [],        # 重要事件: [{"date":"2026-10-24","text":"生日"}]
    "status": "",        # 当前状态: "备考中" / "刚买了新电脑"
    "facts": [],         # 事实: ["女朋友叫小红", "喜欢熬夜"]
    "last_seen": 0,      # 最后活跃时间戳
    "message_count": 0,  # 累计发言数
}

# 敏感信息过滤正则
_SENSITIVE_RE = re.compile(
    r'(密码|password|token|secret|api.?key|手机号|身份证|银行卡)',
    re.IGNORECASE,
)

# ══════════════════════════════════════════════════════════
#  v2.3.33 画像防污染
#  背景：上线后 52 个用户攒了 334 条 facts，其中 20% 是疑问句
#  （"我是谁""我的好感度是多少""我是不是女的"），且每次都注入提示词。
#  写入层原来只有一道很窄的过滤（查询/帮我/域名/搜索/什么/怎么），
#  漏掉 谁/多少/吗/呢/咋/是不是/如何 —— 本模块统一收口，写入与读取都过一遍。
# ══════════════════════════════════════════════════════════

# 疑问特征：出现任一即判定为「提问」而非「事实陈述」
_QUESTION_RE = re.compile(
    r'[?？]|谁|啥|什么|哪|吗|呢|多少|怎么|咋|为何|为什么|是不是|有没有|如何|几位|几点'
)

# 半句/被截断的特征：以连词开头，或含句内标点。
# 实测脏数据里有一类是「因此忽然灵光一现」「我，一个是服务器提供方」——
# 既不是疑问句也不是完整事实，是从长句里截下来的碎片，同样没有价值。
_FRAGMENT_RE = re.compile(
    r'^(因此|所以|但是|然后|而且|因为|如果|虽然|不过|并且|于是|接着|另外|还有|其实)'
    r'|[，,；;]'
)

# facts 上限（超出丢最老的）。每条 <20 字，12 条约 240 字，可接受。
_MAX_FACTS = 12
# 注入提示词时取最近几条
_INJECT_FACTS = 4
# 允许的身份标签白名单（_quick_extract 的 identities + 少量已知长期特征）
_TAG_WHITELIST = {
    "大学生", "高中生", "初中生", "中职生", "小学生",
    "程序员", "上班族", "夜猫子", "学生",
    # 长期特征（由 _quick_extract 之外的来源补充，保守保留）
    "公网", "本地服务器", "非云服务器", "furry", "狼", "猫娘",
    # 赛事/职业（用户群里的稳定身份）
    "MC玩家", "音游玩家",
}
# 允许的兴趣白名单。
# 为什么也要白名单：LLM 会把「当天聊的话题」当兴趣存下来，实测攒出
# 「443端口」「80端口」「DC-DC」「ICP备案」「boost升压电路」这种一次性话题。
# 画像要的是**长期**兴趣，一次性的东西由 context 负责，不该进画像。
_INTEREST_WHITELIST = {
    "游戏", "编程", "音乐", "动漫", "科技", "运动",
    "摄影", "阅读", "美食", "旅行", "影视", "绘画", "手工", "写作",
}
# 允许的雷点白名单
_DISLIKE_WHITELIST = {"政治", "恐怖", "剧透", "脏话", "鬼故事", "恐怖片"}
# 允许的语气词白名单（LLM 爱编「假设性提问」这种不是语气的词）
_TONE_WHITELIST = {"幽默", "温柔", "直接", "可爱", "简洁", "活泼", "正经", "方言化"}
# 无意义的 status（LLM 硬凑出来的）
_STATUS_BLOCK = {"未知", "事实", "无", "正常", "不明", "null", "None"}


def _looks_like_question(text: str) -> bool:
    """判断一段文本是不是「提问」而非「关于用户的事实」。

    用于两个地方：
      1. 写入时拦截（新数据不脏）
      2. 读取注入时过滤（历史脏数据不注入）
    第二处是必须的 —— 已经存进 JSON 的垃圾没人清，光改写入层救不了老用户。
    """
    return bool(_QUESTION_RE.search(str(text)))


def _clean_facts(facts) -> list[str]:
    """清洗 facts：去疑问句、去半句碎片、去过长、去空"""
    out = []
    for f in facts or []:
        s = str(f).strip()
        if not s or len(s) > 20:
            continue
        if _looks_like_question(s):
            continue
        if _FRAGMENT_RE.search(s):
            continue
        out.append(s)
    return out


# 代词 / 疑问词，不能当昵称
_NAME_BLOCK = {
    "你", "我", "他", "她", "它", "您", "咱",
    "你们", "我们", "他们", "她们", "大家", "自己",
    "这个", "那个", "一个", "什么", "谁", "哪个", "哪里",
}


def _valid_name(name: str) -> bool:
    """校验昵称是否可信。

    原来只挡了「谁/什么/你/我」这种，**漏了 bot 自己的名字** ——
    用户调侃「我是幻梦」「幻梦是给…」就会被抽成昵称。
    实测 52 个用户里 9 个的昵称是「幻梦」（bot 名），只有 12 个是对的。
    """
    n = str(name).strip()
    if not (2 <= len(n) <= 12):
        return False
    if n in _NAME_BLOCK or _looks_like_question(n):
        return False
    try:
        from core.config import get_bot_name
        bn = (get_bot_name() or "").strip().lower()
        if bn:
            cand = {bn, bn + "bot", bn.replace("bot", "").strip(), "@" + bn}
            if n.lower() in cand:
                return False
    except Exception:
        pass
    return True



def _load_all() -> dict[str, dict]:
    """加载全部用户画像"""
    if not _DATA_FILE.exists():
        return {}
    try:
        return json.loads(_DATA_FILE.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("画像文件损坏，重置")
        return {}


def _save_all(data: dict[str, dict]):
    """保存全部用户画像"""
    _DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    _DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_profile(user_id: int) -> dict[str, Any]:
    """获取用户画像，不存在返回默认"""
    uid = str(user_id)
    data = _load_all()
    if uid not in data:
        data[uid] = dict(_DEFAULT_PROFILE)
    return data[uid]


def update_profile(user_id: int, updates: dict[str, Any]):
    """增量更新用户画像"""
    uid = str(user_id)
    data = _load_all()
    if uid not in data:
        data[uid] = dict(_DEFAULT_PROFILE)
    p = data[uid]

    # ── 集合语义字段：顺序无所谓，排序去重 ──
    for key in ("tags", "interests", "dislikes"):
        if key in updates and isinstance(updates[key], list):
            incoming = [str(x).strip() for x in updates[key] if str(x).strip()]
            if key == "tags":
                # tags 只允许白名单身份词。原来 LLM/关键词抽到什么都存，
                # 攒出「赴汤蹈火」「upload说」「bought sex toys」这种非身份标签。
                incoming = [t for t in incoming if t in _TAG_WHITELIST]
            existing = set(p.get(key, []))
            existing.update(incoming)
            p[key] = sorted(existing)

    # ── facts：**保持时间序**，超上限丢最老的 ──
    #   原来是 sorted()。按字典序排会永久把前几条老噪音钉在开头，
    #   而 build_profile_text 只取前 N 条 → 新提取到的有效信息永远进不去。
    if "facts" in updates and isinstance(updates["facts"], list):
        merged = [str(x).strip() for x in (p.get("facts") or []) if str(x).strip()]
        for f in _clean_facts(updates["facts"]):
            if f not in merged:
                merged.append(f)
        p["facts"] = merged[-_MAX_FACTS:]

    # ── 标量字段：逐个校验，不再「非空就写」 ──
    if updates.get("name") and _valid_name(updates["name"]):
        p["name"] = str(updates["name"]).strip()
    if updates.get("tone"):
        t = str(updates["tone"]).strip()
        if t in _TONE_WHITELIST:      # 挡掉「假设性提问」这种 LLM 编的语气
            p["tone"] = t
    if updates.get("status"):
        s = str(updates["status"]).strip()
        if s and s not in _STATUS_BLOCK and len(s) <= 20:
            p["status"] = s

    # events 追加
    if "events" in updates and isinstance(updates["events"], list):
        p.setdefault("events", []).extend(updates["events"])

    p["last_seen"] = int(time.time())
    # 每次调用代表一次发言（pipeline 每收到一条消息调一次）
    p["message_count"] = p.get("message_count", 0) + 1

    _save_all(data)


def _is_sensitive(msg: str) -> bool:
    """检查是否包含敏感信息，避免存到画像"""
    return bool(_SENSITIVE_RE.search(msg))


async def extract_from_message(
    user_id: int, sender_name: str, msg: str,
) -> dict[str, Any] | None:
    """
    用 cheap LLM 从发言提取用户信息。
    返回增量更新 dict，无收获返回 None。
    """
    if not msg or len(msg) < 8 or _is_sensitive(msg):
        return None

    # 简单关键词提取先（零成本），兜底再调 LLM
    quick = _quick_extract(msg)
    if quick:
        return quick

    # LLM 提取：只对≥20字的长消息调用，避免浪费
    if len(msg) < 20 or not re.search(r'[\u4e00-\u9fa5a-zA-Z]{4}', msg):
        return None

    try:
        from services.llm import call_llm
        from core.config import get_config
        cfg = get_config()
        prompt = (
            f"从发言中提取用户信息，返回JSON。键: name,tags,interests,tone,status,facts\n"
            f"只提取明确提及的信息，不要臆测。无信息返回{{}}\n"
            f"发言: {msg[:200]}"
        )
        result = await call_llm(cfg.cheap_model, [{"role": "user", "content": prompt}],
                                max_tokens=150, temperature=0.1, timeout=8.0)
        if result:
            return _parse_llm_result(result)
    except Exception:
        pass
    return None


def _quick_extract(msg: str) -> dict[str, Any] | None:
    """零成本关键词快速提取，有收获直接返回，不做 LLM"""
    result: dict[str, Any] = {}

    # 姓名提取（严格：只取 2-3 字中文名，前后有分隔）
    _NOISE_NAMES = {"谁", "什么", "哪个", "哪里", "怎么样", "为啥", "你", "我", "他", "它", "你们", "我们", "他们",
                     "这个", "那个", "一个", "两个", "真的", "假的", "可以", "没问题", "不知道",
                     "采购", "销售", "学生", "同学", "你好", "好的", "嗯", "哦", "啊", "哈", "是", "不是",
                     "习惯了", "你同学", "最强小学生", "fv", "神", "god", "admin", "root"}
    m = re.search(r'(?:^|[，。！？\s])我(?:叫|是)([\u4e00-\u9fa5]{2,3})(?:[，。！？\s]|$)', msg)
    if not m:
        m = re.search(r'(?:^|[，。！？\s])I\'?m\s+([a-zA-Z]{2,8})(?:[，。！？\s]|$)', msg, re.IGNORECASE)
    if m:
        name = m.group(1)
        if name not in _NOISE_NAMES and _valid_name(name):
            result["name"] = name

    # 身份标签
    identities = {
        "大学生": r'(?:大学|大一|大二|大三|大四|本科)',
        "高中生": r'(?:高中|高二|高三|高考)',
        "初中生": r'(?:初中|初三|中考)',
        "中职生": r'(?:中职|职校|技校)',
        "程序员": r'(?:程序员|码农|写代码|编程|开发)',
        "上班族": r'(?:上班|工作|公司|老板)',
        "夜猫子": r'(?:熬夜|通宵|凌晨|睡不着)',
        "学生": r'(?:作业|考试|开学|老师|成绩|复习|备考)',
    }
    for tag, pat in identities.items():
        if re.search(pat, msg):
            result.setdefault("tags", []).append(tag)

    # 兴趣
    interests_map = {
        "游戏": r'(?:打游戏|游戏|王者|原神|LOL|吃鸡|Minecraft|我的世界|MC|音游|ADOFAI)',
        "编程": r'(?:编程|写代码|Python|Java|C\+\+|前端|后端|bug)',
        "音乐": r'(?:音乐|听歌|唱歌|网易云|QQ音乐|钢琴|吉他)',
        "动漫": r'(?:动漫|番|二次元|cos)',
        "科技": r'(?:科技|数码|手机|电脑|硬件|显卡)',
        "运动": r'(?:跑步|健身|打球|篮球|足球)',
    }
    for interest, pat in interests_map.items():
        if re.search(pat, msg):
            result.setdefault("interests", []).append(interest)

    # 状态
    status_map = {
        "备考中": r'(?:备考|考试|复习|冲刺)',
        "减肥中": r'(?:减肥|节食|健身|跑步)',
        "找工作中": r'(?:找工作|面试|招聘|简历)',
        "摸鱼中": r'(?:摸鱼|划水|无聊|不想上班)',
        "生气中": r'(?:气死|烦|火大|想骂人)',
        "开心": r'(?:开心|高兴|哈哈|笑死|乐)',
        "难过": r'(?:难过|伤心|哭|emo|抑郁)',
    }
    for status, pat in status_map.items():
        if re.search(pat, msg):
            result["status"] = status
            break

    # 事实
    #  ★ v2.3.33: 这里**不再提取 facts**。
    #  原来是 `我(叫|是)X` 的开放匹配，会把角色扮演当成事实：
    #  实测清洗后仍有 250 条，绝大多数是「我是你主人」「我就是神」「我是fv」
    #  「我可是茂密」这类玩梗，真正客观事实不到 10 条。
    #  这类碎片还缺上下文（同一句"我是主人"在不同语境含义相反），
    #  bot 读了会误判用户身份，收益为负。
    #  facts 改为只由 LLM 路径产生 —— 它能看整句语义，且 prompt 明确
    #  「只提取明确提及的信息，不要臆测」，再由 _clean_facts 兜底。
    facts = []

    # 偏好语气（要求明确的交流偏好表达）
    tone_map = {
        "幽默": r'(?:搞笑|幽默|笑话|梗|整活)',
        "温柔": r'(?:温柔点|摸摸我|抱抱我|安慰一下)',
        "直接": r'(?:说重点|别废话|直接点|一句话说清楚|简洁点)',
    }
    for tone, pat in tone_map.items():
        if re.search(pat, msg):
            result["tone"] = tone
            break

    if facts:
        result["facts"] = facts

    # 雷点/厌恶
    dislikes = []
    dislike_map = {
        "政治": r'政治',
        "恐怖": r'恐怖|吓人|鬼故事',
        "剧透": r'剧透|剧透',
        "脏话": r'脏话|骂人',
    }
    for dislike, pat in dislike_map.items():
        if re.search(pat, msg):
            dislikes.append(dislike)
    if dislikes:
        result["dislikes"] = dislikes

    return result if len(result) > 0 else None


def _parse_llm_result(raw: str) -> dict[str, Any] | None:
    """解析 LLM 返回的 JSON（所有字段都过校验，不信 LLM 的自由发挥）"""
    raw = raw.strip()
    # 去掉可能的 markdown 代码块
    m = re.search(r'\{[^{}]*\}', raw, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
        result: dict[str, Any] = {}

        if data.get("name") and _valid_name(data["name"]):
            result["name"] = str(data["name"]).strip()
        if data.get("tone") and str(data["tone"]).strip() in _TONE_WHITELIST:
            result["tone"] = str(data["tone"]).strip()
        if data.get("status"):
            s = str(data["status"]).strip()
            if s not in _STATUS_BLOCK and len(s) <= 20:
                result["status"] = s

        for k, allow in (("tags", _TAG_WHITELIST), ("interests", _INTEREST_WHITELIST)):
            if isinstance(data.get(k), list):
                vals = [str(x).strip() for x in data[k] if str(x).strip()]
                vals = [v for v in vals if v in allow]
                if vals:
                    result[k] = vals

        if isinstance(data.get("facts"), list):
            f = _clean_facts(data["facts"])
            if f:
                result["facts"] = f

        return result if result else None
    except json.JSONDecodeError:
        return None


def build_profile_text(user_id: int) -> str:
    """生成画像文本，用于注入 LLM 上下文。

    ★ 这里**必须再过滤一遍**，不能只靠写入层：
      已经落盘的脏数据（疑问句 facts、bot 名昵称、一次性话题当兴趣）
      没人清也不会自动消失，只在写入层拦截救不了老用户。
      读取层过滤的好处是「存量数据立刻变干净，且不用改历史文件」。
    """
    p = get_profile(user_id)
    parts = []

    # 昵称：过校验（挡掉「幻梦」这种 = bot 自己的名字）
    name = str(p.get("name") or "").strip()
    if name and _valid_name(name):
        parts.append(f"昵称: {name}")

    tags = [t for t in (p.get("tags") or []) if t in _TAG_WHITELIST]
    if tags:
        parts.append(f"标签: {', '.join(tags)}")

    interests = [i for i in (p.get("interests") or []) if i in _INTEREST_WHITELIST]
    if interests:
        parts.append(f"兴趣: {', '.join(interests[:5])}")

    if p.get("tone") and p["tone"] in _TONE_WHITELIST:
        parts.append(f"喜欢语气: {p['tone']}")

    status = str(p.get("status") or "").strip()
    if status and status not in _STATUS_BLOCK and len(status) <= 20:
        parts.append(f"当前状态: {status}")

    # facts：清洗后取**最近的** N 条（保持时间序才有意义）
    facts = _clean_facts(p.get("facts"))
    if facts:
        parts.append(f"已知: {'; '.join(facts[-_INJECT_FACTS:])}")

    dislikes = [d for d in (p.get("dislikes") or []) if d in _DISLIKE_WHITELIST]
    if dislikes:
        parts.append(f"避开: {', '.join(dislikes[:3])}")

    if p.get("events"):
        ev = [e.get("text", "") for e in p["events"][-3:] if isinstance(e, dict) and e.get("text")]
        if ev:
            parts.append(f"事件: {'; '.join(ev)}")

    return "\n".join(parts) if parts else ""


# ════════════════════════════════════════════════════════════
# 测试接口
# ════════════════════════════════════════════════════════════

def _test_profile_apis():
    """内部测试: 存储/读取/更新/注入"""
    import tempfile, os
    global _DATA_FILE
    old_path = _DATA_FILE
    tmp = Path(tempfile.mktemp(suffix=".json"))
    _DATA_FILE = tmp  # type: ignore

    try:
        # 1. 新用户返回默认
        p = get_profile(12345)
        assert p["name"] == "", f"默认 name 应为空: {p['name']}"
        assert p["tags"] == [], f"默认 tags 应为空: {p['tags']}"

        # 2. 更新画像
        update_profile(12345, {"name": "小明", "tags": ["学生"], "interests": ["游戏"]})
        p2 = get_profile(12345)
        assert p2["name"] == "小明"
        assert "学生" in p2["tags"]
        assert "游戏" in p2["interests"]

        # 3. 增量更新（不覆盖已有）
        update_profile(12345, {"tags": ["夜猫子"], "tone": "幽默"})
        p3 = get_profile(12345)
        assert "学生" in p3["tags"], "增量应保留旧tag"
        assert "夜猫子" in p3["tags"], "增量应添加新tag"
        assert p3["tone"] == "幽默"

        # 4. 画像文本生成
        txt = build_profile_text(12345)
        assert "小明" in txt
        assert "学生" in txt
        assert "游戏" in txt

        # 5. 快速关键词提取
        r = _quick_extract("我是小明，现在在备考，烦死了")
        assert r and r.get("name") == "小明"
        assert "学生" in r.get("tags", [])
        assert r.get("status") == "备考中"

        r2 = _quick_extract("今天写代码写了一天，好累")
        assert "编程" in r2.get("interests", [])

        r3 = _quick_extract("哈哈这个笑话笑死我了")
        assert r3.get("tone") == "幽默"

        # 6. 敏感信息过滤
        r4 = _quick_extract("我的密码是123456")
        assert r4 is None, "密码相关内容不应提取"

        # 7. 空消息
        r5 = _quick_extract("嗯")
        assert r5 is None

        # 8. 多用户隔离
        update_profile(99999, {"name": "小红"})
        p_a = get_profile(12345)
        p_b = get_profile(99999)
        assert p_a["name"] == "小明"
        assert p_b["name"] == "小红"

        # 9. events
        update_profile(12345, {"events": [{"date": "2026-10-24", "text": "生日"}]})
        p_e = get_profile(12345)
        assert len(p_e["events"]) == 1
        assert "生日" in build_profile_text(12345)

        # 10. LLM 结果解析
        llm_out = '{"name":"大黄","tags":["程序员"],"interests":["游戏"]}'
        parsed = _parse_llm_result(llm_out)
        assert parsed and parsed["name"] == "大黄"
        assert "程序员" in parsed["tags"]

        # 11. 坏 JSON
        assert _parse_llm_result("乱七八糟") is None
        assert _parse_llm_result("") is None

    finally:
        _DATA_FILE = old_path  # type: ignore
        if tmp.exists():
            tmp.unlink()

    print("✅ 11/11 测试通过")
    return True


def _test_pipeline_integration():
    """模拟 pipeline 集成: extract → update → inject"""
    from unittest.mock import AsyncMock, patch

    # 模拟 pipeline 调用
    user_id = 11111
    msg = "我是小明，我喜欢打游戏和写代码，最近在备考"

    # Step 1: 提取
    extracted = _quick_extract(msg)
    assert extracted is not None
    update_profile(user_id, extracted)

    # Step 2: 注入
    profile_text = build_profile_text(user_id)
    assert "小明" in profile_text
    assert "游戏" in profile_text
    assert "备考" in profile_text

    # Step 3: 连续多轮
    msg2 = "我不喜欢政治话题"
    extracted2 = _quick_extract(msg2)
    if extracted2:
        update_profile(user_id, extracted2)
    p = get_profile(user_id)
    assert "小明" in p.get("name", "")

    print("✅ Pipeline 集成测试通过")


if __name__ == "__main__":
    _test_profile_apis()
    _test_pipeline_integration()

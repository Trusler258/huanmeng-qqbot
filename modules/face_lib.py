"""
表情库 — 关键词匹配本地表情图片
用法: get_face("开心") → (filepath, CQ码)
LLM 输出 [FACE:开心] → pipeline 自动发图

支持两种素材命名（可混用）：
  1. 中文名：「眯眼开心.jpg」→ 取末 2 汉字作情绪词「开心」
  2. 英文标签：「cat_shizuku_thinking_01.png」→ 经 _TAG_CN 映射成「思考」

v2.1.21: 新增英文标签支持（132 张英文命名素材的接入）。
"""

from __future__ import annotations

import os
import random
import re
from pathlib import Path
from typing import Optional

from core.logger import get_logger

logger = get_logger("face_lib")

# 关键词 → 文件名映射（由文件名自动生成）
_KEYWORD_MAP: dict[str, list[str]] = {}
_initialized = False

# ── 英文标签 → 中文情绪词（v2.1.21）──────────────────────────
# 素材命名格式：<标签>_<编号>.png（如 sleeping_026.png、hug_cat_089.png）
# **元组第一个词是主词**（写进提示词的可用列表），后面的是匹配别名。
#
# ⚠️ 历史教训：最初按原始标注表的英文字段直译（thinking→思考、cold→无语…），
# 但那份标注与实际画面不符率约 60%（「疑惑」17 张里只有 4 张真是疑惑）。
# 现在是**逐张看图重标 + 重命名**后的结果，标签与实际画面对应。
# 校对全表见 data/faces_tag_corrected.md
_TAG_CN: dict[str, tuple[str, ...]] = {
    # ── 情绪 ──
    "happy":      ("开心", "微笑", "高兴"),
    "smirk":      ("坏笑", "得意", "偷笑"),
    "smug":       ("得意", "得瑟", "傲娇"),
    "shy":        ("害羞", "脸红", "不好意思"),
    "cry":        ("大哭", "哭", "流泪"),
    "sad":        ("委屈", "难过", "伤心"),
    "down":       ("失落", "沮丧", "低落"),
    "angry":      ("生气", "不满", "恼怒"),
    "upset":      ("不满", "嘟嘴", "不开心"),
    "panic":      ("慌张", "惊慌", "手忙脚乱"),
    "shout":      ("大喊", "喊", "激动"),
    "surprise":   ("惊讶", "震惊", "吃惊"),
    "confused":   ("疑惑", "困惑", "不解"),
    "bored":      ("无聊", "没事干", "闲"),
    "tired":      ("疲惫", "累了", "叹气"),
    "miss":       ("不舍", "舍不得", "留恋"),
    "sorry":      ("道歉", "对不起", "抱歉"),
    "congrats":   ("祝贺", "恭喜", "庆祝"),
    "cheer":      ("加油", "打气", "鼓励"),
    "refuse":     ("拒绝", "不行", "不"),
    "hungry":     ("饿", "想吃", "肚子饿"),
    "close_eyes": ("闭眼", "安静", "平和"),
    # ── 状态 / 动作 ──
    "sleeping":   ("睡觉", "睡了", "晚安"),
    "sleepy":     ("困", "打瞌睡", "犯困"),
    "yawn":       ("打哈欠", "困了", "哈欠"),
    "daze":       ("发呆", "放空", "走神"),
    "greet":      ("打招呼", "你好", "问候"),
    "wave":       ("挥手", "再见", "拜拜"),
    "wink":       ("眨眼", "放电", "抛媚眼"),
    "hug_cat":    ("抱猫", "抱抱", "蹭蹭"),
    "hug_toy":    ("抱玩偶", "抱娃娃", "抱着"),
    "hug_cake":   ("抱蛋糕", "拿蛋糕", "甜点"),
    "hug_heart":  ("抱爱心", "比心", "爱心"),
    "hug_flower": ("抱花", "送花", "鲜花"),
    "hug_thing":  ("抱东西", "抱着", "拿东西"),
    "hold_tray":  ("端东西", "端茶", "服务"),
    "eat":        ("吃东西", "吃饭", "干饭"),
    "eat_icecream": ("吃冰淇淋", "冰淇淋", "甜食"),
    "drink":      ("喝饮料", "喝水", "喝"),
    "tease_cat":  ("逗猫", "逗猫棒", "玩"),
    "salute":     ("敬礼", "收到", "遵命"),
    "ok":         ("没问题", "好的", "OK"),
    "hop":        ("蹦跳", "跳跃", "兴奋"),
    "peek":       ("偷看", "探头", "张望"),
    "look":       ("张望", "看", "张望"),
    "stand":      ("站着", "呆站", "立着"),
    "sit":        ("坐着", "坐下", "坐"),
    "lie":        ("趴着", "躺着", "趴"),
}

# 英文标签提取：从 "cat_shizuku_wink_holdcat_76" 里剥出 "wink_holdcat"
_TAG_TAIL = re.compile(r"_\d+$")       # 去末尾编号 _76
# 已知的「角色/系列」前缀段——只剥这些，不做贪婪剥离。
# 教训：曾用「只要这段不在 _TAG_CN 里就当前缀丢掉」，结果 thumbs_up 的 thumbs
# 被当成前缀吃掉，只剩 up 查不到映射 → 整张图丢失。
_TAG_PREFIXES = {"cat", "shizuku", "nya", "neko", "girl", "sticker"}


def _init():
    """扫描 data/faces/，建「关键词 → 文件」映射（幂等，只跑一次）"""
    global _initialized
    if _initialized:
        return
    faces_dir = Path(__file__).resolve().parent.parent / "data" / "faces"
    if not faces_dir.is_dir():
        logger.warning("表情目录不存在: %s", faces_dir)
        _initialized = True
        return
    n = 0
    for f in sorted(faces_dir.iterdir()):
        if f.suffix.lower() not in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
            continue
        n += 1
        stem = f.stem
        # ① 文件名切词（中文靠切词，英文靠完整名）
        for kw in _extract_keywords(stem):
            _KEYWORD_MAP.setdefault(kw, []).append(str(f))
        # ② v2.1.21: 英文标签 → 中文情绪词，让 LLM 能用中文调用
        for cn in _tags_from_stem(stem):
            _KEYWORD_MAP.setdefault(cn, []).append(str(f))
    logger.info("表情库加载: %d 关键词, %d 文件", len(_KEYWORD_MAP), n)
    _initialized = True


def _extract_keywords(name: str) -> list[str]:
    """从文件名提取关键词（中文走切词，英文标签走映射表）"""
    kw = [name]  # 完整文件名也是一个关键词
    # 中文：拆出2-4字的短语
    han = [c for c in name if '\u4e00' <= c <= '\u9fff']
    if han:
        for i in range(len(name)):
            for j in (2, 3, 4):
                if i + j <= len(name):
                    kw.append(name[i:i + j])
    return kw


def _tag_from_stem(stem: str) -> str:
    """从英文文件名 stem 里剥出标签部分

    cat_shizuku_wink_holdcat_76 → wink_holdcat
    cat_shizuku_thumbs_up_78    → thumbs_up      （thumbs 不是前缀，不能吃）
    眯眼开心                    → 空（中文名不走这里）
    """
    if not re.search(r"[a-zA-Z]", stem):
        return ""
    s = _TAG_TAIL.sub("", stem.lower())
    parts = s.split("_")
    # 只剥开头的**已知角色前缀**（cat/shizuku/...），保护 thumbs_up 这类标签
    while len(parts) > 1 and parts[0] in _TAG_PREFIXES:
        parts.pop(0)
    return "_".join(parts)


def _tags_from_stem(stem: str) -> list[str]:
    """取出该文件对应的中文情绪词（英文标签经映射，中文名取末2汉字）

    每个文件**只返回一个主情绪词**：标签命中的第一个中文词。
    理由：132 张素材里很多是「wink_holdcat」这类组合动作，若把每个子标签的
    全部同义词都塞进去（眨眼/放电/抱猫/抱着猫），提示词会臃肿到没人看，
    且同一张图挂在 5 个词下会让 LLM 选用过于随机。
    组合标签优先取整词（眨眼抱猫），其次按顺序取第一个命中的子标签。
    """
    tag = _tag_from_stem(stem)
    if tag:
        if tag in _TAG_CN:
            return [_TAG_CN[tag][0]]
        for part in tag.split("_"):
            if part in _TAG_CN:
                return [_TAG_CN[part][0]]
        return []
    han = [c for c in stem if '\u4e00' <= c <= '\u9fff']
    if len(han) >= 2:
        return ["".join(han[-2:])]
    return []


def get_face(keyword: str) -> Optional[str]:
    """根据关键词获取表情的绝对路径，找到返回首个匹配"""
    _init()
    keyword = keyword.strip()
    # 精确匹配优先
    if keyword in _KEYWORD_MAP:
        return random.choice(_KEYWORD_MAP[keyword])
    # 模糊匹配：包含关键词
    for kw, files in _KEYWORD_MAP.items():
        if keyword in kw or kw in keyword:
            return random.choice(files)
    return None


def make_cq(filepath: str, sub_type: int = 1) -> str:
    """生成表情 CQ 码。

    v2.1.22: 默认带 `sub_type=1`，让 QQ 按「**动画表情**」样式渲染，
    而不是普通图片（占满屏幕、点开看大图）。

    NapCat 实证（读 napcat.mjs 源码 + WS 实测）：
      - `sendMsg.data.sub_type` 被原样透传给 `createValidSendPicElement(context, path, summary, subType)`
      - 该值最终写进 picElement.picSubType，并进入 extBizInfo.pic.bizType
      - NapCat 自己解析收到的图时就是靠它区分：
            picSubType === 0 ? "[图片]" : "[动画表情]"
      - CQ 正则 `[CQ:(\\w+)((,\\w+=[^,\\]]*)*)]` 允许任意 `\\w+=` 属性 → `sub_type` 可达

    ⚠️ 该值为 0/1 时只影响**客户端渲染样式**（气泡内小图 vs 大图），
    不改变消息段类型，仍然是图片，好友/群聊都生效。

    ⚠️ 路径拼接坑：Linux 绝对路径 /root/x.png 直接拼 `file:///` 会得到
    `file:////root/x.png`（4 斜杠），NapCat 剥掉 `file://` 后剩下 `//root/...`
    → ENOENT 找不到文件。必须先 lstrip("/") 再拼（与 sender.build_local_image_cq 一致）。
    """
    normalized = str(filepath).replace("\\", "/").lstrip("/")
    base = f"[CQ:image,file=file:///{normalized}"
    if sub_type:
        base += f",sub_type={sub_type}"
    return base + "]"


def list_keywords() -> list[str]:
    """列出所有一级关键词（完整文件名）"""
    _init()
    return sorted(set(
        k for k in _KEYWORD_MAP if len(k) >= 3 and all('\u4e00' <= c <= '\u9fff' for c in k)
    ))[:30]


def get_face_keywords() -> list[str]:
    """提取「情绪词」关键词列表，供提示词动态展示可用表情。

    v2.1.18 背景：文件名形如「眯眼开心.jpg」「捂嘴害羞.jpg」（修饰 + 情绪），
    取末尾 2 个汉字就能拿到 LLM 真正想表达的情绪词（开心/害羞/坏笑…）。
    比 list_keywords() 的切词碎片（'举手欢'/'低头失'）可读得多。
    提示词用这个动态生成，用户换成自己的表情包后自动跟随，不用改提示词。

    v2.1.21：素材换成英文标签命名后（cat_shizuku_thinking_01.png），
    汉字提取会全空 → 改走 `_tags_from_stem()`，英文标签经 _TAG_CN 翻译成中文情绪词。
    两种命名可混用，结果自动合并去重。
    """
    _init()
    from pathlib import Path as _P
    faces_dir = _P(__file__).resolve().parent.parent / "data" / "faces"
    words: list[str] = []
    if not faces_dir.is_dir():
        return words
    for f in sorted(faces_dir.iterdir()):
        if f.suffix.lower() not in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
            continue
        for w in _tags_from_stem(f.stem):
            if w not in words:
                words.append(w)
    return words


def face_keywords_hint() -> str:
    """生成提示词用的「可用表情」一行（无表情时返回空串）"""
    ws = get_face_keywords()
    return "/".join(ws) if ws else ""

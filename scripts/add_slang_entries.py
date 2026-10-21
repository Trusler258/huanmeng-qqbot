"""向 data/slang.json 追加「性取向谐音词族」条目（用户提供的「给 / 拉」）。

用法：python scripts/add_slang_entries.py [--write]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
P = ROOT / "data" / "slang.json"

NEW = [
    {
        "name": "给佬", "alias": ["给捞", "基佬"], "circles": ["通用"], "risk": "mild",
        "meaning": "gay 的音译（给= gay），配上「佬」的戏谑说法。多用于玩笑调侃，"
                   "被这么叫的人可能觉得被冒犯，别拿来骂人",
        "example": "这也太给佬了", "source": "用户补充", "added_by": "manual",
    },
    {
        "name": "小给", "alias": ["给给", "男给给"], "circles": ["通用"], "risk": "mild",
        "meaning": "对年轻 gay 的调侃称呼，叠词「给给」偏亲昵，正式说法是「给男」",
        "example": "他是小给", "source": "用户补充", "added_by": "manual",
    },
    {
        "name": "给达", "alias": ["gaydar"], "circles": ["通用"], "risk": "mild",
        "meaning": "gaydar 的音译，指「一眼看出对方是不是 gay」的直觉",
        "example": "我给他达响了", "source": "用户补充", "added_by": "manual",
    },
    {
        "name": "拉拉", "alias": ["拉子"], "circles": ["通用"], "risk": "mild",
        "meaning": "lesbian 的音译，指女同性恋（「拉子」偏港台用法）",
        "example": "她俩是拉拉", "source": "用户补充", "added_by": "manual",
    },
    {
        "name": "蕾丝边", "alias": [], "circles": ["通用"], "risk": "mild",
        "meaning": "lesbian 的另一个音译变体，同「拉拉」；注意别和「蕾丝裙」混了",
        "example": "", "source": "用户补充", "added_by": "manual",
    },
    {
        "name": "百合", "alias": [], "circles": ["通用", "ACG"], "risk": "safe",
        "meaning": "ACG 语境指女性之间的恋爱关系（源自日本），是作品类型词，"
                   "不完全等同于现实里的 les",
        "example": "这本是百合向的", "source": "用户补充", "added_by": "manual",
    },
]


def main() -> int:
    raw = json.loads(P.read_text(encoding="utf-8"))
    ents = raw.get("entries") or []
    exist = {str(e.get("name", "")).lower() for e in ents}
    today = time.strftime("%Y-%m-%d")
    added, skipped = [], []
    for e in NEW:
        if e["name"].lower() in exist:
            skipped.append(e["name"])
            continue
        e = dict(e, last_seen=today)
        ents.append(e)
        added.append(e["name"])
    print("新增:", added or "无", "| 跳过(已存在):", skipped or "无")

    if "--write" in sys.argv:
        raw["entries"] = ents
        raw.setdefault("_meta", {})["updated_at"] = today
        P.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        print("已写入，总条数", len(ents))
    else:
        print("（预览模式，加 --write 才落盘）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

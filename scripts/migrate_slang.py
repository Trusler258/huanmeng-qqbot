"""把 data/skills/15_slang.md 迁移为结构化 data/slang.json（一次性，可重跑）。

用法：python scripts/migrate_slang.py [--write]
- 不带 --write 只预览统计与前 5 条
- 带 --write 写入 data/slang.json（保留原 md 不动）
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MD = ROOT / "data" / "skills" / "15_slang.md"
OUT = ROOT / "data" / "slang.json"

# 分组 → 圈子标签（用于消歧；"通用"= 跨圈）
GROUP_CIRCLES: dict[str, list[str]] = {
    "缩写/字母梗": ["通用"],
    "语气/状态词": ["通用"],
    "游戏圈泛化（QQ 群高频率）": ["游戏"],
    "MC 圈黑话（幻梦服务的主要人群）": ["MC"],
    "社交/吹水": ["通用"],
}

# 带攻击/涉黄风险的词 → mild（允许理解，不鼓励主动使用）
MILD_WORDS = {
    "出生", "畜生", "小丑", "舔狗", "破防", "寄", "逆天", "傻逼", "牛马",
    "社死", "抽象", "阴阳", "阴阳怪气", "搞黄色", "开车", "瑟瑟", "擦边",
}


def parse_md(text: str) -> tuple[list[dict], list[str]]:
    entries: list[dict] = []
    group = ""
    skipped: list[str] = []
    for raw in text.split("\n"):
        line = raw.strip()
        if line.startswith("## "):
            group = line[3:].strip()
            continue
        if not line.startswith("- "):
            continue
        head = line[2:]
        sep = "：" if "：" in head else (":" if ":" in head else "")
        if not sep:
            skipped.append(line)
            continue
        word_part, meaning = head.split(sep, 1)
        meaning = meaning.strip()
        aliases = [p.strip().strip("`*「」\"'") for p in word_part.split("/")]
        aliases = [a for a in aliases if a]
        if not aliases:
            skipped.append(line)
            continue
        name, rest = aliases[0], aliases[1:]
        # 释义里的「例：xxx」拆成 example
        example = ""
        m = re.search(r"[（(]?例[：:]\s*(.+?)[）)]?$", meaning)
        if m:
            example = m.group(1).strip()
            # 释义里别再重复一段例句
            meaning = meaning[:m.start()].rstrip(" ，,。;；")
        circles = GROUP_CIRCLES.get(group, ["通用"])
        entries.append({
            "name": name,
            "alias": rest,
            "circles": circles,
            "risk": "mild" if name.lower() in MILD_WORDS else "safe",
            "meaning": meaning,
            "example": example,
            "source": group or "未分组",
            "last_seen": date.today().isoformat(),
            "added_by": "migrated",
        })
    return entries, skipped


def main() -> int:
    text = MD.read_text(encoding="utf-8")
    entries, skipped = parse_md(text)
    data = {
        "_meta": {
            "version": 1,
            "updated_at": date.today().isoformat(),
            "source": "data/slang.json（唯一真相源；原 15_slang.md 仅作人读备份）",
            "fields": {
                "name": "主词（触发判定用它和 alias）",
                "alias": "别名，同义，命中任一个即视为该条",
                "circles": "圈子标签，注入时展示给模型做消歧（不硬过滤）",
                "risk": "safe / mild（mild=骂人或擦边，只理解不主动用）",
                "meaning": "释义",
                "example": "例句，可为空",
                "source": "原分组/出处",
                "last_seen": "最近一次在群里出现（半衰期用）",
                "added_by": "migrated / manual / learn",
            },
        },
        "entries": entries,
    }
    circles_stat: dict[str, int] = {}
    for e in entries:
        for c in e["circles"]:
            circles_stat[c] = circles_stat.get(c, 0) + 1
    print(f"解析 {len(entries)} 条，跳过 {len(skipped)} 行")
    print("圈子分布:", circles_stat)
    print("mild 条数:", sum(1 for e in entries if e["risk"] == "mild"))
    for e in entries[:5]:
        print("  样例:", json.dumps(e, ensure_ascii=False))
    if skipped:
        print("跳过样例:", skipped[:3])

    if "--write" in sys.argv:
        if MD.exists():
            bak = MD.with_suffix(".md.bak")
            if not bak.exists():
                bak.write_bytes(MD.read_bytes())
                print("已备份 md →", bak.name)
        OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print("已写入", OUT, f"({OUT.stat().st_size} 字节)")
    else:
        print("（预览模式，加 --write 才写文件）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

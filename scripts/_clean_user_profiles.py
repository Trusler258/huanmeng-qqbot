# -*- coding: utf-8 -*-
"""用户画像清洗：把历史脏数据按新规则清一遍。

用法:
    python3 scripts/_clean_user_profiles.py            # 只预览，不动文件
    python3 scripts/_clean_user_profiles.py --write    # 实际写回（先自动备份）

清洗项（与 core/user_profile.py 的校验规则一致）:
    name       → _valid_name（挡 bot 名/代词/疑问词）
    tags       → 白名单（身份词）
    interests  → 白名单（长期兴趣，挡掉「443端口」这类一次性话题）
    tone       → 白名单（挡掉「假设性提问」这类 LLM 编的语气）
    status     → 去「未知/事实/无」这类无意义值
    facts      → _clean_facts（去疑问句/半句碎片/过长），保时间序，限上限
    dislikes   → 白名单
"""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, '/root/bot' if Path('/root/bot').exists() else str(Path(__file__).resolve().parent.parent))

from core.user_profile import (  # noqa: E402
    _clean_facts, _valid_name, _MAX_FACTS,
    _TAG_WHITELIST, _INTEREST_WHITELIST, _TONE_WHITELIST,
    _DISLIKE_WHITELIST, _STATUS_BLOCK, _DEFAULT_PROFILE,
)

DATA = Path(__file__).resolve().parent.parent / "data" / "user_profiles.json"


def clean_one(p: dict, keep_facts: bool = False) -> tuple[dict, dict]:
    """清洗单个用户，返回 (新画像, 变化统计)

    keep_facts=False（默认）时 facts 整体清空，理由：
      过完 _clean_facts 后仍有 250 条，绝大多数是「我是你主人」「我就是神」
      「我是fv」「我可是茂密」这类玩梗碎片，真正客观事实不到 10 条。
      而这些碎片每轮发言都会注入提示词、干扰 bot 对用户身份的判断 ——
      空着比塞着噪音好，清掉后由新代码从零积累干净数据。
    """
    q = json.loads(json.dumps(p))     # 深拷贝
    stat = {"name": 0, "tags": 0, "interests": 0, "tone": 0,
            "status": 0, "facts": 0, "dislikes": 0}

    name = str(q.get("name") or "").strip()
    if name and not _valid_name(name):
        q["name"] = ""
        stat["name"] = 1

    for key, allow in (("tags", _TAG_WHITELIST), ("interests", _INTEREST_WHITELIST),
                       ("dislikes", _DISLIKE_WHITELIST)):
        old = q.get(key) or []
        new = [x for x in old if x in allow]
        if len(new) != len(old):
            stat[key] = len(old) - len(new)
        q[key] = sorted(set(new))

    if q.get("tone") and q["tone"] not in _TONE_WHITELIST:
        q["tone"] = ""
        stat["tone"] = 1

    s = str(q.get("status") or "").strip()
    if s and (s in _STATUS_BLOCK or len(s) > 20):
        q["status"] = ""
        stat["status"] = 1

    old_facts = q.get("facts") or []
    new_facts = _clean_facts(old_facts)[-_MAX_FACTS:] if keep_facts else []
    stat["facts"] = len(old_facts) - len(new_facts)
    q["facts"] = new_facts

    return q, stat


def is_empty(p: dict) -> bool:
    """除 last_seen/message_count 外没有任何有效信息"""
    for k in ("name", "tags", "interests", "tone", "status", "facts", "events", "dislikes"):
        v = p.get(k)
        if v:
            return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="实际写回（默认只预览）")
    ap.add_argument("--keep-facts", action="store_true",
                    help="保留清洗后的 facts（默认整体清空，见 clean_one 说明）")
    args = ap.parse_args()

    data = json.loads(DATA.read_text(encoding="utf-8"))
    print("读取 %s" % DATA)
    print("用户数: %d\n" % len(data))

    total = {"name": 0, "tags": 0, "interests": 0, "tone": 0,
             "status": 0, "facts": 0, "dislikes": 0}
    out = {}
    emptied = []
    for uid, p in data.items():
        q, stat = clean_one(p, keep_facts=args.keep_facts)
        for k in total:
            total[k] += stat[k]
        if is_empty(q) and not is_empty(p):
            # 清洗后变空了 → 只留空骨架，但保留活跃度字段供面板展示
            q = dict(_DEFAULT_PROFILE)
            q["last_seen"] = p.get("last_seen", 0)
            q["message_count"] = p.get("message_count", 0)
            emptied.append(uid)
        out[uid] = q

    print("=" * 60)
    print("清洗统计（被剔除的条目数）")
    print("=" * 60)
    labels = {"name": "昵称(不可信)", "tags": "标签(非身份词)", "interests": "兴趣(一次性话题)",
              "tone": "语气(LLM 编的)", "status": "状态(无意义)", "facts": "事实(疑问句/碎片)",
              "dislikes": "雷点(不在白名单)"}
    for k, v in total.items():
        print("  %-22s %d" % (labels[k], v))

    print("\n清洗后完全无信息的用户: %d 个" % len(emptied))

    # 抽样对比
    print("\n" + "=" * 60)
    print("抽样对比（前 6 个曾经有 facts 的用户）")
    print("=" * 60)
    shown = 0
    for uid, p in data.items():
        if not p.get("facts") or shown >= 6:
            continue
        shown += 1
        print("\n-- UID %s" % uid)
        print("  旧 name  : %r" % (p.get("name") or ""))
        print("  新 name  : %r" % (out[uid].get("name") or ""))
        print("  旧 tags  : %s" % (p.get("tags") or []))
        print("  新 tags  : %s" % (out[uid].get("tags") or []))
        print("  旧 facts : %d 条 %s" % (len(p.get("facts") or []), (p.get("facts") or [])[:4]))
        print("  新 facts : %d 条 %s" % (len(out[uid]["facts"]), out[uid]["facts"][:4]))

    if not args.write:
        print("\n" + "!" * 60)
        print("预览模式 —— 未修改任何文件。确认无误后加 --write 执行。")
        print("!" * 60)
        return

    # 备份后写回
    bk = DATA.with_suffix(".json.bak_clean_%s" % time.strftime("%Y%m%d_%H%M%S"))
    shutil.copy2(DATA, bk)
    DATA.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n已备份: %s" % bk)
    print("已写回: %s" % DATA)


if __name__ == "__main__":
    main()

"""v2.1.21 回归测试 — 表情库英文标签支持

背景：素材换成英文标签命名（cat_shizuku_thinking_01.png）后，原「取末2汉字」
逻辑提取全空 → LLM 拿不到可用情绪词，表情功能等于全废。

验证：
  1. 中文命名仍正常（末2汉字）— 不破坏原有行为
  2. 英文标签经 _TAG_CN 翻译成中文情绪词
  3. 前缀剥离只吃已知角色前缀（thumbs_up 不能被吃成 up）
  4. 每个文件只返回 1 个主词（防提示词臃肿）
  5. 组合标签优先（wink_holdcat → 眨眼抱猫）
  6. 未映射标签安全返回空（不抛异常）
  7. 逐张看图修正过的 5 处译名生效
  8. 真实 data/faces 目录加载：覆盖率 100%、无异常

运行: python tests/_test_phase_v2121_face_en.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def main():
    print("=" * 62)
    print("v2.1.21 表情库英文标签支持回归测试")
    print("=" * 62)

    from modules.face_lib import _tag_from_stem, _tags_from_stem, _TAG_CN

    # ── 1. 中文命名不破坏 ──
    print("\n[1] 中文命名（原有行为）")
    check("眯眼开心 → 开心", _tags_from_stem("眯眼开心") == ["开心"])
    check("捂嘴害羞 → 害羞", _tags_from_stem("捂嘴害羞") == ["害羞"])
    check("呲牙坏笑 → 坏笑", _tags_from_stem("呲牙坏笑") == ["坏笑"])
    check("放声大哭 → 大哭", _tags_from_stem("放声大哭") == ["大哭"])
    check("单字文件名安全", _tags_from_stem("笑") == [])

    # ── 2. 英文标签翻译 ──
    # ⚠️ 2026-09-12 v2.1.22: 素材已按校对表**重命名**（thinking→daze、cold→panic 等），
    # 早期用旧标签名写的断言已失效。这里统一改用重命名后的真实标签。
    print("\n[2] 英文标签翻译")
    cases = {
        "cat_shizuku_daze_01": "发呆",
        "cat_shizuku_sleepy_14": "困",
        "cat_shizuku_confused_106": "疑惑",
        "cat_shizuku_surprise_04": "惊讶",
        "cat_shizuku_cheer_78": "加油",
        "cat_shizuku_hug_cat_05": "抱猫",
        "cat_shizuku_eat_96": "吃东西",
        "cat_shizuku_wave_25": "挥手",
    }
    for stem, want in cases.items():
        got = _tags_from_stem(stem)
        check(f"{stem} → {want}", got == [want], f"got={got}")

    # ── 3. 前缀剥离不能吃标签 ──
    # 用 _TAG_CN 里真实存在的下划线标签验证（thumbs_up 已不在最终词表，改用 angry_shout）
    print("\n[3] 前缀剥离边界（带下划线的标签不能被当角色前缀吃掉）")
    check("angry_shout 完整保留", _tag_from_stem("cat_shizuku_angry_shout_73") == "angry_shout",
          f"got={_tag_from_stem('cat_shizuku_angry_shout_73')}")
    check("eat_icecream 完整保留",
          _tag_from_stem("cat_shizuku_eat_icecream_122") == "eat_icecream",
          f"got={_tag_from_stem('cat_shizuku_eat_icecream_122')}")
    check("hug_heart 完整保留", _tag_from_stem("cat_shizuku_hug_heart_89") == "hug_heart")
    check("close_eyes 完整保留", _tag_from_stem("cat_shizuku_close_eyes_120") == "close_eyes")

    # ── 4. 每文件只返回一个主词 ──
    print("\n[4] 单主词（防提示词臃肿）")
    r = _tags_from_stem("cat_shizuku_angry_shout_73")
    check("angry_shout 只出 1 个词", len(r) == 1, f"got={r}")

    # ── 5. 组合标签优先 ──
    # ⚠️ v2.1.22: wink_holdcat 已重命名为 wink_*（抱猫单独成 hug_cat），
    # 故组合标签测试改用实际存在的「抱+物」组合
    print("\n[5] 组合标签")
    check("hug_cake → 抱蛋糕",
          _tags_from_stem("cat_shizuku_hug_cake_31") == ["抱蛋糕"],
          f"got={_tags_from_stem('cat_shizuku_hug_cake_31')}")
    check("eat_icecream → 吃冰淇淋",
          _tags_from_stem("cat_shizuku_eat_icecream_122") == ["吃冰淇淋"],
          f"got={_tags_from_stem('cat_shizuku_eat_icecream_122')}")

    # ── 6. 未映射安全 ──
    print("\n[6] 未映射标签安全性")
    check("未知标签返回空", _tags_from_stem("cat_shizuku_unknown_tag_99") == [])
    check("纯编号返回空", _tags_from_stem("12345") == [])

    # ── 7. 看图修正过的译名（重命名后的最终标签）──
    print("\n[7] 逐张看图修正的译名（别按英文直译）")
    fixes = {
        "cat_shizuku_daze_01": "发呆",        # 原 thinking，曾误译「思考」，实为趴着放空
        "cat_shizuku_panic_115": "慌张",      # 原 cold，曾误译「无语」，实为发抖打颤
        "cat_shizuku_down_65": "失落",        # 原 dizzy，曾误译「晕」，实为垂头丧气
        "cat_shizuku_tease_cat_124": "逗猫",  # 原 hold_rod，曾误译「拿棍」，实为举逗猫棒
        "cat_shizuku_ok_78": "没问题",        # 原 thumbs_up，实为点头「がんばれ」
    }
    for stem, want in fixes.items():
        got = _tags_from_stem(stem)
        check(f"{stem} → {want}", got == [want], f"got={got}")

    # ── 8. 真实目录加载 ──
    print("\n[8] 真实 data/faces 加载")
    from modules.face_lib import get_face_keywords, get_face

    faces_dir = Path(__file__).resolve().parent.parent / "data" / "faces"
    n_files = len([f for f in faces_dir.iterdir()
                   if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp")]) \
        if faces_dir.is_dir() else 0

    if n_files == 0:
        print("  [SKIP] data/faces 为空（本地被 .gitignore 排除，请在服务器跑）")
    else:
        words = get_face_keywords()
        check(f"情绪词非空（{len(words)} 个）", len(words) > 0, f"got={len(words)}")
        check("情绪词无重复", len(words) == len(set(words)))

        # 每张图至少能被一个词命中
        mapped = 0
        for f in sorted(faces_dir.iterdir()):
            if f.suffix.lower() not in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
                continue
            if _tags_from_stem(f.stem):
                mapped += 1
        check(f"覆盖率 {mapped}/{n_files} = 100%", mapped == n_files,
              f"{mapped}/{n_files}")

        # 每个情绪词都能取到图
        dead = [w for w in words if get_face(w) is None]
        check("所有情绪词均可取图", not dead, f"dead={dead}")

        print(f"\n  情绪词({len(words)}): {'/'.join(words)}")

    print("\n" + "=" * 62)
    print(f"结果: {PASS} passed, {FAIL} failed")
    print("=" * 62)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

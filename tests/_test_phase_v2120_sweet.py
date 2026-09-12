"""v2.1.20 回归测试 — 私聊贴贴风格测试项（sweet_style）

验证：
  1. features 注册表里有 sweet_style，默认关闭，别名可解析
  2. 开关关闭时 system 不含贴贴风格章节（默认行为）
  3. 开关打开时 system 注入贴贴风格配方（关系自指/节奏切换/短句爆破）
  4. 开关再次关闭后 system 恢复干净（两层回退：章节不注入）
  5. _OPTIONAL_SECTIONS 登记 private_sweet_style（不走关键词热加载）
  6. 群聊场景永不注入（私聊专属）
  7. _CMD_DESC 与真实注册表同步（防 LLM 幻觉调用）

运行: python tests/_test_phase_v2120_sweet.py
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
    print("=" * 60)
    print("v2.1.20 贴贴风格测试项（sweet_style）回归测试")
    print("=" * 60)

    # ── 1. 注册表 ──
    print("\n[1] features 注册表")
    from modules.features import FEATURES, is_enabled, resolve, set_enabled

    check("sweet_style 已注册", "sweet_style" in FEATURES)
    check("默认关闭", FEATURES["sweet_style"].get("default") is False)
    check("中文别名可解析", resolve("测试风格") == "sweet_style")
    check("别名 sweet 可解析", resolve("sweet") == "sweet_style")
    check("is_enabled 默认返回 False", is_enabled("sweet_style") is False)

    # ── 2. skill 章节存在且含关键配方 ──
    print("\n[2] skill 章节内容")
    from services.llm import _load_skill_sections

    sec = _load_skill_sections()
    check("private_sweet_style 章节存在", "private_sweet_style" in sec)
    sweet = sec.get("private_sweet_style", "")
    check("含关系自指", "关系自指" in sweet)
    check("含节奏切换", "节奏切换" in sweet)
    check("含短句爆破", "短句爆破" in sweet)
    check("含禁解释", "禁解释" in sweet)
    check("无硬编码人名（含'主人'通称）", "主人" in sweet and "Trusler" not in sweet)
    check("表情包规则保留", "[FACE:" in sweet)

    # ── 3. 开关关闭 → system 不含章节 ──
    print("\n[3] 默认关闭时 system 内容")
    from services.llm import _build_system_text

    _KW = dict(bot_name="测试喵", personality="你是一只测试用的猫娘")

    sys_off = _build_system_text(is_group=False, **_KW)
    check("不含贴贴风格章节标题", "贴贴风格" not in sys_off)
    check("不含关系自指配方", "关系自指" not in sys_off)
    check("默认 private_tone 仍在", "private_tone 规则" in sys_off or "主人" in sys_off)

    # ── 4. 开关打开 → 注入 ──
    print("\n[4] 激活后 system 内容")
    set_enabled("sweet_style", True)
    sys_on = _build_system_text(is_group=False, **_KW)
    check("注入贴贴风格章节", "贴贴风格" in sys_on)
    check("注入关系自指", "关系自指" in sys_on)
    check("注入节奏切换", "节奏切换" in sys_on)
    check("注入短句爆破", "短句爆破" in sys_on)

    # ── 5. 群聊永不注入 ──
    print("\n[5] 群聊场景")
    sys_grp = _build_system_text(is_group=True, **_KW)
    check("群聊不含贴贴风格", "贴贴风格" not in sys_grp)

    # ── 6. 再关闭 → 恢复干净 ──
    print("\n[6] 关闭后恢复")
    set_enabled("sweet_style", False)
    sys_off2 = _build_system_text(is_group=False, **_KW)
    check("再次关闭后不含章节", "贴贴风格" not in sys_off2)
    check("恢复默认私聊语气", "主人" in sys_off2)

    # ── 7. _OPTIONAL_SECTIONS 登记 ──
    print("\n[7] 热加载豁免")
    from services.llm import _OPTIONAL_SECTIONS

    check("private_sweet_style 在 _OPTIONAL_SECTIONS", "private_sweet_style" in _OPTIONAL_SECTIONS)

    # ── 8. _CMD_DESC 同步 ──
    print("\n[8] 指令文档同步")
    from services.llm import _CMD_DESC

    desc = _CMD_DESC.get("key", "")
    check("key 描述提到 sweet_style", "sweet_style" in desc)
    check("key 描述提到 face_inline", "face_inline" in desc)

    # ── 清理：恢复默认（关） ──
    set_enabled("sweet_style", False)

    print("\n" + "=" * 60)
    print(f"结果: {PASS} passed, {FAIL} failed")
    print("=" * 60)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

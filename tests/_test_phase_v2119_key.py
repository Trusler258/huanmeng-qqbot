# -*- coding: utf-8 -*-
"""v2.1.19 实验测试项开关 /~key 回归测试

运行: python tests/_test_phase_v2119_key.py
覆盖:
  1. features 模块基础（开关读写 / 别名解析 / 未知 code 安全）
  2. 持久化与恢复（写文件 → 重载生效 / reset 回默认）
  3. /~key 指令四种用法（列表 / 激活 / 关闭 / reset）
  4. 权限（仅管理员）
  5. 两层回退都生效：提示词不注入 + pipeline 走默认分支
  6. 安全性（文件损坏/缺失 → 走默认值，不抛异常）
  7. 通用性（新测试项只需往注册表加一条，指令无需改动）
"""
import asyncio
import json
import os
import re
import sys

for p in (r"G:\py\qqbot", "/root/bot", os.getcwd()):
    if p not in sys.path and os.path.isdir(p):
        sys.path.insert(0, p)

ROOT = None
for p in (r"G:\py\qqbot", "/root/bot", os.getcwd()):
    if os.path.isfile(os.path.join(p, "core", "pipeline.py")):
        ROOT = p
        break
assert ROOT, "找不到项目根目录"

FEAT_FILE = os.path.join(ROOT, "data", "features.json")
_BACKUP = None
if os.path.isfile(FEAT_FILE):
    with open(FEAT_FILE, encoding="utf-8") as f:
        _BACKUP = f.read()


def _restore():
    """测试结束恢复原状态，避免污染运行环境"""
    try:
        if _BACKUP is None:
            if os.path.isfile(FEAT_FILE):
                os.remove(FEAT_FILE)
        else:
            with open(FEAT_FILE, "w", encoding="utf-8") as f:
                f.write(_BACKUP)
    except Exception:
        pass


import modules.features as F
from core.config import get_config

cfg = get_config()
ADMIN = cfg.admin_qq

try:
    # ── 1. 基础 ──
    assert "face_inline" in F.FEATURES, "face_inline 应已注册"
    assert F.is_enabled("face_inline") is True, "逐句配图默认开启"
    # 别名解析
    for alias in ("face_inline", "face", "配图", "表情", "FACE_INLINE"):
        assert F.resolve(alias) == "face_inline", f"别名词解析失败: {alias}"
    assert F.resolve("不存在的测试项") is None
    # 未知 code：读写都应安全失败，不抛异常
    assert F.is_enabled("no_such_feature") is False, "未知项应返回 False"
    assert F.set_enabled("no_such_feature", True) is False, "未知项写入应返回 False"
    assert F.reset("no_such_feature") is False
    print("[1] features 基础 + 别名解析 + 未知项安全 OK")

    # ── 2. 持久化 ──
    F.set_enabled("face_inline", False)
    assert os.path.isfile(FEAT_FILE), "写入后应生成 features.json"
    with open(FEAT_FILE, encoding="utf-8") as f:
        saved = json.load(f)
    assert saved.get("face_inline") is False, f"文件内容不对: {saved}"
    # 模拟"重启"：清缓存后重新读取
    F._cache = None
    assert F.is_enabled("face_inline") is False, "重载后应读到关闭状态"
    # reset 回默认
    F.reset("face_inline")
    F._cache = None
    assert F.is_enabled("face_inline") is True, "reset 后应回到默认（开启）"
    print("[2] 持久化 + 重载 + reset 回默认 OK")

    # ── 3. /~key 指令 ──
    from modules.commands import cmd_key

    async def run():
        out = {}
        out["list"] = await cmd_key([], ADMIN, 0, "Trusler", False, cfg.bot_qq)
        out["off"] = await cmd_key(["face_inline", "off"], ADMIN, 0, "Trusler", False, cfg.bot_qq)
        out["state_off"] = F.is_enabled("face_inline")
        out["on"] = await cmd_key(["配图"], ADMIN, 0, "Trusler", False, cfg.bot_qq)
        out["state_on"] = F.is_enabled("face_inline")
        out["reset"] = await cmd_key(["face", "reset"], ADMIN, 0, "Trusler", False, cfg.bot_qq)
        out["unknown"] = await cmd_key(["nope"], ADMIN, 0, "Trusler", False, cfg.bot_qq)
        out["bad_action"] = await cmd_key(["face_inline", "乱写"], ADMIN, 0, "Trusler", False, cfg.bot_qq)
        out["denied"] = await cmd_key(["face_inline"], 99999, 0, "路人", False, cfg.bot_qq)
        return out

    r = asyncio.run(run())
    assert "实验测试项" in r["list"] and "face_inline" in r["list"], r["list"]
    assert r["state_off"] is False, "off 应关闭"
    assert "恢复默认" in r["off"], r["off"]
    assert r["state_on"] is True, "重新激活应开启"
    assert "已激活" in r["on"], r["on"]
    assert "默认" in r["reset"], r["reset"]
    assert "没有这个测试项" in r["unknown"], r["unknown"]
    assert "不认识这个操作" in r["bad_action"], r["bad_action"]
    assert "只有管理员" in r["denied"], r["denied"]
    print("[3] /~key 列表/激活/关闭/reset/未知项 OK")
    print("[4] 权限：非管理员被拒 OK")

except Exception:
    _restore()
    raise

# ── 5. 两层回退都生效 ──
from services.llm import _build_system_text

MARK = "情绪的节拍"   # private_face_inline 规则独有（changelog 里不会出现）
F.set_enabled("face_inline", True)
on_txt = _build_system_text(cfg.bot_name, cfg.system_prompt, False)
F.set_enabled("face_inline", False)
off_txt = _build_system_text(cfg.bot_name, cfg.system_prompt, False)
assert MARK in on_txt, "开启时私聊 system 应注入逐句配图规则"
assert MARK not in off_txt, "关闭时不应注入（这才叫真正恢复默认）"
assert len(on_txt) > len(off_txt)
# 群聊不受影响
F.set_enabled("face_inline", True)
grp_txt = _build_system_text(cfg.bot_name, cfg.system_prompt, True)
assert MARK not in grp_txt, "群聊不该注入私聊专属的表情规则"

# pipeline 分支存在
with open(os.path.join(ROOT, "core", "pipeline.py"), encoding="utf-8") as f:
    pipe = f.read()
assert "is_enabled as _feat_on" in pipe and '"face_inline"' in pipe, "pipeline 未接开关"
assert "逐句配图已关闭" in pipe, "缺少关闭分支的日志"
assert "默认行为：清理内联标记" in pipe, "缺少默认行为分支说明"
print(f"[5] 两层回退 OK（私聊 system {len(on_txt)} → {len(off_txt)} 字符；pipeline 双分支就位）")

# ── 6. 安全性：文件损坏 / 缺失 ──
with open(FEAT_FILE, "w", encoding="utf-8") as f:
    f.write("{ 这不是合法 json ")
F._cache = None
assert F.is_enabled("face_inline") is True, "文件损坏应回退到注册表默认值（不崩）"
if os.path.isfile(FEAT_FILE):
    os.remove(FEAT_FILE)
F._cache = None
assert F.is_enabled("face_inline") is True, "文件缺失应回退默认值"
print("[6] 文件损坏/缺失 → 走默认值不崩 OK")

# ── 7. 通用性：加新测试项不用改指令 ──
F.FEATURES["_selftest_flag"] = {
    "aliases": ("自测项",),
    "default": False,
    "desc": "回归测试用临时项",
    "affects": "无",
}
try:
    F._cache = None
    assert F.is_enabled("_selftest_flag") is False, "新项应取默认值"
    r2 = asyncio.run(cmd_key(["_selftest_flag"], ADMIN, 0, "Trusler", False, cfg.bot_qq))
    assert "已激活" in r2, r2
    assert F.is_enabled("_selftest_flag") is True
    r3 = asyncio.run(cmd_key([], ADMIN, 0, "Trusler", False, cfg.bot_qq))
    assert "_selftest_flag" in r3, "列表应包含新项"
    r4 = asyncio.run(cmd_key(["自测项", "off"], ADMIN, 0, "Trusler", False, cfg.bot_qq))
    assert F.is_enabled("_selftest_flag") is False
    print("[7] 通用性 OK（新增测试项零改指令，列表/别名/开关自动生效）")
finally:
    F.FEATURES.pop("_selftest_flag", None)
    F._cache = None
    _restore()

print("\n全部 7 组通过: v2.1.19 实验测试项开关 /~key")

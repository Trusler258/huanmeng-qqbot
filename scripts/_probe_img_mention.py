# -*- coding: utf-8 -*-
"""验证 v2.3.60 的「@bot 发图不回复」修复

背景：`_process_image` 在"被@且识别成功"时返回 `[图片]:描述"..."`（**以 [图片] 开头**），
而 dispatcher 用 `startswith("[图片]")` 判失败 → 把成功当失败 → msg_type 不转"文字"
→ pipeline 判"非图片消息不进管道" → 静默不回复。

本脚本做两件事：
  1. 纯逻辑：三组返回值在「旧判定」与「新判定」下的结果对比
  2. 真实调用：拿一张本地图跑一次视觉识别，确认返回值形态
     （证明修复后真实场景会被判为"成功"）

用法（服务器上）：python3 scripts/_probe_img_mention.py
"""
import asyncio
import sys

sys.path.insert(0, "/root/bot")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 只用于探针的自检图片（recognize_image 只接受 http/https URL，不吃本地路径）
IMG = "https://cdn.cloudflare.steamstatic.com/steam/apps/977950/header.jpg"


def old_check(ret: str) -> bool:
    """修复前：not img_result.startswith("[图片]")"""
    return not ret.startswith("[图片]")


def new_check(ret: str) -> bool:
    """修复后：img_result.strip() != "[图片]" """
    return ret.strip() != "[图片]"


CASES = [
    ("[图片]", "识别功能关闭 / @识别失败 / 后台异步"),
    ('[图片]:描述"一只猫在键盘上"', "@同步识别成功（★ 本次修复的点）"),
    ('[图片]:描述"x" ', "成功但尾部有空白"),
]


def main() -> int:
    print("=== 1. 纯逻辑对比（True = 会把 msg_type 转成「文字」= 会回复）===")
    print("   %-30s %-34s %-8s %-8s" % ("返回值", "场景", "旧判定", "新判定"))
    ok = True
    for ret, desc in CASES:
        o, n = old_check(ret), new_check(ret)
        flag = "" if o == n else "   ← 修复生效"
        print("   %-30s %-34s %-8s %-8s%s"
              % (ret[:28], desc, o, n, flag))
    if old_check(CASES[1][0]):
        print("   ⚠ 修复未生效：成功案例仍被旧判定放行（不该发生）")
        ok = False
    if not new_check(CASES[1][0]):
        print("   ⚠ 新判定把成功案例判为失败")
        ok = False
    if new_check(CASES[0][0]):
        print("   ⚠ 新判定把裸 [图片] 判为成功（会导致拿空描述去回复）")
        ok = False
    print("   逻辑结论:", "通过" if ok else "失败")
    print()

    print("=== 2. 真实识别（确认返回值形态）===")
    try:
        from core.config import load_bot_config
        from services.image_api import recognize_image
        cfg = load_bot_config()
        _m = cfg.image_model
        _mn = getattr(_m, "model", None) or getattr(_m, "name", None) or getattr(_m, "model_name", "?")
        print("   识别开关 =", _m.switch, "| 模型 =", _mn)
        if not cfg.image_model.switch:
            print("   识别功能关闭，跳过")
            return 0
        desc = asyncio.run(recognize_image(IMG, cfg.image_model, chat_id=0))
        print("   识别返回: %r" % (desc or "")[:90])
        if not desc or not desc.strip():
            print("   识别为空 → _process_image 会 return '[图片]'（不进管道，正确）")
            return 0
        ret = '[图片]:描述"%s"' % desc[:80].replace("\n", " ")
        print("   模拟 _process_image 返回值: %r" % ret[:80])
        print("   新判定 =", new_check(ret), "（应为 True → 会回复）")
    except Exception as e:
        print("   真实识别异常: %s: %r" % (type(e).__name__, e))
    return 0


if __name__ == "__main__":
    sys.exit(main())

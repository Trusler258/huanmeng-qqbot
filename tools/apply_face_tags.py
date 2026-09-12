"""按人工校对表重命名 data/faces 里的表情文件

命名格式：<拼音/英文标签>_<编号>.png，例如 sleeping_001.png
中文标签 → 文件名用英文（避免非 ASCII 文件名在上传/CQ码里的编码问题）

用法:
  python tools/apply_face_tags.py <faces目录> <校对表md> [--dry-run]
"""
import re
import sys
from pathlib import Path

# 中文标签 → 文件名标签（英文，避免编码坑）
CN2FILE = {
    "发呆": "daze", "抱玩偶": "hug_toy", "惊讶": "surprise", "站着": "stand",
    "抱东西": "hug_thing", "抱花": "hug_flower", "端东西": "hold_tray",
    "蹦跳": "hop", "坐着": "sit", "抱爱心": "hug_heart", "抱猫": "hug_cat",
    "困": "sleepy", "睡觉": "sleeping", "委屈": "sad", "祝贺": "congrats",
    "眨眼": "wink", "抱蛋糕": "hug_cake", "敬礼": "salute",
    "打招呼": "greet", "道歉": "sorry", "坏笑": "smirk", "拒绝": "refuse",
    "不满": "upset", "趴着": "lie", "喝饮料": "drink", "开心": "happy",
    "害羞": "shy", "得意": "smug", "生气": "angry", "挥手": "wave",
    "眨眼张嘴": "wink", "没问题": "ok", "失落": "down", "慌张": "panic",
    "饿": "hungry", "疑惑": "confused", "张望": "look", "加油": "cheer",
    "大哭": "cry", "不舍": "miss", "无聊": "bored", "打哈欠": "yawn",
    "吃东西": "eat", "大喊": "shout", "偷看": "peek", "闭眼": "close_eyes",
    "疲惫": "tired", "逗猫": "tease_cat", "吃冰淇淋": "eat_icecream",
}


def parse_table(md_path: Path) -> dict[int, str]:
    """解析校对表 → {编号: 建议标签}

    表格行格式（无前导竖线）：
        001 | thinking | 蓝发趴桌顶橘子 | 发呆
    """
    out: dict[int, str] = {}
    for line in md_path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*(\d{3})\s*\|[^|]*\|[^|]*\|\s*([^|]*?)\s*\|?\s*$", line)
        if m:
            out[int(m.group(1))] = m.group(2).strip()
    return out


def main():
    faces = Path(sys.argv[1] if len(sys.argv) > 1 else "data/faces")
    table = Path(sys.argv[2] if len(sys.argv) > 2 else "data/faces_tag_corrected.md")
    dry = "--dry-run" in sys.argv

    tags = parse_table(table)
    if not tags:
        print("解析校对表失败")
        return 1
    print(f"校对表: {len(tags)} 条（有标签 {sum(1 for v in tags.values() if v)} 个）")

    files = sorted(f for f in faces.iterdir()
                   if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp"))
    print(f"目录文件: {len(files)} 个\n")

    renamed = skipped = dropped = 0
    plan: list[tuple[Path, Path]] = []

    for f in files:
        # 从原文件名取编号（cat_shizuku_xxx_01.png → 1）
        m = re.search(r"_(\d+)$", f.stem)
        if not m:
            print(f"  [跳过] 无编号: {f.name}")
            skipped += 1
            continue
        num = int(m.group(1))
        cn = tags.get(num, "")
        if not cn:
            plan.append((f, None))   # 无标签 → 标记待移除
            dropped += 1
            continue
        en = CN2FILE.get(cn)
        if not en:
            print(f"  [警告] 标签「{cn}」没有英文映射（编号 {num:03d}），用拼音兜底")
            en = "tag" + str(num)
        new = f.with_name(f"{en}_{num:03d}{f.suffix.lower()}")
        plan.append((f, new))
        renamed += 1

    print(f"计划: 重命名 {renamed} 个, 移除 {dropped} 个（无标签/弃用）\n")
    for old, new in plan[:12]:
        print(f"  {old.name:36} → {new.name if new else '(移除)'}")
    if len(plan) > 12:
        print(f"  ... 共 {len(plan)} 项")

    if dry:
        print("\n[dry-run] 未实际改动")
        return 0

    # 先移到临时目录，避免改名冲突
    tmp = faces.parent / "_faces_retag_tmp"
    if tmp.exists():
        import shutil
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)

    for i, (old, new) in enumerate(plan):
        if new is None:
            old.rename(tmp / f"_drop_{old.name}")
        else:
            old.rename(tmp / new.name)

    # 清空原目录后回填（保持目录干净 + 顺序）
    for f in list(faces.iterdir()):
        if f.is_file():
            f.unlink()
    kept = 0
    for f in sorted(tmp.iterdir()):
        if f.name.startswith("_drop_"):
            continue
        f.rename(faces / f.name)
        kept += 1

    import shutil
    shutil.rmtree(tmp)
    print(f"\n完成: 保留 {kept} 个, 移除 {dropped} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())

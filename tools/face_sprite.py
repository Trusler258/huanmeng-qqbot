"""把 data/faces 里的表情拼成一张带标注的 sprite 总览图

用途：
  1. 人工核对素材质量与标签贴切度
  2. 可选喂给多模态模型，让 LLM「看见」自己能发什么图

用法: python tools/face_sprite.py <faces目录> <输出png> [每行张数]
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FACE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp")


def load_font(size: int):
    """找一个能显示中文的字体"""
    cands = [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ]
    for c in cands:
        if Path(c).exists():
            try:
                return ImageFont.truetype(c, size)
            except Exception:
                continue
    return ImageFont.load_default()


def main():
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "data/faces")
    out = Path(sys.argv[2] if len(sys.argv) > 2 else "data/face_sprite.png")
    cols = int(sys.argv[3]) if len(sys.argv) > 3 else 8

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from modules.face_lib import _tags_from_stem

    files = sorted(f for f in src.iterdir() if f.suffix.lower() in FACE_EXT)
    if not files:
        print("没有图片")
        return 1

    CELL = 130          # 每格边长
    PAD = 6
    LABEL_H = 24        # 标签行高
    rows = (len(files) + cols - 1) // cols

    W = cols * (CELL + PAD) + PAD
    H = rows * (CELL + LABEL_H + PAD) + PAD
    canvas = Image.new("RGB", (W, H), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)
    font = load_font(16)

    for i, f in enumerate(files):
        r, c = divmod(i, cols)
        x = PAD + c * (CELL + PAD)
        y = PAD + r * (CELL + LABEL_H + PAD)
        try:
            im = Image.open(f).convert("RGBA")
            im.thumbnail((CELL, CELL), Image.LANCZOS)
        except Exception as e:
            print("skip", f.name, e)
            continue
        # 白底贴图居中
        bg = Image.new("RGBA", (CELL, CELL), (255, 255, 255, 255))
        bg.paste(im, ((CELL - im.width) // 2, (CELL - im.height) // 2), im)
        canvas.paste(bg.convert("RGB"), (x, y))
        draw.rectangle([x, y, x + CELL - 1, y + CELL - 1], outline=(220, 220, 220))

        # 标签：中文情绪词 + 编号
        tags = _tags_from_stem(f.stem)
        num = f.stem.rsplit("_", 1)[-1]
        label = f"{tags[0] if tags else '?'} {num}"
        try:
            tw = draw.textlength(label, font=font)
        except Exception:
            tw = len(label) * 8
        draw.text((x + (CELL - tw) / 2, y + CELL + 2), label,
                  fill=(40, 40, 40), font=font)

    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out)
    print(f"已生成 {out}  ({W}x{H}, {len(files)} 张, {cols} 列)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

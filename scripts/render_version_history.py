# -*- coding: utf-8 -*-
"""幻梦版本演进长图渲染器。

数据源（两份，缺一不可）：
  1. config/update.toml   —— Beta 时代（beta 0.4.3 起源 → beta 0.8.1）
  2. data/update_log.md   —— Huanmeng 2.0 时代（v2.0.0 → 最新）

中间 beta 0.9.x ~ v1.x 无存档（史料缺口），长图里如实标注。

用法：
    python scripts/render_version_history.py

产物（项目根目录）：
    幻梦版本演进史_<首版本>-<末版本>.png   高清原图（2x）
    幻梦版本演进史_<首版本>-<末版本>.jpg   分享版（1080 宽，QQ 友好）
"""
import html
import json
import pathlib
import re
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT_W = 1080
SCALE = 2
PAGE_H = 4000   # 分页版每页目标高度（CSS px）→ 实际输出 8000px，长宽比约 1:3.7

TYPE_META = {
    "feature": ("新功能", "#34d399"),
    "improve": ("优化", "#4fc3f7"),
    "fix": ("修复", "#fbbf24"),
    "change": ("变更", "#c084fc"),
}


def _date_key(date_str: str) -> tuple:
    """'2026.5.8' / '2026.5'（缺日）→ 可排序元组。"""
    parts = [int(x) for x in re.findall(r"\d+", date_str or "")]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def _ver_key(ver: str) -> tuple:
    """'beta 0.7.4' / 'v2.0.4aa' → 可排序元组。

    Beta 段版本号严格递增但日期有缺（'2026.5'），故 Beta 段按版本号排序；
    字母后缀（aa/ab…）按 base26 折算，保证 v2.0.4z < v2.0.4aa。
    """
    m = re.match(r"(?:beta\s+|v)?(\d+)\.(\d+)\.(\d+)([a-z]*)", ver or "")
    if not m:
        return (0, 0, 0, 0)
    major, minor, patch, suffix = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4)
    suf = 0
    for ch in suffix:
        suf = suf * 26 + (ord(ch) - ord("a") + 1)
    return (major, minor, patch, suf)


def load_beta() -> list[dict]:
    """解析 config/update.toml（Beta 时代）。"""
    p = ROOT / "config" / "update.toml"
    if not p.is_file():
        return []
    data = tomllib.loads(p.read_text(encoding="utf-8"))
    out = []
    for item in data.get("update", []):
        changes = item.get("changes", [])
        groups: dict[str, list[str]] = {}
        for c in changes:
            groups.setdefault(c.get("type", "change"), []).append(c.get("text", ""))
        out.append({
            "era": "beta",
            "ver": item.get("version", ""),
            "date": item.get("date", ""),
            "title": item.get("tag", ""),
            "summary": "",
            "groups": groups,
            "points": [],
        })
    return out


def load_2x() -> list[dict]:
    """解析 data/update_log.md（Huanmeng 2.0 时代）。"""
    p = ROOT / "data" / "update_log.md"
    if not p.is_file():
        return []
    src = p.read_text(encoding="utf-8")
    heads = list(re.finditer(r"^## (v\d+\.\d+\.\d+[a-z]*)\s*[—\-–]\s*(.*)$", src, re.M))
    out = []
    for i, h in enumerate(heads):
        ver = h.group(1)
        rest = h.group(2).strip()
        body = src[h.end():(heads[i + 1].start() if i + 1 < len(heads) else len(src))]
        date = ""
        dm = re.search(r"[（(](\d{4}\.\d{1,2}\.\d{1,2})[)）]\s*$", rest)
        if dm:
            date, rest = dm.group(1), rest[:dm.start()].strip()
        else:
            dm2 = re.search(r"[—\-–]\s*(\d{4}\.\d{1,2}\.\d{1,2})\s*$", rest)
            if dm2:
                date, rest = dm2.group(1), rest[:dm2.start()].strip()
        summ = re.search(r"一句话总结：(.+)", body)
        points = [ln.strip()[2:].strip() for ln in body.splitlines()
                  if ln.strip().startswith("- ") and "一句话" not in ln]
        out.append({
            "era": "2x",
            "ver": ver,
            "date": date,
            "title": rest.strip(),
            "summary": summ.group(1).strip() if summ else "",
            "groups": {},
            "points": points[:5],
        })
    return list(reversed(out))   # 文件是倒序（最新在上），翻正后再按日期稳定排序


def esc(s: str) -> str:
    return html.escape(s or "")


def render_card(v: dict) -> str:
    ver_cls = "ver-beta" if v["era"] == "beta" else "ver-2x"
    inner = [f'<div class="title">{esc(v["title"])}</div>']
    if v["summary"]:
        inner.append(f'<div class="summary">{esc(v["summary"])}</div>')
    if v["groups"]:
        for t, items in v["groups"].items():
            label, color = TYPE_META.get(t, ("变更", "#c084fc"))
            inner.append(f'<div class="grp"><span class="tag" style="color:{color};'
                         f'border-color:{color}44;background:{color}14">{label}</span></div>')
            for it in items[:5]:
                inner.append(f'<div class="pt">· {esc(it)}</div>')
            if len(items) > 5:
                inner.append(f'<div class="pt more">… 另有 {len(items) - 5} 项</div>')
    for p in v["points"]:
        inner.append(f'<div class="pt">· {esc(p)}</div>')
    return f'''
    <div class="card">
      <div class="card-left">
        <div class="ver {ver_cls}">{esc(v["ver"])}</div>
        <div class="date">{esc(v["date"])}</div>
      </div>
      <div class="card-body">{"".join(inner)}</div>
    </div>'''


def build_html(versions: list[dict]) -> str:
    first, last = versions[0], versions[-1]
    beta = [v for v in versions if v["era"] == "beta"]
    era2x = [v for v in versions if v["era"] == "2x"]

    parts = []
    if beta:
        parts.append('<div class="era"><span class="era-line"></span>'
                     '<span class="era-txt">Beta 时代 · 从 Maibot 精简起步</span></div>')
        parts.extend(render_card(v) for v in beta)
    if beta and era2x:
        parts.append('<div class="gap">· · · 中间版本（beta 0.9.x ~ v1.x）无存档 · · ·</div>')
        parts.append('<div class="era"><span class="era-line"></span>'
                     '<span class="era-txt">Huanmeng 2.0 时代 · 架构重写与全面插件化</span></div>')
        parts.extend(render_card(v) for v in era2x)

    return f'''<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8"><style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family:"Microsoft YaHei","PingFang SC",sans-serif; background:#0b0c1a;
        color:#e8e9f5; width:{OUT_W}px; margin:0 auto; }}
.header {{ text-align:center; padding:56px 40px 36px;
           background:linear-gradient(180deg,#171838 0%,#0b0c1a 100%); border-bottom:1px solid #2a2b55; }}
.header h1 {{ font-size:40px; letter-spacing:2px;
              background:linear-gradient(90deg,#8b7bff,#4fc3f7);
              -webkit-background-clip:text; background-clip:text; color:transparent; }}
.header .sub {{ margin-top:12px; font-size:17px; color:#9a9cc8; }}
.header .stats {{ margin-top:22px; display:flex; gap:24px; justify-content:center; }}
.stat {{ background:#161739; border:1px solid #2a2b55; border-radius:14px; padding:12px 24px; }}
.stat .n {{ font-size:26px; color:#8b7bff; font-weight:bold; }}
.stat .l {{ font-size:12px; color:#9a9cc8; margin-top:4px; }}
.timeline {{ position:relative; padding:26px 36px 20px; }}
.timeline::before {{ content:''; position:absolute; left:118px; top:24px; bottom:24px; width:3px;
                     background:linear-gradient(180deg,#8b7bff,#4fc3f7,#34d399); opacity:.45; }}
.era {{ position:relative; margin:26px 0 10px 130px; display:flex; align-items:center; gap:12px; }}
.era-line {{ width:26px; height:3px; background:#8b7bff; border-radius:2px; }}
.era-txt {{ font-size:17px; font-weight:700; color:#c9c6ff; letter-spacing:1px; }}
.gap {{ margin:30px 0 26px 130px; font-size:14px; color:#5c5e8f; letter-spacing:3px; }}
.card {{ display:flex; position:relative; padding:16px 0; }}
.card::before {{ content:''; position:absolute; left:111px; top:40px; width:15px; height:15px;
                 border-radius:50%; background:linear-gradient(135deg,#8b7bff,#4fc3f7);
                 box-shadow:0 0 12px #8b7bff88; }}
.card-left {{ width:96px; text-align:right; padding-top:6px; }}
.ver {{ font-size:17px; font-weight:900; letter-spacing:.3px; }}
.ver-beta {{ color:#f0abfc; }}
.ver-2x {{ color:#b8b4ff; }}
.date {{ font-size:12px; color:#6f7199; margin-top:4px; }}
.card-body {{ flex:1; background:#141530; border:1px solid #26274d; border-radius:13px;
              padding:16px 20px; margin-left:32px; }}
.title {{ font-size:17px; font-weight:700; color:#d9d7ff; }}
.summary {{ margin-top:7px; font-size:14px; line-height:1.6; color:#b6b8dc; }}
.grp {{ margin-top:9px; }}
.tag {{ display:inline-block; padding:1px 9px; border-radius:20px; font-size:11px;
        border:1px solid; margin-right:6px; }}
.pt {{ margin-top:5px; font-size:12.5px; color:#8f91b8; line-height:1.55; }}
.pt.more {{ color:#5f6191; }}
.footer {{ text-align:center; padding:28px; color:#55578a; font-size:13px;
           border-top:1px solid #1e1f45; }}
</style></head><body>
<div class="header">
  <h1>幻梦 Huanmeng · 版本演进史</h1>
  <div class="sub">{esc(first["ver"])} → {esc(last["ver"])} · 从 2025.8.25 到 {esc(last["date"])}</div>
  <div class="stats">
    <div class="stat"><div class="n">{len(versions)}</div><div class="l">有存档的版本</div></div>
    <div class="stat"><div class="n">{len(beta)} / {len(era2x)}</div><div class="l">Beta / 2.x</div></div>
    <div class="stat"><div class="n">13个月</div><div class="l">跨度</div></div>
  </div>
</div>
<div class="timeline">
{"".join(parts)}
</div>
<div class="footer">Generated from config/update.toml + data/update_log.md</div>
</body></html>'''


def main() -> None:
    beta = sorted(load_beta(), key=lambda v: _ver_key(v["ver"]))
    # 2.x：日期为主键（完整），版本号为次键——同一天发布多个版本时按版本号递增，
    # 不能依赖文件物理顺序（update_log 里版本并非严格倒序）
    era2x = sorted(load_2x(), key=lambda v: (_date_key(v["date"]), _ver_key(v["ver"])))
    versions = beta + era2x
    if not versions:
        raise SystemExit("没有解析到任何版本，检查两份数据源")

    out_dir = ROOT / "docs" / "version_history"
    out_dir.mkdir(parents=True, exist_ok=True)
    htm = out_dir / "_version_history.html"
    htm.write_text(build_html(versions), encoding="utf-8")

    tag = f'{versions[0]["ver"]}-{versions[-1]["ver"]}'.replace("beta ", "b")
    png = out_dir / f"幻梦版本演进史_{tag}_完整版.png"

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": OUT_W, "height": 900}, device_scale_factor=SCALE)
        page.goto(htm.resolve().as_uri())
        page.wait_for_timeout(400)
        page.screenshot(path=str(png), full_page=True)
        # 量每张卡片的边界（CSS px），供按卡片精确分页用
        bounds = page.evaluate("""() => ({
            cards: [...document.querySelectorAll('.card')].map(c => {
                const r = c.getBoundingClientRect();
                return {top: Math.round(r.top + window.scrollY),
                        bottom: Math.round(r.bottom + window.scrollY)};
            }),
        })""")
        b.close()

    from PIL import Image, ImageDraw, ImageFont
    Image.MAX_IMAGE_PIXELS = None
    im = Image.open(png)
    w, h = im.size

    # 完整版 JPG（缩小到 1080 宽，QQ 友好）
    jpg_full = out_dir / f"幻梦版本演进史_{tag}_完整版.jpg"
    im.resize((OUT_W, int(h * OUT_W / w)), Image.LANCZOS).convert("RGB") \
      .save(jpg_full, "JPEG", quality=88, optimize=True, progressive=True)

    # ── 分页版：按卡片边界切，绝不切断卡片（每页目标 PAGE_H 个 CSS 像素高）──
    cards = bounds["cards"]
    groups: list[list[int]] = []
    cur: list[int] = []
    start_top = 0
    for i, c in enumerate(cards):
        if cur and c["bottom"] - start_top > PAGE_H:
            groups.append(cur)
            cur, start_top = [], c["top"]
        cur.append(i)
    if cur:
        groups.append(cur)

    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 30)
    except Exception:
        font = ImageFont.load_default()

    page_files = []
    for gi, g in enumerate(groups, 1):
        y0 = 0 if gi == 1 else max(cards[g[0]]["top"] - 10, 0)
        y1 = h // SCALE if gi == len(groups) else min(cards[g[-1]]["bottom"] + 10, h // SCALE)
        crop = im.crop((0, y0 * SCALE, w, min(y1 * SCALE, h))).convert("RGB")
        d = ImageDraw.Draw(crop)
        label = f"{gi}/{len(groups)}"
        d.text((crop.width - 150, 22), label, font=font, fill=(120, 122, 170))
        pf = out_dir / f"幻梦版本演进史_{tag}_p{gi:02d}of{len(groups):02d}.jpg"
        crop.save(pf, "JPEG", quality=88, optimize=True, progressive=True)
        page_files.append(pf)

    beta_n = sum(1 for v in versions if v["era"] == "beta")
    print(f"版本数：{len(versions)}（beta {beta_n} / 2x {len(versions) - beta_n}）")
    print(f"输出目录：{out_dir}")
    print(f"完整版 PNG：{w}×{h}  {png.stat().st_size // 1024} KB")
    print(f"完整版 JPG：{jpg_full.stat().st_size // 1024} KB")
    print(f"分页版：{len(page_files)} 张（每页约 {PAGE_H} CSS px）")
    for pf in page_files:
        with Image.open(pf) as pim:
            print(f"  {pf.name}  {pim.size[0]}×{pim.size[1]}  {pf.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()

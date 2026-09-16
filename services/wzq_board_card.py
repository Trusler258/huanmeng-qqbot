"""
棋盘卡（五子棋 / 围棋）—— Pillow 1:1 复刻 data/templates/wzq_board.html

围棋与五子棋**共用这一个模板**（modules/go_game.build_board_html 也是读它，
只换网格数与标题），所以两者共用同一份复刻代码，靠参数区分：
    · 五子棋：15 路 / cell 32 / 行号上→下递减 / 星位 9 点
    · 围棋：9·13·19 路 / cell 44·34·26 / 行号上→下递增 / 星位随尺寸 / 列字母跳过 I

目的：渲染引擎由 Chromium 换成 Pillow 提速（服务器 i3-2130：~870ms → ~80ms），
     **原画质不变**：配色/圆角/阴影/字号/位置全部照抄模板 CSS，不重新设计。

⚠️ 布局常量全部来自 Playwright 读 DOM **实测**（勿按 CSS 字面估算）：
   · CSS 有 `line-height:1.6` → 行高 = 1.6 × 字号（**不是**字体 metrics）
   · 每处 1px border 都要计入高度
   · flex `space-around` 的两侧元素中心在 20.2% / 79.8%（不是 25% / 75%）
   实测脚本：/tmp/_geo_wzq.py（产出 body 高 896.6、head 77.8、players 113.4…）
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from services.card_base import (
    cjk, mono, rounded_mask, ring_mask, blend_over, paste_alpha_rounded,
    grad_text, to_img, linear_grad, radial_glow, radial_circle, save_jpeg,
)

# ══════════════════════════════════════════════════════════
#  配色（逐项取自 wzq_board.html :root 与各规则）
# ══════════════════════════════════════════════════════════
C = {
    "primary":       (236, 72, 153),    # --primary        #ec4899
    "primary_light": (244, 114, 182),   # --primary-light  #f472b6
    "accent":        (139, 92, 246),    # --accent         #8b5cf6
    "accent_light":  (167, 139, 250),   # --accent-light   #a78bfa
    "deepseek":      (86, 134, 254),    # #5686FE
    "cyan":          (34, 211, 238),    # #22d3ee
    "success":       (52, 211, 153),    # --success        #34d399
    "warning":       (251, 191, 36),    # --warning        #fbbf24
    "board_bg":      (42, 31, 21),      # --board-bg       #2a1f15
    "board_line":    (74, 53, 32),      # --board-line     #4a3520
    "board_edge":    (26, 18, 8),       # --board-edge     #1a1208
    "t2":            (184, 184, 184),   # rgba(255,255,255,.72) 叠在深底
    "t3":            (102, 102, 102),   # rgba(255,255,255,.40) 叠在深底
    "card_bg":       (20, 18, 32),      # --bg-card rgba(20,18,32,.55)
    "card_a":        0.55,
    "hairline":      (255, 255, 255),
    "hairline_a":    0.10,              # --border rgba(255,255,255,.10)
}

# body 背景：linear-gradient(165deg,#07060f,#0d0b1a 45%,#080a16) + 4 处椭圆光晕
BODY_LINEAR = [(0.00, (7, 6, 15)), (0.45, (13, 11, 26)), (1.00, (8, 10, 22))]
BODY_GLOWS = [
    # (位置x%, 位置y%, 半径x比重, 半径y比重, 颜色, alpha, fade)
    (0.15, -0.10, 0.80, 0.55, C["primary"], 0.42, 0.50),
    (0.88, 1.10, 0.70, 0.50, C["accent"], 0.38, 0.50),
    (0.50, 0.50, 0.65, 0.60, C["deepseek"], 0.12, 0.60),
    (0.05, 0.80, 0.50, 0.40, C["cyan"], 0.14, 0.55),
]

# ── 布局（px，全部实测）──
LH = 1.6


def lh(fs: float) -> float:
    """CSS 行高（body 设了 line-height:1.6）"""
    return LH * fs


W = 680
WRAP_PT, WRAP_PX, WRAP_PB = 22, 18, 28
CARD_R = 15
BD = 1

HEAD_PT, HEAD_PX, HEAD_PB = 18, 24, 14
LOGO_SZ, LOGO_R = 40, 11
TITLE_FS, SUB_FS = 15, 10.5

PLAYERS_PT, PLAYERS_PB = 18, 14
NAME_FS, ROLE_FS, VS_FS = 14, 10, 16
STONE_SZ = 30
NAME_MB, STONE_MB = 8, 4
PLAYER_CX = 0.202            # flex space-around 实测

BOARD_PAD_PT = 20
# 棋盘默认值 = 五子棋（围棋经参数覆盖：9路 cell44 / 13路 cell34 / 19路 cell26）
BOARD_N, CELL, LABEL, STONE, STAR = 15, 32, 24, 26, 7
BOARD_R = 6
STATUS_FS, STATUS_PT, STATUS_MB = 14, 12, 16
FOOT_FS, FOOT_PT, FOOT_PX, FOOT_PB = 10, 12, 24, 16

def star_points_of(size: int) -> set:
    """该尺寸的星位（与 modules/go_game.star_points 保持一致）"""
    if size == 9:
        return {(2, 2), (2, 6), (6, 2), (6, 6), (4, 4)}
    if size == 13:
        return {(3, 3), (3, 9), (9, 3), (9, 9), (6, 6)}
    if size == 19:
        return {(3, 3), (3, 9), (3, 15), (9, 3), (9, 9), (9, 15),
                (15, 3), (15, 9), (15, 15)}
    # 15 路（五子棋）
    return {(3, 3), (3, 7), (3, 11), (7, 3), (7, 7),
            (7, 11), (11, 3), (11, 7), (11, 11)}


STAR_POINTS = star_points_of(BOARD_N)


def _body_bg(size) -> Image.Image:
    """模板 body 背景：4 处椭圆光晕叠在线性渐变上"""
    Wd, Hd = size
    bg = linear_grad(size, BODY_LINEAR, 165.0)
    for px, py, rw, rh, color, alpha, fade in BODY_GLOWS:
        bg = radial_glow(bg, (Wd * px, Hd * py), (Wd * rw, Hd * rh), color, alpha, fade)
    return to_img(bg)


def _grad_line(img, x0, y, w, c1, c2, alpha: float = 0.5):
    """两端渐隐的彩色细线（对应 CSS 用 linear-gradient 做的分隔线）"""
    w = int(w)
    if w <= 2:
        return
    t = np.linspace(0.0, 1.0, w, dtype=np.float32)
    a = np.clip(1.0 - np.abs(t - 0.5) * 2, 0, 1) * alpha
    seg = np.zeros((1, w, 3), dtype=np.float32)
    for k in range(3):
        seg[0, :, k] = c1[k] * (1 - t) + c2[k] * t
    img.paste(to_img(seg), (int(x0), int(y)),
              Image.fromarray((a * 255).astype(np.uint8).reshape(1, w), "L"))


def _hairline(d, img, x0, x1, y, alpha=None):
    """1px 分隔线。

    ⚠️ 半透明必须预混合：RGB 图上 ImageDraw 的 4 元组 fill 会**忽略 alpha**
      （日榜卡复刻时踩过，页脚药丸因此变实心深棕）
    """
    d.line([x0, y, x1, y],
           fill=blend_over(img, (x0 + 4, y), C["hairline"],
                           C["hairline_a"] if alpha is None else alpha), width=1)


def render_wzq_board(game, black_name: str, white_name: str,
                     col_labels: list, date_str: str,
                     title_en: str = "GOMOKU", brand_en: str = "HUANMENG",
                     board_n: int = BOARD_N, cell: int = CELL, stone: int = STONE,
                     star_points: set | None = None,
                     row_label_mode: str = "desc",
                     sub_text: str | None = None,
                     status_text: str | None = None,
                     status_class: str | None = None,
                     move_count: int | None = None) -> Image.Image:
    """渲染棋盘卡（1:1 复刻 data/templates/wzq_board.html 外观）

    五子棋与围棋**共用同一模板**，靠参数区分：
      · 五子棋：默认值（15 路 / cell 32 / 行号上→下递减）
      · 围棋：19(或13/9) 路 / cell 26(34/44) / 行号上→下递增 / 星位不同
                （围棋惯例列字母跳过 I；sub/status 文本由调用方传入）

    game 需含：board[r][c]（0 空/1 黑/2 白）、status、turn、winner、
              move_count、last_move
    """
    # ── 状态文本（照抄模板逻辑）──
    if game.status == "waiting":
        sclass, stext = "waiting", "等待 %s 接受挑战... (/~wzq accept)" % white_name
    elif game.status == "playing":
        cur = black_name if game.turn == 1 else white_name
        sclass = "playing"
        stext = "轮到 %s 落子 (%s)" % (cur, "黑" if game.turn == 1 else "白")
    elif game.winner == 1:
        sclass, stext = "win", "%s (黑) 获胜！" % black_name
    elif game.winner == 2:
        sclass, stext = "win", "%s (白) 获胜！" % white_name
    else:
        sclass, stext = "draw", "平局！"
    if sub_text is None:
        sub_text = "手数 %s | %s" % (game.move_count,
                                    stext.split("：")[0] if "：" in stext else stext)
    if status_text is not None:
        stext = status_text
    if status_class is not None:
        sclass = status_class
    _moves = game.move_count if move_count is None else move_count
    _stars = STAR_POINTS if star_points is None else star_points

    # ── 盒子高度（实测公式）──
    head_inner = max(LOGO_SZ, lh(TITLE_FS) + 4 + lh(SUB_FS))              # 44.8
    head_h = HEAD_PT + head_inner + HEAD_PB + BD                          # 77.8
    players_inner = lh(NAME_FS) + NAME_MB + STONE_SZ + STONE_MB + lh(ROLE_FS)
    players_h = PLAYERS_PT + players_inner + PLAYERS_PB + BD              # 113.4
    board_box = LABEL + board_n * cell + BD * 2
    board_h = BOARD_PAD_PT + board_box + BOARD_PAD_PT
    status_h = STATUS_PT + lh(STATUS_FS) + STATUS_PT + BD * 2             # 48.4
    foot_h = BD + FOOT_PT + lh(FOOT_FS) + FOOT_PB                         # 45

    card_w = W - WRAP_PX * 2
    card_h = int(round(head_h + players_h + board_h + status_h + STATUS_MB + foot_h))
    H = int(round(WRAP_PT + card_h + WRAP_PB))

    y_players = head_h
    y_board_sec = head_h + players_h
    y_status = y_board_sec + board_h
    y_foot = y_status + status_h + STATUS_MB

    # ── 背景 ──
    img = _body_bg((W, H))
    ox, oy = WRAP_PX, WRAP_PT

    # box-shadow: 0 12px 48px rgba(0,0,0,.65), 0 0 100px rgba(236,72,153,.10),
    #             0 0 40px rgba(139,92,246,.08)
    # ⚠️ 外阴影只画在元素外部（CSS 会把元素区域内的阴影裁掉）。
    #   不挖掉卡片区 → 粉/紫光晕糊进卡内，卡内整体偏亮。
    _inner = Image.new("L", (W, H), 0)
    _inner.paste(rounded_mask((card_w, card_h), CARD_R), (ox, oy))
    for blur, alpha, col, dy in ((24, 0.65, (0, 0, 0), 12),
                                 (50, 0.10, C["primary"], 0),
                                 (20, 0.08, C["accent"], 0)):
        sh = Image.new("L", (W, H), 0)
        sh.paste(rounded_mask((card_w, card_h), CARD_R), (ox, oy + dy))
        _a = alpha
        sh = sh.filter(ImageFilter.GaussianBlur(blur)).point(lambda v, a=_a: int(v * a))
        sh = Image.composite(Image.new("L", (W, H), 0), sh, _inner)
        img.paste(Image.new("RGB", (W, H), col), (0, 0), sh)

    # 卡片底 rgba(20,18,32,.55)
    paste_alpha_rounded(img, Image.new("RGB", (card_w, card_h), C["card_bg"]),
                        (ox, oy), CARD_R, C["card_a"])
    # 1px 渐变边框（135deg 粉→紫→蓝→青）
    border = to_img(linear_grad((card_w, card_h),
                                [(0.00, C["primary"]), (0.35, C["accent"]),
                                 (0.65, C["deepseek"]), (1.00, C["cyan"])], 135.0))
    img.paste(border, (ox, oy), ring_mask((card_w, card_h), CARD_R, 1))
    # 卡顶高光线 .card::after（left/right 各 15%，中间亮两端隐）
    _grad_line(img, ox + int(card_w * 0.15), oy, card_w * 0.70,
               (255, 255, 255), (255, 255, 255), 0.70)

    d = ImageDraw.Draw(img)
    f_name, f_role, f_foot = mono(NAME_FS), mono(ROLE_FS), mono(FOOT_FS)

    # ══════ .head ══════
    lx, ly = ox + HEAD_PX, oy + HEAD_PT
    logo_arr = linear_grad((LOGO_SZ, LOGO_SZ),
                           [(0.0, (255, 255, 255)), (1.0, C["primary"])], 135.0)
    base = np.array(C["card_bg"], dtype=np.float32)[None, None, :]
    paste_alpha_rounded(img, to_img(logo_arr * 0.35 + base * 0.65), (lx, ly), LOGO_R)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([lx, ly, lx + LOGO_SZ - 1, ly + LOGO_SZ - 1], LOGO_R,
                        outline=C["primary"], width=1)
    d.text((lx + LOGO_SZ / 2, ly + LOGO_SZ / 2), "HM", font=mono(13),
           fill=C["primary_light"], anchor="mm")

    tx = lx + LOGO_SZ + 12
    y_title_c = oy + HEAD_PT + lh(TITLE_FS) / 2
    grad_text(img, (tx, y_title_c), "幻梦", mono(TITLE_FS),
              C["primary_light"], C["accent"], 135.0)
    d = ImageDraw.Draw(img)
    w_nm = d.textlength("幻梦", font=mono(TITLE_FS))
    d.text((tx + w_nm + 6, y_title_c), "/", font=mono(TITLE_FS), fill=C["t3"], anchor="lm")
    d.text((tx + w_nm + 18, y_title_c), title_en, font=mono(11), fill=C["t2"], anchor="lm")
    d.text((tx, oy + HEAD_PT + lh(TITLE_FS) + 4 + lh(SUB_FS) / 2),
           "%s · %s" % (sub_text, date_str), font=mono(SUB_FS),
           fill=C["t3"], anchor="lm")
    head_bot = oy + head_h - BD
    _hairline(d, img, ox, ox + card_w, head_bot)
    _grad_line(img, ox + HEAD_PX, head_bot, card_w - HEAD_PX * 2,
               C["primary"], C["accent"], 0.5)

    # ══════ .players（flex space-around）══════
    yp = oy + y_players
    name_h, role_h = lh(NAME_FS), lh(ROLE_FS)
    pairs = ((black_name, True, game.status == "playing" and game.turn == 1),
             (white_name, False, game.status == "playing" and game.turn == 2))
    for i, (nm, is_black, active) in enumerate(pairs):
        bx = ox + card_w * (PLAYER_CX if i == 0 else (1 - PLAYER_CX))
        d.text((bx, yp + PLAYERS_PT + name_h / 2), nm, font=f_name,
               fill=C["primary_light"] if active else C["t2"], anchor="mm")
        sy = yp + PLAYERS_PT + name_h + NAME_MB
        # ⚠️ 局部量取名 stone_img，避免遮蔽 render_wzq_board 的 stone（棋子直径）参数
        if is_black:
            stone_img = radial_circle((STONE_SZ, STONE_SZ), (0x66, 0x66, 0x66),
                                      (10, 10, 10), 35, 35)
        else:
            stone_img = radial_circle((STONE_SZ, STONE_SZ), (255, 255, 255),
                                      (176, 176, 176), 35, 35)
        sx = int(bx - STONE_SZ / 2)
        img.paste(stone_img, (sx, int(sy)),
                  rounded_mask((STONE_SZ, STONE_SZ), STONE_SZ // 2))
        d = ImageDraw.Draw(img)
        if not is_black:
            d.ellipse([sx, sy, sx + STONE_SZ - 1, sy + STONE_SZ - 1],
                      outline=(136, 136, 136), width=1)
        d.text((bx, sy + STONE_SZ + STONE_MB + role_h / 2),
               "BLACK" if is_black else "WHITE", font=f_role, fill=C["t3"], anchor="mm")
    d.text((ox + card_w / 2, yp + players_h / 2), "VS", font=mono(VS_FS),
           fill=C["t3"], anchor="mm")
    _hairline(d, img, ox, ox + card_w, oy + y_players + players_h - BD)

    # ══════ .board-section / .board-grid ══════
    bx0 = ox + (card_w - board_box) / 2    # 棋盘水平居中
    by0 = oy + y_board_sec + BOARD_PAD_PT
    board = Image.new("RGB", (board_box, board_box), C["board_bg"])
    ImageDraw.Draw(board).rounded_rectangle(
        [0, 0, board_box - 1, board_box - 1], BOARD_R,
        outline=C["board_edge"], width=1)
    # inset 0 0 30px rgba(0,0,0,.3)：边缘暗角 → 用 (255 - 中心遮罩) 当投影遮罩
    inner = Image.new("L", (board_box, board_box), 0)
    ImageDraw.Draw(inner).rounded_rectangle(
        [10, 10, board_box - 11, board_box - 11], BOARD_R, fill=255)
    inner = inner.filter(ImageFilter.GaussianBlur(16))
    board.paste(Image.new("RGB", board.size, (0, 0, 0)), (0, 0),
                inner.point(lambda v: int((255 - v) * 0.30)))
    img.paste(board, (int(bx0), int(by0)))

    d = ImageDraw.Draw(img)
    gx0, gy0 = bx0 + BD + LABEL, by0 + BD + LABEL
    grid_w = board_n * cell
    for r in range(board_n):
        yy = int(gy0 + r * cell + cell / 2)
        d.line([gx0, yy, gx0 + grid_w, yy], fill=C["board_line"], width=1)
    for c in range(board_n):
        xx = int(gx0 + c * cell + cell / 2)
        d.line([xx, gy0, xx, gy0 + grid_w], fill=C["board_line"], width=1)
    for c, lab in enumerate(col_labels):
        d.text((gx0 + c * cell + cell / 2, by0 + BD + LABEL / 2), lab,
               font=mono(10), fill=C["t3"], anchor="mm")
    # 行号：五子棋上→下递减（15…1）；围棋上→下递增（1…19）
    for k in range(board_n):
        lab = (board_n - k) if row_label_mode == "desc" else (k + 1)
        d.text((bx0 + BD + LABEL / 2, gy0 + k * cell + cell / 2), str(lab),
               font=mono(10), fill=C["t3"], anchor="mm")

    # 棋子（board 行 1 在底部 → 屏幕行 = BOARD_N-1-r）
    for r in range(board_n):
        for c in range(board_n):
            st = game.board[r][c]
            ccx = gx0 + c * cell + cell / 2
            # 屏幕行：DOM 第 k 行对应 board 的 r = board_n-1-k（两种棋一致）
            ccy = gy0 + (board_n - 1 - r) * cell + cell / 2
            if st == 0:
                if (r, c) in _stars:
                    d.ellipse([ccx - STAR / 2, ccy - STAR / 2,
                               ccx + STAR / 2, ccy + STAR / 2], fill=C["board_line"])
                continue
            sx, sy = int(ccx - stone / 2), int(ccy - stone / 2)
            # .last-move: box-shadow 0 0 0 2px primary, 0 0 14px rgba(236,72,153,.7)
            if game.last_move and tuple(game.last_move) == (r, c):
                gl = Image.new("L", (W, H), 0)
                ImageDraw.Draw(gl).ellipse(
                    [sx - 7, sy - 7, sx + stone + 6, sy + stone + 6], fill=255)
                gl = gl.filter(ImageFilter.GaussianBlur(7)).point(lambda v: int(v * 0.7))
                img.paste(Image.new("RGB", (W, H), C["primary"]), (0, 0), gl)
                d = ImageDraw.Draw(img)
            if st == 1:
                piece = radial_circle((stone, stone), (0x66, 0x66, 0x66),
                                      (10, 10, 10), 35, 35)
                sh_a = 0.8
            else:
                piece = radial_circle((stone, stone), (255, 255, 255),
                                      (176, 176, 176), 35, 35)
                sh_a = 0.4
            sh = Image.new("L", (W, H), 0)
            ImageDraw.Draw(sh).ellipse([sx, sy + 2, sx + stone, sy + stone + 2], fill=255)
            _sa = sh_a
            sh = sh.filter(ImageFilter.GaussianBlur(3)).point(lambda v, a=_sa: int(v * a))
            img.paste(Image.new("RGB", (W, H), (0, 0, 0)), (0, 0), sh)
            img.paste(piece, (sx, sy), rounded_mask((stone, stone), stone // 2))
            d = ImageDraw.Draw(img)
            if st == 2:
                d.ellipse([sx, sy, sx + stone - 1, sy + stone - 1],
                          outline=(136, 136, 136), width=1)

    # ══════ .status-bar ══════
    stx, sty = ox + HEAD_PX, oy + y_status
    stw = card_w - HEAD_PX * 2
    fg, bg_a, brd_a = {
        "win":     (C["warning"], 0.10, 0.40),
        "playing": (C["primary"], 0.10, 0.40),
        "waiting": (C["warning"], 0.10, 0.40),
        "draw":    (C["t3"], 0.04, 0.10),
    }[sclass]
    paste_alpha_rounded(img,
                        Image.new("RGB", (int(stw), int(status_h)),
                                  (255, 255, 255) if sclass == "draw" else fg),
                        (stx, sty), 9, bg_a)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([stx, sty, stx + stw - 1, sty + status_h - 1], 9,
                        outline=blend_over(img, (stx + stw / 2, sty), fg, brd_a), width=1)
    d.text((stx + stw / 2, sty + status_h / 2), stext,
           font=cjk(STATUS_FS, bold=True), fill=fg, anchor="mm")

    # ══════ .foot ══════
    fy0 = oy + y_foot
    fa = np.linspace(0.15, 0.30, int(foot_h), dtype=np.float32)[:, None]
    fmask = Image.fromarray(
        (np.repeat(fa, int(card_w), axis=1) * 255).astype(np.uint8), "L")
    img.paste(Image.new("RGB", (int(card_w), int(foot_h)), (0, 0, 0)),
              (ox, int(fy0)), fmask)
    d = ImageDraw.Draw(img)
    _hairline(d, img, ox, ox + card_w, int(fy0))
    _grad_line(img, ox + 24, int(fy0), card_w - 48, C["primary"], C["accent"], 0.4)
    fyc = fy0 + FOOT_PT + lh(FOOT_FS) / 2
    fx = ox + FOOT_PX
    d.ellipse([fx, fyc - 3, fx + 6, fyc + 3], fill=C["success"])
    fx += 6 + 8
    for s_, col in (("幻梦 Bot", C["primary_light"]), (" · ", C["t3"]),
                    ("手数 %s" % _moves, C["t3"]), (" · ", C["t3"]),
                    ("H8 或 8,8", C["t3"])):
        d.text((fx, fyc), s_, font=f_foot, fill=col, anchor="lm")
        fx += d.textlength(s_, font=f_foot)
    grad_text(img, (ox + card_w - FOOT_PX, fyc), brand_en, mono(9.5),
              C["primary"], C["accent"], 135.0, anchor="rm")

    return img


def save_wzq_board(game, black_name: str, white_name: str, col_labels: list,
                   date_str: str, out_path, title_en: str = "GOMOKU",
                   brand_en: str = "HUANMENG", **kw):
    """渲染并保存为 JPEG（bot 走这条）。**kw 透传给 render_wzq_board（围棋用它传棋盘参数）"""
    return save_jpeg(render_wzq_board(game, black_name, white_name, col_labels,
                                      date_str, title_en, brand_en, **kw),
                     out_path, 95)


# 通用别名：围棋也走这个（wzq 名字保留是为了不动既有引用）
render_board_card = render_wzq_board
save_board_card = save_wzq_board

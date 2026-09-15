"""
围棋（9×9）对战模块
- 自实现规则：落子 / 提子 / 自杀禁手 / 打劫 / pass / 数子
- AI：启发式评分（吃子 / 气 / 连接 / 进攻）+ 可选一步反制，分四档
- 渲染：HTML 棋盘 → Playwright 截图（见 render_go_board）

设计取舍：
- 用 9×9（群聊快棋；19×19 在聊天场景太慢且 AI 更难做好）
- 数子用中国规则简化版（子数 + 围空），不做死活判定 —— 结算时提示"仅供参考"
"""
from __future__ import annotations

import json
import random
import time
from pathlib import Path

from core.logger import get_logger

logger = get_logger("go")

def _bot_name() -> str:
    """bot 自己的显示名（AI 对手在棋盘/文案里用它，而不是"AI"/"玩家0"）"""
    try:
        from core.config import get_config
        return get_config().bot_name or "幻梦"
    except Exception:
        return "幻梦"


BOARD_SIZE = 9
EMPTY, BLACK, WHITE = 0, 1, 2
COLOR_NAME = {BLACK: "黑", WHITE: "白"}

_ROOT = Path(__file__).resolve().parent.parent
_GAME_FILE = _ROOT / "data" / "go_games.json"

DIFFICULTIES = {
    "easy":   {"random": 0.55, "lookahead": 0, "label": "新手"},
    "normal": {"random": 0.20, "lookahead": 0, "label": "普通"},
    "hard":   {"random": 0.05, "lookahead": 1, "label": "困难"},
    "expert": {"random": 0.00, "lookahead": 2, "label": "专家"},
}
DEFAULT_DIFFICULTY = "normal"
DIFFICULTY_ALIAS = {
    "新手": "easy", "简单": "easy", "easy": "easy",
    "普通": "normal", "中等": "normal", "normal": "normal",
    "困难": "hard", "hard": "hard",
    "专家": "expert", "expert": "expert",
}


def resolve_difficulty(raw: str) -> str | None:
    if not raw:
        return DEFAULT_DIFFICULTY
    s = str(raw).strip()
    return DIFFICULTY_ALIAS.get(s.lower()) or DIFFICULTY_ALIAS.get(s)


# ════════════════════════════════════════════════════════════
#  棋局状态
# ════════════════════════════════════════════════════════════

_games: dict[int, dict] = {}


def _empty_board() -> list[list[int]]:
    return [[EMPTY] * BOARD_SIZE for _ in range(BOARD_SIZE)]


def _save():
    try:
        _GAME_FILE.parent.mkdir(parents=True, exist_ok=True)
        _GAME_FILE.write_text(json.dumps({str(k): v for k, v in _games.items()},
                                         ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.warning("围棋存档失败: %s", e)


def _load():
    global _games
    if not _GAME_FILE.exists():
        return
    try:
        raw = json.loads(_GAME_FILE.read_text(encoding="utf-8"))
        _games = {int(k): v for k, v in raw.items()}
    except Exception as e:
        logger.warning("围棋读档失败: %s", e)


def get_game(chat_id: int) -> dict | None:
    return _games.get(chat_id)


# ════════════════════════════════════════════════════════════
#  规则：气 / 提子 / 禁手 / 打劫
# ════════════════════════════════════════════════════════════

def _neighbors(r: int, c: int):
    if r > 0: yield r - 1, c
    if r < BOARD_SIZE - 1: yield r + 1, c
    if c > 0: yield r, c - 1
    if c < BOARD_SIZE - 1: yield r, c + 1


def _group(board: list, r: int, c: int) -> tuple[set, int]:
    """返回同色连通块的点集与气数"""
    color = board[r][c]
    if color == EMPTY:
        return set(), 0
    stack = [(r, c)]
    seen = {(r, c)}
    libs = set()
    while stack:
        cr, cc = stack.pop()
        for nr, nc in _neighbors(cr, cc):
            v = board[nr][nc]
            if v == EMPTY:
                libs.add((nr, nc))
            elif v == color and (nr, nc) not in seen:
                seen.add((nr, nc))
                stack.append((nr, nc))
    return seen, len(libs)


def try_place(board: list, r: int, c: int, color: int,
              ko_point: tuple | None = None) -> tuple:
    """尝试落子。

    返回 (ok, 新盘面, 被提子列表, 新的打劫禁着点, 错误说明)
    """
    if not (0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE):
        return False, board, [], None, "超出棋盘"
    if board[r][c] != EMPTY:
        return False, board, [], None, "这里已经有子了"
    if ko_point and (r, c) == ko_point:
        return False, board, [], None, "打劫：这手不能立刻提回"

    nb = [row[:] for row in board]
    nb[r][c] = color
    opp = WHITE if color == BLACK else BLACK

    captured: list = []
    for nr, nc in _neighbors(r, c):
        if nb[nr][nc] == opp:
            grp, libs = _group(nb, nr, nc)
            if libs == 0:
                for gr, gc in grp:
                    nb[gr][gc] = EMPTY
                captured.extend(grp)

    mygroup, mylibs = _group(nb, r, c)
    if mylibs == 0:
        return False, board, [], None, "自杀手（这手之后自己没气）"

    new_ko = None
    # 单子提单子且自己只剩一口气 → 形成劫，禁对方立即回提
    if len(captured) == 1 and len(mygroup) == 1 and mylibs == 1:
        new_ko = captured[0]
    return True, nb, captured, new_ko, ""


def score(board: list) -> tuple[int, int]:
    """中国规则简化数子：己方子数 + 只被己方包围的空点（不做死活判定）"""
    b = sum(row.count(BLACK) for row in board)
    w = sum(row.count(WHITE) for row in board)
    seen = set()
    for r in range(BOARD_SIZE):
        for c in range(BOARD_SIZE):
            if board[r][c] != EMPTY or (r, c) in seen:
                continue
            # flood fill 空区，看邻接哪些颜色
            stack = [(r, c)]
            region = {(r, c)}
            touch = set()
            while stack:
                cr, cc = stack.pop()
                for nr, nc in _neighbors(cr, cc):
                    v = board[nr][nc]
                    if v == EMPTY and (nr, nc) not in region:
                        region.add((nr, nc))
                        stack.append((nr, nc))
                    elif v != EMPTY:
                        touch.add(v)
            seen |= region
            if touch == {BLACK}:
                b += len(region)
            elif touch == {WHITE}:
                w += len(region)
    return b, w


# ════════════════════════════════════════════════════════════
#  AI
# ════════════════════════════════════════════════════════════

def _move_score(board: list, r: int, c: int, color: int, ko_point) -> tuple:
    """启发式评估一手棋。返回 (分数, 提子数, 新盘面)"""
    ok, nb, caps, nko, _ = try_place(board, r, c, color, ko_point)
    if not ok:
        return None, 0, None
    opp = WHITE if color == BLACK else BLACK
    s = 0.0
    s += len(caps) * 12.0                       # 提子价值最高
    _, mylibs = _group(nb, r, c)
    s += min(mylibs, 6) * 1.5                   # 自己气越多越安全
    # 进攻：压缩对手气（打吃）
    for nr, nc in _neighbors(r, c):
        if board[nr][nc] == opp:
            _, ol = _group(board, nr, nc)
            if ol == 1:
                s += 6.0                        # 直接叫吃
            elif ol == 2:
                s += 2.0
    # 连接：靠近己方棋子
    for nr, nc in _neighbors(r, c):
        if board[nr][nc] == color:
            s += 1.2
    # 别填自己的眼：若自己某块棋只有这一个气，等于自杀式填眼
    if mylibs == 1 and len(caps) == 0:
        s -= 8.0
    # 线位偏好（3~5 线好，1 线差）
    line = min(r, c, BOARD_SIZE - 1 - r, BOARD_SIZE - 1 - c) + 1
    s += {1: -3.0, 2: -0.5, 3: 1.2, 4: 1.2, 5: 0.6}.get(line, 0.0)
    # 空盘初期别往角上钻
    if not any(v != EMPTY for row in board for v in row):
        cr = cc = BOARD_SIZE // 2
        s -= (abs(r - cr) + abs(c - cc)) * 0.15
    return s, len(caps), nb


def ai_pick(board: list, color: int, ko_point, difficulty: str = DEFAULT_DIFFICULTY):
    """AI 选点。返回 (r, c) 或 None（表示 pass）"""
    prof = DIFFICULTIES.get(difficulty) or DIFFICULTIES[DEFAULT_DIFFICULTY]
    cands = []
    for r in range(BOARD_SIZE):
        for c in range(BOARD_SIZE):
            if board[r][c] != EMPTY:
                continue
            sc, caps, nb = _move_score(board, r, c, color, ko_point)
            if sc is not None:
                cands.append((sc, r, c, nb))
    if not cands:
        return None
    cands.sort(key=lambda x: -x[0])

    if prof["random"] and random.random() < prof["random"]:
        return random.choice(cands)[1:3]

    # lookahead：看对手最佳回应的得分，扣掉（一层近似）
    if prof["lookahead"] > 0:
        opp = WHITE if color == BLACK else BLACK
        top = cands[:8]
        refined = []
        for sc, r, c, nb in top:
            worst = 0.0
            for rr in range(BOARD_SIZE):
                for cc in range(BOARD_SIZE):
                    if nb[rr][cc] != EMPTY:
                        continue
                    osc, _, _ = _move_score(nb, rr, cc, opp, None)
                    if osc is not None and osc > worst:
                        worst = osc
            refined.append((sc - worst * 0.8, r, c))
        refined.sort(key=lambda x: -x[0])
        return refined[0][1], refined[0][2]

    return cands[0][1], cands[0][2]


# ════════════════════════════════════════════════════════════
#  对外：开局 / 落子 / pass / 认输
# ════════════════════════════════════════════════════════════

def start_game(user_id: int, chat_id: int, difficulty: str = DEFAULT_DIFFICULTY) -> str:
    if chat_id in _games:
        return "这里已经有一局围棋在进行中喵~ 用 /~go resign 认输结束"
    diff = resolve_difficulty(difficulty) or DEFAULT_DIFFICULTY
    _games[chat_id] = {
        "player_id": user_id,
        "board": _empty_board(),
        "turn": BLACK,                 # 玩家执黑先行
        "player_color": BLACK,
        "captures": {BLACK: 0, WHITE: 0},
        "ko_point": None,
        "passes": 0,
        "move_count": 0,
        "last_move": None,
        "status": "playing",
        "difficulty": diff,
        "start_time": int(time.time()),
    }
    _save()
    return f"ok:{DIFFICULTIES[diff]['label']}"


def _coord_label(r: int, c: int) -> str:
    letters = "ABCDEFGHJ"        # 围棋惯例跳过 I
    return f"{letters[c]}{BOARD_SIZE - r}"


def parse_coord(raw: str) -> tuple[int, int] | None:
    """解析落子坐标：D4 / d4 / 4,4 / 4 4"""
    s = str(raw).strip().upper().replace(",", " ").replace("，", " ")
    parts = [p for p in s.split() if p]
    letters = "ABCDEFGHJ"
    if len(parts) == 2:
        try:
            r0, c0 = int(parts[0]) - 1, int(parts[1]) - 1
            if 0 <= r0 < BOARD_SIZE and 0 <= c0 < BOARD_SIZE:
                return r0, c0
        except ValueError:
            pass
    m = parts[0] if parts else s
    if len(m) >= 2 and m[0] in letters:
        try:
            num = int(m[1:])
        except ValueError:
            return None
        c0 = letters.index(m[0])
        r0 = BOARD_SIZE - num
        if 0 <= r0 < BOARD_SIZE and 0 <= c0 < BOARD_SIZE:
            return r0, c0
    return None


def _ai_turn(game: dict) -> str:
    """玩家下完 → AI 接手。返回附加消息"""
    diff = game.get("difficulty", DEFAULT_DIFFICULTY)
    color = WHITE if game["player_color"] == BLACK else BLACK
    pick = ai_pick(game["board"], color, game.get("ko_point"), diff)
    if pick is None:
        game["passes"] += 1
        game["turn"] = game["player_color"]
        if game["passes"] >= 2:
            _finish(game)
            return f"{_bot_name()} 选择停一手，双方连续 pass，终局！"
        return f"{_bot_name()} 选择停一手（pass）"
    r, c = pick
    ok, nb, caps, nko, err = try_place(game["board"], r, c, color, game.get("ko_point"))
    if not ok:                                  # 理论上不会发生
        logger.warning("围棋AI落子失败: %s", err)
        game["turn"] = game["player_color"]
        return f"{_bot_name()} 停一手"
    game["board"] = nb
    game["captures"][color] += len(caps)
    game["ko_point"] = nko
    game["last_move"] = [r, c]
    game["move_count"] += 1
    game["passes"] = 0
    game["turn"] = game["player_color"]
    _save()
    extra = f"（提了 {len(caps)} 子）" if caps else ""
    extra += "（打劫）" if nko else ""
    return f"{_bot_name()} 落子 {_coord_label(r, c)}{extra}"


def make_move(user_id: int, chat_id: int, raw: str) -> tuple:
    """玩家落子。返回 (ok, 消息, 是否需要渲染)"""
    game = _games.get(chat_id)
    if not game:
        return False, "这里没有围棋对局喵~ 用 /~go start [难度] 开局", False
    if game["status"] != "playing":
        return False, "这局已经结束了喵~", False
    if user_id != game["player_id"]:
        return False, "这不是你的对局喵~", False
    if game["turn"] != game["player_color"]:
        return False, "还没轮到你喵~", False

    pos = parse_coord(raw)
    if pos is None:
        return False, f"坐标「{raw}」看不懂喵~ 试试 D4 或 4,4（字母跳过 I）", False
    r, c = pos
    ok, nb, caps, nko, err = try_place(game["board"], r, c,
                                       game["player_color"], game.get("ko_point"))
    if not ok:
        return False, f"不能下这里喵：{err}", False

    game["board"] = nb
    game["captures"][game["player_color"]] += len(caps)
    game["ko_point"] = nko
    game["last_move"] = [r, c]
    game["move_count"] += 1
    game["passes"] = 0
    game["turn"] = WHITE if game["player_color"] == BLACK else BLACK

    msg = f"你落子 {_coord_label(r, c)}"
    if caps:
        msg += f"，提了 {len(caps)} 子"
    msg += "；" + _ai_turn(game)
    _save()
    return True, msg, True


def do_pass(user_id: int, chat_id: int) -> tuple:
    game = _games.get(chat_id)
    if not game or game["status"] != "playing":
        return False, "这里没有进行中的围棋对局喵~", False
    if user_id != game["player_id"]:
        return False, "这不是你的对局喵~", False
    game["passes"] += 1
    game["turn"] = WHITE if game["player_color"] == BLACK else BLACK
    msg = "你选择停一手；" + _ai_turn(game)
    return True, msg, game["status"] == "playing"


def _finish(game: dict):
    game["status"] = "finished"
    b, w = score(game["board"])
    game["final_score"] = {"black": b, "white": w}
    _save()


def resign_game(user_id: int, chat_id: int) -> str:
    game = _games.get(chat_id)
    if not game:
        return "这里没有围棋对局喵~"
    if user_id != game["player_id"]:
        return "这不是你的对局喵~"
    _finish(game)
    _record_go_context(chat_id, game, f"玩家认输，{_bot_name()} 获胜")
    del _games[chat_id]
    _save()
    return f"你认输了喵~ {_bot_name()} 获胜！"


def end_game(chat_id: int) -> str:
    """双方连续 pass 后结算"""
    game = _games.get(chat_id)
    if not game:
        return "这里没有围棋对局喵~"
    _finish(game)
    b, w = game["final_score"]["black"], game["final_score"]["white"]
    diff = game.get("difficulty", DEFAULT_DIFFICULTY)
    del _games[chat_id]
    _save()
    _record_go_context(chat_id, game, f"终局结算 黑{b} : 白{w}")
    return (f"终局！数子结果（中国规则简化，未判死活）：\n"
            f"  你(黑) {b} 子  ·  {_bot_name()}(白) {w} 子\n"
            f"  {'你赢了' if b > w else ((_bot_name() + ' 赢') if w > b else '平局')}喵~（难度：{DIFFICULTIES.get(diff, {}).get('label', diff)}）")


def _record_go_context(group_id: int, game: dict, result: str) -> None:
    """把围棋结果写进会话上下文（同五子棋/象棋）"""
    try:
        from core.context_manager import get_context_mgr
        pid = game.get("player_id")
        try:
            from core.config import get_config
            pname = get_config().get_display_name(str(pid), group_id=group_id) if pid else "玩家"
        except Exception:
            pname = str(pid)
        moves = game.get("move_count", 0)
        get_context_mgr().append_to_context(
            group_id,
            f"[棋局] 围棋对局结束：{pname}(黑) vs {_bot_name()}(白)，{result}，共 {moves} 手",
        )
        logger.info("围棋结果已写入上下文: chat=%d %s", group_id, result)
    except Exception as e:
        logger.warning("围棋结果写入上下文失败: %s", e)




# ════════════════════════════════════════════════════════════
#  渲染（HTML 棋盘 → Playwright 截图）
# ════════════════════════════════════════════════════════════

def build_board_html(game: dict) -> str:
    """复用五子棋的棋盘模板（data/templates/wzq_board.html），只换网格数与标题。

    模板占位符：BOARD_N / CELL / STONE / TITLE_EN / CELLS / COL_LABELS 等
    """
    tmpl = (_ROOT / "data" / "templates" / "wzq_board.html").read_text(encoding="utf-8")
    letters = "ABCDEFGHJ"          # 围棋惯例：跳过 I
    col_labels = "".join(f'<div class="col-label">{c}</div>' for c in letters)
    star = {(2, 2), (2, 6), (6, 2), (6, 6), (4, 4)}
    last = list(game.get("last_move") or [])
    cells = ""
    for r in reversed(range(BOARD_SIZE)):
        cells += f'<div class="row-label">{BOARD_SIZE - r}</div>'
        for c in range(BOARD_SIZE):
            v = game["board"][r][c]
            content = ""
            if v != EMPTY:
                color = "black" if v == BLACK else "white"
                lm = " last-move" if last == [r, c] else ""
                content = f'<div class="stone-piece {color}{lm}"></div>'
            elif (r, c) in star:
                content = '<div class="star-point"></div>'
            cells += f'<div class="cell">{content}</div>'

    my_turn = game.get("turn") == game.get("player_color")
    caps = game.get("captures", {})
    prof = DIFFICULTIES.get(game.get("difficulty", DEFAULT_DIFFICULTY), {})
    sub = (f'围棋 9×9 · 难度{prof.get("label", "普通")} · 手数 {game.get("move_count", 0)}'
           f' · 提子 {caps.get(BLACK, 0)}:{caps.get(WHITE, 0)}')
    status_text = "该你落子（你执黑）" if my_turn else f"{_bot_name()} 思考中…"
    status_class = "playing" if game.get("status") == "playing" else "win"
    return (tmpl
            .replace("${BOARD_N}", str(BOARD_SIZE))
            .replace("${CELL}", "44")
            .replace("${STONE}", "36")
            .replace("${TITLE_EN}", "GO")
            .replace("${SUB_TEXT}", sub)
            .replace("${DATE}", time.strftime("%Y-%m-%d %H:%M"))
            .replace("${BLACK_NAME}", "你")
            .replace("${WHITE_NAME}", _bot_name())
            .replace("${BLACK_ACTIVE}", "active-turn" if my_turn else "")
            .replace("${WHITE_ACTIVE}", "" if my_turn else "active-turn")
            .replace("${COL_LABELS}", col_labels)
            .replace("${CELLS}", cells)
            .replace("${STATUS_CLASS}", status_class)
            .replace("${STATUS_TEXT}", status_text)
            .replace("${MOVE_COUNT}", str(game.get("move_count", 0))))


async def render_board(chat_id: int, test_game: dict | None = None) -> str | None:
    """渲染棋盘（复用 changelog.render_card_to_image，与五子棋同一条渲染管线）"""
    game = test_game or _games.get(chat_id)
    if not game:
        return None
    html = build_board_html(game)
    try:
        from modules.changelog import render_card_to_image
        return await render_card_to_image(html, f"go_{chat_id}.png", width=680)
    except Exception as e:
        logger.warning("围棋棋盘渲染失败: %s", e)
        return None


_load()

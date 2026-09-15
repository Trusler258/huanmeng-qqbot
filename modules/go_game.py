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


def _player_name(qq: int, chat_id: int) -> str:
    """玩家显示名（qq=0 表示 AI → 用 bot 名）"""
    if not qq:
        return _bot_name()
    try:
        from core.config import get_config
        return get_config().get_display_name(str(qq), group_id=chat_id)
    except Exception:
        return str(qq)


BOARD_SIZE = 19          # 默认棋盘（19 标准 / 13 快棋 / 9 迷你），可用 /~go start [难度] [尺寸] 指定
BOARD_SIZES = (9, 13, 19)
# 每格像素尺寸：盘越大格子越小，保证出图宽度基本一致
_CELL_SIZES = {9: 44, 13: 34, 19: 26}
EMPTY, BLACK, WHITE = 0, 1, 2
# 坐标字母：围棋惯例跳过 I
_ALL_LETTERS = "ABCDEFGHJKLMNOPQRST"
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


def letters_of(size: int) -> str:
    """该尺寸的列坐标字母（跳过 I）"""
    return _ALL_LETTERS[:size]


def star_points(size: int) -> set:
    """该尺寸的星位"""
    if size == 9:
        return {(2, 2), (2, 6), (6, 2), (6, 6), (4, 4)}
    if size == 13:
        return {(3, 3), (3, 9), (9, 3), (9, 9), (6, 6)}
    return {(3, 3), (3, 9), (3, 15), (9, 3), (9, 9), (9, 15),
            (15, 3), (15, 9), (15, 15)}


def _empty_board(size: int = BOARD_SIZE) -> list[list[int]]:
    return [[EMPTY] * size for _ in range(size)]


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
        # 合并语义：只补内存里没有的对局，不覆盖内存中更新的状态
        # （web 服务与 bot 若是不同进程，靠这个也能看到对方创建的棋局）
        for k, v in raw.items():
            # ★ JSON 会把整数键转成字符串：captures={1:0,2:0} 存成 {"1":0,"2":0}，
            #   直接读回会在 captures[BLACK] 处 KeyError → 这里转回 int 键
            caps = v.get("captures")
            if isinstance(caps, dict):
                v["captures"] = {int(kk): vv for kk, vv in caps.items()}
            _games.setdefault(int(k), v)
    except Exception as e:
        logger.warning("围棋读档失败: %s", e)


def web_reload() -> None:
    """从磁盘覆盖读入棋局（不动文件）。供棋局 Web 跨进程看到最新落子。

    _load() 是 setdefault 合并语义（只补没有的），双进程下 web 进程
    一旦内存里有旧局就永远看不到 bot 进程的新落子 —— 这里用覆盖语义。
    """
    if not _GAME_FILE.exists():
        return
    try:
        raw = json.loads(_GAME_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("围棋覆盖读失败: %s", e)
        return
    for k, v in raw.items():
        caps = v.get("captures")
        if isinstance(caps, dict):
            v["captures"] = {int(kk): vv for kk, vv in caps.items()}
        _games[int(k)] = v


def get_game(chat_id: int) -> dict | None:
    return _games.get(chat_id)


# ════════════════════════════════════════════════════════════
#  规则：气 / 提子 / 禁手 / 打劫
# ════════════════════════════════════════════════════════════

def _neighbors(r: int, c: int, size: int):
    if r > 0: yield r - 1, c
    if r < size - 1: yield r + 1, c
    if c > 0: yield r, c - 1
    if c < size - 1: yield r, c + 1


def _group(board: list, r: int, c: int) -> tuple[set, int]:
    """返回同色连通块的点集与气数"""
    color = board[r][c]
    if color == EMPTY:
        return set(), 0
    stack = [(r, c)]
    seen = {(r, c)}
    libs = set()
    size = len(board)
    while stack:
        cr, cc = stack.pop()
        for nr, nc in _neighbors(cr, cc, size):
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
    size = len(board)
    if not (0 <= r < size and 0 <= c < size):
        return False, board, [], None, "超出棋盘"
    if board[r][c] != EMPTY:
        return False, board, [], None, "这里已经有子了"
    if ko_point and (r, c) == ko_point:
        return False, board, [], None, "打劫：这手不能立刻提回"

    nb = [row[:] for row in board]
    nb[r][c] = color
    opp = WHITE if color == BLACK else BLACK

    captured: list = []
    for nr, nc in _neighbors(r, c, size):
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
    size = len(board)
    b = sum(row.count(BLACK) for row in board)
    w = sum(row.count(WHITE) for row in board)
    seen = set()
    for r in range(size):
        for c in range(size):
            if board[r][c] != EMPTY or (r, c) in seen:
                continue
            # flood fill 空区，看邻接哪些颜色
            stack = [(r, c)]
            region = {(r, c)}
            touch = set()
            while stack:
                cr, cc = stack.pop()
                for nr, nc in _neighbors(cr, cc, size):
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
    size = len(board)
    for nr, nc in _neighbors(r, c, size):
        if board[nr][nc] == opp:
            _, ol = _group(board, nr, nc)
            if ol == 1:
                s += 6.0                        # 直接叫吃
            elif ol == 2:
                s += 2.0
    # 连接：靠近己方棋子
    for nr, nc in _neighbors(r, c, size):
        if board[nr][nc] == color:
            s += 1.2
    # 别填自己的眼：若自己某块棋只有这一个气，等于自杀式填眼
    if mylibs == 1 and len(caps) == 0:
        s -= 8.0
    # 线位偏好（3~5 线好，1 线差）
    line = min(r, c, size - 1 - r, size - 1 - c) + 1
    s += {1: -3.0, 2: -0.5, 3: 1.2, 4: 1.2, 5: 0.6}.get(line, 0.0)
    # 空盘初期别往角上钻
    if not any(v != EMPTY for row in board for v in row):
        cr = cc = size // 2
        s -= (abs(r - cr) + abs(c - cc)) * 0.15
    return s, len(caps), nb


def _candidates(board: list) -> list:
    """邻近启发：只评估已有棋子周围 2 格内的空点。

    19×19 全盘有 361 个点，每点还要 flood-fill 算气 —— 直接扫会慢到秒级；
    围棋的有效着法几乎都在已有棋子附近，收敛候选集后速度提升一个数量级。
    空盘时返回天元。
    """
    size = len(board)
    if not any(v != EMPTY for row in board for v in row):
        mid = size // 2
        return [(mid, mid)]
    pts = set()
    for r in range(size):
        for c in range(size):
            if board[r][c] == EMPTY:
                continue
            for dr in (-2, -1, 0, 1, 2):
                for dc in (-2, -1, 0, 1, 2):
                    nr, nc = r + dr, c + dc
                    if 0 <= nr < size and 0 <= nc < size and board[nr][nc] == EMPTY:
                        pts.add((nr, nc))
    return list(pts)


def ai_pick(board: list, color: int, ko_point, difficulty: str = DEFAULT_DIFFICULTY):
    """AI 选点。返回 (r, c) 或 None（表示 pass）"""
    prof = DIFFICULTIES.get(difficulty) or DIFFICULTIES[DEFAULT_DIFFICULTY]
    size = len(board)
    cands = []
    for r, c in _candidates(board):
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
            for rr, cc in _candidates(nb):
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

def resolve_board_size(raw) -> int | None:
    """解析棋盘尺寸参数（9/13/19，支持 99/小/中/大 等说法）"""
    if raw is None or str(raw).strip() == "":
        return BOARD_SIZE
    s = str(raw).strip().lower()
    alias = {"小": 9, "迷你": 9, "mini": 9, "99": 9, "小盘": 9,
             "中": 13, "标准快棋": 13, "mid": 13,
             "大": 19, "标准": 19, "大盘": 19, "full": 19, "标准盘": 19}
    if s in alias:
        return alias[s]
    try:
        n = int(s)
        return n if n in BOARD_SIZES else None
    except Exception:
        return None


def start_game(user_id: int, chat_id: int, difficulty: str = DEFAULT_DIFFICULTY,
               size: int = BOARD_SIZE, opponent_id: int = 0) -> str:
    """开局。opponent_id=0 → 人机；否则是群内双人对战（发起人执黑）"""
    old = _games.get(chat_id)
    if old and old.get("status") == "playing":
        return "这里已经有一局围棋在进行中喵~ 用 /~go resign 认输结束"
    diff = resolve_difficulty(difficulty) or DEFAULT_DIFFICULTY
    if size not in BOARD_SIZES:
        size = BOARD_SIZE
    _games[chat_id] = {
        "player_id": user_id,
        "opponent_id": int(opponent_id or 0),   # 0 = AI；非 0 = 群内对手的 QQ
        "size": size,
        "board": _empty_board(size),
        "turn": BLACK,                 # 玩家执黑先行
        "player_color": BLACK,
        "captures": {BLACK: 0, WHITE: 0},
        "ko_point": None,
        "passes": 0,
        "move_count": 0,
        "moves": [],
        "last_move": None,
        "status": "playing",
        "difficulty": diff,
        "start_time": int(time.time()),
    }
    _save()
    return f"ok:{DIFFICULTIES[diff]['label']}"


def _color_of(game: dict, user_id: int) -> int:
    """该用户在这局里执什么颜色；不是参与者返回 0"""
    if not user_id:
        return 0
    if user_id == game.get("player_id"):
        return BLACK
    if user_id == game.get("opponent_id"):
        return WHITE
    return 0


def is_pvp(game: dict) -> bool:
    """是否群内双人对战（对手不是 AI）"""
    return bool(game.get("opponent_id"))


def _coord_label(r: int, c: int, size: int = BOARD_SIZE) -> str:
    return f"{letters_of(size)[c]}{size - r}"


def parse_coord(raw: str, size: int = BOARD_SIZE) -> tuple[int, int] | None:
    """解析落子坐标：D4 / d4 / 4,4 / 4 4"""
    s = str(raw).strip().upper().replace(",", " ").replace("，", " ")
    parts = [p for p in s.split() if p]
    letters = letters_of(size)
    if len(parts) == 2:
        try:
            r0, c0 = int(parts[0]) - 1, int(parts[1]) - 1
            if 0 <= r0 < size and 0 <= c0 < size:
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
        r0 = size - num
        if 0 <= r0 < size and 0 <= c0 < size:
            return r0, c0
    return None


def _push_move(game: dict, r: int, c: int, color: int) -> None:
    """记一手棋（供网页棋谱/复盘用）"""
    size = len(game["board"])
    game.setdefault("moves", []).append({
        "n": len(game.get("moves") or []) + 1,
        "r": r, "c": c,
        "color": "black" if color == BLACK else "white",
        "label": _coord_label(r, c, size),
    })


def _push_pass(game: dict, color: int) -> None:
    game.setdefault("moves", []).append({
        "n": len(game.get("moves") or []) + 1,
        "pass": True,
        "color": "black" if color == BLACK else "white",
        "label": "pass",
    })


def _ai_turn(game: dict) -> str:
    """玩家下完 → AI 接手。返回附加消息"""
    diff = game.get("difficulty", DEFAULT_DIFFICULTY)
    color = WHITE if game["player_color"] == BLACK else BLACK
    pick = ai_pick(game["board"], color, game.get("ko_point"), diff)
    if pick is None:
        game["passes"] += 1
        game["turn"] = game["player_color"]
        _push_pass(game, color)
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
    _push_move(game, r, c, color)
    _save()
    size = len(game["board"])
    extra = f"（提了 {len(caps)} 子）" if caps else ""
    extra += "（打劫）" if nko else ""
    return f"{_bot_name()} 落子 {_coord_label(r, c, size)}{extra}"


def make_move(user_id: int, chat_id: int, raw: str) -> tuple:
    """玩家落子。返回 (ok, 消息, 是否需要渲染)"""
    game = _games.get(chat_id)
    if not game:
        return False, "这里没有围棋对局喵~ 用 /~go start [难度] 开局", False
    if game["status"] != "playing":
        return False, "这局已经结束了喵~", False
    color = _color_of(game, user_id)
    if not color:
        return False, "这不是你的对局喵~", False
    if game["turn"] != color:
        return False, "还没轮到你喵~", False

    size = len(game["board"])
    pos = parse_coord(raw, size)
    if pos is None:
        return False, f"坐标「{raw}」看不懂喵~ 试试 D4 或 4,4（字母跳过 I）", False
    r, c = pos
    ok, nb, caps, nko, err = try_place(game["board"], r, c, color, game.get("ko_point"))
    if not ok:
        return False, f"不能下这里喵：{err}", False

    game["board"] = nb
    game["captures"][color] += len(caps)
    game["ko_point"] = nko
    game["last_move"] = [r, c]
    game["move_count"] += 1
    game["passes"] = 0
    game["turn"] = WHITE if color == BLACK else BLACK
    _push_move(game, r, c, color)

    msg = f"你落子 {_coord_label(r, c, size)}"
    if caps:
        msg += f"，提了 {len(caps)} 子"
    if is_pvp(game):
        _save()                                   # 人人对战：等对方走
        return True, msg, True
    msg += "；" + _ai_turn(game)
    _save()
    return True, msg, True


def do_pass(user_id: int, chat_id: int) -> tuple:
    game = _games.get(chat_id)
    if not game:
        return False, "这里没有进行中的围棋对局喵~", False
    if game["status"] != "playing":
        return False, "这局已经结束了喵~", False
    color = _color_of(game, user_id)
    if not color:
        return False, "这不是你的对局喵~", False
    if game["turn"] != color:
        return False, "还没轮到你喵~", False
    game["passes"] += 1
    game["turn"] = WHITE if color == BLACK else BLACK
    _push_pass(game, color)
    if is_pvp(game):
        msg = "你选择停一手"
        if game["passes"] >= 2:
            _finish(game)
            msg += "，对方也停一手，终局！"
        _save()
        return True, msg, game["status"] == "playing"
    msg = "你选择停一手；" + _ai_turn(game)
    return True, msg, game["status"] == "playing"


def _finish(game: dict):
    game["status"] = "finished"
    game["end_time"] = int(time.time())
    b, w = score(game["board"])
    game["final_score"] = {"black": b, "white": w}
    _save()


def resign_game(user_id: int, chat_id: int) -> str:
    game = _games.get(chat_id)
    if not game:
        return "这里没有围棋对局喵~"
    color = _color_of(game, user_id)
    if not color:
        return "这不是你的对局喵~"
    if game.get("status") != "playing":
        return "这局已经结束了喵~"
    _finish(game)
    if is_pvp(game):
        win_color = WHITE if color == BLACK else BLACK
        win_id = game["player_id"] if win_color == BLACK else game["opponent_id"]
        game["winner_color"] = "black" if win_color == BLACK else "white"
        game["result_text"] = f"{_player_name(user_id, chat_id)} 认输，{_player_name(win_id, chat_id)} 获胜"
    else:
        game["winner_color"] = "white"
        game["result_text"] = f"你认输，{_bot_name()} 获胜"
    _record_go_context(chat_id, game, game["result_text"])
    _save()
    return f"你认输了喵~ {game['result_text'].rsplit('，', 1)[-1]}！"


def end_game(chat_id: int) -> str:
    """双方连续 pass 后结算"""
    game = _games.get(chat_id)
    if not game:
        return "这里没有围棋对局喵~"
    _finish(game)
    b, w = game["final_score"]["black"], game["final_score"]["white"]
    diff = game.get("difficulty", DEFAULT_DIFFICULTY)
    bn = _player_name(game.get("player_id"), chat_id)
    wn = _player_name(game.get("opponent_id"), chat_id)
    if b > w:
        verdict, game["winner_color"] = f"{bn} 获胜", "black"
    elif w > b:
        verdict, game["winner_color"] = f"{wn} 获胜", "white"
    else:
        verdict, game["winner_color"] = "平局", ""
    game["result_text"] = f"终局数子 黑{b} : 白{w}，{verdict}"
    _save()
    _record_go_context(chat_id, game, f"终局结算 黑{b} : 白{w}")
    return (f"终局！数子结果（中国规则简化，未判死活）：\n"
            f"  {bn}(黑) {b} 子  ·  {wn}(白) {w} 子\n"
            f"  {verdict}喵~（难度：{DIFFICULTIES.get(diff, {}).get('label', diff)}）")


def _record_go_context(group_id: int, game: dict, result: str) -> None:
    """把围棋结果写进会话上下文（同五子棋/象棋）。同一局只写一次"""
    if game.get("_ctx_done"):
        return
    game["_ctx_done"] = True
    try:
        from core.context_manager import get_context_mgr
        pname = _player_name(game.get("player_id"), group_id)
        oname = _player_name(game.get("opponent_id"), group_id)
        mode = "对战" if is_pvp(game) else "人机"
        moves = game.get("move_count", 0)
        get_context_mgr().append_to_context(
            group_id,
            f"[棋局] 围棋{mode}结束：{pname}(黑) vs {oname}(白)，{result}，共 {moves} 手",
        )
        logger.info("围棋结果已写入上下文: chat=%d %s", group_id, result)
    except Exception as e:
        logger.warning("围棋结果写入上下文失败: %s", e)




def web_state(chat_id: int, user_id: int) -> dict | None:
    """棋局快照（供棋局网页渲染）。没有对局返回 None"""
    game = get_game(chat_id)
    if not game:
        return None
    board = game.get("board") or []
    size = len(board) or BOARD_SIZE
    caps = game.get("captures", {})
    st_b = sum(row.count(BLACK) for row in board) if board else 0
    st_w = sum(row.count(WHITE) for row in board) if board else 0
    return {
        "board": game["board"],
        "size": size,
        "letters": letters_of(size),
        "stars": sorted(f"{r},{c}" for r, c in star_points(size)),
        "turn": game.get("turn"),
        "player_color": game.get("player_color"),
        "move_count": game.get("move_count", 0),
        "last_move": game.get("last_move"),
        "captures": {"black": caps.get(BLACK, 0), "white": caps.get(WHITE, 0)},
        "stones": {"black": st_b, "white": st_w},
        "moves": list(game.get("moves") or [])[-60:],
        "passes": game.get("passes", 0),
        "elapsed": max(0, int(time.time()) - int(game.get("start_time") or 0)),
        "final_score": game.get("final_score"),
        "result_text": game.get("result_text"),
        "winner_color": game.get("winner_color") or "",
        "difficulty": DIFFICULTIES.get(game.get("difficulty", "normal"), {}).get("label", "普通"),
        "pvp": is_pvp(game),
        "finished": game.get("status") != "playing",
        "bot": _bot_name(),
        "black": {"id": game.get("player_id"), "name": _player_name(game.get("player_id"), chat_id)},
        "white": {"id": game.get("opponent_id") or 0, "name": _player_name(game.get("opponent_id"), chat_id)},
    }


# ════════════════════════════════════════════════════════════
#  渲染（HTML 棋盘 → Playwright 截图）
# ════════════════════════════════════════════════════════════

def build_board_html(game: dict) -> str:
    """复用五子棋的棋盘模板（data/templates/wzq_board.html），只换网格数与标题。

    模板占位符：BOARD_N / CELL / STONE / TITLE_EN / CELLS / COL_LABELS 等
    """
    size = len(game["board"]) or BOARD_SIZE
    cell = _CELL_SIZES.get(size, 30)
    tmpl = (_ROOT / "data" / "templates" / "wzq_board.html").read_text(encoding="utf-8")
    letters = letters_of(size)      # 围棋惯例：跳过 I
    col_labels = "".join(f'<div class="col-label">{c}</div>' for c in letters)
    star = star_points(size)
    last = list(game.get("last_move") or [])
    cells = ""
    for r in reversed(range(size)):
        cells += f'<div class="row-label">{size - r}</div>'
        for c in range(size):
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
    sub = (f'围棋 {size}×{size} · 难度{prof.get("label", "普通")} · 手数 {game.get("move_count", 0)}'
           f' · 提子 {caps.get(BLACK, 0)}:{caps.get(WHITE, 0)}')
    status_text = "该你落子（你执黑）" if my_turn else f"{_bot_name()} 思考中…"
    status_class = "playing" if game.get("status") == "playing" else "win"
    return (tmpl
            .replace("${BOARD_N}", str(size))
            .replace("${CELL}", str(cell))
            .replace("${STONE}", str(int(cell * 0.82)))
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

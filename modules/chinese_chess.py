"""
中国象棋对战模块
- 使用 python-chinese-chess 引擎
- 支持中文记谱（炮二平五）和 UCI 坐标（h2e2）
- AI 使用 minimax + alpha-beta 剪枝
- SVG 棋盘渲染 → PNG 图片
"""
from __future__ import annotations

import asyncio
import json
import random
import time
from pathlib import Path

from core.logger import get_logger

logger = get_logger("xq")

_ROOT = Path(__file__).resolve().parent.parent
_GAME_FILE = _ROOT / "data" / "xq_games.json"
AI_DEPTH = 2

# 棋子估值
PIECE_VALUES = {"k": 10000, "a": 200, "b": 200, "n": 400, "r": 600, "c": 300, "p": 100}


def _load_games() -> dict:
    if _GAME_FILE.exists():
        try:
            return json.loads(_GAME_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_games(data: dict):
    _GAME_FILE.parent.mkdir(parents=True, exist_ok=True)
    _GAME_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _evaluate(board) -> int:
    score = 0
    for ch in board.board_fen():
        if ch.lower() in PIECE_VALUES:
            v = PIECE_VALUES[ch.lower()]
            score += v if ch.isupper() else -v
    return score


def _minimax(board, depth: int, alpha: int, beta: int, maximizing: bool) -> int:
    if depth == 0 or board.is_game_over():
        return _evaluate(board)
    moves = list(board.legal_moves)
    random.shuffle(moves)
    if maximizing:
        best = -999999
        for move in moves:
            board.push(move)
            best = max(best, _minimax(board, depth - 1, alpha, beta, False))
            board.pop()
            alpha = max(alpha, best)
            if beta <= alpha:
                break
        return best
    else:
        best = 999999
        for move in moves:
            board.push(move)
            best = min(best, _minimax(board, depth - 1, alpha, beta, True))
            board.pop()
            beta = min(beta, best)
            if beta <= alpha:
                break
        return best


def ai_best_move(board) -> "cchess.Move":
    moves = list(board.legal_moves)
    random.shuffle(moves)
    best_move = moves[0]
    best_score = -999999 if board.turn else 999999
    for move in moves:
        board.push(move)
        score = _minimax(board, AI_DEPTH - 1, -999999, 999999, not board.turn)
        board.pop()
        if board.turn:
            if score > best_score:
                best_score = score
                best_move = move
        else:
            if score < best_score:
                best_score = score
                best_move = move
    return best_move


_last_render_task = None   # 最近一次棋盘渲染任务


def _start_render(svg: str, out: str) -> None:
    """启动棋盘渲染（异步）。

    注意：必须配合 wait_render() 使用 —— 渲染是异步的，而上层拿到路径后
    会立刻发图，若不等待就会发出**上一手**的旧盘面（文件名固定会被覆盖）。
    """
    global _last_render_task
    try:
        _last_render_task = asyncio.ensure_future(_svg_to_png(svg, out))
    except RuntimeError as e:          # 没有运行中的事件循环（同步上下文调用）
        logger.warning("棋盘渲染无法启动（无事件循环）: %s", e)


async def wait_render() -> None:
    """等待最近一次棋盘渲染完成（发图前调用）"""
    global _last_render_task
    task = _last_render_task
    _last_render_task = None
    if task is not None:
        try:
            await task
        except Exception as e:
            logger.warning("等待棋盘渲染失败: %s", e)


def _record_xq_context(group_id: int, game: dict, result: str) -> None:
    """把象棋结果写进会话上下文（同五子棋——否则 LLM 不知道刚下过棋）"""
    try:
        from core.context_manager import get_context_mgr
        pid = game.get("player_id")
        try:
            from core.config import get_config
            pname = get_config().get_display_name(str(pid), group_id=group_id) if pid else "玩家"
        except Exception:
            pname = str(pid)
        moves = len(game.get("move_history", []))
        get_context_mgr().append_to_context(
            group_id,
            f"[棋局] 中国象棋对局结束：{pname}(红) vs AI(黑)，{result}，共 {moves} 回合",
        )
        logger.info("象棋结果已写入上下文: group=%d %s", group_id, result)
    except Exception as e:
        logger.warning("象棋结果写入上下文失败: %s", e)


def _board_to_svg(board, lastmove=None, checkers=None):
    import cchess.svg
    kwargs = {"board": board, "size": 600, "coordinates": True}
    if lastmove is not None:
        kwargs["lastmove"] = lastmove
    if checkers:
        kwargs["checkers"] = checkers
    if board.turn:
        kwargs["orientation"] = cchess.RED
    svg = cchess.svg.board(**kwargs)
    # ★ cchess 默认把棋盘居中塞进 1200x1200 的正方画布（棋盘本体其实只有 800x900），
    #   四周留白过多 → 视觉上棋盘被压成"方块"。这里裁到棋盘实际范围（留 20px 边距）。
    import re as _re
    svg = _re.sub(r'viewBox="-600 -600 1200 1200"', 'viewBox="-420 -470 840 940"', svg, count=1)
    svg = _re.sub(r'width="\d+" height="\d+"', 'width="840" height="940"', svg, count=1)
    return svg


async def _svg_to_png(svg_str: str, out_path: str) -> bool:
    try:
        from modules.changelog import _ensure_browser
        browser = await _ensure_browser()
        page = await browser.new_page(viewport={"width": 880, "height": 980})
        html = (f'<html><body style="margin:0;background:#eb5">'
                f'<div id="xqwrap" style="width:840px;height:940px">{svg_str}</div></body></html>')
        await page.set_content(html)
        await page.wait_for_timeout(400)
        # 截包裹层：尺寸与棋盘严格一致（840x940），无任何留白
        el = await page.query_selector("#xqwrap")
        if el:
            await el.screenshot(path=out_path)
        else:
            await page.screenshot(path=out_path, full_page=True)
        await page.close()
        return True
    except Exception as e:
        logger.warning("棋盘渲染失败: %s", e)
        return False


def start_game(user_id: int, group_id: int) -> str:
    import cchess
    games = _load_games()
    key = str(group_id)
    if key in games:
        return "当前群已经有一局象棋在进行中喵~ 用 /~xq resign 认输结束"
    board = cchess.Board()
    games[key] = {
        "player_id": user_id,
        "fen_history": [board.fen()],
        "move_history": [],
        "start_time": int(time.time()),
    }
    _save_games(games)
    return "ok"


def get_game(group_id: int) -> dict | None:
    return _load_games().get(str(group_id))


def _build_board_from_moves(moves: list) -> "cchess.Board":
    import cchess
    board = cchess.Board()
    for m in moves:
        board.push_uci(m)
    return board


def _parse_move(notation: str, board):
    """解析走法，支持中文记谱和 UCI"""
    import cchess
    s = notation.strip()
    # 尝试 UCI
    try:
        return cchess.Move.from_uci(s.lower())
    except Exception:
        pass
    # 尝试中文
    try:
        board.push_notation(s)
        m = board.peek()
        board.pop()
        return m
    except Exception:
        return None


def make_move(user_id: int, group_id: int, notation: str) -> tuple:
    game = get_game(group_id)
    if not game:
        return False, "当前群没有象棋对局喵~ 用 /~xq start 开始", None
    if user_id != game["player_id"]:
        return False, "这不是你的对局喵~", None

    import cchess
    board = _build_board_from_moves(game["move_history"])

    if board.is_game_over():
        _delete_game(group_id)
        return False, "这局已经结束了喵~", None

    move = _parse_move(notation, board)
    if move is None or move not in board.legal_moves:
        return False, f"走法「{notation}」不对喵~ 试试 h2e2 或 炮二平五", None

    board.push(move)
    uci = move.uci()
    game["move_history"].append(uci)
    game["fen_history"].append(board.fen())

    lastmove = board.peek()

    # 将死/困毙
    if board.is_checkmate():
        _delete_game(group_id)
        out = str(_ROOT / "data" / "img_temp" / f"xq_{group_id}.png")
        svg = _board_to_svg(board, lastmove=lastmove, checkers=board.checkers())
        _start_render(svg, out)
        return True, "将死！你赢了喵~", out
    if board.is_stalemate():
        _delete_game(group_id)
        return True, "困毙！和棋喵~", None

    # AI
    ai_move = ai_best_move(board)
    board.push(ai_move)
    game["move_history"].append(ai_move.uci())
    game["fen_history"].append(board.fen())

    ai_comment = " 将军！" if board.is_check() else ""

    end_msg = ""
    if board.is_checkmate():
        end_msg = " 将死！AI赢了喵~"
        _delete_game(group_id)
    elif board.is_stalemate():
        end_msg = " 困毙！和棋喵~"
        _delete_game(group_id)
    else:
        _save_game_after_move(group_id, game)

    try:
        out = str(_ROOT / "data" / "img_temp" / f"xq_{group_id}.png")
        svg = _board_to_svg(board, lastmove=ai_move, checkers=board.checkers())
        _start_render(svg, out)
        return True, f"你: {uci}  |  AI: {ai_move.uci()}{ai_comment}{end_msg}", out
    except Exception as e:
        return True, f"你: {uci}  |  AI: {ai_move.uci()}{ai_comment}{end_msg}", None


def resign_game(user_id: int, group_id: int) -> str:
    game = get_game(group_id)
    if not game:
        return "当前群没有象棋对局喵~"
    if user_id != game["player_id"]:
        return "这不是你的对局喵~"
    _delete_game(group_id)
    _record_xq_context(group_id, game, "玩家认输，AI 获胜")
    return "你认输了喵~ AI 获胜！"


def show_board(group_id: int) -> tuple:
    game = get_game(group_id)
    if not game:
        return "当前群没有象棋对局喵~", None
    board = _build_board_from_moves(game["move_history"])
    try:
        out = str(_ROOT / "data" / "img_temp" / f"xq_{group_id}.png")
        last = board.peek() if board.move_stack else None
        svg = _board_to_svg(board, lastmove=last, checkers=board.checkers())
        _start_render(svg, out)
        return f"当前棋盘（共{len(game['move_history'])}步）", out
    except Exception as e:
        return f"渲染失败: {e}", None


def show_history(group_id: int) -> str:
    game = get_game(group_id)
    if not game:
        return "当前群没有象棋对局喵~"
    moves = game["move_history"]
    if not moves:
        return "还没有走棋喵~"
    lines = ["走棋记录:"]
    for i, m in enumerate(moves):
        color = "红" if i % 2 == 0 else "黑"
        lines.append(f"  {i+1:2d}. [{color}] {m}")
    return "\n".join(lines)


def _save_game_after_move(group_id: int, game: dict):
    games = _load_games()
    games[str(group_id)] = game
    _save_games(games)


def _delete_game(group_id: int):
    games = _load_games()
    games.pop(str(group_id), None)
    _save_games(games)


# 别名
_build_svg = _board_to_svg
INIT_BOARD_FEN = "rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR"

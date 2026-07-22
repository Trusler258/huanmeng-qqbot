"""测试中国象棋模板 — 示例棋局（中盘）"""
import sys
sys.path.insert(0, 'G:/py/qqbot')

import asyncio
from modules.changelog import render_card_to_image

PIECE_CHAR = {
    'r': '帥', 'a': '仕', 'b': '相', 'n': '馬', 'c': '炮', 'p': '兵',
    'R': '將', 'A': '士', 'B': '象', 'N': '馬', 'C': '砲', 'P': '卒',
}

# 示例棋局：走了几手的中盘局面
board = [
    ['r',None,None,'a','r',None,None,'n','c'],      # 10
    [None,None,None,None,None,None,None,None,None],   # 9
    [None,'c','n',None,None,None,'b',None,None],      # 8
    ['p',None,None,None,'p',None,None,None,'p'],      # 7
    [None,None,'p',None,None,None,None,'p',None],     # 6
    [None,None,None,None,'P',None,None,None,None],    # 5
    [None,None,'P',None,None,'C',None,None,'P'],      # 4
    ['P',None,None,'P','C',None,'P',None,None],       # 3
    [None,None,None,None,None,None,None,'B',None],    # 2
    ['R','N','B','A',None,'A',None,'N','R'],          # 1
]

CELL = 56

def build_cell(r, c):
    h = 'h-full'; v = 'v-full'
    if c == 0: h = 'h-right'
    elif c == 8: h = 'h-left'
    if r == 4:
        if 1 <= c <= 7: v = 'v-bot'
    elif r == 5:
        if 1 <= c <= 7: v = 'v-top'
    if r == 0: v = v.replace('v-full', 'v-bot')
    elif r == 9: v = v.replace('v-full', 'v-top')
    return f'<div class="cell"><div class="h-line {h}"></div><div class="v-line {v}"></div></div>'

def build_cells():
    rows = []
    for r in range(10):
        rows.append('<div class="row">' + ''.join(build_cell(r, c) for c in range(9)) + '</div>')
    return '\n'.join(rows)

def build_pieces(board, last_move):
    parts = []
    for r in range(10):
        for c in range(9):
            p = board[r][c]
            if not p: continue
            color = 'red' if p.islower() else 'black'
            char = PIECE_CHAR.get(p.upper(), '?')
            left = c * CELL + CELL // 2
            top = r * CELL + CELL // 2
            lm = ' last-move' if last_move == (r, c) else ''
            parts.append(f'<div class="piece {color}{lm}" style="left:{left}px;top:{top}px;">{char}</div>')
    return '\n'.join(parts)

def build_palace():
    lines = []
    for r, top in [(0, 0), (1, CELL), (7, 7*CELL), (8, 8*CELL)]:
        lines.append(f'<div class="palace-x" style="left:{4*CELL}px;top:{top}px;transform:rotate(45deg) scaleY(0.5);"></div>')
        lines.append(f'<div class="palace-x" style="left:{6*CELL}px;top:{top+CELL}px;transform:rotate(-45deg) scaleY(0.5);"></div>')
    return '\n'.join(lines)

tmpl = open('data/templates/cchess_board.html', encoding='utf-8').read()
tmpl = tmpl.replace('${SUB_TEXT}', '示例棋局 · 中盘局面')
tmpl = tmpl.replace('${RED_NAME}', 'Trusler')
tmpl = tmpl.replace('${BLACK_NAME}', 'AI 对手')
tmpl = tmpl.replace('${STATUS_CLASS}', '')
tmpl = tmpl.replace('${STATUS_TEXT}', '轮到红方落子')
tmpl = tmpl.replace('${TURN_INDICATOR}', '<span class="turn-indicator red"></span>')
tmpl = tmpl.replace('${COL_LABELS}', ''.join(f'<div>{i+1}</div>' for i in range(9)))
tmpl = tmpl.replace('${CELLS}', build_cells())
tmpl = tmpl.replace('${PALACE_LINES}', build_palace())
tmpl = tmpl.replace('${PIECES}', build_pieces(board, (3, 5)))
tmpl = tmpl.replace('${BOARD_W}', str(9 * CELL + 6))
tmpl = tmpl.replace('${BOARD_H}', str(10 * CELL + 6))
tmpl = tmpl.replace('${MOVE_COUNT}', '手数: 12 · 红方走棋')
tmpl = tmpl.replace('${DATE}', '2026-07-13 13:32')

async def main():
    path = await render_card_to_image(tmpl, 'cchess_test.png', width=700)
    print(f'输出: {path}')

asyncio.run(main())

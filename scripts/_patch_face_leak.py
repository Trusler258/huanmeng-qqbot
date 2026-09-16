"""一次性补丁：修复补充句（follow-up）路径的 [FACE:] 标记泄漏

背景：core/pipeline.py 的补充句分支只清理 [fav:N]，漏了 [FACE:]，
      实测 2026-09-16 私聊把 "[FACE:疲惫]" 原样发给了用户。
本脚本：
  1. 在模块级新增 extract_inline_face() 公共函数
  2. JSON 分支 + 纯文本分支改为「剥标记 + 改发真表情」
仅用 ASCII 锚点匹配，避免全角字符编码问题。
"""
from pathlib import Path

P = Path(__file__).resolve().parent.parent / "core" / "pipeline.py"
s = P.read_text(encoding="utf-8")
orig_len = len(s)


def rep(old: str, new: str, label: str):
    global s
    n = s.count(old)
    assert n == 1, f"[{label}] 期望 1 处，实际 {n} 处"
    s = s.replace(old, new, 1)
    print(f"  [OK] {label}")


HELPER = '''def extract_inline_face(text: str) -> tuple:
    """抽出文本里的 [FACE:关键词] → (去掉标记的文本, 表情 CQ 或 None)

    ★ v2.3.27: 补充句（follow-up）路径原本只清理 [fav:N]，漏了 [FACE:]，
      导致 "[FACE:疲惫]" 原样发给用户（实测 2026-09-16 私聊泄漏）。
      主回复与戳一戳路径一直有这段逻辑，这里抽成公共函数复用。
    """
    if not text:
        return text, None
    _re_face = re.compile(r'\\[FACE:([^\\]]*)\\]?')
    kws = [k.strip() for k in _re_face.findall(text) if k.strip()]
    clean = _re_face.sub("", text).strip()
    cq = None
    if kws:
        try:
            from modules.face_lib import get_face, make_cq
            for kw in kws:
                fp = get_face(kw)
                if fp:
                    cq = make_cq(fp)
                    logger.info("表情匹配(补充句): 关键词=%s", kw)
                    break
            else:
                logger.debug("表情库未匹配(补充句): %s", kws)
        except Exception:
            logger.warning("补充句表情解析失败: %s", kws, exc_info=True)
    return clean, cq


'''

print("1) 插入模块级 helper")
rep("async def process_message(msg_type, msg_content, chat_id, sender_name, user_id, is_group, bot_qq,",
    HELPER + "async def process_message(msg_type, msg_content, chat_id, sender_name, user_id, is_group, bot_qq,",
    "helper 插入（process_message 之前）")

print("2) 补丁 JSON 分支")
rep('''                                        sentence = sentence[:3000].strip()
                                        if sentence:
                                            ctx.append_to_context(chat_id, _ctx_safe(f"{cfg.bot_name}: {sentence}", 200))
                                            await send_by_chat_type(sentence, chat_id if is_group else chat_id,
                                                                   is_group=True if is_group else False,
                                                                   user_id=user_id if not is_group else None)
                                    return  # 已处理，跳过下面''',
    '''                                        sentence = sentence[:3000].strip()
                                        # ★ v2.3.27: 剥掉 [FACE:关键词] 并改发真表情
                                        #   （此前直接发原文 → "[FACE:疲惫]" 泄漏给用户）
                                        sentence, _fcq = extract_inline_face(sentence)
                                        if sentence:
                                            ctx.append_to_context(chat_id, _ctx_safe(f"{cfg.bot_name}: {sentence}", 200))
                                            await send_by_chat_type(sentence, chat_id if is_group else chat_id,
                                                                   is_group=True if is_group else False,
                                                                   user_id=user_id if not is_group else None)
                                        if _fcq:
                                            await send_by_chat_type(_fcq, chat_id if is_group else chat_id,
                                                                   is_group=True if is_group else False,
                                                                   user_id=user_id if not is_group else None)
                                    return  # 已处理，跳过下面''',
    "JSON 分支")

print("3) 补丁纯文本分支")
rep('''                        ctx.append_to_context(chat_id, _ctx_safe(f"{cfg.bot_name}: {f_text}", 200))
                        await send_by_chat_type(f_text, chat_id if is_group else chat_id,
                                               is_group=True if is_group else False,
                                               user_id=user_id if not is_group else None)''',
    '''                        # ★ v2.3.27: 原来只清 [fav:N]，漏了 [FACE:] → 标记泄漏给用户
                        f_text, _fcq2 = extract_inline_face(f_text)
                        if f_text:
                            ctx.append_to_context(chat_id, _ctx_safe(f"{cfg.bot_name}: {f_text}", 200))
                            await send_by_chat_type(f_text, chat_id if is_group else chat_id,
                                                   is_group=True if is_group else False,
                                                   user_id=user_id if not is_group else None)
                        if _fcq2:
                            await send_by_chat_type(_fcq2, chat_id if is_group else chat_id,
                                                   is_group=True if is_group else False,
                                                   user_id=user_id if not is_group else None)''',
    "纯文本分支")

P.write_text(s, encoding="utf-8")
print(f"\n写入完成：{orig_len} → {len(s)} 字节 (+{len(s) - orig_len})")

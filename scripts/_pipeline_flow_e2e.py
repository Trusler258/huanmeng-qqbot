"""
管线级端到端验证：真实 LLM + 完整 process_message，发送函数打桩捕获顺序。
不往 QQ 发任何消息（send_by_chat_type / send_sentences / send_raw_* 全部替换）。
用法: python3 _pipeline_flow_e2e.py
"""
import sys, asyncio, json

sys.path.insert(0, "/root/bot")

# ── 打桩：在导入 pipeline 前替换发送函数 ──
import services.sender as S
import services.llm as L

SENT_LOG = []          # (kind, content)

class _FakeTask:
    def add_done_callback(self, cb): pass

async def fake_send_by_chat_type(content, chat_id, is_group=True, user_id=None, **kw):
    SENT_LOG.append(("msg", str(content)[:120].replace("\n", " ")))
    return True

async def fake_send_sentences(sentences, chat_id, is_group, user_id=None, faces=None, **kw):
    for i, s in enumerate(sentences):
        f = (faces or [None] * len(sentences))[i] if faces else None
        SENT_LOG.append(("msg", str(s)[:120]))
        if f:
            SENT_LOG.append(("face", str(f)[:60]))
    return True

def fake_send_raw(*a, **kw):
    SENT_LOG.append(("raw", str(a)[:80]))
    return _FakeTask()

S.send_by_chat_type = fake_send_by_chat_type
S.send_sentences = fake_send_sentences
S.send_raw_group = fake_send_raw
S.send_raw_user = fake_send_raw
# pipeline 里是 from services.sender import send_sentences, send_by_chat_type —— 已绑定
# 需要替换 pipeline 模块命名空间里的引用
import core.pipeline as P
P.send_by_chat_type = fake_send_by_chat_type
P.send_sentences = fake_send_sentences
P.send_raw_group = fake_send_raw
P.send_raw_user = fake_send_raw

# note/save 之类落盘的调用保持原样（无副作用方向），EZ

async def main():
    cfg = P.get_config()
    admin = int(getattr(cfg, "admin_qq", 0))
    chat = 1017391415   # 白名单群（只用于上下文键，不会真发消息）

    await P.process_message(
        msg_type="文字",
        msg_content="帮我查下功耗，查完评价一下省不省电",
        chat_id=chat,
        sender_name="Trusler",
        user_id=admin,
        is_group=True,
        bot_qq=cfg.bot_qq,
        raw_event={"message_id": "e2e_test_1", "message_type": "group",
                   "group_id": chat, "user_id": admin},
        raw_message=f"[CQ:at,qq={cfg.bot_qq}] 帮我查下功耗，查完评价一下省不省电",
        is_command=False,
    )
    # 等待发送任务与后台 CALL 结果任务完成
    await asyncio.sleep(5)

    print("═══ 发送顺序 ═══")
    for kind, content in SENT_LOG:
        print(f"  [{kind}] {content}")
    joined = json.dumps(SENT_LOG, ensure_ascii=False)
    checks = {
        "有工具调用提示": any("工具调用" in c for _, c in SENT_LOG),
        "有功耗真实输出(度/瓦)": any(("度" in c or "W" in c) for _, c in SENT_LOG),
        "穿插(提示不在最后)": SENT_LOG and not ("工具调用" in SENT_LOG[-1][1]),
    }
    for k, v in checks.items():
        print(("  ✓ " if v else "  ✗ ") + k)
    print("共", len(SENT_LOG), "条发送")

if __name__ == "__main__":
    asyncio.run(main())

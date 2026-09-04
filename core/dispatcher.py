"""
消息分发器（原 bot.py 主循环中的消息处理逻辑）
- 解析 WebSocket 事件
- 分发到：戳一戳处理 / 指令处理 / 普通消息管道
- 统一日志记录每个消息的处理路径
"""

from __future__ import annotations

import asyncio
import json
import re
import httpx

from core.logger import get_logger
from core.config import get_config
from utils.message_parser import parse_msg
from utils.username import replace_at_in_message
from modules.commands import handle_command
from core.pipeline import process_message, handle_poke_event

logger = get_logger("dispatcher")


class EventDispatcher:
    """
    事件分发器。
    接收原始 WebSocket 文本，解析并路由到对应的处理器。
    """

    def __init__(self):
        self._msg_count = 0   # 统计：已处理的消息总数
        # 用 dict 保持插入顺序（Python 3.7+），set 是无序的，截断时会随机丢一半
        self._seen_ids: dict[str, None] = {}
        self._seen_max = 500  # 去重集合上限

    @property
    def msg_count(self) -> int:
        return self._msg_count

    async def dispatch(self, raw_message: str) -> None:
        """
        处理一条 WebSocket 原始消息。
        
        Args:
            raw_message: NapCat 推送的 JSON 字符串
        """
        try:
            await self._dispatch_inner(raw_message)
        except Exception as e:
            import traceback
            logger.error("dispatch 异常(前%d): %s\n%s\nTRACEBACK:\n%s",
                self._msg_count, e,
                raw_message[:300] if raw_message else None,
                traceback.format_exc())

    async def _dispatch_inner(self, raw_message: str) -> None:
        if not raw_message:
            return
        try:
            event = json.loads(raw_message)
        except (json.JSONDecodeError, TypeError):
            logger.warning("无法解析 JSON (第%d条消息): %s...", self._msg_count, raw_message[:80])
            return

        post_type = event.get("post_type") or event.get("post_type", "")
        # ★ NapCat 偶发不带 post_type, 但有 message_type → 等同 message
        if not post_type and event.get("message_type"):
            post_type = "message"

        # ── 消息去重（NapCat 偶发双发同一消息）──
        msg_id = str(event.get("message_id", ""))
        if msg_id:
            if msg_id in self._seen_ids:
                return  # 已处理过，跳过
            self._seen_ids[msg_id] = None
            if len(self._seen_ids) > self._seen_max:
                # 按插入顺序保留最近一半（dict 保持插入顺序）
                keep = list(self._seen_ids.keys())[-(self._seen_max // 2):]
                self._seen_ids = {k: None for k in keep}

        # ── 静默过滤: meta_event (心跳包，每30秒一次，不计数不记录) ──
        if post_type == "meta_event":
            return

        self._msg_count += 1

        # ── 路由 1: notice（戳一搓）──
        if post_type == "notice":
            await self._handle_notice(event)
            return

        # ── 路由 2: request（好友请求 / 加群请求）──
        if post_type == "request":
            await self._handle_request(event)
            return

        # ── 路由 2: message（普通消息）──
        parsed = parse_msg(raw_message)
        if parsed is None:
            logger.debug("跳过非消息事件: post_type=%s", post_type)
            return

        await self._handle_message(parsed, event, raw_message)

    # ════════════════════════════════════════════════════════
    #  内部处理器
    # ════════════════════════════════════════════════════════

    async def _handle_notice(self, event: dict):
        """处理 notice 类型事件：群消息撤回 / 戳一戳 / 新人入群"""
        notice_type = event.get("notice_type", "")

        # ── 群消息撤回 ──
        if notice_type == "group_recall":
            await self._handle_recall(event)
            return

        # ── 新人入群 ──
        if notice_type == "group_increase":
            group_id = event.get("group_id", 0)
            user_id = event.get("user_id", 0)
            cfg = get_config()
            # 仅在白名单群 + 配置了欢迎语的群发送
            if group_id in cfg.group_list:
                gs = cfg.group_settings.get(group_id, {})
                welcome = gs.get("welcome_msg", "").strip()
                if welcome:
                    # 替换模板变量
                    msg = welcome.replace("{user}", str(user_id)).replace("{group}", str(group_id))
                    # 如果没写 {user}，自动 @ 新人
                    if "{user}" not in welcome:
                        msg = f"[CQ:at,qq={user_id}] {msg}"
                    try:
                        from services.sender import send_group_msg
                        await send_group_msg(msg, group_id)
                        logger.info("新人入群欢迎: group=%d user=%d", group_id, user_id)
                        # 附带公会登记提示
                        await send_group_msg(
                            f"[CQ:at,qq={user_id}] 欢迎！可以用 /~gh add <公会名> 登记公会喵~",
                            group_id
                        )
                    except Exception as e:
                        logger.warning("发送欢迎语失败: %s", e)
            return

        # ── 戳一戳 ──
        if notice_type != "notify" or event.get("sub_type") != "poke":
            logger.debug("忽略 notice: type=%s sub=%s", notice_type, event.get("sub_type"))
            return

        target_id = event.get("target_id", 0)
        cfg = get_config()
        if int(target_id) != int(cfg.bot_qq):
            logger.debug("戳一戳目标不是机器人 (target=%d bot=%d)", target_id, cfg.bot_qq)
            return

        user_id = event.get("user_id", 0)
        group_id = event.get("group_id", user_id)
        is_group = "group_id" in event
        # ★ 分群感知: 戳一戳优先用当前群昵称
        sender_name = cfg.get_display_name(user_id, group_id) if is_group else cfg.qq_name_map.get(str(user_id), str(user_id))

        # ★ 白名单检查：非白名单群/私聊不响应戳一戳
        if is_group and group_id not in cfg.group_list:
            logger.debug("戳一戳忽略(非白名单群): group=%d", group_id)
            return
        if not is_group and cfg.private_whitelist and user_id not in cfg.private_whitelist:
            logger.debug("戳一戳忽略(非白名单私聊): user=%d", user_id)
            return

        logger.info("👆 戳一戳事件: from=%s(%d) is_group=%s", sender_name, user_id, is_group)

        await handle_poke_event(
            sender_name=sender_name,
            user_id=user_id,
            chat_id=group_id,
            is_group=is_group,
        )

    async def _handle_request(self, event: dict):
        """处理 request 类型事件：好友请求"""
        request_type = event.get("request_type", "")
        if request_type != "friend":
            logger.debug("忽略 request: type=%s", request_type)
            return

        user_id = event.get("user_id", 0)
        comment = event.get("comment", "")
        flag = event.get("flag", "")
        # 获取昵称
        nickname = event.get("nickname", str(user_id))

        from modules.friend_request import add_pending
        add_pending(flag, user_id, nickname, comment)

        # 通知管理员
        from services.sender import send_private_msg
        from core.config import get_config
        cfg = get_config()
        admin_qq = cfg.admin_qq

        msg = (
            f"[好友请求]\n"
            f"昵称: {nickname}\n"
            f"QQ: {user_id}\n"
            f"理由: {comment}\n"
            f"---\n"
            f"发送 /#添加 接受\n"
            f"发送 /#添加 wl 接受并加入私聊白名单\n"
            f"发送 /#拒绝 拒绝"
        )
        try:
            await send_private_msg(msg, admin_qq)
            logger.info("好友请求通知已发送给管理员: user=%d(%s)", user_id, nickname)
        except Exception as e:
            logger.warning("发送好友请求通知失败: %s", e)

    async def _handle_recall(self, event: dict):
        """处理群消息撤回：标记 msglog 中的消息为已撤回"""
        user_id = event.get("user_id", 0)
        operator_id = event.get("operator_id", 0)
        group_id = event.get("group_id", 0)
        message_id = int(event.get("message_id", 0))

        try:
            from modules.recall import mark_recalled
            mark_recalled(group_id, message_id, operator_id, user_id)
            logger.info("🗑️ 撤回: group=%d msg_id=%d user=%d op=%d",
                       group_id, message_id, user_id, operator_id)
        except Exception as e:
            logger.warning("处理撤回消息异常: %s", e)

    async def _handle_message(self, parsed, event: dict, raw_message: str):
        """处理 message 类型事件"""
        msg_type, msg_content, chat_id, sender_name, reply_id, text_prefix = parsed
        
        cfg = get_config()
        user_id = event.get("user_id", 0)
        is_group = (event.get("message_type") == "group")
        bot_qq = cfg.bot_qq

        # ═══ 白名单与权限检查（最优先，连日志都不记）═══
        is_command = msg_type == "文字" and (
            msg_content.startswith("/~") or msg_content.startswith("/#") or
            (msg_content.startswith("/") and len(msg_content) > 1 and msg_content[1:2] not in "~#/ ")
        )

        # ★ 分群指令白名单（群内才检查）
        if is_command and is_group:
            gs = cfg.group_settings.get(chat_id, {})
            if "cmd_whitelist" in gs:
                grp_cmds = gs["cmd_whitelist"]
                if not grp_cmds:  # None 或空列表 → 允许所有指令
                    pass
                else:
                    cmd_name = msg_content.split()[0].lstrip("/~#").lower()
                    if cmd_name not in grp_cmds:
                        from services.sender import send_group_msg
                        try:
                            await send_group_msg("该群指令已被限制，请联系管理员", chat_id)
                        except Exception as e:
                            logger.warning("发送白名单拒绝提示失败: %s", e)
                            pass
                        return  # 指令不在白名单

        if is_group:
            if chat_id not in cfg.group_list and not is_command:
                return  # 非白名单群 → 彻底静默，不记日志、不计数量、不处理图片
        else:
            if not cfg.enable_private and not is_command:
                return  # 私聊未启用
            if cfg.private_whitelist and user_id not in cfg.private_whitelist and not is_command:
                return  # 不在私聊白名单中

        # ═══ 以下：白名单通过 / 指令消息 — 正常处理 ═══

        # ★ 全群忽略检查（admin 豁免）
        from modules.ignore_users import is_ignored
        if is_ignored(user_id) and user_id != cfg.admin_qq:
            logger.debug("忽略用户: uid=%d (在忽略列表)", user_id)
            return

        # ★ 主动录制消息到撤回缓冲区（仅白名单群）
        message_id = int(event.get("message_id", 0))  # 防止字符串/整数类型不匹配
        if is_group and chat_id in cfg.group_list and message_id:
            from modules.recall import record_incoming_message
            image_url = msg_content if msg_type == "图片" else ""
            record_incoming_message(chat_id, user_id, message_id, msg_type, msg_content, image_url)

        logger.info(
            "📩 消息 #%d | type=%s | from=%s(%d) | chat=%d | group=%s | content='%s...'",
            self._msg_count, msg_type, sender_name, user_id, chat_id, is_group,
            msg_content[:30].replace("\n", " "),
        )

        # ── 昵称解析：事件自带 card(分群正确) 优先，缺失时用映射补全 ──
        # parse_msg 的 sender_name 已提取 card>nickname>user_id。
        # 分组场景下 card 就是当前群的名片，天然分群正确，绝不覆盖；
        # 只有没拿到 card（sender_name 是 QQ号/空）时才用映射补全。
        if sender_name == str(user_id) or not sender_name:
            preset_name = cfg.get_display_name(user_id, chat_id) if is_group else cfg.qq_name_map.get(str(user_id))
            if preset_name:
                old_name = sender_name
                sender_name = preset_name
                if old_name != sender_name:
                    logger.debug("昵称补全: '%s' → '%s' (QQ=%d)", old_name, sender_name, user_id)

        # ★ 群消息统计记录（仅白名单群 + 非指令消息）
        if is_group and chat_id in cfg.group_list and not is_command:
            from modules.stats import record_message
            record_message(chat_id, user_id, msg_content, sender_name)
            # ★ SQLite 全文检索索引（ADDITIVE，失败不影响主流程）
            try:
                from db.store import get_search_store
                get_search_store().index_message(chat_id, user_id, sender_name, msg_content)
            except Exception as _e:
                logger.debug("群消息索引入库失败(忽略): %s", _e)

        # ── 合并转发处理 ──
        if msg_type == "转发":
            fwd_result = await self._process_forward(msg_content, sender_name, chat_id)
            if text_prefix:
                msg_content = f"{text_prefix}\n{fwd_result}"
            else:
                msg_content = fwd_result
            msg_type = "文字"  # 转为文字进入管道

        # ── 图片处理 ──
        if msg_type == "图片":
            # ★ v2.0.4aa: raw_message 是 CQ 码形态 [CQ:at,qq=xxx]，原 @QQ 正则匹配不到；
            #   补 CQ 匹配修复"@bot 发图识别"静默失效
            is_img_mentioned = bool(
                re.search(rf'@{bot_qq}(?!\d)', raw_message)
                or re.search(rf'\[CQ:at,qq={bot_qq}[,\]]', raw_message or "")
            )
            img_result = await self._process_image(msg_content, cfg, chat_id, is_group, user_id, sender_name, is_img_mentioned)
            if is_img_mentioned and not img_result.startswith("[图片]"):
                msg_type = "文字"
            if text_prefix:
                msg_content = f"{text_prefix} {img_result}"
            else:
                msg_content = img_result
        elif msg_type == "文件":
            # 尝试识别错误报告文件（直接发文件，不需要 @）
            file_url = msg_content  # parse_msg 提取的文件 URL
            filename = ""
            try:
                raw_data = json.loads(raw_message)
                for seg in raw_data.get("message", []):
                    if seg.get("type") == "file":
                        filename = seg.get("data", {}).get("file", "")
                        break
            except Exception as e:
                logger.debug("解析文件消息filename失败: %s", e)
                pass
            if "错误报告" in filename and file_url.startswith("http"):
                logger.info("检测到直接发送的错误报告文件: %s", filename)
                from modules.error_report import process_error_report
                error_report_content = await process_error_report(file_url, filename, sender_name)
                msg_content = "[文件]"
            else:
                msg_content = "[文件]"
                logger.debug("文件消息: 已替换为占位符")

        # ── @ 替换（指令消息跳过：保留 QQ 号给指令/插件解析，如 /~摸头 @QQ）──
        if not is_command and re.search(r"@\d{5,12}", msg_content):
            logger.debug("检测到@，开始替换用户名...")
            msg_content = await replace_at_in_message(
                msg_content,
                cfg.host, cfg.port,
                bot_qq=bot_qq, bot_name=cfg.bot_name,
                group_id=chat_id if is_group else None,
            )

        # ── 引用消息提取（reply）──
        quoted_text = ""
        error_report_content = None  # ★ 错误报告内容
        
        if reply_id:
            logger.info("📎 检测到引用消息 (message_id=%s)，正在获取原文...", reply_id)
            quoted_text = await self._fetch_quoted_msg(reply_id)
            
            # ★ 检查引用消息中是否包含文件（错误报告）
            # 私聊无条件检查，群聊需 @机器人
            # ★ v2.0.4aa: 同 pipeline，@ 检测兼容 bot 名替换 + CQ 码
            is_mentioned = bool(
                re.search(rf'@{bot_qq}(?!\d)', msg_content)
                or (cfg.bot_name and re.search(rf'@{re.escape(str(cfg.bot_name))}(?!\w)', msg_content))
                or re.search(rf'\[CQ:at,qq={bot_qq}[,\]]', raw_message or "")
            )
            if not is_group or is_mentioned:
                logger.info("📎 检测到@机器人 + 引用消息，检查是否包含错误报告文件...")
                file_info = await self._fetch_quoted_file(reply_id)
                
                if file_info:
                    logger.info("📎 引用消息包含文件: %s", file_info["filename"])
                    
                    # 处理错误报告
                    from modules.error_report import process_error_report
                    error_report_content = await process_error_report(
                        file_url=file_info["url"],
                        filename=file_info["filename"],
                        sender_name=sender_name,
                    )
                    
                    if error_report_content:
                        logger.info("✅ 错误报告处理成功，%d 字符", len(error_report_content))
                    else:
                        logger.warning("❌ 错误报告处理失败或文件名不包含关键词")
                
                # ★ 检测引用消息中的图片（战绩截图等）
                if not error_report_content:
                    img_desc = await self._fetch_quoted_image(reply_id)
                    if img_desc:
                        if quoted_text:
                            quoted_text = quoted_text + "\n" + img_desc
                        else:
                            quoted_text = img_desc
                        logger.info("📎 引用图片描述已注入: %d字", len(img_desc))
            
            if quoted_text and not error_report_content:
                logger.info("📎 引用原文 (%d字): '%s...'", len(quoted_text), quoted_text[:50])
            elif not quoted_text and not error_report_content:
                logger.warning("📎 无法获取引用消息内容 (id=%s)", reply_id)

        # ★ 私聊消息也索引到 SQLite（ADDITIVE，失败不影响主流程）
        if not is_group and not is_command:
            try:
                from db.store import get_search_store
                get_search_store().index_message(chat_id, user_id, sender_name, msg_content)
            except Exception as _e:
                logger.debug("私聊消息索引入库失败(忽略): %s", _e)

        # 调用消息处理管道（通过队列，不阻塞当前消息接收）
        # ★ v2.0.4r: 传 is_command → 队列层给指令最高优先级，不被普通消息堵住
        from core.queues import enqueue_message
        await enqueue_message(
            msg_type=msg_type,
            msg_content=msg_content,
            chat_id=chat_id,
            sender_name=sender_name,
            user_id=user_id,
            is_group=is_group,
            bot_qq=bot_qq,
            raw_event=event,
            raw_message=raw_message,
            quoted_msg=quoted_text,   # ★ 引用消息原文
            error_report=error_report_content,  # ★ 错误报告内容
            is_command=is_command,    # ★ v2.0.4r 指令插队标记
        )

    async def _process_image(self, image_url_or_path: str, cfg, chat_id: int, is_group: bool, user_id: int, sender_name: str, is_mentioned: bool = False) -> str:
        """处理图片消息：被@时同步等待识别并注入描述，否则后台异步"""
        if not cfg.image_model.switch:
            return "[图片]"

        from services.image_api import recognize_image, save_image_description
        import hashlib

        if is_mentioned:
            # ★ 被@时同步等待识别，让 LLM 直接看到图片描述
            try:
                description = await recognize_image(image_url_or_path, cfg.image_model, chat_id=chat_id)
                if description and description.strip():
                    short_desc = description[:80].replace("\n", " ")
                    logger.info("[chat=%d] 图片@同步识别完成: '%s...' (%d字)", chat_id, short_desc, len(description))
                    # 注入上下文
                    from core.context_manager import get_context_mgr
                    ctx = get_context_mgr()
                    ctx.append_to_context(chat_id, f'{sender_name}:[图片]:描述"{short_desc}"')
                    return f'[图片]:描述"{short_desc}"'
            except Exception as e:
                logger.warning("图片@同步识别失败: %s", e)
            return "[图片]"

        # 非@：后台异步识别
        async def _bg_recognize():
            try:
                from services.image_api import recognize_image, save_image_description
                import hashlib
                description = await recognize_image(image_url_or_path, cfg.image_model, chat_id=chat_id)
                if not description or not description.strip():
                    return
                short_desc = description[:80].replace("\n", " ")
                logger.info("[chat=%d] 图片后台识别完成: '%s...' (%d字)", chat_id, short_desc, len(description))
                # 注入上下文
                from core.context_manager import get_context_mgr
                ctx = get_context_mgr()
                ctx.append_to_context(chat_id, f'[图片描述]"{short_desc}" {sender_name}')
                # 保存到仓库
                try:
                    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as cl:
                        r = await cl.get(image_url_or_path)
                        if r.status_code == 200:
                            md5 = hashlib.md5(r.content).hexdigest()
                            asyncio.create_task(save_image_description(md5, description, author=sender_name, chat_id=chat_id))
                except Exception as e:
                    logger.warning("图片MD5计算/保存失败: %s", e)
            except Exception as e:
                logger.warning("图片后台识别/保存失败: %s", e)

        asyncio.ensure_future(_bg_recognize())
        return "[图片]"

        asyncio.ensure_future(_bg_recognize())
        return "[图片]"

    async def _process_forward(self, forward_id: str, sender_name: str, chat_id: int) -> str:
        """
        处理合并转发消息：调用 get_forward_msg API，格式化为文本。
        
        Args:
            forward_id: 转发消息 ID
            sender_name: 发送者昵称
            chat_id: 对话 ID
            
        Returns:
            格式化后的文本，失败返回 [合并转发]
        """
        from services.sender import get_ws_manager
        
        try:
            mgr = get_ws_manager()
            data = await mgr.call_api("get_forward_msg", {"id": forward_id})
            if not data:
                logger.warning("[chat=%d] get_forward_msg 返回空 id=%s", chat_id, forward_id)
                return "[合并转发]"
            
            messages = data.get("messages", [])
            if not messages:
                logger.warning("[chat=%d] 合并转发无 messages 字段 id=%s", chat_id, forward_id)
                return "[合并转发]"
            
            lines = [f"[合并转发 · {len(messages)} 条消息]"]
            truncated = False
            total_chars = len(lines[0])  # 实时统计总字符数（含前缀和换行）
            max_total_chars = 6000  # 总字符上限，保证 LLM 有足够上下文
            max_line_chars = 120   # 单条上限
            
            for i, node in enumerate(messages):
                # 提取节点信息
                node_sender = node.get("sender", {})
                nick = node_sender.get("nickname", str(node_sender.get("user_id", "未知")))
                msg_content = ""
                
                # content 可能是 string 或 list of segments
                raw_content = node.get("content", node.get("message", ""))
                if isinstance(raw_content, list):
                    parts = []
                    for seg in raw_content:
                        if isinstance(seg, dict):
                            t = seg.get("type", "")
                            d = seg.get("data", {})
                            if t == "text":
                                parts.append(d.get("text", ""))
                            elif t == "image":
                                parts.append("[图片]")
                            elif t == "face":
                                parts.append("[表情]")
                            elif t == "at":
                                qq = d.get("qq", "")
                                parts.append(f"@{qq}")
                            else:
                                parts.append(f"[{t}]")
                    msg_content = "".join(parts)
                else:
                    msg_content = str(raw_content)
                
                msg_content = msg_content.strip()
                if len(msg_content) > max_line_chars:
                    msg_content = msg_content[:max_line_chars] + "..."
                if not msg_content:
                    msg_content = "[空消息]"
                
                line = f"  {nick}: {msg_content}"
                total_chars += len(line) + 1  # +1 是换行符
                if total_chars > max_total_chars:
                    truncated = True
                    break
                lines.append(line)
            
            if truncated:
                lines.append(f"  ... (共 {len(messages)} 条, 仅展示前 {i} 条)")
            
            result = "\n".join(lines)
            shown = i if truncated else len(lines) - 1  # 减去 header
            logger.info("[chat=%d] 合并转发解析完成: %d/%d 条 (%d 字符)",
                       chat_id, shown, len(messages), len(result))
            return result
            
        except Exception as e:
            logger.error("[chat=%d] 合并转发解析失败 id=%s: %s", chat_id, forward_id, e)
            return "[合并转发]"

    async def _fetch_quoted_msg(self, message_id: int) -> str:
        """
        通过 NapCat OneBot API 获取被引用消息的文本内容。
        如果引用的是合并转发，自动解析为格式化文本。

        Args:
            message_id: 引用的消息 ID

        Returns:
            原始消息文本；获取失败返回空字符串
        """
        from services.sender import get_ws_manager
        import re as _re

        mgr = get_ws_manager()
        data = await mgr.call_api("get_msg", {"message_id": message_id})

        if not data:
            return ""

        # ── 路径 1: message 段数组（array 格式）──
        msg_segments = data.get("message", [])
        if isinstance(msg_segments, list):
            parts = []
            for seg in msg_segments:
                seg_type = seg.get("type", "")
                if seg_type == "text":
                    parts.append(seg.get("data", {}).get("text", ""))
                elif seg_type == "forward":
                    fwd_id = seg.get("data", {}).get("id", "")
                    if fwd_id:
                        logger.debug("📎 引用消息是合并转发 id=%s，正在解析...", fwd_id)
                        try:
                            fwd_text = await self._process_forward(fwd_id, sender_name="引用", chat_id=0)
                            parts.append(fwd_text)
                        except Exception as e:
                            logger.debug("📎 解析引用合并转发失败: %s", e)
                            parts.append("[合并转发]")
            result = "".join(parts).strip()
            if result:
                return result

        # ── 路径 2: raw_message（CQ 码格式）──
        raw = (data.get("raw_message") or "").strip()
        if not raw:
            return ""

        # 检测 raw_message 中的 [CQ:forward,id=...] 并解析
        fwd_ids = _re.findall(r'\[CQ:forward,id=(\d+)', raw)
        if fwd_ids:
            for fwd_id in fwd_ids:
                try:
                    fwd_text = await self._process_forward(fwd_id, sender_name="引用", chat_id=0)
                    return fwd_text
                except Exception as e:
                    logger.debug("📎 解析引用合并转发失败(raw): %s", e)
            return "[合并转发]"

        return raw

    async def _fetch_quoted_file(self, message_id: int) -> Optional[dict]:
        """
        通过 NapCat OneBot API 获取被引用消息中的文件信息。

        Args:
            message_id: 引用的消息 ID

        Returns:
            字典 {"url": str, "filename": str} 或 None
        """
        from services.sender import get_ws_manager
        import re

        mgr = get_ws_manager()
        data = await mgr.call_api("get_msg", {"message_id": message_id})

        if not data:
            return None

        # 方法1: 从 message 段数组中提取文件信息
        msg_segments = data.get("message", [])
        if isinstance(msg_segments, list):
            for seg in msg_segments:
                if seg.get("type") == "file":
                    file_data = seg.get("data", {})
                    file_url = file_data.get("url", "")
                    filename = file_data.get("name", "")
                    
                    if file_url and filename:
                        logger.info("📎 从消息段检测到文件: filename=%s url=%s...", 
                                   filename, file_url[:80])
                        return {"url": file_url, "filename": filename}
        
        # 方法2: 从 raw_message 的 CQ 码中解析文件（NapCat 常见格式）
        raw_message = data.get("raw_message", "")
        if "[CQ:file" in raw_message:
            # 解析 CQ 码: [CQ:file,file=xxx.zip,file_id=xxx,url=xxx]
            # 提取文件名
            file_match = re.search(r'file=([^,\]]+)', raw_message)
            filename = file_match.group(1) if file_match else ""
            
            # 提取 URL
            url_match = re.search(r'url=([^,\]]+)', raw_message)
            file_url = url_match.group(1) if url_match else ""
            
            if file_url and filename:
                logger.info("📎 从 CQ 码检测到文件: filename=%s url=%s...", 
                           filename, file_url[:80])
                return {"url": file_url, "filename": filename}
            elif filename:
                # 有文件名但没有URL，可能需要通过 file_id 下载
                file_id_match = re.search(r'file_id=([^,\]]+)', raw_message)
                file_id = file_id_match.group(1) if file_id_match else ""
                
                if file_id:
                    # 尝试通过 OneBot API 获取文件下载链接
                    logger.info("📎 检测到文件但无URL，尝试通过 file_id 获取: %s", file_id)
                    file_info = await mgr.call_api("get_file", {"file_id": file_id})
                    if file_info and file_info.get("url"):
                        file_url = file_info["url"]
                        logger.info("📎 通过 file_id 获取到 URL: %s...", file_url[:80])
                        return {"url": file_url, "filename": filename}
        
        return None

    async def _fetch_quoted_image(self, message_id: int) -> Optional[str]:
        """
        获取被引用消息中的图片信息。
        优先检查 WDSJ 战绩缓存（bot 自己发的图），其次才调视觉模型。
        """
        from services.sender import get_ws_manager
        import re

        mgr = get_ws_manager()
        data = await mgr.call_api("get_msg", {"message_id": message_id})
        if not data:
            return None

        # ★ 检查是否是 bot 发的 WDSJ 战绩图
        from core.config import get_config
        bot_qq = get_config().bot_qq
        raw = data.get("raw_message", "")
        wdsj_match = re.search(r'wdsj_([\w-]+)\.png', raw)
        if wdsj_match and data.get("sender", {}).get("user_id") == bot_qq:
            from services.wdsj_cache import get as wdsj_get
            cached = wdsj_get(wdsj_match.group(1))
            if cached:
                logger.info("📎 引用 WDSJ 战绩图(缓存命中): %s/%s",
                           cached["player"], cached["template"])
                return f"[战绩数据] {cached['summary']}"

        # 从 message 段中提取图片 URL
        img_url = ""
        msg_segments = data.get("message", [])
        if isinstance(msg_segments, list):
            for seg in msg_segments:
                if seg.get("type") == "image":
                    img_url = seg.get("data", {}).get("url", "")
                    if img_url:
                        break

        # 从 CQ 码中提取（备用）
        if not img_url:
            m = re.search(r'\[CQ:image[^\]]*url=([^,\]]+)', raw)
            if m:
                img_url = m.group(1)

        if not img_url:
            return None

        logger.info("📎 引用消息包含图片，开始识别: %s...", img_url[:60])
        try:
            from services.image_api import recognize_image
            from core.config import get_config
            cfg = get_config()
            if not cfg.image_model.switch:
                return None
            desc = await recognize_image(img_url, cfg.image_model)
            if desc and desc.strip():
                return f'[图片]:描述"{desc.strip()[:200]}"'
        except Exception as e:
            logger.warning("引用图片识别失败: %s", e)

        return None


# ── 模块级引用（供外部模块读取 dispatcher 实例）──────────────
_current_dispatcher: EventDispatcher | None = None


def get_current_dispatcher() -> "EventDispatcher | None":
    return _current_dispatcher

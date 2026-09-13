<template>
  <!-- 悬浮球：收起状态 -->
  <div
    v-if="!open"
    class="ai-fab"
    :style="fabStyle"
    @mousedown="onFabDragStart"
    @click="onFabClick"
  >
    <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8">
      <path d="M12 3a7 7 0 0 1 7 7c0 2.4-1.2 4.5-3 5.7V19a2 2 0 0 1-2 2h-4a2 2 0 0 1-2-2v-3.3A7 7 0 0 1 12 3z" />
      <path d="M9.5 21h5" stroke-linecap="round" />
      <circle cx="9.5" cy="10.5" r="0.8" fill="currentColor" stroke="none" />
      <circle cx="14.5" cy="10.5" r="0.8" fill="currentColor" stroke="none" />
    </svg>
    <span class="ai-fab-tip">问问幻梦</span>
  </div>

  <!-- 悬浮窗：展开状态 -->
  <div v-else class="ai-window" :style="winStyle">
    <div class="ai-window-header" @mousedown="onWinDragStart">
      <div class="ai-window-title">
        <span class="ai-dot" />
        幻梦助手
      </div>
      <a-space :size="4">
        <a-button type="text" size="mini" @click="clearChat">
          <icon-refresh />
        </a-button>
        <a-button type="text" size="mini" @click="open = false">
          <icon-close />
        </a-button>
      </a-space>
    </div>

    <div ref="listRef" class="ai-window-body">
      <div v-if="!messages.length" class="ai-welcome">
        <p>我是面板助手，可以帮你：</p>
        <div v-for="q in quickQuestions" :key="q" class="ai-welcome-item" @click="send(q)">
          {{ q }}
        </div>
      </div>
      <div
        v-for="(m, i) in messages"
        :key="i"
        class="ai-msg"
        :class="`ai-msg-${m.role}`"
      >
        <div
          v-if="m.role === 'user'"
          class="ai-msg-bubble"
          :class="{ 'ai-streaming': m.streaming }"
        >{{ m.content }}</div>
        <div
          v-else
          class="ai-msg-bubble ai-md"
          :class="{ 'ai-streaming': m.streaming }"
          v-html="mdRender(m.content)"
        ></div>
        <div v-if="m.action" class="ai-msg-action">
          <a-button size="mini" type="primary" status="success" @click="execAction(m.action)">
            {{
              m.action.type === 'navigate'
                ? '前往'
                : m.action.type === 'locate-config'
                  ? '去改'
                  : '高亮'
            }}
          </a-button>
        </div>
      </div>
      <div v-if="showTyping" class="ai-msg ai-msg-assistant">
        <div class="ai-msg-bubble ai-typing"><span /><span /><span /></div>
      </div>
    </div>

    <div class="ai-window-footer">
      <a-input
        v-model="draft"
        placeholder="输入问题，回车发送"
        size="small"
        :disabled="pending"
        @press-enter="send()"
      />
      <a-button
        type="primary"
        size="small"
        :loading="pending"
        :disabled="!draft.trim()"
        @click="send()"
      >
        发送
      </a-button>
    </div>
  </div>
</template>

<script lang="ts" setup>
  import { ref, computed, nextTick, onMounted, onUnmounted } from 'vue';
  import { useRouter } from 'vue-router';
  import { Message } from '@arco-design/web-vue';
  import {
    chatWithAssistant,
    streamWithAssistant,
    type AssistantAction,
  } from '@/api/assistant';

  const router = useRouter();

  const open = ref(false);
  const pending = ref(false);
  const draft = ref('');
  const messages = ref<{
    role: 'user' | 'assistant';
    content: string;
    action?: AssistantAction;
    streaming?: boolean;
  }[]>([]);

  /** 打字气泡只在「流还没吐出第一个字」时显示 */
  const showTyping = computed(() => {
    const last = messages.value[messages.value.length - 1];
    return (
      pending.value &&
      (!last || last.role !== 'assistant' || (!last.content && !last.action))
    );
  });

  // ── 打字机节流：模型出字太快，缓冲后按舒适速度渲染 ──
  // 基础 ~90 字/秒（3 字 / 33ms）；积压多时自动加速，保证长回复 ~4 秒内追上
  const renderBuf = ref('');
  let drainTimer: ReturnType<typeof setInterval> | null = null;
  let streamClosed = false;
  let drainEntry:
    | { content: string; streaming?: boolean; action?: AssistantAction }
    | null = null;
  let drainAction: AssistantAction | null = null;

  const stopDrain = () => {
    if (drainTimer) {
      clearInterval(drainTimer);
      drainTimer = null;
    }
  };
  const startDrain = () => {
    if (drainTimer) return;
    drainTimer = setInterval(() => {
      const buf = renderBuf.value;
      if (!buf) {
        // 流已结束且缓冲排空 → 收尾
        if (streamClosed) {
          stopDrain();
          if (drainEntry) {
            drainEntry.streaming = false;
            if (drainAction) drainEntry.action = drainAction;
          }
          if (drainAction) execAction(drainAction);
          drainEntry = null;
          drainAction = null;
          pending.value = false;
          scrollBottom();
        }
        return;
      }
      const speed = Math.max(3, Math.ceil(buf.length / 100));
      if (drainEntry) drainEntry.content += buf.slice(0, speed);
      renderBuf.value = buf.slice(speed);
      scrollBottom();
    }, 33);
  };

  const quickQuestions = [
    '改机器人人格在哪？',
    '看看最近日志有没有报错',
    '怎么给用户发积分？',
    '关键词开关在哪里调？',
  ];

  // ── 拖动逻辑（球与窗共用） ──
  const fabPos = ref({ x: 0, y: 0 });       // 相对右下角偏移
  const winPos = ref({ x: -1, y: -1 });     // -1 = 居中默认
  const drag = ref<{ startX: number; startY: number; baseX: number; baseY: number } | null>(null);
  const moved = ref(false);

  const fabStyle = computed(() => ({
    right: `${16 + fabPos.value.x}px`,
    bottom: `${16 + fabPos.value.y}px`,
  }));
  const winStyle = computed(() =>
    winPos.value.x < 0
      ? { right: '16px', bottom: '16px' }
      : { left: `${winPos.value.x}px`, top: `${winPos.value.y}px`, right: 'auto', bottom: 'auto' }
  );

  const onFabDragStart = (e: MouseEvent) => {
    drag.value = { startX: e.clientX, startY: e.clientY, baseX: fabPos.value.x, baseY: fabPos.value.y };
    moved.value = false;
    window.addEventListener('mousemove', onDragMove);
    window.addEventListener('mouseup', onDragEnd);
  };
  const onWinDragStart = (e: MouseEvent) => {
    // 只响应标题栏左键拖动
    if ((e.target as HTMLElement).closest('button')) return;
    const el = document.querySelector('.ai-window') as HTMLElement | null;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    winPos.value = { x: rect.left, y: rect.top };
    drag.value = { startX: e.clientX, startY: e.clientY, baseX: rect.left, baseY: rect.top };
    window.addEventListener('mousemove', onDragMove);
    window.addEventListener('mouseup', onDragEnd);
  };
  const onDragMove = (e: MouseEvent) => {
    if (!drag.value) return;
    const dx = e.clientX - drag.value.startX;
    const dy = e.clientY - drag.value.startY;
    if (Math.abs(dx) > 3 || Math.abs(dy) > 3) moved.value = true;
    if (open.value) {
      const maxX = window.innerWidth - 380;
      const maxY = window.innerHeight - 120;
      winPos.value = {
        x: Math.min(Math.max(0, drag.value.baseX + dx), maxX),
        y: Math.min(Math.max(0, drag.value.baseY + dy), maxY),
      };
    } else {
      fabPos.value = {
        x: Math.max(0, drag.value.baseX - dx),
        y: Math.max(0, drag.value.baseY - dy),
      };
    }
  };
  const onDragEnd = () => {
    drag.value = null;
    window.removeEventListener('mousemove', onDragMove);
    window.removeEventListener('mouseup', onDragEnd);
  };
  const onFabClick = () => {
    if (moved.value) return;   // 拖动结束不算点击
    open.value = true;
  };

  // ── Markdown 渲染（轻量自写，不引依赖）──
  // 安全：先整体 HTML 转义，再转换标记；链接只允许 http(s)，防 javascript: 注入
  const escapeHtml = (s: string) =>
    s
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');

  const mdRender = (src: string): string => {
    if (!src) return '';
    const codeBlocks: string[] = [];
    let text = escapeHtml(src);
    // 1. 代码块 → 占位符（避免内部被行内规则误伤）
    text = text.replace(/```(\w*)\n?([\s\S]*?)```/g, (_m, _lang, code) => {
      codeBlocks.push(
        `<pre class="ai-md-pre"><code>${code.replace(/\n$/, '')}</code></pre>`
      );
      return `\u0000B${codeBlocks.length - 1}\u0000`;
    });
    // 2. 行内代码
    text = text.replace(/`([^`\n]+)`/g, '<code class="ai-md-code">$1</code>');
    // 3. 加粗
    text = text.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
    // 4. 列表（- / * / 1.）在斜体前处理，避免行首 * 被吃
    text = text.replace(/^[-*] (.*)$/gm, '<div class="ai-md-li">· $1</div>');
    text = text.replace(/^(\d+)\. (.*)$/gm, '<div class="ai-md-li">$1. $2</div>');
    // 5. 斜体（单个 *，排除已转换的标签）
    text = text.replace(/(^|[^<>/\w*])\*([^*\n]+)\*(?![\w*])/g, '$1<em>$2</em>');
    // 6. 删除线
    text = text.replace(/~~([^~\n]+)~~/g, '<del>$1</del>');
    // 7. 链接（仅 http/https）
    text = text.replace(
      /\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener">$1</a>'
    );
    // 8. 标题 / 引用
    text = text.replace(/^#{1,4} (.*)$/gm, '<div class="ai-md-h">$1</div>');
    text = text.replace(/^&gt; (.*)$/gm, '<div class="ai-md-quote">$1</div>');
    // 9. 换行 + 还原代码块
    text = text.replace(/\n/g, '<br>');
    text = text.replace(/\u0000B(\d+)\u0000/g, (_m, i) => codeBlocks[+i] ?? '');
    return text;
  };

  // ── 对话 ──
  const listRef = ref<HTMLElement>();
  const scrollBottom = async () => {
    await nextTick();
    listRef.value?.scrollTo({ top: listRef.value.scrollHeight, behavior: 'smooth' });
  };

  const send = async (preset?: string) => {
    const text = (preset ?? draft.value).trim();
    if (!text || pending.value) return;
    draft.value = '';
    messages.value.push({ role: 'user', content: text });
    scrollBottom();
    pending.value = true;
    const history = messages.value.slice(0, -1).slice(-8).map((m) => ({
      role: m.role, content: m.content,
    }));
    // 流式：先占一个空 assistant 气泡，delta 进缓冲按打字机速度渲染
    messages.value.push({ role: 'assistant', content: '', streaming: true });
    // 取响应式代理（push 后从数组里拿，直接改原始对象不触发更新）
    const entry = messages.value[messages.value.length - 1];
    renderBuf.value = '';
    streamClosed = false;
    drainEntry = entry;
    drainAction = null;
    let failed = false;
    try {
      await streamWithAssistant(text, history, {
        onDelta: (t) => {
          renderBuf.value += t;
          startDrain();
        },
        onDone: (act) => {
          streamClosed = true;
          drainAction = act;
          // 动作在缓冲排空后才执行（startDrain 收尾），字打完再跳页
        },
        onError: (msg) => {
          renderBuf.value += `（${msg}）`;
        },
      });
    } catch {
      failed = true;
    }
    // 连接层失败（流没建立）→ 回退非流式接口
    if (failed && !entry.content && !renderBuf.value) {
      stopDrain();
      streamClosed = false;
      drainEntry = null;
      messages.value.splice(messages.value.indexOf(entry), 1);
      try {
        const res = await chatWithAssistant(text, history);
        const { reply, action } = res.data;
        messages.value.push({ role: 'assistant', content: reply, action });
        if (action) execAction(action);
      } catch {
        messages.value.push({ role: 'assistant', content: '（请求失败，稍后再试）' });
      }
      pending.value = false;
      scrollBottom();
    }
    // 成功建立流的情况由 startDrain 排空后收尾（streaming=false / execAction / pending=false）
  };

  const clearChat = () => {
    messages.value = [];
  };

  /** 执行 LLM 指令：navigate 跳页面 / highlight 高亮元素 / locate-config 定位到配置输入框 */
  const execAction = (act: AssistantAction) => {
    if (act.type === 'navigate') {
      router.push(act.target).catch(() => {});
      Message.success({ content: '助手已带你到对应页面', duration: 2000 });
    } else if (act.type === 'highlight') {
      nextTick(() => highlight(act.target));
    } else if (act.type === 'locate-config') {
      // target = "<toml文件名>|<点分路径>"；先跳到配置页，再派发定位事件
      const [fileName, keyPath] = act.target.split('|');
      router
        .push('/config/editor')
        .catch(() => {})
        .finally(() => {
          // 等页面路由切换+组件挂载后再派发
          setTimeout(() => {
            window.dispatchEvent(
              new CustomEvent('ai-locate-config-key', {
                detail: { path: keyPath, file: fileName },
              })
            );
          }, 400);
        });
      Message.success({ content: '助手已定位到对应配置项', duration: 2000 });
    }
  };

  /** 高亮特效：紫色脉冲描边 3 秒后移除 */
  let hlTimer: ReturnType<typeof setTimeout> | null = null;
  const highlight = (selector: string) => {
    let el: Element | null = null;
    try {
      el = document.querySelector(selector);
    } catch {
      el = null;
    }
    if (!el) {
      Message.warning('没找到要高亮的元素');
      return;
    }
    document.querySelectorAll('.ai-highlight').forEach((n) => n.classList.remove('ai-highlight'));
    el.classList.add('ai-highlight');
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    if (hlTimer) clearTimeout(hlTimer);
    hlTimer = setTimeout(() => el?.classList.remove('ai-highlight'), 3000);
  };

  onUnmounted(() => {
    if (hlTimer) clearTimeout(hlTimer);
    stopDrain();
  });
</script>

<script lang="ts">
  export default { name: 'AiAssistant' };
</script>

<style lang="less" scoped>
  .ai-fab {
    position: fixed;
    z-index: 1000;
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 10px 14px;
    border-radius: 24px;
    background: linear-gradient(135deg, rgb(var(--primary-5)), rgb(var(--primary-7)));
    color: #fff;
    cursor: grab;
    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.2);
    user-select: none;
    transition: box-shadow 0.2s;
    &:hover {
      box-shadow: 0 6px 20px rgba(0, 0, 0, 0.3);
    }
    &:active {
      cursor: grabbing;
    }
  }
  .ai-fab-tip {
    font-size: 12px;
    font-weight: 500;
  }

  .ai-window {
    position: fixed;
    z-index: 1001;
    width: 380px;
    height: 480px;
    display: flex;
    flex-direction: column;
    background: var(--color-bg-2);
    color: var(--color-text-1);
    border: 1px solid var(--color-border-2);
    border-radius: 12px;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.18);
    overflow: hidden;
  }
  .ai-window-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 14px;
    background: linear-gradient(135deg, rgb(var(--primary-5)), rgb(var(--primary-7)));
    color: #fff;
    cursor: grab;
    user-select: none;
    &:active {
      cursor: grabbing;
    }
  }
  .ai-window-title {
    display: flex;
    align-items: center;
    gap: 8px;
    font-weight: 600;
    font-size: 14px;
  }
  .ai-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: #6ff;
    animation: ai-pulse 1.6s ease-in-out infinite;
  }
  @keyframes ai-pulse {
    0%, 100% { opacity: 1; transform: scale(1); }
    50% { opacity: 0.5; transform: scale(0.8); }
  }

  .ai-window-body {
    flex: 1;
    overflow-y: auto;
    padding: 12px;
    display: flex;
    flex-direction: column;
    gap: 10px;
    background: var(--color-fill-1);
  }
  .ai-welcome {
    font-size: 13px;
    color: var(--color-text-2);
    p { margin: 0 0 8px; }
  }
  .ai-welcome-item {
    padding: 8px 10px;
    margin-bottom: 6px;
    border: 1px solid var(--color-border-2);
    border-radius: 8px;
    font-size: 12px;
    cursor: pointer;
    transition: all 0.2s;
    background: var(--color-bg-2);
    &:hover {
      border-color: rgb(var(--primary-6));
      color: rgb(var(--primary-6));
    }
  }

  .ai-msg {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    &-user {
      align-items: flex-end;
    }
  }
  .ai-msg-bubble {
    max-width: 85%;
    padding: 8px 12px;
    border-radius: 10px;
    font-size: 13px;
    line-height: 1.5;
    word-break: break-word;
    white-space: pre-wrap;
  }
  .ai-msg-user .ai-msg-bubble {
    background: rgb(var(--primary-6));
    color: #fff;
    border-bottom-right-radius: 2px;
  }
  .ai-msg-assistant .ai-msg-bubble {
    background: var(--color-bg-2);
    border: 1px solid var(--color-border-2);
    border-bottom-left-radius: 2px;
    /* 显式主题文字色：不设会继承到浏览器默认纯黑 #000，比面板其他文字死黑 */
    color: var(--color-text-1);
  }
  .ai-msg-action {
    margin-top: 4px;
  }

  /* 流式打字光标 */
  .ai-streaming::after {
    content: '▍';
    margin-left: 2px;
    color: rgb(var(--primary-6));
    animation: ai-cursor 0.9s steps(1) infinite;
  }
  @keyframes ai-cursor {
    50% { opacity: 0; }
  }

  .ai-typing {
    display: flex;
    gap: 4px;
    align-items: center;
    span {
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: var(--color-text-3);
      animation: ai-blink 1.2s ease-in-out infinite;
      &:nth-child(2) { animation-delay: 0.2s; }
      &:nth-child(3) { animation-delay: 0.4s; }
    }
  }
  @keyframes ai-blink {
    0%, 100% { opacity: 0.3; }
    50% { opacity: 1; }
  }

  .ai-window-footer {
    display: flex;
    gap: 8px;
    padding: 10px 12px;
    border-top: 1px solid var(--color-border-2);
    background: var(--color-bg-2);
  }
</style>

<style lang="less">
  /* 全局高亮特效（不能 scoped，目标在页面组件内部） */
  .ai-highlight {
    animation: ai-highlight-pulse 0.9s ease-in-out 3;
    position: relative;
    border-radius: 8px;
  }
  @keyframes ai-highlight-pulse {
    0%, 100% {
      box-shadow: 0 0 0 0 rgba(114, 46, 209, 0);
      background-color: transparent;
    }
    50% {
      box-shadow: 0 0 0 6px rgba(114, 46, 209, 0.35);
      background-color: rgba(114, 46, 209, 0.08);
    }
  }

  /* ── 助手气泡 Markdown（v-html 内容不受 scoped 约束，挂全局） ── */
  .ai-msg-bubble.ai-md {
    white-space: normal;
    word-break: break-word;
    .ai-md-h {
      font-weight: 600;
      font-size: 13px;
      margin: 4px 0 2px;
      color: var(--color-text-1);
    }
    .ai-md-li {
      padding-left: 10px;
      margin: 1px 0;
    }
    .ai-md-quote {
      border-left: 3px solid rgb(var(--primary-6));
      padding-left: 8px;
      margin: 2px 0;
      color: var(--color-text-2);
    }
    .ai-md-code {
      font-family: 'JetBrains Mono', Consolas, monospace;
      background: var(--color-fill-2);
      padding: 1px 5px;
      border-radius: 4px;
      font-size: 12px;
      color: rgb(var(--primary-6));
    }
    .ai-md-pre {
      background: var(--color-fill-2);
      border-radius: 6px;
      padding: 8px 10px;
      overflow-x: auto;
      margin: 4px 0;
      code {
        font-family: 'JetBrains Mono', Consolas, monospace;
        font-size: 12px;
        color: var(--color-text-1);
        background: none;
        padding: 0;
      }
    }
    a {
      color: rgb(var(--primary-6));
      text-decoration: none;
      &:hover {
        text-decoration: underline;
      }
    }
    strong {
      font-weight: 600;
    }
    del {
      color: var(--color-text-3);
    }
  }
</style>

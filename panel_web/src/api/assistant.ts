/**
 * AI 助手接口（panel/routers/assistant.py，前缀 /api/assistant）
 *
 * v2.3.3 流式：/chat/stream 走 SSE（delta 逐字 + done 附动作），
 * axios 不支持流式响应 → 用 fetch + ReadableStream；token 用与拦截器同款 Bearer。
 */
import axios from 'axios';
import { getToken } from '@/utils/auth';

export interface AssistantAction {
  type: 'navigate' | 'highlight' | 'locate-config';
  target: string;
}

export interface AssistantChatResult {
  reply: string;
  action: AssistantAction | null;
}

export interface ChatHistoryItem {
  role: 'user' | 'assistant';
  content: string;
}

export function chatWithAssistant(message: string, history: ChatHistoryItem[] = []) {
  return axios.post<AssistantChatResult>('/api/assistant/chat', {
    message,
    history,
  });
}

export interface AssistantStreamCallbacks {
  /** 收到一段增量文本 */
  onDelta: (text: string) => void;
  /** 流结束：动作（可能为 null） */
  onDone: (action: AssistantAction | null) => void;
  /** 流中出错（LLM 失败等），文本已照常转发到已收到的部分 */
  onError?: (msg: string) => void;
}

/** 流式对话。resolve 表示流正常读完；reject 表示连接层失败（可回退非流式） */
export async function streamWithAssistant(
  message: string,
  history: ChatHistoryItem[],
  cb: AssistantStreamCallbacks
): Promise<void> {
  const token = getToken();
  const resp = await fetch('/api/assistant/chat/stream', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ message, history }),
  });
  if (!resp.ok || !resp.body) {
    throw new Error(`HTTP ${resp.status}`);
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  let finalAction: AssistantAction | null = null;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const parts = buf.split('\n\n');
    buf = parts.pop() ?? '';
    for (const part of parts) {
      const line = part.split('\n').find((l) => l.startsWith('data:'));
      if (!line) continue;
      try {
        const payload = JSON.parse(line.slice(5).trim());
        if (typeof payload.t === 'string' && payload.t) cb.onDelta(payload.t);
        if (payload.err) cb.onError?.(payload.err);
        if (payload.done) {
          finalAction = (payload.action as AssistantAction | null) ?? null;
        }
      } catch {
        /* 坏帧忽略 */
      }
    }
  }
  cb.onDone(finalAction);
}

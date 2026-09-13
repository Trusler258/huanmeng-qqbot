/**
 * AI 助手接口（panel/routers/assistant.py，前缀 /api/assistant）
 */
import axios from 'axios';

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

/**
 * 批次A API：messages / memory / logs / system
 */

import axios from 'axios';

// ── messages ──────────────────────────────────────────────
export interface MsgRow {
  id: number;
  chat_id: number;
  user_id: number;
  name: string;
  content: string;
  ts: number;
}

export const searchMessages = (params: {
  q?: string;
  chat?: string;
  limit?: number;
  offset?: number;
}) => axios.get('/api/messages/search', { params });

export const getMsgStats = () => axios.get('/api/messages/stats');

export const getMsglogFiles = () => axios.get('/api/messages/msglog/files');

export const getMsglog = (
  chatId: string,
  params: { limit?: number; offset?: number; q?: string }
) => axios.get(`/api/messages/msglog/${chatId}`, { params });

// ── memory ────────────────────────────────────────────────
export const getNotesList = () => axios.get('/api/memory/notes');
export const getNote = (chatId: string) =>
  axios.get(`/api/memory/notes/${chatId}`);
export const addNoteLine = (chatId: string, text: string) =>
  axios.post(`/api/memory/notes/${chatId}/lines`, { text });
export const updateNoteLine = (chatId: string, index: number, text: string) =>
  axios.put(`/api/memory/notes/${chatId}/lines`, { index, text });
export const deleteNoteLine = (chatId: string, index: number) =>
  axios.delete(`/api/memory/notes/${chatId}/lines`, { data: { index } });

export const getStmList = () => axios.get('/api/memory/stm');
export const getStm = (chatId: string, params?: { limit?: number; tag?: string }) =>
  axios.get(`/api/memory/stm/${chatId}`, { params });

export const getSelfKnowledge = () => axios.get('/api/memory/self-knowledge');
export const getSkillsList = () => axios.get('/api/memory/skills');
export const getSkillFile = (name: string) =>
  axios.get(`/api/memory/skills/${name}`);
export const writePanelFile = (body: {
  path: string;
  content: string;
  confirm: string;
}) => axios.post('/api/memory/file', body);
export const readPanelFile = (path: string) =>
  axios.get('/api/memory/file', { params: { path } });

// ── logs ──────────────────────────────────────────────────
export const getLogFiles = () => axios.get('/api/logs/files');
export const getLogTail = (params: {
  file?: string;
  lines?: number;
  keyword?: string;
  level?: string;
}) => axios.get('/api/logs', { params });

// ── system ────────────────────────────────────────────────
export interface ServiceItem {
  name: string;
  active: string;
  in_service: boolean;
  sub_state: string;
  since: string;
}

export const getServices = () => axios.get('/api/system/services');
export const serviceAction = (service: string, action: string, confirm: string) =>
  axios.post('/api/system/services/action', { service, action, confirm });
export const getPorts = () => axios.get('/api/system/ports');
export const getUpdateLog = () => axios.get('/api/system/update-log');
export const getAudit = (limit = 200) =>
  axios.get('/api/system/audit', { params: { limit } });
export const getSysInfo = () => axios.get('/api/system/info');

// 操作栈
export interface OpItem {
  id: string;
  ts: string;
  kind: string;
  note: string;
  path?: string;
  backup?: string;
}

export const getOps = (limit = 30) =>
  axios.get('/api/system/ops', { params: { limit } });
export const rollbackOps = (steps: number, confirm: string) =>
  axios.post('/api/system/ops/rollback', { steps, confirm });
export const clearOps = () =>
  axios.post('/api/system/ops/clear', { confirm: 'CLEAR' });

// 崩溃自愈
export const getSelfheal = () => axios.get('/api/system/selfheal');
export const selfhealToggle = (on: boolean, confirm: string) =>
  axios.post('/api/system/selfheal/toggle', { on, confirm });
export const selfhealArm = () => axios.post('/api/system/selfheal/arm');
export const selfhealDisarm = () => axios.post('/api/system/selfheal/disarm');
export const selfhealKeepalive = () =>
  axios.post('/api/system/selfheal/keepalive');
export const selfhealTest = (confirm: string) =>
  axios.post('/api/system/selfheal/test', { confirm });
export const selfhealEventsClear = () =>
  axios.post('/api/system/selfheal/events/clear');

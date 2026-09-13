/**
 * 批次B API：media / database / groups / prompts
 */

import axios from 'axios';

// ── media ─────────────────────────────────────────────────
export interface MediaCategory {
  key: string;
  label: string;
  desc: string;
  deletable: boolean;
  warn: string;
  exists: boolean;
  count: number;
  size: number;
}

export interface MediaImage {
  name: string;
  size: number;
  mtime: number;
  ext: string;
}

export const getMediaCategories = () => axios.get('/api/media/categories');
export const getMediaImages = (
  category: string,
  params?: { sort?: string; order?: string; limit?: number; offset?: number; ext?: string }
) => axios.get(`/api/media/${category}`, { params });
export const mediaThumbUrl = (category: string, name: string) =>
  `/api/media/${category}/thumb/${encodeURIComponent(name)}`;
export const deleteMediaImages = (category: string, names: string[], confirm: string) =>
  axios.post('/api/media/delete', { category, names, confirm });
export const getMediaTrash = () => axios.get('/api/media/trash/list');
export const purgeMediaTrash = (batch: string, confirm: string) =>
  axios.post('/api/media/trash/purge', { batch, confirm });

// ── database ──────────────────────────────────────────────
export interface DbInfo {
  key: string;
  label: string;
  file: string;
  rel: string;
  note: string;
  exists: boolean;
  size: number;
  tables: { name: string; rows: number }[];
  error?: string;
}

export const getDbList = () => axios.get('/api/database/list');
export const getTableSchema = (dbkey: string, table: string) =>
  axios.get(`/api/database/${dbkey}/schema/${table}`);
export const getTableRows = (
  dbkey: string,
  table: string,
  params?: { limit?: number; offset?: number; order_by?: string; order?: string }
) => axios.get(`/api/database/${dbkey}/rows/${table}`, { params });
export const searchTable = (
  dbkey: string,
  table: string,
  params: { q: string; limit?: number }
) => axios.get(`/api/database/${dbkey}/search/${table}`, { params });
export const execSql = (dbkey: string, sql: string, confirm: string) =>
  axios.post(`/api/database/${dbkey}/exec`, { sql, confirm });
export const reindexTable = (dbkey: string, table: string, confirm: string) =>
  axios.post(`/api/database/${dbkey}/reindex/${table}`, { confirm });
export const optimizeDb = (dbkey: string, confirm: string) =>
  axios.post(`/api/database/${dbkey}/optimize`, { confirm });

// ── groups ────────────────────────────────────────────────
export interface GroupItem {
  group_id: number;
  name: string;
  sources: string[];
  last_active: string;
  msglog_lines: number;
  msglog_size: number;
  memory_lines: number;
  memory_size: number;
  note_count: number;
  today?: { messages: number; users: number };
}

export const getGroupList = () => axios.get('/api/groups');
export const getGroupDetail = (groupId: number | string) =>
  axios.get(`/api/groups/${groupId}`);
export const getGroupMembers = (groupId: number | string) =>
  axios.get(`/api/groups/${groupId}/members`);
export const writeGroupNote = (groupId: number | string, groupIdNum: number, content: string) =>
  axios.post(`/api/groups/${groupId}/note`, { group_id: groupIdNum, content });
export const writeGroupMemory = (groupId: number | string, content: string) =>
  axios.put(`/api/groups/${groupId}/memory`, { content, confirm: String(groupId) });
export const getGroupBackups = (groupId: number | string) =>
  axios.get(`/api/groups/${groupId}/backups`);

// ── prompts ───────────────────────────────────────────────
export interface PromptFile {
  name: string;
  group: string;
  size: number;
  mtime: number;
}

export const getPromptList = () => axios.get('/api/prompts');
export const getPrompt = (name: string) => axios.get(`/api/prompts/${name}`);
export const writePrompt = (name: string, content: string) =>
  axios.put(`/api/prompts/${name}`, { content, confirm: name });
export const createPrompt = (name: string, content: string) =>
  axios.post('/api/prompts', { name, content, confirm: name });
export const deletePrompt = (name: string, confirm: string) =>
  axios.delete(`/api/prompts/${name}`, { data: { confirm } });
export const getPromptBackups = (name: string) =>
  axios.get(`/api/prompts/${name}/backups`);
export const restorePrompt = (name: string, backup: string) =>
  axios.post(`/api/prompts/${name}/restore`, { backup, confirm: name });

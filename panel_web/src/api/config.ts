/**
 * 配置编辑接口（panel/routers/config_editor.py，前缀 /api/config）
 */
import axios from 'axios';

export interface ConfigFileMeta {
  name: string;
  label: string;
  desc: string;
  known: boolean;
  size: number;
  mtime: number;
}

export interface ConfigFileDetail {
  ok: boolean;
  name: string;
  size: number;
  raw: string;
  sanitized: string;
  parsed: unknown;
  parse_error: string;
}

export interface BackupItem {
  file: string;
  size: number;
  mtime: number;
}

export interface EnvItem {
  key: string;
  configured: boolean;
  length: number;
  hint: string;
}

export function getConfigFiles() {
  return axios.get<{ count: number; files: ConfigFileMeta[] }>('/api/config/files');
}

export function getConfigEnv() {
  return axios.get<{ exists: boolean; items: EnvItem[] }>('/api/config/env');
}

export function getConfigFile(name: string) {
  return axios.get<ConfigFileDetail>(`/api/config/file/${name}`);
}

export function writeConfigFile(name: string, content: string) {
  return axios.put(`/api/config/file/${name}`, { content, confirm: name });
}

/** 单键修改（表单模式）：保持文件其余部分原样，含注释与键序 */
export function setConfigKey(name: string, key: string, value: unknown) {
  return axios.post(`/api/config/file/${name}/set`, { key, value, confirm: name });
}

export function getConfigBackups(name: string) {
  return axios.get<{ count: number; backups: BackupItem[] }>(
    `/api/config/file/${name}/backups`
  );
}

export function restoreConfigFile(name: string, backup: string) {
  return axios.post(`/api/config/file/${name}/restore`, { backup, confirm: name });
}

// ── 前端轻量 TOML 解析（仅用于表单展示，写操作仍由后端校验） ──
export interface FormField {
  /** 点分路径：section.key（无 section 时就是 key） */
  path: string;
  key: string;
  section: string;
  value: unknown;
  /** 值类型：决定表单控件 */
  type: 'bool' | 'number' | 'string' | 'array' | 'multiline' | 'unknown';
  /** 从行内/上方注释提取的描述 */
  desc: string;
  /** 原始行文本（只读展示用） */
  line: string;
}

/** 解析 toml 原文 → 表单字段列表（带注释描述） */
export function parseTomlForm(raw: string): FormField[] {
  const fields: FormField[] = [];
  const lines = raw.split('\n');
  let section = '';
  let pendingComment = ''; // 上方连续注释块

  const kvRe = /^(\s*)(?:"([^"]*)"|'([^']*)'|([^\s=\[\]{}#,]+))\s*=\s*(.+?)(\s*#\s*(.*))?$/;

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const trimmed = line.trim();
    if (!trimmed) {
      pendingComment = '';
      continue;
    }
    const secMatch = trimmed.match(/^\[([^\]]+)\]$/);
    if (secMatch) {
      section = secMatch[1].trim().replace(/^["']|["']$/g, '');
      pendingComment = '';
      continue;
    }
    if (trimmed.startsWith('#')) {
      const c = trimmed.replace(/^#+\s*/, '');
      pendingComment = pendingComment ? `${pendingComment}；${c}` : c;
      continue;
    }
    const m = line.match(kvRe);
    if (!m) continue;

    const key = (m[2] ?? m[3] ?? m[4] ?? '').trim();
    const valueRaw = (m[5] ?? '').trim();
    const inlineDesc = (m[7] ?? '').trim();
    if (!key) continue;

    // 值类型识别
    let type: FormField['type'] = 'unknown';
    let value: unknown = valueRaw;
    if (/^(true|false)$/.test(valueRaw)) {
      type = 'bool';
      value = valueRaw === 'true';
    } else if (/^-?\d+(\.\d+)?$/.test(valueRaw)) {
      type = 'number';
      value = Number(valueRaw);
    } else if (valueRaw.startsWith('[') && valueRaw.endsWith(']')) {
      type = 'array';
      try {
        value = JSON.parse(valueRaw.replace(/"/g, '"').replace(/"/g, '"').replace(/'/g, '"'));
      } catch {
        try {
          value = JSON.parse(valueRaw);
        } catch {
          value = valueRaw;
        }
      }
    } else if (valueRaw.startsWith('"""') || valueRaw.startsWith("'''")) {
      type = 'multiline';
      value = valueRaw;
    } else {
      type = 'string';
      // 去掉包裹引号还原字符串值
      if (
        (valueRaw.startsWith('"') && valueRaw.endsWith('"') && valueRaw.length >= 2) ||
        (valueRaw.startsWith("'") && valueRaw.endsWith("'") && valueRaw.length >= 2)
      ) {
        value = valueRaw.slice(1, -1);
      }
    }

    fields.push({
      path: section ? `${section}.${key}` : key,
      key,
      section,
      value,
      type,
      desc: inlineDesc || pendingComment,
      line: trimmed,
    });
    pendingComment = '';
  }
  return fields;
}

/** 按 section 分组 */
export function groupBySection(fields: FormField[]) {
  const groups: { section: string; fields: FormField[] }[] = [];
  const idx = new Map<string, number>();
  for (const f of fields) {
    const k = f.section || '(顶层)';
    if (!idx.has(k)) {
      idx.set(k, groups.length);
      groups.push({ section: k, fields: [] });
    }
    groups[idx.get(k)!].fields.push(f);
  }
  return groups;
}

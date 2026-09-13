/**
 * 概览接口（panel/routers/overview.py）
 *
 * GET /api/overview → { ok, version, server_time, uptime_seconds,
 *   bot: { alive, service }, services: {...}, today: { messages, groups, active_users },
 *   totals: { groups_tracked, fav_entries, profiles, notes_files, stm_files,
 *             msglog_files, data_size, db_size },
 *   features: {...}, system: { mem_total_kb, mem_available_kb, load_avg, cpu_count } }
 *
 * GET /api/overview/trend?days=14 → { ok, days, group, data: [{date, messages, active_users}] }
 */
import axios from 'axios';

export interface OverviewData {
  version: string;
  server_time: string;
  uptime_seconds: number;
  bot: { alive: boolean; service: string };
  services: Record<string, string>;
  today: { date: string; messages: number; groups: number; active_users: number };
  totals: {
    groups_tracked: number;
    fav_entries: number;
    profiles: number;
    notes_files: number;
    stm_files: number;
    msglog_files: number;
    data_size: number;
    db_size: number;
  };
  features: Record<string, unknown>;
  system: {
    mem_total_kb: number;
    mem_available_kb: number;
    load_avg: number[];
    cpu_count: number;
  };
}

export interface TrendPoint {
  date: string;
  messages: number;
  active_users: number;
}

export function getOverview() {
  return axios.get<OverviewData>('/api/overview');
}

export function getTrend(days = 14, group = '') {
  return axios.get<{ days: number; group: string; data: TrendPoint[] }>(
    '/api/overview/trend',
    { params: { days, group } }
  );
}

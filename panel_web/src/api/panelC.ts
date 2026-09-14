/**
 * 批次C API：social / economy / games / earthquake / plugins / commands / features
 */

import axios from 'axios';

// ── social ────────────────────────────────────────────────
export const getFavList = (params?: { limit?: number; offset?: number; group?: string }) =>
  axios.get('/api/social/fav', { params });
export const setFav = (key: string, value: number) =>
  axios.post('/api/social/fav', { key, value });
export const deleteFav = (key: string) =>
  axios.delete('/api/social/fav', { params: { key } });
export const getProfiles = (params?: { limit?: number; offset?: number }) =>
  axios.get('/api/social/profiles', { params });
export const getProfile = (qq: string) => axios.get(`/api/social/profiles/${qq}`);
export const setProfile = (qq: string, data: Record<string, unknown>) =>
  axios.post('/api/social/profiles', { qq, data });
export const deleteProfile = (qq: string) =>
  axios.delete(`/api/social/profiles/${qq}`);
export const getStatsGroups = () => axios.get('/api/social/stats/groups');
export const getGroupStats = (group: string, date?: string) =>
  axios.get(`/api/social/stats/${group}`, { params: { date } });

// ── economy ───────────────────────────────────────────────
export const getEconomy = () => axios.get('/api/economy');
export const adjustPoints = (uid: string, delta: number, reason: string) =>
  axios.post('/api/economy/points', { uid, delta, reason });
export const grantItem = (uid: string, item: string, count: number) =>
  axios.post('/api/economy/items', { uid, item, count });

// ── games ─────────────────────────────────────────────────
export const getWzqStats = (params?: { limit?: number; offset?: number }) =>
  axios.get('/api/games/wzq', { params });
export const getWdsjHistory = (params?: { limit?: number; offset?: number }) =>
  axios.get('/api/games/wdsj', { params });
export const getCountdowns = () => axios.get('/api/games/countdown');
export const getSearchCache = (limit = 50) =>
  axios.get('/api/games/search-cache', { params: { limit } });
export const clearSearchCache = () => axios.delete('/api/games/search-cache');
export const getLottery = () => axios.get('/api/games/lottery');

// ── earthquake ────────────────────────────────────────────
export const getEqStatus = () => axios.get('/api/earthquake');
export const setEqSubscribe = (
  group: string,
  enabled: boolean,
  minMagnitude: number,
  provinces: string[]
) =>
  axios.post('/api/earthquake/subscribe', {
    group,
    enabled,
    min_magnitude: minMagnitude,
    provinces,
  });
export const delEqSubscribe = (group: string) =>
  axios.delete(`/api/earthquake/subscribe/${group}`);
export const getEqRecent = () => axios.get('/api/earthquake/recent');

// ── plugins ───────────────────────────────────────────────
export const getPlugins = () => axios.get('/api/plugins');
export const getHmpPlugins = () => axios.get('/api/plugins/hmp');
export const getCapabilities = () => axios.get('/api/plugins/capabilities');
export const getEventbus = () => axios.get('/api/plugins/eventbus');

// ── commands ──────────────────────────────────────────────
export const getCommands = (params?: { q?: string; limit?: number }) =>
  axios.get('/api/commands', { params });
export const getLlmAudit = () => axios.get('/api/commands/llm-audit');

// ── features ──────────────────────────────────────────────
export const getFeatures = () => axios.get('/api/features');
export const toggleFeature = (key: string, enabled: boolean) =>
  axios.post('/api/features', { key, enabled, confirm: key });
export const resetFeature = (key: string) =>
  axios.post(`/api/features/reset?key=${encodeURIComponent(key)}`);

// ── licenses（/~key 许可码）────────────────────────────────
export const getLicenses = () => axios.get('/api/licenses');
export const createLicense = (type: string, note: string, count = 1) =>
  axios.post('/api/licenses', { type, note, count });
export const deleteLicense = (code: string) =>
  axios.delete(`/api/licenses?code=${encodeURIComponent(code)}`);
export const revokeLicense = (qq: string) =>
  axios.post('/api/licenses/revoke', { qq });

// ── luck（幸运值）────────────────────────────────────────
export const getLuckList = (params?: { date?: string; q?: string }) =>
  axios.get('/api/social/luck', { params });
export const setLuck = (qq: string, value: string, date = '') =>
  axios.post('/api/social/luck', { qq, value, date, confirm: 'luck' });
export const deleteLuck = (qq: string, date = '') =>
  axios.delete('/api/social/luck', { params: { qq, date } });

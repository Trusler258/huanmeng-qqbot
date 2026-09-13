import axios from 'axios';

/**
 * 幻梦面板后端 API 契约（对应 panel/routers/auth.py）：
 *   POST /api/auth/login  { password }            → { ok, token, ... }
 *   GET  /api/auth/status                          → { ok, ... }（免认证）
 *   其余接口全部要求 Authorization: Bearer <token>
 *
 * 后端所有响应都是 { ok: true, ... } 结构，没有 { code, msg, data } 包装，
 * 所以拦截器里不能套用 Arco Pro 模板的 code===20000 判定。
 */

export interface LoginData {
  /** 后端只要密码，不区分用户名 */
  password: string;
}

export interface LoginRes {
  token: string;
}

export function login(data: LoginData) {
  // 模板登录页传 { username, password }，多传的字段后端会忽略
  return axios.post<LoginRes>('/api/auth/login', data);
}

export function logout() {
  // 后端是无状态 JWT，没有 logout 接口，前端清 token 即可
  return Promise.resolve({ data: { ok: true } });
}

/** 用户信息：后端只有单一管理员，直接本地构造 */
export function getUserInfo() {
  return Promise.resolve({
    data: {
      name: 'admin',
      avatar: '',
      roles: ['admin'],
      email: 'admin@huanmeng.local',
    },
  });
}

export function getMenuList() {
  // 菜单走前端静态路由（settings.json menuFromServer=false）
  return Promise.resolve({ data: [] });
}

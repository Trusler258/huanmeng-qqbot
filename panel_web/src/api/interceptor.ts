import axios from 'axios';
import type { AxiosRequestConfig, AxiosResponse } from 'axios';
import { Message, Modal } from '@arco-design/web-vue';
import { useUserStore } from '@/store';
import { getToken } from '@/utils/auth';

/**
 * 幻梦面板后端响应约定：
 *   成功：HTTP 200，body { ok: true, ... }
 *   失败：HTTP 4xx/5xx，body { detail: '原因' }（FastAPI 默认结构）
 *
 * 401 有两种含义，必须区分：
 *   - POST /api/auth/login 的 401 = 密码错误（留在登录页提示，绝不弹"已过期"）
 *   - 其余接口的 401 = token 缺失/过期/无效（清 token → 回登录页）
 */

export interface HttpResponse<T = unknown> {
  ok: boolean;
  [key: string]: T | boolean | unknown;
}

if (import.meta.env.VITE_API_BASE_URL) {
  axios.defaults.baseURL = import.meta.env.VITE_API_BASE_URL;
}

axios.interceptors.request.use(
  (config: AxiosRequestConfig) => {
    // 每个请求带上 JWT
    const token = getToken();
    if (token) {
      if (!config.headers) {
        config.headers = {};
      }
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => {
    return Promise.reject(error);
  }
);

// 是否正在弹重新登录框（防止多个并发 401 弹好几个）
let reloginShowing = false;

// 判断当前是否停在登录页（兼容 history 路由与 hash 路由）
const onLoginPage = () =>
  window.location.pathname.endsWith('/login') ||
  window.location.hash.includes('/login');

axios.interceptors.response.use(
  (response: AxiosResponse) => response,
  (error) => {
    const status = error?.response?.status;
    const detail: string = error?.response?.data?.detail || error?.message || '请求出错';
    const reqUrl: string = error?.config?.url || '';
    const isLoginRequest = /\/api\/auth\/login/.test(reqUrl);

    if (status === 401) {
      // 登录接口本身的 401 = 密码错误 / 未初始化等，原样提示，绝不当成"会话过期"
      if (isLoginRequest) {
        Message.error({ content: detail, duration: 5 * 1000 });
        return Promise.reject(new Error(detail));
      }

      // 其余接口 401 = token 无效/过期：清 token 回登录页
      const userStore = useUserStore();
      userStore.logout();
      if (!reloginShowing && !onLoginPage()) {
        reloginShowing = true;
        Modal.warning({
          title: '登录已过期',
          content: '请重新登录',
          okText: '重新登录',
          hideCancel: true,
          async onOk() {
            reloginShowing = false;
            window.location.href = '/login';
          },
        });
      }
      return Promise.reject(new Error('登录已过期'));
    }

    if (status === 429) {
      // 登录失败递增封禁的提示（后端 429 = too many attempts）
      Message.error({ content: detail, duration: 6 * 1000 });
      return Promise.reject(new Error(detail));
    }

    // 登录失败(400)等信息直接给后端的 detail，中文可读
    Message.error({ content: detail, duration: 5 * 1000 });
    return Promise.reject(new Error(detail));
  }
);

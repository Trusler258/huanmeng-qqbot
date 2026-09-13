import { DEFAULT_LAYOUT } from '../base';
import { AppRouteRecordRaw } from '../types';

const MONITOR: AppRouteRecordRaw = {
  path: '/monitor',
  name: 'monitor',
  component: DEFAULT_LAYOUT,
  meta: {
    locale: 'menu.monitor',
    requiresAuth: true,
    icon: 'icon-computer',
    order: 2,
  },
  children: [
    {
      path: 'system',
      name: 'SystemStatus',
      component: () => import('@/views/system/index.vue'),
      meta: {
        locale: 'menu.monitor.system',
        requiresAuth: true,
        roles: ['*'],
      },
    },
    {
      path: 'logs',
      name: 'LogViewer',
      component: () => import('@/views/logs/index.vue'),
      meta: {
        locale: 'menu.monitor.logs',
        requiresAuth: true,
        roles: ['*'],
      },
    },
  ],
};

export default MONITOR;

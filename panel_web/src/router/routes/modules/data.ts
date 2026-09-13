import { DEFAULT_LAYOUT } from '../base';
import { AppRouteRecordRaw } from '../types';

const DATA: AppRouteRecordRaw = {
  path: '/data',
  name: 'data',
  component: DEFAULT_LAYOUT,
  meta: {
    locale: 'menu.data',
    requiresAuth: true,
    icon: 'icon-search',
    order: 3,
  },
  children: [
    {
      path: 'messages',
      name: 'MessagesSearch',
      component: () => import('@/views/messages/index.vue'),
      meta: {
        locale: 'menu.data.messages',
        requiresAuth: true,
        roles: ['*'],
      },
    },
    {
      path: 'memory',
      name: 'MemoryPage',
      component: () => import('@/views/memory/index.vue'),
      meta: {
        locale: 'menu.data.memory',
        requiresAuth: true,
        roles: ['*'],
      },
    },
  ],
};

export default DATA;

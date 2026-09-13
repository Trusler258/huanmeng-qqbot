import { DEFAULT_LAYOUT } from '../base';
import { AppRouteRecordRaw } from '../types';

const CONFIG: AppRouteRecordRaw = {
  path: '/config',
  name: 'config',
  component: DEFAULT_LAYOUT,
  meta: {
    locale: 'menu.config',
    requiresAuth: true,
    icon: 'icon-settings',
    order: 1,
  },
  children: [
    {
      path: 'editor',
      name: 'ConfigEditor',
      component: () => import('@/views/config-editor/index.vue'),
      meta: {
        locale: 'menu.config.editor',
        requiresAuth: true,
        roles: ['*'],
      },
    },
  ],
};

export default CONFIG;

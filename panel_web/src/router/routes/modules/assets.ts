import { DEFAULT_LAYOUT } from '../base';
import { AppRouteRecordRaw } from '../types';

const ASSETS: AppRouteRecordRaw = {
  path: '/resources',
  name: 'assets',
  component: DEFAULT_LAYOUT,
  meta: {
    locale: 'menu.assets',
    requiresAuth: true,
    icon: 'icon-cloud',
    order: 4,
  },
  children: [
    {
      path: 'media',
      name: 'MediaPage',
      component: () => import('@/views/media/index.vue'),
      meta: {
        locale: 'menu.assets.media',
        requiresAuth: true,
        roles: ['*'],
      },
    },
    {
      path: 'database',
      name: 'DatabasePage',
      component: () => import('@/views/database/index.vue'),
      meta: {
        locale: 'menu.assets.database',
        requiresAuth: true,
        roles: ['*'],
      },
    },
    {
      path: 'groups',
      name: 'GroupsPage',
      component: () => import('@/views/groups/index.vue'),
      meta: {
        locale: 'menu.assets.groups',
        requiresAuth: true,
        roles: ['*'],
      },
    },
    {
      path: 'prompts',
      name: 'PromptsPage',
      component: () => import('@/views/prompts/index.vue'),
      meta: {
        locale: 'menu.assets.prompts',
        requiresAuth: true,
        roles: ['*'],
      },
    },
  ],
};

export default ASSETS;

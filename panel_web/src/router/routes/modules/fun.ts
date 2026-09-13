import { DEFAULT_LAYOUT } from '../base';
import { AppRouteRecordRaw } from '../types';

const FUN: AppRouteRecordRaw = {
  path: '/fun',
  name: 'fun',
  component: DEFAULT_LAYOUT,
  meta: {
    locale: 'menu.fun',
    requiresAuth: true,
    icon: 'icon-experimental',
    order: 5,
  },
  children: [
    {
      path: 'social',
      name: 'SocialPage',
      component: () => import('@/views/social/index.vue'),
      meta: { locale: 'menu.fun.social', requiresAuth: true, roles: ['*'] },
    },
    {
      path: 'economy',
      name: 'EconomyPage',
      component: () => import('@/views/economy/index.vue'),
      meta: { locale: 'menu.fun.economy', requiresAuth: true, roles: ['*'] },
    },
    {
      path: 'games',
      name: 'GamesPage',
      component: () => import('@/views/games/index.vue'),
      meta: { locale: 'menu.fun.games', requiresAuth: true, roles: ['*'] },
    },
    {
      path: 'earthquake',
      name: 'EarthquakePage',
      component: () => import('@/views/earthquake/index.vue'),
      meta: { locale: 'menu.fun.earthquake', requiresAuth: true, roles: ['*'] },
    },
    {
      path: 'plugins',
      name: 'PluginsPage',
      component: () => import('@/views/plugins/index.vue'),
      meta: { locale: 'menu.fun.plugins', requiresAuth: true, roles: ['*'] },
    },
    {
      path: 'commands',
      name: 'CommandsPage',
      component: () => import('@/views/commands/index.vue'),
      meta: { locale: 'menu.fun.commands', requiresAuth: true, roles: ['*'] },
    },
    {
      path: 'features',
      name: 'FeaturesPage',
      component: () => import('@/views/features/index.vue'),
      meta: { locale: 'menu.fun.features', requiresAuth: true, roles: ['*'] },
    },
  ],
};

export default FUN;

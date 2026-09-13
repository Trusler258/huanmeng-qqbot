<template>
  <a-menu
    :mode="topMenu ? 'horizontal' : 'vertical'"
    v-model:collapsed="collapsed"
    v-model:open-keys="openKeys"
    :show-collapse-button="appStore.device !== 'mobile'"
    :auto-open="false"
    :selected-keys="selectedKeys"
    :auto-open-selected="true"
    :level-indent="34"
    style="height: 100%;width:100%;"
    @collapse="setCollapse"
  >
    <a-sub-menu
      v-for="item in visibleTopItems"
      :key="item.name"
    >
      <template #icon>
        <component :is="iconComp(item.meta?.icon)" v-if="item.meta?.icon" />
      </template>
      <template #title>{{ t(item.meta?.locale || '') }}</template>
      <template v-for="child in item.children || []" :key="child.name">
        <a-menu-item v-if="!child.children || !child.children.length" @click="goto(child)">
          <template #icon>
            <component :is="iconComp(child.meta?.icon)" v-if="child.meta?.icon" />
          </template>
          {{ t(child.meta?.locale || '') }}
        </a-menu-item>
        <a-sub-menu v-else :key="`sub-${child.name}`">
          <template #icon>
            <component :is="iconComp(child.meta?.icon)" v-if="child.meta?.icon" />
          </template>
          <template #title>{{ t(child.meta?.locale || '') }}</template>
          <a-menu-item
            v-for="leaf in child.children || []"
            :key="leaf.name"
            @click="goto(leaf)"
          >
            <template #icon>
              <component :is="iconComp(leaf.meta?.icon)" v-if="leaf.meta?.icon" />
            </template>
            {{ t(leaf.meta?.locale || '') }}
          </a-menu-item>
        </a-sub-menu>
      </template>
    </a-sub-menu>
  </a-menu>
</template>

<script lang="ts">
  import { defineComponent, ref, computed, Component } from 'vue';
  import { useI18n } from 'vue-i18n';
  import { useRoute, useRouter, RouteRecordRaw } from 'vue-router';
  import type { RouteMeta } from 'vue-router';
  import {
    IconDashboard,
    IconExclamationCircle,
  } from '@arco-design/web-vue/es/icon';
  import { useAppStore } from '@/store';
  import { listenerRouteChange } from '@/utils/route-listener';
  import { openWindow, regexUrl } from '@/utils';
  import useMenuTree from './use-menu-tree';

  // 静态图标映射：路由 meta.icon 名 -> arco 图标组件。
  // 不用运行时 compile()（h(compile(`<icon/>`))），那条链在打包时
  // 会因跨 chunk 符号摇树丢绑定（compile is not defined）。
  // 新增带图标的路由时，在这里补一行映射即可。
  const ICON_MAP: Record<string, Component> = {
    'icon-dashboard': IconDashboard,
    'icon-exclamation-circle': IconExclamationCircle,
  };

  const iconComp = (name?: string): Component | null =>
    (name && ICON_MAP[name]) || null;

  export default defineComponent({
    emit: ['collapse'],
    setup() {
      const { t } = useI18n();
      const appStore = useAppStore();
      const router = useRouter();
      const route = useRoute();
      const { menuTree } = useMenuTree();
      const collapsed = computed({
        get() {
          if (appStore.device === 'desktop') return appStore.menuCollapse;
          return false;
        },
        set(value: boolean) {
          appStore.updateSettings({ menuCollapse: value });
        },
      });

      const topMenu = computed(() => appStore.topMenu);
      const openKeys = ref<string[]>([]);
      const selectedKeys = ref<string[]>([]);

      // 过滤 hideInMenu（模板里不好写递归过滤，这里把顶层树预处理一遍）
      const filterTree = (items: RouteRecordRaw[] = []): RouteRecordRaw[] =>
        items
          .filter((it) => !(it.meta as RouteMeta | undefined)?.hideInMenu)
          .map((it) =>
            it.children?.length
              ? { ...it, children: filterTree(it.children) }
              : it
          );
      const visibleTopItems = computed(() => filterTree(menuTree.value));

      const goto = (item: RouteRecordRaw) => {
        if (regexUrl.test(item.path)) {
          openWindow(item.path);
          selectedKeys.value = [item.name as string];
          return;
        }
        const { hideInMenu, activeMenu } = (item.meta || {}) as RouteMeta;
        if (route.name === item.name && !hideInMenu && !activeMenu) {
          selectedKeys.value = [item.name as string];
          return;
        }
        router.push({ name: item.name });
      };

      const findMenuOpenKeys = (target: string) => {
        const result: string[] = [];
        let isFind = false;
        const backtrack = (item: RouteRecordRaw, keys: string[]) => {
          if (item.name === target) {
            isFind = true;
            result.push(...keys);
            return;
          }
          if (item.children?.length) {
            item.children.forEach((el) => {
              backtrack(el, [...keys, el.name as string]);
            });
          }
        };
        menuTree.value.forEach((el: RouteRecordRaw) => {
          if (isFind) return;
          backtrack(el, [el.name as string]);
        });
        return result;
      };

      listenerRouteChange((newRoute) => {
        const { requiresAuth, activeMenu, hideInMenu } = newRoute.meta;
        if (requiresAuth && (!hideInMenu || activeMenu)) {
          const menuOpenKeys = findMenuOpenKeys(
            (activeMenu || newRoute.name) as string
          );
          const keySet = new Set([...menuOpenKeys, ...openKeys.value]);
          openKeys.value = [...keySet];
          selectedKeys.value = [
            activeMenu || menuOpenKeys[menuOpenKeys.length - 1],
          ];
        }
      }, true);

      const setCollapse = (val: boolean) => {
        if (appStore.device === 'desktop')
          appStore.updateSettings({ menuCollapse: val });
      };

      return {
        t,
        appStore,
        topMenu,
        collapsed,
        openKeys,
        selectedKeys,
        visibleTopItems,
        goto,
        setCollapse,
        iconComp,
      };
    },
  });
</script>

<style lang="less" scoped>
  :deep(.arco-menu-inner) {
    .arco-menu-inline-header {
      display: flex;
      align-items: center;
    }
    .arco-icon {
      &:not(.arco-icon-down) {
        font-size: 18px;
      }
    }
  }
</style>

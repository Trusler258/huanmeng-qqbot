<template>
  <div class="container">
    <Breadcrumb :items="['menu.games', 'menu.games.list']" />
    <div class="layout">
      <a-tabs type="card" size="medium">
        <a-tab-pane key="wzq" title="五子棋战绩">
          <a-table :data="wzqItems" :loading="wzqLoading" :pagination="{ pageSize: 15 }" size="small">
            <template #columns>
              <a-table-column v-for="(col, i) in wzqCols" :key="col" :title="col" :data-index="col" :width="i === 0 ? 140 : undefined">
                <template #cell="{ record }">{{ fmtVal(record[col]) }}</template>
              </a-table-column>
            </template>
            <template #empty><a-empty description="暂无战绩" /></template>
          </a-table>
        </a-tab-pane>

        <a-tab-pane key="wdsj" title="洛花星雨">
          <a-table :data="wdsjItems" :loading="wdsjLoading" :pagination="{ pageSize: 15 }" size="small">
            <template #columns>
              <a-table-column v-for="(col, i) in wdsjCols" :key="col" :title="col" :data-index="col" :width="i === 0 ? 140 : undefined">
                <template #cell="{ record }">{{ fmtVal(record[col]) }}</template>
              </a-table-column>
            </template>
            <template #empty><a-empty description="暂无历史" /></template>
          </a-table>
        </a-tab-pane>

        <a-tab-pane key="countdown" title="倒计时">
          <a-table :data="countdowns" :loading="cdLoading" :pagination="false" size="small">
            <template #columns>
              <a-table-column v-for="col in cdCols" :key="col" :title="col" :data-index="col">
                <template #cell="{ record }">{{ fmtVal(record[col]) }}</template>
              </a-table-column>
            </template>
            <template #empty><a-empty description="暂无倒计时" /></template>
          </a-table>
        </a-tab-pane>

        <a-tab-pane key="cache" title="搜索缓存">
          <template #extra>
            <a-popconfirm content="清空全部搜索缓存？" @ok="doClearCache">
              <a-button size="small" status="danger">清空缓存</a-button>
            </a-popconfirm>
          </template>
          <a-table :data="cacheItems" :loading="cacheLoading" :pagination="{ pageSize: 15 }" size="small">
            <template #columns>
              <a-table-column v-for="col in cacheCols" :key="col" :title="col" :data-index="col">
                <template #cell="{ record }">
                  <span class="cell-text">{{ fmtVal(record[col]) }}</span>
                </template>
              </a-table-column>
            </template>
            <template #empty><a-empty description="暂无缓存" /></template>
          </a-table>
        </a-tab-pane>
      </a-tabs>
    </div>
  </div>
</template>

<script lang="ts" setup>
  import { ref, onMounted } from 'vue';
  import { Message } from '@arco-design/web-vue';
  import Breadcrumb from '@/components/breadcrumb/index.vue';
  import useLoading from '@/hooks/loading';
  import {
    getWzqStats,
    getWdsjHistory,
    getCountdowns,
    getSearchCache,
    clearSearchCache,
  } from '@/api/panelC';

  const { loading: wzqLoading, setLoading: setWzqLoading } = useLoading();
  const { loading: wdsjLoading, setLoading: setWdsjLoading } = useLoading();
  const { loading: cdLoading, setLoading: setCdLoading } = useLoading();
  const { loading: cacheLoading, setLoading: setCacheLoading } = useLoading();

  const wzqItems = ref<any[]>([]);
  const wzqCols = ref<string[]>([]);
  const wdsjItems = ref<any[]>([]);
  const wdsjCols = ref<string[]>([]);
  const countdowns = ref<any[]>([]);
  const cdCols = ref<string[]>([]);
  const cacheItems = ref<any[]>([]);
  const cacheCols = ref<string[]>([]);

  const deriveCols = (rows: any[]) => {
    if (!rows.length) return [];
    const keys = new Set<string>();
    for (const r of rows.slice(0, 20)) {
      Object.keys(r).forEach((k) => {
        if (!k.startsWith('_')) keys.add(k);
      });
    }
    return [...keys].slice(0, 8);
  };

  const fmtVal = (v: any) => {
    if (v === null || v === undefined) return '';
    if (typeof v === 'object') return JSON.stringify(v).slice(0, 80);
    const s = String(v);
    return s.length > 100 ? `${s.slice(0, 100)}...` : s;
  };

  const loadAll = async () => {
    setWzqLoading(true);
    try {
      const res = await getWzqStats({ limit: 200 });
      wzqItems.value = res.data.items || [];
      wzqCols.value = deriveCols(wzqItems.value);
    } finally {
      setWzqLoading(false);
    }

    setWdsjLoading(true);
    try {
      const res = await getWdsjHistory({ limit: 200 });
      wdsjItems.value = res.data.history || res.data.items || [];
      wdsjCols.value = deriveCols(wdsjItems.value);
    } finally {
      setWdsjLoading(false);
    }

    setCdLoading(true);
    try {
      const res = await getCountdowns();
      countdowns.value = res.data.items || [];
      cdCols.value = deriveCols(countdowns.value);
    } finally {
      setCdLoading(false);
    }

    setCacheLoading(true);
    try {
      const res = await getSearchCache(100);
      cacheItems.value = res.data.items || [];
      cacheCols.value = deriveCols(cacheItems.value);
    } finally {
      setCacheLoading(false);
    }
  };

  const doClearCache = async () => {
    const res = await clearSearchCache();
    Message.success(`已清空 ${res.data.cleared ?? 0} 条缓存`);
    await loadAll();
  };

  onMounted(loadAll);
</script>

<script lang="ts">
  export default { name: 'GamesPage' };
</script>

<style lang="less" scoped>
  .container {
    padding: 0 20px 20px;
  }
  .layout {
    margin-top: 16px;
  }
  .cell-text {
    font-size: 12px;
    word-break: break-all;
  }
</style>

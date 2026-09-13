<template>
  <div class="container">
    <Breadcrumb :items="['menu.earthquake', 'menu.earthquake.status']" />
    <div class="layout">
      <a-space :size="16" direction="vertical" fill>
        <!-- 订阅管理 -->
        <a-card class="general-card" title="群订阅" :loading="loading">
          <template #extra>
            <a-button size="small" type="primary" @click="addVisible = true">新增订阅</a-button>
          </template>
          <a-table :data="subscriptions" :pagination="false" size="small">
            <template #columns>
              <a-table-column title="群号" data-index="group" :width="160" />
              <a-table-column title="启用" :width="100">
                <template #cell="{ record }">
                  <a-tag :color="record.enabled ? 'green' : 'gray'" size="small">
                    {{ record.enabled ? '开' : '关' }}
                  </a-tag>
                </template>
              </a-table-column>
              <a-table-column title="最小震级" data-index="min_magnitude" :width="110" />
              <a-table-column title="省份过滤">
                <template #cell="{ record }">
                  {{ (record.provinces || []).join('、') || '全部' }}
                </template>
              </a-table-column>
              <a-table-column title="操作" :width="100">
                <template #cell="{ record }">
                  <a-popconfirm :content="`取消群 ${record.group} 的订阅？`" @ok="removeSub(record.group)">
                    <a-button size="mini" status="danger">退订</a-button>
                  </a-popconfirm>
                </template>
              </a-table-column>
            </template>
            <template #empty><a-empty description="暂无订阅" /></template>
          </a-table>
        </a-card>

        <!-- 模块状态 -->
        <a-card class="general-card" title="模块状态" :loading="loading">
          <a-space wrap>
            <a-tag v-for="(v, k) in data?.data_files || {}" :key="k" size="medium">
              {{ k }}: {{ fmtFile(v) }}
            </a-tag>
          </a-space>
          <a-divider style="margin: 12px 0" />
          <div class="sec-title">最近推送（{{ pushes.length }}）</div>
          <div class="push-list">
            <div v-for="(p, i) in pushes" :key="i" class="push-line">{{ p }}</div>
            <a-empty v-if="!pushes.length" description="暂无推送记录" />
          </div>
        </a-card>

        <!-- 最近地震 -->
        <a-card class="general-card" title="最近地震（CENC）" :loading="recentLoading">
          <a-table :data="recentEqs" :pagination="{ pageSize: 10 }" size="small">
            <template #columns>
              <a-table-column v-for="col in recentCols" :key="col" :title="col" :data-index="col">
                <template #cell="{ record }">
                  <span class="cell-text">{{ fmtVal(record[col]) }}</span>
                </template>
              </a-table-column>
            </template>
            <template #empty><a-empty description="接口无数据" /></template>
          </a-table>
        </a-card>
      </a-space>
    </div>

    <!-- 新增订阅 -->
    <a-modal v-model:visible="addVisible" title="新增地震订阅" @ok="doAdd" @cancel="addVisible = false">
      <a-space direction="vertical" fill style="width: 100%">
        <a-input v-model="newGroup" placeholder="群号" />
        <a-input-number v-model="newMinMag" :min="0" :max="10" :step="0.5" placeholder="最小震级 M" style="width: 100%" />
        <a-input v-model="newProvinces" placeholder="省份过滤（顿号/逗号分隔，留空=全部）" />
      </a-space>
    </a-modal>
  </div>
</template>

<script lang="ts" setup>
  import { ref, onMounted } from 'vue';
  import { Message } from '@arco-design/web-vue';
  import Breadcrumb from '@/components/breadcrumb/index.vue';
  import useLoading from '@/hooks/loading';
  import {
    getEqStatus,
    setEqSubscribe,
    delEqSubscribe,
    getEqRecent,
  } from '@/api/panelC';

  const { loading, setLoading } = useLoading();
  const { loading: recentLoading, setLoading: setRecentLoading } = useLoading();

  const data = ref<any>(null);
  const subscriptions = ref<any[]>([]);
  const pushes = ref<any[]>([]);
  const pushCols = ref<string[]>([]);
  const recentEqs = ref<any[]>([]);
  const recentCols = ref<string[]>([]);

  const addVisible = ref(false);
  const newGroup = ref('');
  const newMinMag = ref(4.0);
  const newProvinces = ref('');

  const fmtVal = (v: any) => {
    if (v === null || v === undefined) return '';
    if (typeof v === 'object') return JSON.stringify(v).slice(0, 80);
    return String(v).slice(0, 100);
  };
  const fmtFile = (v: any) => {
    if (typeof v === 'number') return String(v);
    if (typeof v === 'string') return v.slice(0, 40);
    return JSON.stringify(v).slice(0, 60);
  };

  const deriveCols = (rows: any[]) => {
    if (!rows.length) return [];
    const keys = new Set<string>();
    for (const r of rows.slice(0, 10)) Object.keys(r).forEach((k) => !k.startsWith('_') && keys.add(k));
    return [...keys].slice(0, 7);
  };

  const load = async () => {
    setLoading(true);
    try {
      const res = await getEqStatus();
      data.value = res.data;
      // data_files 里 key 含 subscribe 的 JSON 就是订阅表
      const files: Record<string, any> = res.data.data_files || {};
      const subKey = Object.keys(files).find((k) => k.includes('subscribe'));
      const subObj = subKey ? files[subKey] : null;
      subscriptions.value = subObj && typeof subObj === 'object'
        ? Object.entries(subObj).map(([group, cfg]: [string, any]) => ({
            group,
            enabled: cfg?.enabled ?? true,
            min_magnitude: cfg?.min_magnitude ?? cfg?.min_mag ?? '-',
            provinces: cfg?.provinces || [],
          }))
        : [];
      pushes.value = res.data.recent_pushes || [];
      pushCols.value = pushes.value.length ? ['log'] : [];
    } finally {
      setLoading(false);
    }
    setRecentLoading(true);
    try {
      const res = await getEqRecent();
      recentEqs.value = res.data.items || res.data.earthquakes || res.data.data || [];
      recentCols.value = deriveCols(recentEqs.value);
    } catch {
      recentEqs.value = [];
    } finally {
      setRecentLoading(false);
    }
  };

  const doAdd = async () => {
    if (!newGroup.value.trim()) return;
    const provinces = newProvinces.value
      .split(/[、,，\s]+/)
      .map((s) => s.trim())
      .filter(Boolean);
    await setEqSubscribe(newGroup.value.trim(), true, newMinMag.value || 4.0, provinces);
    Message.success('订阅已保存');
    addVisible.value = false;
    newGroup.value = '';
    await load();
  };

  const removeSub = async (group: string) => {
    await delEqSubscribe(group);
    Message.success('已退订');
    await load();
  };

  onMounted(load);
</script>

<script lang="ts">
  export default { name: 'EarthquakePage' };
</script>

<style lang="less" scoped>
  .container {
    padding: 0 20px 20px;
  }
  .layout {
    margin-top: 16px;
  }
  .sec-title {
    font-weight: 600;
    font-size: 14px;
    margin-bottom: 8px;
  }
  .cell-text {
    font-size: 12px;
    word-break: break-all;
  }
  .push-list {
    max-height: 260px;
    overflow: auto;
    font-family: 'JetBrains Mono', Consolas, monospace;
    font-size: 12px;
  }
  .push-line {
    padding: 3px 0;
    border-bottom: 1px dashed var(--color-border-1);
    color: var(--color-text-2);
    word-break: break-all;
  }
</style>

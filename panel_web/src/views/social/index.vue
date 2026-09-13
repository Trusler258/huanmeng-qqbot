<template>
  <div class="container">
    <Breadcrumb :items="['menu.social', 'menu.social.fav']" />
    <div class="layout">
      <a-tabs type="card" size="medium">
        <!-- 好感度 -->
        <a-tab-pane key="fav" title="好感度">
          <a-input-search
            v-model="favGroup"
            placeholder="按群号过滤（留空看全部）"
            style="max-width: 280px; margin-bottom: 12px"
            @search="loadFav"
            @clear="loadFav"
          />
          <a-table :data="favItems" :loading="favLoading" :pagination="{ pageSize: 15 }" size="small">
            <template #columns>
              <a-table-column title="键（群+QQ）" data-index="key" />
              <a-table-column title="好感度" :width="110">
                <template #cell="{ record }">
                  <span :style="{ color: record.fav >= 0 ? 'var(--color-danger-6)' : 'var(--color-success-6)' }">
                    {{ record.fav }}
                  </span>
                </template>
              </a-table-column>
              <a-table-column title="操作" :width="160">
                <template #cell="{ record }">
                  <a-space size="mini">
                    <a-input-number v-model="record._edit" size="mini" style="width: 90px" />
                    <a-button size="mini" type="outline" @click="saveFav(record)">存</a-button>
                    <a-popconfirm content="删除这条好感度？" @ok="removeFav(record.key)">
                      <a-button size="mini" status="danger">删</a-button>
                    </a-popconfirm>
                  </a-space>
                </template>
              </a-table-column>
            </template>
          </a-table>
        </a-tab-pane>

        <!-- 用户画像 -->
        <a-tab-pane key="profiles" title="用户画像">
          <a-table :data="profiles" :loading="profLoading" :pagination="{ pageSize: 15 }" size="small">
            <template #columns>
              <a-table-column title="QQ" data-index="qq" :width="130" />
              <a-table-column title="画像摘要">
                <template #cell="{ record }">
                  {{ summarize(record.data) }}
                </template>
              </a-table-column>
              <a-table-column title="操作" :width="100">
                <template #cell="{ record }">
                  <a-button size="mini" @click="viewProfile(record)">详情</a-button>
                </template>
              </a-table-column>
            </template>
          </a-table>
        </a-tab-pane>

        <!-- 群统计 -->
        <a-tab-pane key="stats" title="群统计">
          <a-grid :cols="24" :col-gap="16">
            <a-grid-item :span="6">
              <a-card class="general-card" title="有统计的群" :loading="statsLoading">
                <div
                  v-for="g in statsGroups"
                  :key="g.group"
                  class="pick-item"
                  :class="{ active: g.group === curStatsGroup }"
                  @click="selectStatsGroup(g.group)"
                >
                  {{ g.group }}
                  <span class="pick-meta">{{ g.days }} 天</span>
                </div>
              </a-card>
            </a-grid-item>
            <a-grid-item :span="18">
              <a-card class="general-card" :title="curStatsGroup ? `群 ${curStatsGroup} 统计` : '选择群'">
                <a-table :data="groupStats" :pagination="{ pageSize: 12 }" size="small">
                  <template #columns>
                    <a-table-column title="日期" data-index="date" :width="120" />
                    <a-table-column title="消息数" data-index="messages" :width="110" />
                    <a-table-column title="活跃用户" data-index="users" :width="110" />
                    <a-table-column title="文件" data-index="file" />
                  </template>
                  <template #empty><a-empty description="选择左侧群查看" /></template>
                </a-table>
              </a-card>
            </a-grid-item>
          </a-grid>
        </a-tab-pane>
      </a-tabs>
    </div>

    <!-- 画像详情抽屉 -->
    <a-drawer v-model:visible="profVisible" title="用户画像详情" :width="480" unmount-on-close>
      <pre class="doc-pre">{{ profDetail }}</pre>
    </a-drawer>
  </div>
</template>

<script lang="ts" setup>
  import { ref, onMounted } from 'vue';
  import { Message } from '@arco-design/web-vue';
  import Breadcrumb from '@/components/breadcrumb/index.vue';
  import useLoading from '@/hooks/loading';
  import {
    getFavList,
    setFav,
    deleteFav,
    getProfiles,
    getStatsGroups,
    getGroupStats,
  } from '@/api/panelC';

  const { loading: favLoading, setLoading: setFavLoading } = useLoading();
  const { loading: profLoading, setLoading: setProfLoading } = useLoading();
  const { loading: statsLoading, setLoading: setStatsLoading } = useLoading();

  const favItems = ref<any[]>([]);
  const favGroup = ref('');
  const profiles = ref<any[]>([]);
  const statsGroups = ref<any[]>([]);
  const curStatsGroup = ref('');
  const groupStats = ref<any[]>([]);
  const profVisible = ref(false);
  const profDetail = ref('');

  const summarize = (d: any) => {
    if (!d) return '';
    if (typeof d === 'string') return d.slice(0, 100);
    return Object.entries(d)
      .slice(0, 4)
      .map(([k, v]) => `${k}: ${String(v).slice(0, 30)}`)
      .join(' ｜ ');
  };

  const loadFav = async () => {
    setFavLoading(true);
    try {
      const res = await getFavList({ limit: 500, group: favGroup.value || undefined });
      favItems.value = (res.data.items || []).map((it: any) => ({
        ...it,
        _edit: it.fav,
      }));
    } finally {
      setFavLoading(false);
    }
  };

  const saveFav = async (row: any) => {
    await setFav(row.key, row._edit);
    Message.success(`${row.key} 好感度已更新`);
    await loadFav();
  };

  const removeFav = async (key: string) => {
    await deleteFav(key);
    Message.success('已删除');
    await loadFav();
  };

  const loadProfiles = async () => {
    setProfLoading(true);
    try {
      const res = await getProfiles({ limit: 500 });
      profiles.value = res.data.items || [];
    } finally {
      setProfLoading(false);
    }
  };

  const viewProfile = (row: any) => {
    profDetail.value =
      typeof row.data === 'string' ? row.data : JSON.stringify(row.data, null, 2);
    profVisible.value = true;
  };

  const loadStatsGroups = async () => {
    setStatsLoading(true);
    try {
      const res = await getStatsGroups();
      statsGroups.value = res.data.groups || res.data.items || [];
    } finally {
      setStatsLoading(false);
    }
  };

  const selectStatsGroup = async (g: string) => {
    curStatsGroup.value = g;
    const res = await getGroupStats(g);
    groupStats.value = res.data.days || res.data.items || [];
  };

  onMounted(async () => {
    await Promise.all([loadFav(), loadProfiles(), loadStatsGroups()]);
  });
</script>

<script lang="ts">
  export default { name: 'SocialPage' };
</script>

<style lang="less" scoped>
  .container {
    padding: 0 20px 20px;
  }
  .layout {
    margin-top: 16px;
  }
  .pick-item {
    padding: 7px 10px;
    border-radius: 6px;
    cursor: pointer;
    font-size: 13px;
    &:hover {
      background: var(--color-fill-2);
    }
    &.active {
      background: var(--color-primary-light-1);
    }
  }
  .pick-meta {
    color: var(--color-text-3);
    font-size: 12px;
    margin-left: 6px;
  }
  .doc-pre {
    font-size: 13px;
    white-space: pre-wrap;
    word-break: break-all;
  }
</style>

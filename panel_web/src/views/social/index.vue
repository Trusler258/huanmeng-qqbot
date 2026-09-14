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

        <!-- 幸运值 -->
        <a-tab-pane key="luck" title="幸运值">
          <a-card class="general-card" title="设置幸运值" style="margin-bottom: 12px">
            <a-space wrap>
              <span class="luck-label">QQ</span>
              <a-input v-model="luckForm.qq" placeholder="QQ 号" style="width: 150px" />
              <span class="luck-label">幸运值</span>
              <a-input v-model="luckForm.value" placeholder="数值或文本" style="width: 120px" />
              <span class="luck-label">日期</span>
              <a-input v-model="luckForm.date" :placeholder="luckToday || '今天（留空）'" style="width: 150px" allow-clear />
              <a-button type="primary" :loading="luckSaving" @click="saveLuck">保存</a-button>
            </a-space>
            <div class="luck-tip">
              数据存于 data/luck.json（按日期分层）。留空日期 = 改今天。改完 bot 侧 /~luck 立刻能查到。
            </div>
          </a-card>

          <a-table
            v-for="d in luckDays"
            :key="d.date"
            :data="d.rows"
            :loading="luckLoading"
            :pagination="{ pageSize: 10, hideOnSinglePage: true }"
            size="small"
            style="margin-bottom: 12px"
          >
            <template #title>
              <span :class="{ 'luck-today': d.is_today }">
                {{ d.date }}{{ d.is_today ? '（今天）' : '' }}
              </span>
              <span class="luck-count">{{ d.count }} 人</span>
            </template>
            <template #columns>
              <a-table-column title="QQ" data-index="qq" :width="150" />
              <a-table-column title="幸运值" data-index="value" />
              <a-table-column title="操作" :width="140">
                <template #cell="{ record }">
                  <a-space size="mini">
                    <a-button size="mini" type="text" @click="fillLuck(d.date, record)">填入表单</a-button>
                    <a-popconfirm content="删除这条记录？" @ok="delLuck(d.date, record.qq)">
                      <a-button size="mini" type="text" status="danger">删</a-button>
                    </a-popconfirm>
                  </a-space>
                </template>
              </a-table-column>
            </template>
          </a-table>
          <a-empty v-if="!luckDays.length" description="还没有幸运值记录" />
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
    getLuckList,
    setLuck,
    deleteLuck,
  } from '@/api/panelC';

  const { loading: favLoading, setLoading: setFavLoading } = useLoading();
  const { loading: profLoading, setLoading: setProfLoading } = useLoading();
  const { loading: statsLoading, setLoading: setStatsLoading } = useLoading();
  const { loading: luckLoading, setLoading: setLuckLoading } = useLoading();
  const { loading: luckSaving, setLoading: setLuckSaving } = useLoading();

  const favItems = ref<any[]>([]);
  const favGroup = ref('');
  const profiles = ref<any[]>([]);
  const statsGroups = ref<any[]>([]);
  const curStatsGroup = ref('');
  const groupStats = ref<any[]>([]);
  const profVisible = ref(false);
  const profDetail = ref('');

  // ── 幸运值 ──
  const luckDays = ref<any[]>([]);
  const luckToday = ref('');
  const luckForm = ref({ qq: '', value: '', date: '' });

  const loadLuck = async () => {
    setLuckLoading(true);
    try {
      const res = await getLuckList();
      luckDays.value = res.data.days || [];
      luckToday.value = res.data.today || '';
    } finally {
      setLuckLoading(false);
    }
  };

  const saveLuck = async () => {
    if (!luckForm.value.qq.trim()) {
      Message.warning('先填 QQ 号');
      return;
    }
    if (!luckForm.value.value.trim()) {
      Message.warning('先填幸运值');
      return;
    }
    setLuckSaving(true);
    try {
      await setLuck(luckForm.value.qq.trim(), luckForm.value.value.trim(), luckForm.value.date.trim());
      Message.success('已保存');
      await loadLuck();
    } finally {
      setLuckSaving(false);
    }
  };

  const fillLuck = (date: string, row: any) => {
    luckForm.value = { qq: row.qq, value: String(row.value), date };
  };

  const delLuck = async (date: string, qq: string) => {
    await deleteLuck(qq, date);
    Message.success('已删除');
    await loadLuck();
  };

  /**
   * AI 助手联动：助手要"改某人某天的幸运值"时派发此事件预填表单。
   * detail = {qq, value?, date?}
   */
  const onAiFillLuck = (e: Event) => {
    const d = (e as CustomEvent).detail || {};
    if (!d.qq) return;
    luckForm.value = {
      qq: String(d.qq),
      value: d.value === undefined || d.value === null ? '' : String(d.value),
      date: d.date ? String(d.date) : '',
    };
  };

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
    await Promise.all([loadFav(), loadProfiles(), loadStatsGroups(), loadLuck()]);
    // AI 助手联动：预填幸运值表单（助手派发 ai-fill-luck 事件）
    window.addEventListener('ai-fill-luck', onAiFillLuck);
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

<template>
  <div class="container">
    <Breadcrumb :items="['menu.dashboard', 'menu.dashboard.overview']" />
    <div class="layout">
      <a-space :size="16" direction="vertical" fill>
        <!-- 服务状态 -->
        <a-card class="general-card" :title="$t('overview.bot.title')" :loading="loading">
          <a-grid :cols="4" :col-gap="16" :row-gap="16">
            <a-grid-item>
              <stat-cell
                :label="$t('overview.bot.status')"
                :text="overview?.bot?.alive ? $t('overview.bot.running') : $t('overview.bot.down')"
                :color="overview?.bot?.alive ? 'var(--color-success-6)' : 'var(--color-danger-6)'"
              />
            </a-grid-item>
            <a-grid-item>
              <stat-cell :label="$t('overview.bot.version')" :text="overview?.version ?? '-'" />
            </a-grid-item>
            <a-grid-item>
              <stat-cell :label="$t('overview.bot.uptime')" :text="panelUptime" />
            </a-grid-item>
            <a-grid-item>
              <stat-cell
                :label="$t('overview.bot.todayMsgs')"
                :text="`${overview?.today?.messages ?? '-'} / ${overview?.today?.active_users ?? 0} 人`"
              />
            </a-grid-item>
          </a-grid>
          <a-space :size="8" wrap style="margin-top: 16px">
            <a-tag
              v-for="(state, svc) in overview?.services || {}"
              :key="svc"
              :color="state === 'active' ? 'green' : state === 'unknown' ? 'gray' : 'red'"
              size="medium"
            >
              {{ svc }}: {{ state }}
            </a-tag>
          </a-space>
        </a-card>

        <!-- 趋势图 -->
        <a-card class="general-card" :title="$t('overview.trend.title')" :loading="trendLoading">
          <template #extra>
            <a-radio-group v-model="trendDays" type="button" size="small" @change="loadTrend">
              <a-radio :value="7">7 {{ $t('overview.trend.days') }}</a-radio>
              <a-radio :value="14">14 {{ $t('overview.trend.days') }}</a-radio>
              <a-radio :value="30">30 {{ $t('overview.trend.days') }}</a-radio>
            </a-radio-group>
          </template>
          <div ref="trendChartEl" class="trend-chart" />
        </a-card>

        <!-- 数据规模 + 系统 -->
        <a-grid :cols="2" :col-gap="16" :row-gap="16">
          <a-grid-item>
            <a-card class="general-card" :title="$t('overview.data.title')" :loading="loading">
              <a-grid :cols="2" :col-gap="16" :row-gap="12">
                <a-grid-item>
                  <stat-cell :label="$t('overview.data.groups')" :text="num(overview?.totals?.groups_tracked)" />
                </a-grid-item>
                <a-grid-item>
                  <stat-cell :label="$t('overview.data.fav')" :text="num(overview?.totals?.fav_entries)" />
                </a-grid-item>
                <a-grid-item>
                  <stat-cell :label="$t('overview.data.profiles')" :text="num(overview?.totals?.profiles)" />
                </a-grid-item>
                <a-grid-item>
                  <stat-cell :label="$t('overview.data.notes')" :text="num(overview?.totals?.notes_files)" />
                </a-grid-item>
                <a-grid-item>
                  <stat-cell :label="$t('overview.data.dataSize')" :text="fmtSize(overview?.totals?.data_size)" />
                </a-grid-item>
                <a-grid-item>
                  <stat-cell :label="$t('overview.data.dbSize')" :text="fmtSize(overview?.totals?.db_size)" />
                </a-grid-item>
              </a-grid>
            </a-card>
          </a-grid-item>
          <a-grid-item>
            <a-card class="general-card" :title="$t('overview.sys.title')" :loading="loading">
              <a-grid :cols="2" :col-gap="16" :row-gap="12">
                <a-grid-item>
                  <stat-cell :label="$t('overview.sys.mem')" :text="memText" />
                </a-grid-item>
                <a-grid-item>
                  <stat-cell :label="$t('overview.sys.cpu')" :text="`${overview?.system?.cpu_count ?? '-'} 核`" />
                </a-grid-item>
                <a-grid-item>
                  <stat-cell :label="$t('overview.sys.load')" :text="loadText" />
                </a-grid-item>
                <a-grid-item>
                  <stat-cell :label="$t('overview.sys.serverTime')" :text="overview?.server_time ?? '-'" />
                </a-grid-item>
              </a-grid>
            </a-card>
          </a-grid-item>
        </a-grid>
      </a-space>
    </div>
  </div>
</template>

<script lang="ts" setup>
  import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue';
  import * as echarts from 'echarts';
  import Breadcrumb from '@/components/breadcrumb/index.vue';
  import useLoading from '@/hooks/loading';
  import StatCell from '@/components/stat-cell/index.vue';
  import { getOverview, getTrend, type OverviewData } from '@/api/dashboard';

  const { loading, setLoading } = useLoading();
  const { loading: trendLoading, setLoading: setTrendLoading } = useLoading();

  const overview = ref<OverviewData | null>(null);
  const trendDays = ref(14);
  const trendChartEl = ref<HTMLElement>();
  let chart: echarts.ECharts | null = null;
  let timer: number | undefined;

  // 数字兜底：null/undefined 显示 '-'
  const num = (v?: number | null) => (v === null || v === undefined ? '-' : String(v));

  const fmtSize = (bytes?: number) => {
    if (bytes === null || bytes === undefined) return '-';
    if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
    if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
    if (bytes >= 1024) return `${(bytes / 1024).toFixed(0)} KB`;
    return `${bytes} B`;
  };

  const fmtUptime = (sec?: number) => {
    if (sec === null || sec === undefined) return '-';
    const d = Math.floor(sec / 86400);
    const h = Math.floor((sec % 86400) / 3600);
    const m = Math.floor((sec % 3600) / 60);
    if (d > 0) return `${d}天${h}时`;
    if (h > 0) return `${h}时${m}分`;
    return `${m}分`;
  };

  const panelUptime = computed(() => fmtUptime(overview.value?.uptime_seconds));
  const memText = computed(() => {
    const s = overview.value?.system;
    if (!s?.mem_total_kb) return '-';
    const usedGb = ((s.mem_total_kb - s.mem_available_kb) / 1024 ** 2).toFixed(1);
    return `${usedGb} / ${(s.mem_total_kb / 1024 ** 2).toFixed(1)} GB`;
  });
  const loadText = computed(() => {
    const l = overview.value?.system?.load_avg;
    return Array.isArray(l) ? l.join(' / ') : '-';
  });

  const renderTrend = (data: { date: string; messages: number; active_users: number }[]) => {
    if (!trendChartEl.value) return;
    if (!chart) {
      chart = echarts.init(trendChartEl.value);
    }
    chart.setOption({
      tooltip: { trigger: 'axis' },
      legend: { data: ['消息量', '活跃用户'], bottom: 0 },
      grid: { left: 48, right: 24, top: 24, bottom: 48 },
      xAxis: { type: 'category', data: data.map((d) => d.date.slice(5)) },
      yAxis: [
        { type: 'value', name: '消息' },
        { type: 'value', name: '用户' },
      ],
      series: [
        {
          name: '消息量',
          type: 'line',
          smooth: true,
          showSymbol: false,
          data: data.map((d) => d.messages),
          areaStyle: { opacity: 0.12 },
        },
        {
          name: '活跃用户',
          type: 'line',
          smooth: true,
          showSymbol: false,
          yAxisIndex: 1,
          data: data.map((d) => d.active_users),
        },
      ],
    });
  };

  const loadTrend = async () => {
    setTrendLoading(true);
    try {
      const res = await getTrend(trendDays.value);
      // 先关 loading 再渲染：a-card 的 loading 是 skeleton 替换内容区，
      // loading=true 时 trendChartEl 不在 DOM，echarts.init 拿不到容器
      setTrendLoading(false);
      await nextTick();
      renderTrend(res.data.data || []);
    } finally {
      setTrendLoading(false);
    }
  };

  const loadAll = async () => {
    setLoading(true);
    try {
      const res = await getOverview();
      overview.value = res.data;
    } finally {
      setLoading(false);
    }
  };

  const onResize = () => chart?.resize();

  onMounted(async () => {
    await loadAll();
    await loadTrend();
    window.addEventListener('resize', onResize);
    timer = window.setInterval(loadAll, 30_000);
  });
  onUnmounted(() => {
    window.removeEventListener('resize', onResize);
    if (timer) window.clearInterval(timer);
    chart?.dispose();
    chart = null;
  });
</script>

<script lang="ts">
  export default { name: 'Overview' };
</script>

<style lang="less" scoped>
  .container {
    padding: 0 20px 20px;
  }
  .layout {
    margin-top: 16px;
  }
  .trend-chart {
    width: 100%;
    height: 320px;
  }
</style>

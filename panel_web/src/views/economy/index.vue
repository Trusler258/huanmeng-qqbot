<template>
  <div class="container">
    <Breadcrumb :items="['menu.economy', 'menu.economy.overview']" />
    <div class="layout">
      <a-space :size="16" direction="vertical" fill>
        <a-card class="general-card" title="经济概览" :loading="loading">
          <a-grid :cols="4" :col-gap="16" :row-gap="12">
            <a-grid-item><stat-cell label="用户数" :text="String(data?.user_count ?? '-')" /></a-grid-item>
            <a-grid-item><stat-cell label="积分总量" :text="num(data?.total_points)" /></a-grid-item>
            <a-grid-item><stat-cell label="物品总量" :text="num(data?.total_items)" /></a-grid-item>
            <a-grid-item><stat-cell label="数据文件" :text="data?.file || '-'" /></a-grid-item>
          </a-grid>
        </a-card>

        <a-card class="general-card" title="用户经济数据">
          <template #extra>
            <a-input-search
              v-model="filterUid"
              placeholder="按 UID 过滤"
              style="width: 200px"
              size="small"
            />
          </template>
          <a-table
            :data="filteredRows"
            :pagination="{ pageSize: 15 }"
            size="small"
            :scroll="{ x: 700 }"
          >
            <template #columns>
              <a-table-column title="UID" data-index="uid" :width="130" />
              <a-table-column title="积分" :width="100">
                <template #cell="{ record }">{{ record.points }}</template>
              </a-table-column>
              <a-table-column title="物品" :width="220">
                <template #cell="{ record }">{{ itemsText(record.items) }}</template>
              </a-table-column>
              <a-table-column title="签到天数" data-index="sign_in_days" :width="100" />
              <a-table-column title="上次签到" data-index="last_sign" :width="120" />
              <a-table-column title="操作" :width="150">
                <template #cell="{ record }">
                  <a-space size="mini">
                    <a-button size="mini" type="outline" @click="openAdjust(record)">积分</a-button>
                    <a-button size="mini" type="outline" @click="openGrant(record)">物品</a-button>
                  </a-space>
                </template>
              </a-table-column>
            </template>
          </a-table>
        </a-card>
      </a-space>
    </div>

    <!-- 积分调整 -->
    <a-modal v-model:visible="adjustVisible" title="调整积分" @ok="doAdjust" @cancel="adjustVisible = false">
      <a-space direction="vertical" fill style="width: 100%">
        <span>用户：{{ adjustRow?.uid }}（当前 {{ adjustRow?.points }}）</span>
        <a-input-number v-model="adjustDelta" placeholder="正数加 / 负数减" style="width: 100%" />
        <a-input v-model="adjustReason" placeholder="原因（记入审计）" />
      </a-space>
    </a-modal>

    <!-- 物品发放 -->
    <a-modal v-model:visible="grantVisible" title="发放/扣除物品" @ok="doGrant" @cancel="grantVisible = false">
      <a-space direction="vertical" fill style="width: 100%">
        <span>用户：{{ grantRow?.uid }}</span>
        <a-input v-model="grantItemName" placeholder="物品名" />
        <a-input-number v-model="grantCount" placeholder="数量（负数扣除）" style="width: 100%" />
      </a-space>
    </a-modal>
  </div>
</template>

<script lang="ts" setup>
  import { ref, computed, onMounted } from 'vue';
  import { Message } from '@arco-design/web-vue';
  import Breadcrumb from '@/components/breadcrumb/index.vue';
  import StatCell from '@/components/stat-cell/index.vue';
  import useLoading from '@/hooks/loading';
  import { getEconomy, adjustPoints, grantItem } from '@/api/panelC';

  const { loading, setLoading } = useLoading();

  const data = ref<any>(null);
  const rows = ref<any[]>([]);
  const filterUid = ref('');

  const adjustVisible = ref(false);
  const adjustRow = ref<any>(null);
  const adjustDelta = ref(0);
  const adjustReason = ref('');

  const grantVisible = ref(false);
  const grantRow = ref<any>(null);
  const grantItemName = ref('');
  const grantCount = ref(1);

  const num = (v?: number) => (v === null || v === undefined ? '-' : String(v));
  const itemsText = (items: any) => {
    if (!items || typeof items !== 'object') return '-';
    const entries = Object.entries(items);
    if (!entries.length) return '-';
    return entries.slice(0, 5).map(([k, v]) => `${k}×${v}`).join(' ');
  };

  const filteredRows = computed(() =>
    filterUid.value
      ? rows.value.filter((r) => r.uid.includes(filterUid.value))
      : rows.value
  );

  const load = async () => {
    setLoading(true);
    try {
      const res = await getEconomy();
      data.value = res.data;
      rows.value = res.data.items || [];
    } finally {
      setLoading(false);
    }
  };

  const openAdjust = (row: any) => {
    adjustRow.value = row;
    adjustDelta.value = 0;
    adjustReason.value = '';
    adjustVisible.value = true;
  };
  const doAdjust = async () => {
    if (!adjustRow.value || !adjustDelta.value) return;
    await adjustPoints(adjustRow.value.uid, adjustDelta.value, adjustReason.value);
    Message.success('积分已调整');
    adjustVisible.value = false;
    await load();
  };

  const openGrant = (row: any) => {
    grantRow.value = row;
    grantItemName.value = '';
    grantCount.value = 1;
    grantVisible.value = true;
  };
  const doGrant = async () => {
    if (!grantRow.value || !grantItemName.value.trim()) return;
    await grantItem(grantRow.value.uid, grantItemName.value.trim(), grantCount.value);
    Message.success('物品已发放');
    grantVisible.value = false;
    await load();
  };

  onMounted(load);
</script>

<script lang="ts">
  export default { name: 'EconomyPage' };
</script>

<style lang="less" scoped>
  .container {
    padding: 0 20px 20px;
  }
  .layout {
    margin-top: 16px;
  }
</style>

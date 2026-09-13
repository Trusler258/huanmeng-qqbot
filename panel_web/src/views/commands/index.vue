<template>
  <div class="container">
    <Breadcrumb :items="['menu.commands', 'menu.commands.list']" />
    <div class="layout">
      <a-space :size="16" direction="vertical" fill>
        <a-card class="general-card" title="指令清单">
          <template #extra>
            <a-input-search
              v-model="query"
              placeholder="搜索指令名"
              style="width: 220px"
              size="small"
              @search="load"
              @clear="load"
            />
          </template>
          <a-space wrap style="margin-bottom: 10px">
            <a-tag v-for="(n, cat) in data?.categories || {}" :key="cat" size="medium">
              {{ cat }}: {{ n }}
            </a-tag>
          </a-space>
          <a-table
            :data="items"
            :loading="loading"
            :pagination="{ pageSize: 20 }"
            size="small"
          >
            <template #columns>
              <a-table-column title="指令" data-index="name" :width="140">
                <template #cell="{ record }">
                  <span class="cmd-name">/~{{ record.name || record.key || record.command }}</span>
                </template>
              </a-table-column>
              <a-table-column title="类别" :width="110">
                <template #cell="{ record }">{{ record.category || '-' }}</template>
              </a-table-column>
              <a-table-column title="描述">
                <template #cell="{ record }">
                  <span class="cell-text">{{ record.desc || record.description || '-' }}</span>
                </template>
              </a-table-column>
            </template>
          </a-table>
        </a-card>

        <a-card class="general-card" title="LLM 可见性审计（会但没说的指令）">
          <template #extra>
            <a-button size="small" @click="loadAudit" :loading="auditLoading">重新审计</a-button>
          </template>
          <a-alert
            v-if="audit?.error"
            type="error"
            style="margin-bottom: 10px"
          >{{ audit.error }}</a-alert>
          <a-grid :cols="3" :col-gap="16">
            <a-grid-item>
              <div class="audit-box">
                <div class="audit-title danger">
                  LLM 不知道的指令（{{ audit?.missing_in_llm?.length ?? 0 }}）
                </div>
                <div class="audit-list">
                  <a-tag v-for="c in audit?.missing_in_llm || []" :key="c" size="small" color="red" class="audit-tag">/~{{ c }}</a-tag>
                  <span v-if="!audit?.missing_in_llm?.length" class="audit-none">无 —— 全部已注入</span>
                </div>
              </div>
            </a-grid-item>
            <a-grid-item>
              <div class="audit-box">
                <div class="audit-title">提示词里多写的（{{ audit?.extra_in_llm?.length ?? 0 }}）</div>
                <div class="audit-list">
                  <a-tag v-for="c in audit?.extra_in_llm || []" :key="c" size="small" color="orange" class="audit-tag">/~{{ c }}</a-tag>
                  <span v-if="!audit?.extra_in_llm?.length" class="audit-none">无 —— 无幻觉指令</span>
                </div>
              </div>
            </a-grid-item>
            <a-grid-item>
              <div class="audit-box">
                <div class="audit-title">统计</div>
                <div class="audit-list">
                  <div>注册指令：{{ audit?.registered?.length ?? '-' }}</div>
                  <div>已注入 LLM：{{ audit?.in_llm_prompt?.length ?? '-' }}</div>
                  <div>缺失：{{ audit?.missing_in_llm?.length ?? '-' }}</div>
                  <div>多余：{{ audit?.extra_in_llm?.length ?? '-' }}</div>
                </div>
              </div>
            </a-grid-item>
          </a-grid>
        </a-card>
      </a-space>
    </div>
  </div>
</template>

<script lang="ts" setup>
  import { ref, onMounted } from 'vue';
  import Breadcrumb from '@/components/breadcrumb/index.vue';
  import useLoading from '@/hooks/loading';
  import { getCommands, getLlmAudit } from '@/api/panelC';

  const { loading, setLoading } = useLoading();
  const { loading: auditLoading, setLoading: setAuditLoading } = useLoading();

  const data = ref<any>(null);
  const items = ref<any[]>([]);
  const query = ref('');
  const audit = ref<any>(null);

  const load = async () => {
    setLoading(true);
    try {
      const res = await getCommands({ q: query.value || undefined, limit: 500 });
      data.value = res.data;
      items.value = res.data.items || [];
    } finally {
      setLoading(false);
    }
  };

  const loadAudit = async () => {
    setAuditLoading(true);
    try {
      const res = await getLlmAudit();
      audit.value = res.data;
    } finally {
      setAuditLoading(false);
    }
  };

  onMounted(async () => {
    await Promise.all([load(), loadAudit()]);
  });
</script>

<script lang="ts">
  export default { name: 'CommandsPage' };
</script>

<style lang="less" scoped>
  .container {
    padding: 0 20px 20px;
  }
  .layout {
    margin-top: 16px;
  }
  .cmd-name {
    font-family: 'JetBrains Mono', Consolas, monospace;
    font-size: 13px;
  }
  .cell-text {
    font-size: 12px;
    word-break: break-all;
  }
  .audit-box {
    border: 1px solid var(--color-border-2);
    border-radius: 8px;
    padding: 12px;
    min-height: 140px;
  }
  .audit-title {
    font-weight: 600;
    font-size: 13px;
    margin-bottom: 8px;
    &.danger {
      color: var(--color-danger-6);
    }
  }
  .audit-list {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    font-size: 12px;
    color: var(--color-text-2);
  }
  .audit-tag {
    font-family: monospace;
  }
  .audit-none {
    color: var(--color-success-6);
  }
</style>

<template>
  <div class="container">
    <Breadcrumb :items="['menu.messages', 'menu.messages.search']" />
    <div class="layout">
      <a-space :size="16" direction="vertical" fill>
        <!-- ══ 数据库概况 ══ -->
        <a-card class="general-card" title="消息数据库" :loading="statsLoading">
          <a-grid :cols="4" :col-gap="16" :row-gap="12">
            <a-grid-item>
              <stat-cell label="总消息" :text="num(stats?.total)" />
            </a-grid-item>
            <a-grid-item>
              <stat-cell label="会话数" :text="num(stats?.chats)" />
            </a-grid-item>
            <a-grid-item>
              <stat-cell label="最早消息" :text="stats?.oldest || '-'" />
            </a-grid-item>
            <a-grid-item>
              <stat-cell label="最新消息" :text="stats?.newest || '-'" />
            </a-grid-item>
          </a-grid>
        </a-card>

        <!-- ══ 全文检索 ══ -->
        <a-card class="general-card" title="全文检索">
          <a-input-search
            v-model="query"
            placeholder="搜索历史消息（少于 3 字自动退化为 LIKE 查询）"
            search-button
            :loading="searching"
            style="max-width: 560px"
            @search="doSearch"
            @clear="doSearch"
          />
          <a-space style="margin-left: 12px" />
          <a-table
            :data="results"
            :loading="searching"
            :pagination="pagination"
            size="small"
            style="margin-top: 12px"
            @page-change="onPage"
          >
            <template #columns>
              <a-table-column title="时间" :width="160">
                <template #cell="{ record }">{{ fmtTs(record.ts) }}</template>
              </a-table-column>
              <a-table-column title="会话" :width="140">
                <template #cell="{ record }">{{ record.chat_id }}</template>
              </a-table-column>
              <a-table-column title="用户" :width="130">
                <template #cell="{ record }">{{ record.name || record.user_id }}</template>
              </a-table-column>
              <a-table-column title="内容">
                <template #cell="{ record }">
                  <span class="msg-content">{{ record.content }}</span>
                </template>
              </a-table-column>
            </template>
            <template #empty>
              <a-empty :description="query ? '没有匹配的消息' : '输入关键词搜索'" />
            </template>
          </a-table>
        </a-card>

        <!-- ══ msglog：bot 发出的消息 ══ -->
        <a-card class="general-card" title="Bot 发送记录（msglog）">
          <a-space wrap style="margin-bottom: 12px">
            <a-select
              v-model="msglogChat"
              :options="msglogFiles"
              placeholder="选择会话"
              style="width: 280px"
              allow-clear
              @change="loadMsglog"
            />
            <a-input-search
              v-model="msglogQ"
              placeholder="过滤关键词"
              style="width: 220px"
              @search="loadMsglog"
              @clear="loadMsglog"
            />
          </a-space>
          <a-table
            :data="msglogRows"
            :loading="msglogLoading"
            :pagination="{ pageSize: 15 }"
            size="small"
          >
            <template #columns>
              <a-table-column title="时间" :width="160">
                <template #cell="{ record }">{{ fmtTs(record.ts) }}</template>
              </a-table-column>
              <a-table-column title="内容">
                <template #cell="{ record }">
                  <span class="msg-content">{{ record.content || record.text }}</span>
                </template>
              </a-table-column>
            </template>
            <template #empty><a-empty description="选择会话查看" /></template>
          </a-table>
        </a-card>
      </a-space>
    </div>
  </div>
</template>

<script lang="ts" setup>
  import { ref, reactive, onMounted } from 'vue';
  import Breadcrumb from '@/components/breadcrumb/index.vue';
  import useLoading from '@/hooks/loading';
  import StatCell from '@/components/stat-cell/index.vue';
  import {
    searchMessages,
    getMsgStats,
    getMsglogFiles,
    getMsglog,
  } from '@/api/panel';

  const { loading: statsLoading, setLoading: setStatsLoading } = useLoading();
  const { loading: searching, setLoading: setSearching } = useLoading();
  const { loading: msglogLoading, setLoading: setMsglogLoading } = useLoading();

  const stats = ref<Record<string, any> | null>(null);
  const query = ref('');
  const results = ref<Record<string, any>[]>([]);
  const pagination = reactive({ total: 0, pageSize: 20, current: 1 });

  const msglogFiles = ref<{ label: string; value: string }[]>([]);
  const msglogChat = ref('');
  const msglogQ = ref('');
  const msglogRows = ref<Record<string, any>[]>([]);

  const num = (v?: number | null) => (v === null || v === undefined ? '-' : String(v));
  const fmtTs = (ts: number) => {
    const d = new Date(ts * 1000);
    return isNaN(d.getTime()) ? String(ts) : d.toLocaleString('zh-CN');
  };

  const loadStats = async () => {
    setStatsLoading(true);
    try {
      const res = await getMsgStats();
      stats.value = res.data;
    } finally {
      setStatsLoading(false);
    }
  };

  const doSearch = async () => {
    setSearching(true);
    try {
      const res = await searchMessages({
        q: query.value,
        limit: pagination.pageSize,
        offset: (pagination.current - 1) * pagination.pageSize,
      });
      results.value = res.data.rows || res.data.items || [];
      pagination.total = res.data.total || results.value.length;
    } catch {
      results.value = [];
    } finally {
      setSearching(false);
    }
  };

  const onPage = (page: number) => {
    pagination.current = page;
    doSearch();
  };

  const loadMsglogFiles = async () => {
    try {
      const res = await getMsglogFiles();
      const files: any[] = res.data.files || [];
      msglogFiles.value = files.map((f) => ({
        label: f.chat_id || f.name || f,
        value: f.chat_id || f.name || f,
      }));
    } catch {
      // ignore
    }
  };

  const loadMsglog = async () => {
    if (!msglogChat.value) {
      msglogRows.value = [];
      return;
    }
    setMsglogLoading(true);
    try {
      const res = await getMsglog(msglogChat.value, {
        limit: 200,
        q: msglogQ.value || undefined,
      });
      msglogRows.value = res.data.rows || res.data.items || [];
    } catch {
      msglogRows.value = [];
    } finally {
      setMsglogLoading(false);
    }
  };

  onMounted(async () => {
    await Promise.all([loadStats(), loadMsglogFiles()]);
  });
</script>

<script lang="ts">
  export default { name: 'MessagesSearch' };
</script>

<style lang="less" scoped>
  .container {
    padding: 0 20px 20px;
  }
  .layout {
    margin-top: 16px;
  }
  .msg-content {
    word-break: break-all;
    white-space: pre-wrap;
  }
</style>

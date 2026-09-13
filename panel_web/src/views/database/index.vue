<template>
  <div class="container">
    <Breadcrumb :items="['menu.database', 'menu.database.browse']" />
    <div class="layout">
      <a-grid :cols="24" :col-gap="16">
        <!-- 左：库与表 -->
        <a-grid-item :span="6">
          <a-card class="general-card" title="数据库" :loading="dbLoading">
            <div v-for="db in databases" :key="db.key" class="db-block">
              <div class="db-item" :class="{ active: db.key === curDb }" @click="selectDb(db.key)">
                <div class="db-name">{{ db.label }}</div>
                <div class="db-meta">{{ fmtSize(db.size) }} · {{ db.tables.length }} 表</div>
              </div>
              <div v-if="db.key === curDb" class="table-list">
                <div
                  v-for="t in db.tables"
                  :key="t.name"
                  class="table-item"
                  :class="{ active: t.name === curTable }"
                  @click="selectTable(t.name)"
                >
                  {{ t.name }}
                  <span class="table-rows">{{ t.rows }}</span>
                </div>
              </div>
            </div>
            <a-empty v-if="!databases.length" description="无数据库" />
          </a-card>
        </a-grid-item>

        <!-- 右：表数据 -->
        <a-grid-item :span="18">
          <a-card class="general-card" :title="curTable ? `${curDb}.${curTable}` : '选择表'">
            <template #extra>
              <a-space>
                <a-input-search
                  v-model="searchQ"
                  placeholder="表内检索"
                  style="width: 200px"
                  size="small"
                  @search="doSearch"
                  @clear="loadRows"
                />
                <a-button size="small" @click="sqlVisible = true">执行 SQL</a-button>
                <a-popconfirm v-if="curTable" content="重建该表的 FTS 索引？" @ok="doReindex">
                  <a-button size="small">重建索引</a-button>
                </a-popconfirm>
                <a-popconfirm v-if="curDb" content="整理数据库（VACUUM 回收空间）？" @ok="doOptimize">
                  <a-button size="small">整理</a-button>
                </a-popconfirm>
              </a-space>
            </template>

            <a-table
              :data="rows"
              :loading="rowsLoading"
              :pagination="{
                total,
                pageSize,
                current: page + 1,
                showTotal: true,
                showPageSize: true,
                pageSizeOptions: [20, 50, 100],
              }"
              size="small"
              :scroll="{ x: 800 }"
              @page-change="onPage"
              @page-size-change="onPageSize"
            >
              <template #columns>
                <a-table-column v-for="col in columns" :key="col" :title="col" :data-index="col" ellipsis>
                  <template #cell="{ record }">
                    <span class="cell-text">{{ fmtCell(record[col]) }}</span>
                  </template>
                </a-table-column>
              </template>
              <template #empty><a-empty description="选择左侧表查看数据" /></template>
            </a-table>
          </a-card>
        </a-grid-item>
      </a-grid>
    </div>

    <!-- SQL 执行抽屉 -->
    <a-drawer v-model:visible="sqlVisible" title="执行 SQL（危险操作）" :width="560" unmount-on-close>
      <a-alert type="warning" style="margin-bottom: 12px">
        仅允许 SELECT / UPDATE / DELETE；写操作前自动备份，可在操作栈回滚。
      </a-alert>
      <a-textarea v-model="sql" placeholder="例如：DELETE FROM stats WHERE date &lt; '20260101'" :auto-size="{ minRows: 6, maxRows: 12 }" />
      <a-button type="primary" status="danger" style="margin-top: 12px" :loading="sqlLoading" @click="doExecSql">
        执行
      </a-button>
      <pre v-if="sqlResult" class="sql-result">{{ sqlResult }}</pre>
    </a-drawer>
  </div>
</template>

<script lang="ts" setup>
  import { ref, onMounted } from 'vue';
  import { Message } from '@arco-design/web-vue';
  import Breadcrumb from '@/components/breadcrumb/index.vue';
  import useLoading from '@/hooks/loading';
  import {
    getDbList,
    getTableRows,
    searchTable,
    execSql,
    reindexTable,
    optimizeDb,
    type DbInfo,
  } from '@/api/panelB';

  const { loading: dbLoading, setLoading: setDbLoading } = useLoading();
  const { loading: rowsLoading, setLoading: setRowsLoading } = useLoading();
  const { loading: sqlLoading, setLoading: setSqlLoading } = useLoading();

  const databases = ref<DbInfo[]>([]);
  const curDb = ref('');
  const curTable = ref('');
  const columns = ref<string[]>([]);
  const rows = ref<any[]>([]);
  const total = ref(0);
  const page = ref(0);
  const pageSize = ref(20);
  const searchQ = ref('');
  const sqlVisible = ref(false);
  const sql = ref('');
  const sqlResult = ref('');

  const fmtSize = (bytes: number) => {
    if (!bytes) return '0 B';
    if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
    if (bytes >= 1024) return `${(bytes / 1024).toFixed(0)} KB`;
    return `${bytes} B`;
  };
  const fmtCell = (v: any) => {
    if (v === null || v === undefined) return '';
    if (typeof v === 'object') return JSON.stringify(v);
    const s = String(v);
    return s.length > 120 ? `${s.slice(0, 120)}...` : s;
  };

  const loadDbs = async () => {
    setDbLoading(true);
    try {
      const res = await getDbList();
      databases.value = res.data.databases || [];
      if (!curDb.value && databases.value.length) {
        await selectDb(databases.value[0].key);
      }
    } finally {
      setDbLoading(false);
    }
  };

  const selectDb = async (key: string) => {
    curDb.value = key;
    curTable.value = '';
    rows.value = [];
    columns.value = [];
    const db = databases.value.find((d) => d.key === key);
    if (db?.tables.length) {
      await selectTable(db.tables[0].name);
    }
  };

  const selectTable = async (table: string) => {
    curTable.value = table;
    page.value = 0;
    searchQ.value = '';
    await loadRows();
  };

  const loadRows = async () => {
    if (!curDb.value || !curTable.value) return;
    setRowsLoading(true);
    try {
      const res = await getTableRows(curDb.value, curTable.value, {
        limit: pageSize.value,
        offset: page.value * pageSize.value,
      });
      columns.value = res.data.columns || [];
      rows.value = res.data.rows || [];
      total.value = res.data.total || 0;
    } finally {
      setRowsLoading(false);
    }
  };

  const doSearch = async () => {
    if (!searchQ.value.trim()) {
      await loadRows();
      return;
    }
    if (!curDb.value || !curTable.value) return;
    setRowsLoading(true);
    try {
      const res = await searchTable(curDb.value, curTable.value, {
        q: searchQ.value,
        limit: 100,
      });
      columns.value = res.data.columns || [];
      rows.value = res.data.rows || [];
      total.value = res.data.count || 0;
    } finally {
      setRowsLoading(false);
    }
  };

  const onPage = (p: number) => {
    page.value = p - 1;
    loadRows();
  };
  const onPageSize = (s: number) => {
    pageSize.value = s;
    page.value = 0;
    loadRows();
  };

  const doExecSql = async () => {
    if (!sql.value.trim() || !curDb.value) return;
    setSqlLoading(true);
    try {
      const res = await execSql(curDb.value, sql.value, curDb.value);
      sqlResult.value = JSON.stringify(res.data, null, 2);
      Message.success(`执行完成，影响 ${res.data.affected ?? 0} 行`);
      await loadDbs();
    } catch {
      // 拦截器已提示
    } finally {
      setSqlLoading(false);
    }
  };

  const doReindex = async () => {
    if (!curDb.value || !curTable.value) return;
    await reindexTable(curDb.value, curTable.value, curTable.value);
    Message.success('FTS 索引已重建');
  };

  const doOptimize = async () => {
    if (!curDb.value) return;
    await optimizeDb(curDb.value, curDb.value);
    Message.success('整理完成');
    await loadDbs();
  };

  onMounted(loadDbs);
</script>

<script lang="ts">
  export default { name: 'DatabasePage' };
</script>

<style lang="less" scoped>
  .container {
    padding: 0 20px 20px;
  }
  .layout {
    margin-top: 16px;
  }
  .db-block {
    margin-bottom: 6px;
  }
  .db-item {
    padding: 8px 10px;
    border-radius: 6px;
    cursor: pointer;
    transition: background 0.2s;
    &:hover {
      background: var(--color-fill-2);
    }
    &.active {
      background: var(--color-primary-light-1);
    }
  }
  .db-name {
    font-weight: 500;
    font-size: 13px;
  }
  .db-meta {
    font-size: 12px;
    color: var(--color-text-3);
  }
  .table-list {
    padding: 2px 0 4px 14px;
  }
  .table-item {
    display: flex;
    justify-content: space-between;
    padding: 5px 8px;
    font-size: 12px;
    font-family: monospace;
    border-radius: 4px;
    cursor: pointer;
    color: var(--color-text-2);
    &:hover {
      background: var(--color-fill-2);
    }
    &.active {
      background: var(--color-primary-light-1);
      color: rgb(var(--primary-6));
    }
  }
  .table-rows {
    color: var(--color-text-3);
  }
  .cell-text {
    font-size: 12px;
    word-break: break-all;
  }
  .sql-result {
    margin-top: 12px;
    padding: 10px;
    background: var(--color-fill-1);
    border-radius: 6px;
    font-size: 12px;
    max-height: 240px;
    overflow: auto;
  }
</style>

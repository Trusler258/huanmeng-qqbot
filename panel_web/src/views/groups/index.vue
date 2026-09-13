<template>
  <div class="container">
    <Breadcrumb :items="['menu.groups', 'menu.groups.list']" />
    <div class="layout">
      <a-grid :cols="24" :col-gap="16">
        <!-- 左：群列表 -->
        <a-grid-item :span="8">
          <a-card class="general-card" title="群列表" :loading="loading">
            <div
              v-for="g in groups"
              :key="g.group_id"
              class="group-item"
              :class="{ active: g.group_id === curGroup }"
              @click="selectGroup(g.group_id)"
            >
              <div class="group-name">{{ g.name || g.group_id }}</div>
              <div class="group-meta">
                今日 {{ g.today?.messages ?? 0 }} 条 / {{ g.today?.users ?? 0 }} 人 ·
                msglog {{ g.msglog_lines }} 行 · 记忆 {{ g.memory_lines }} 行
              </div>
            </div>
            <a-empty v-if="!groups.length" description="暂无群数据" />
          </a-card>
        </a-grid-item>

        <!-- 右：详情 -->
        <a-grid-item :span="16">
          <a-card class="general-card" :title="detail ? String(detail.name || detail.group_id) : '选择群'">
            <template #extra>
              <a-space>
                <a-button size="small" @click="loadMembers" :disabled="!curGroup">成员</a-button>
                <a-button size="small" @click="loadBackups" :disabled="!curGroup">备份</a-button>
              </a-space>
            </template>

            <template v-if="detail">
              <!-- 数据概况 -->
              <a-grid :cols="4" :col-gap="12" :row-gap="12">
                <a-grid-item><stat-cell label="最近活跃" :text="detail.last_active || '-'" /></a-grid-item>
                <a-grid-item><stat-cell label="msglog 行数" :text="String(detail.msglog_lines ?? 0)" /></a-grid-item>
                <a-grid-item><stat-cell label="记忆行数" :text="String(detail.memory_lines ?? 0)" /></a-grid-item>
                <a-grid-item><stat-cell label="数据来源" :text="(detail.sources || []).join('、') || '-'" /></a-grid-item>
              </a-grid>

              <a-divider style="margin: 14px 0" />

              <!-- 长期记忆编辑 -->
              <div class="sec-title">
                长期记忆（memory_{{ curGroup }}.md）
                <a-tag size="small" color="gray">编辑器仅展示前 3000 字</a-tag>
              </div>
              <a-textarea v-model="memoryContent" :auto-size="{ minRows: 8, maxRows: 16 }" class="mem-editor" />
              <a-space style="margin-top: 8px">
                <a-popconfirm
                  content="确认覆盖这份长期记忆？会先自动备份"
                  @ok="saveMemory"
                >
                  <a-button type="primary" size="small" :disabled="!memoryDirty">保存记忆</a-button>
                </a-popconfirm>
                <span v-if="memoryDirty" class="dirty-tip">有未保存修改</span>
              </a-space>

              <a-divider style="margin: 14px 0" />

              <!-- 群笔记追加 -->
              <div class="sec-title">群笔记</div>
              <a-input-search
                v-model="newNote"
                placeholder="追加一条群笔记（最多 500 字），回车提交"
                search-button
                style="max-width: 560px"
                @search="addNote"
              />
              <div v-if="noteLines.length" class="note-preview">
                <div v-for="(l, i) in noteLines.slice(-10)" :key="i" class="note-line">
                  <span class="note-idx">{{ noteLines.length - noteLines.slice(-10).length + i + 1 }}.</span> {{ l }}
                </div>
              </div>
            </template>
            <a-empty v-else description="从左侧选择一个群" />
          </a-card>
        </a-grid-item>
      </a-grid>
    </div>

    <!-- 成员抽屉 -->
    <a-drawer v-model:visible="membersVisible" title="群成员概览（msglog 聚合）" :width="460" unmount-on-close>
      <a-table :data="members" :pagination="false" size="small">
        <template #columns>
          <a-table-column title="成员" data-index="name" />
          <a-table-column title="消息数" data-index="count" :width="90" />
        </template>
      </a-table>
      <a-empty v-if="!members.length" description="暂无数据" />
    </a-drawer>

    <!-- 备份抽屉 -->
    <a-drawer v-model:visible="backupsVisible" title="记忆备份" :width="440" unmount-on-close>
      <a-list :data="backups" :bordered="false">
        <template #item="{ item }">
          <div class="backup-item">
            <div>
              <div class="backup-name">{{ item.file }}</div>
              <div class="backup-meta">{{ item.size }} B</div>
            </div>
          </div>
        </template>
        <template #empty><a-empty description="还没有备份" /></template>
      </a-list>
    </a-drawer>
  </div>
</template>

<script lang="ts" setup>
  import { ref, computed, onMounted } from 'vue';
  import { Message } from '@arco-design/web-vue';
  import Breadcrumb from '@/components/breadcrumb/index.vue';
  import StatCell from '@/components/stat-cell/index.vue';
  import useLoading from '@/hooks/loading';
  import {
    getGroupList,
    getGroupDetail,
    getGroupMembers,
    writeGroupNote,
    writeGroupMemory,
    getGroupBackups,
    type GroupItem,
  } from '@/api/panelB';

  const { loading, setLoading } = useLoading();

  const groups = ref<GroupItem[]>([]);
  const curGroup = ref<number | null>(null);
  const detail = ref<any>(null);
  const memoryContent = ref('');
  const memoryOriginal = ref('');
  const newNote = ref('');
  const noteLines = ref<string[]>([]);
  const membersVisible = ref(false);
  const members = ref<any[]>([]);
  const backupsVisible = ref(false);
  const backups = ref<any[]>([]);

  const memoryDirty = computed(() => memoryContent.value !== memoryOriginal.value);

  const loadGroups = async () => {
    setLoading(true);
    try {
      const res = await getGroupList();
      groups.value = res.data.groups || [];
      if (!curGroup.value && groups.value.length) {
        await selectGroup(groups.value[0].group_id);
      }
    } finally {
      setLoading(false);
    }
  };

  const selectGroup = async (gid: number) => {
    curGroup.value = gid;
    const res = await getGroupDetail(gid);
    detail.value = res.data;
    memoryContent.value = res.data.memory_head || '';
    memoryOriginal.value = res.data.memory_head || '';
    noteLines.value = (res.data.note || '').split('\n').filter((l: string) => l.trim());
  };

  const saveMemory = async () => {
    if (!curGroup.value) return;
    await writeGroupMemory(curGroup.value, memoryContent.value);
    Message.success('长期记忆已保存（旧版已备份）');
    memoryOriginal.value = memoryContent.value;
    await selectGroup(curGroup.value);
  };

  const addNote = async () => {
    if (!curGroup.value || !newNote.value.trim()) return;
    await writeGroupNote(curGroup.value, curGroup.value, newNote.value.trim());
    Message.success('笔记已追加');
    newNote.value = '';
    await selectGroup(curGroup.value);
  };

  const loadMembers = async () => {
    if (!curGroup.value) return;
    const res = await getGroupMembers(curGroup.value);
    members.value = res.data.members || [];
    membersVisible.value = true;
  };

  const loadBackups = async () => {
    if (!curGroup.value) return;
    const res = await getGroupBackups(curGroup.value);
    backups.value = res.data.backups || [];
    backupsVisible.value = true;
  };

  onMounted(loadGroups);
</script>

<script lang="ts">
  export default { name: 'GroupsPage' };
</script>

<style lang="less" scoped>
  .container {
    padding: 0 20px 20px;
  }
  .layout {
    margin-top: 16px;
  }
  .group-item {
    padding: 9px 10px;
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
  .group-name {
    font-weight: 500;
    font-size: 13px;
  }
  .group-meta {
    font-size: 12px;
    color: var(--color-text-3);
    margin-top: 2px;
  }
  .sec-title {
    font-weight: 600;
    font-size: 14px;
    margin-bottom: 8px;
  }
  .mem-editor {
    font-family: 'JetBrains Mono', Consolas, monospace;
    font-size: 13px;
  }
  .dirty-tip {
    font-size: 12px;
    color: var(--color-warning-6);
  }
  .note-preview {
    margin-top: 10px;
    max-height: 220px;
    overflow: auto;
  }
  .note-line {
    padding: 3px 0;
    font-size: 13px;
    border-bottom: 1px dashed var(--color-border-1);
    word-break: break-all;
  }
  .note-idx {
    color: var(--color-text-3);
  }
  .backup-item {
    padding: 8px 4px;
    border-bottom: 1px solid var(--color-border-1);
  }
  .backup-name {
    font-family: monospace;
    font-size: 12px;
  }
  .backup-meta {
    font-size: 12px;
    color: var(--color-text-3);
  }
</style>

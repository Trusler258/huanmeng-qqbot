<template>
  <div class="container">
    <Breadcrumb :items="['menu.prompts', 'menu.prompts.list']" />
    <div class="layout">
      <a-grid :cols="24" :col-gap="16">
        <!-- 左：文件列表 -->
        <a-grid-item :span="6">
          <a-card class="general-card" title="提示词文件" :loading="listLoading">
            <div
              v-for="f in files"
              :key="f.name"
              class="pick-item"
              :class="{ active: f.name === curName }"
              @click="selectFile(f.name)"
            >
              {{ f.name }}
              <a-tag v-if="f.group === 'extra'" size="small" color="purple">主</a-tag>
            </div>
            <a-empty v-if="!files.length" description="暂无文件" />
          </a-card>
        </a-grid-item>

        <!-- 右：编辑 -->
        <a-grid-item :span="18">
          <a-card class="general-card" :title="curName || '选择文件'">
            <template #extra>
              <a-space>
                <a-button size="small" @click="loadBackups" :disabled="!curName">历史备份</a-button>
                <a-popconfirm v-if="curName" content="删除这个提示词文件？（移入回收目录）" @ok="doDelete">
                  <a-button size="small" status="danger">删除</a-button>
                </a-popconfirm>
                <a-button
                  type="primary"
                  size="small"
                  :loading="saving"
                  :disabled="!dirty"
                  @click="doSave"
                >
                  保存
                </a-button>
              </a-space>
            </template>

            <template v-if="curName">
              <a-space style="margin-bottom: 10px" wrap>
                <a-tag v-if="meta?.hot_reload" color="green" size="small">热加载（改完即生效）</a-tag>
                <a-tag v-else color="orange" size="small">改完需重启 bot</a-tag>
                <a-tag size="small">{{ fmtSize(meta?.size) }}</a-tag>
                <a-tag v-if="meta?.hint" size="small" color="gray">{{ meta.hint }}</a-tag>
              </a-space>
              <div v-if="outline.length" class="outline">
                <span class="outline-label">章节：</span>
                <a-tag v-for="s in outline" :key="s" size="small" class="outline-tag">{{ s }}</a-tag>
              </div>
              <textarea v-model="content" class="code-editor" spellcheck="false" />
              <div class="editor-meta">
                {{ content.length }} 字符
                <span v-if="dirty" style="color: var(--color-warning-6)">（有未保存修改）</span>
                <span v-else style="color: var(--color-text-3)">（与服务器一致）</span>
              </div>
            </template>
            <a-empty v-else description="从左侧选择一个提示词文件" />
          </a-card>
        </a-grid-item>
      </a-grid>
    </div>

    <!-- 备份抽屉 -->
    <a-drawer v-model:visible="backupsVisible" title="历史备份" :width="440" unmount-on-close>
      <a-list :data="backups" :bordered="false">
        <template #item="{ item }">
          <div class="backup-item">
            <div>
              <div class="backup-name">{{ item.file || item.name }}</div>
              <div class="backup-meta">{{ fmtSize(item.size) }}</div>
            </div>
            <a-popconfirm content="还原到这个备份？当前内容会先被备份" @ok="doRestore(item.file || item.name)">
              <a-button size="mini" type="outline">还原</a-button>
            </a-popconfirm>
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
  import useLoading from '@/hooks/loading';
  import {
    getPromptList,
    getPrompt,
    writePrompt,
    deletePrompt,
    getPromptBackups,
    restorePrompt,
    type PromptFile,
  } from '@/api/panelB';

  const { loading: listLoading, setLoading: setListLoading } = useLoading();
  const { loading: saving, setLoading: setSaving } = useLoading();

  const files = ref<PromptFile[]>([]);
  const curName = ref('');
  const content = ref('');
  const original = ref('');
  const meta = ref<any>(null);
  const outline = ref<string[]>([]);
  const backupsVisible = ref(false);
  const backups = ref<any[]>([]);

  const dirty = computed(() => content.value !== original.value);

  const fmtSize = (bytes?: number) => {
    if (!bytes) return '';
    if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${bytes} B`;
  };

  const loadList = async () => {
    setListLoading(true);
    try {
      const res = await getPromptList();
      files.value = res.data.files || [];
      if (!curName.value && files.value.length) {
        await selectFile(files.value[0].name);
      }
    } finally {
      setListLoading(false);
    }
  };

  const selectFile = async (name: string) => {
    const res = await getPrompt(name);
    curName.value = name;
    content.value = res.data.content || '';
    original.value = content.value;
    meta.value = res.data;
    outline.value = (res.data.outline?.sections || []).map(
      (s: any) => s.title || s
    );
  };

  const doSave = async () => {
    if (!curName.value) return;
    setSaving(true);
    try {
      const res = await writePrompt(curName.value, content.value);
      original.value = content.value;
      Message.success(
        `已保存${res.data.backup ? `（备份 ${res.data.backup}）` : ''}${
          res.data.need_restart ? '；需重启 bot 生效' : '（热加载已生效）'
        }`
      );
    } catch {
      // 拦截器已提示
    } finally {
      setSaving(false);
    }
  };

  const doDelete = async () => {
    if (!curName.value) return;
    await deletePrompt(curName.value, curName.value);
    Message.success('已移入回收目录');
    curName.value = '';
    await loadList();
  };

  const loadBackups = async () => {
    if (!curName.value) return;
    const res = await getPromptBackups(curName.value);
    backups.value = res.data.backups || [];
    backupsVisible.value = true;
  };

  const doRestore = async (backup: string) => {
    await restorePrompt(curName.value, backup);
    Message.success('已还原');
    backupsVisible.value = false;
    await selectFile(curName.value);
  };

  onMounted(loadList);
</script>

<script lang="ts">
  export default { name: 'PromptsPage' };
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
    font-family: monospace;
    transition: background 0.2s;
    display: flex;
    align-items: center;
    gap: 6px;
    &:hover {
      background: var(--color-fill-2);
    }
    &.active {
      background: var(--color-primary-light-1);
    }
  }
  .outline {
    margin-bottom: 10px;
    font-size: 12px;
    .outline-label {
      color: var(--color-text-3);
    }
    .outline-tag {
      margin-right: 4px;
      margin-bottom: 4px;
    }
  }
  .code-editor {
    width: 100%;
    min-height: 460px;
    border: 1px solid var(--color-border-2);
    border-radius: 6px;
    outline: none;
    resize: vertical;
    padding: 12px;
    font-family: 'JetBrains Mono', Consolas, monospace;
    font-size: 13px;
    line-height: 1.6;
    background: var(--color-fill-1);
    color: var(--color-text-1);
    box-sizing: border-box;
  }
  .editor-meta {
    margin-top: 8px;
    font-size: 12px;
    color: var(--color-text-3);
  }
  .backup-item {
    display: flex;
    justify-content: space-between;
    align-items: center;
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

<template>
  <div class="container">
    <Breadcrumb :items="['menu.config', 'menu.config.editor']" />
    <div class="layout">
      <a-grid :cols="24" :col-gap="16" :row-gap="16">
        <!-- 左侧文件列表 -->
        <a-grid-item :span="5" class="left-col">
          <a-card class="general-card" title="配置文件" :loading="filesLoading">
            <div
              v-for="f in files"
              :key="f.name"
              class="file-item"
              :class="{ active: f.name === currentName }"
              @click="selectFile(f.name)"
            >
              <div class="file-item-title">
                <span>{{ f.label || f.name }}</span>
                <a-tag v-if="!f.known" size="small" color="gray">未知</a-tag>
              </div>
              <div class="file-item-desc">{{ f.desc || f.name }}</div>
            </div>
            <a-divider style="margin: 8px 0" />
            <div class="env-entry" @click="showEnv = !showEnv">
              <icon-lock /> .env 密钥（{{ envItems.length }} 项）
            </div>
            <div v-if="showEnv" class="env-list">
              <div v-for="it in envItems" :key="it.key" class="env-item">
                <span class="env-key">{{ it.key }}</span>
                <a-tag size="small" :color="it.configured ? 'green' : 'red'">
                  {{ it.configured ? `已填(${it.length})` : '未填' }}
                </a-tag>
              </div>
            </div>
          </a-card>
        </a-grid-item>

        <!-- 右侧主区 -->
        <a-grid-item :span="19">
          <a-card class="general-card" :loading="detailLoading">
            <template #title>
              <span>{{ currentName ? `${currentLabel}（${currentName}）` : '选择左侧文件' }}</span>
              <a-tag v-if="parseError" color="red" style="margin-left: 8px">解析失败</a-tag>
            </template>
            <template #extra>
              <a-space>
                <a-radio-group
                  v-if="isToml && fields.length"
                  v-model="mode"
                  type="button"
                  size="small"
                >
                  <a-radio value="form">表单</a-radio>
                  <a-radio value="raw">源码</a-radio>
                </a-radio-group>
                <a-button size="small" @click="loadBackups">历史备份</a-button>
                <a-button
                  v-if="mode === 'raw'"
                  type="primary"
                  size="small"
                  :loading="saving"
                  :disabled="!currentName || content === original"
                  @click="saveRaw"
                >
                  保存全文
                </a-button>
              </a-space>
            </template>

            <template v-if="currentName">
              <!-- 顶部：搜索 + section 面包屑 -->
              <div v-if="mode === 'form' && fields.length" class="form-toolbar">
                <a-input-search
                  v-model="searchKey"
                  placeholder="搜索键名/描述，回车定位（跨段落）"
                  size="small"
                  style="width: 280px"
                  allow-clear
                />
                <div class="section-crumb">
                  <span
                    v-if="!searchKey"
                    class="crumb-item root"
                    @click="curSection = ''"
                    :class="{ active: !curSection }"
                  >
                    全部
                  </span>
                  <template v-if="!searchKey">
                    <icon-right class="crumb-sep" />
                    <span class="crumb-item" :class="{ active: !!curSection }">
                      {{ curSection === '' ? `共 ${groups.length} 段` : curSection === '(顶层)' ? '[顶层]' : `[${curSection}]` }}
                    </span>
                  </template>
                  <template v-else>
                    <span class="crumb-item search-hint">
                      命中 {{ searchHits.length }} 处
                    </span>
                  </template>
                </div>
              </div>

              <!-- ══ 表单模式 ══ -->
              <div v-if="mode === 'form'" class="form-mode">
                <!-- 搜索命中：平铺展示（跨 section 聚合） -->
                <template v-if="searchKey && searchHits.length">
                  <div class="form-group">
                    <div class="form-group-title">
                      <icon-search />
                      <span>搜索结果</span>
                      <span class="form-group-count">{{ searchHits.length }} 项</span>
                    </div>
                    <div v-for="f in searchHits" :key="f.path" class="form-row" :data-path="f.path">
                      <div class="form-row-info">
                        <div class="form-row-key">
                          <a-tag size="small" color="purple" class="section-jump" @click="jumpToSection(f)">
                            {{ f.section || '顶层' }}
                          </a-tag>
                          {{ f.key }}
                          <a-tag v-if="f.type === 'array'" size="small" color="arcoblue">数组</a-tag>
                          <a-tag v-else-if="f.type === 'unknown'" size="small" color="gray">复杂值</a-tag>
                        </div>
                        <div v-if="f.desc" class="form-row-desc">{{ f.desc }}</div>
                      </div>
                      <div class="form-row-editor">
                        <field-editor :field="f" @save="saveKey" />
                      </div>
                    </div>
                  </div>
                </template>
                <a-empty
                  v-else-if="searchKey"
                  description="没有匹配的键，试试其他关键词"
                />

                <!-- 正常浏览：只渲染选中 section -->
                <template v-else>
                  <!-- 全部模式：section 摘要卡片 -->
                  <div v-if="!curSection" class="section-grid">
                    <div
                      v-for="g in groups"
                      :key="g.section"
                      class="section-card"
                      @click="curSection = g.section"
                    >
                      <div class="section-card-head">
                        <icon-storage class="section-card-icon" />
                        <span class="section-card-name">
                          {{ g.section === '(顶层)' ? '顶层键' : `[${g.section}]` }}
                        </span>
                      </div>
                      <div class="section-card-count">{{ g.fields.length }} 个配置项</div>
                      <div class="section-card-preview">
                        {{ previewKeys(g.fields) }}
                      </div>
                    </div>
                  </div>

                  <!-- 选中 section：字段列表 -->
                  <div v-else>
                    <div class="form-group">
                      <div class="form-group-title">
                        <icon-storage />
                        <span>{{ curSection === '(顶层)' ? '顶层' : `[${curSection}]` }}</span>
                        <span class="form-group-count">{{ curGroup?.fields.length || 0 }} 项</span>
                      </div>
                      <div v-for="f in curGroup?.fields || []" :key="f.path" class="form-row" :data-path="f.path">
                        <div class="form-row-info">
                          <div class="form-row-key">
                            {{ f.key }}
                            <a-tag v-if="f.type === 'array'" size="small" color="arcoblue">数组</a-tag>
                            <a-tag v-else-if="f.type === 'unknown'" size="small" color="gray">复杂值</a-tag>
                          </div>
                          <div v-if="f.desc" class="form-row-desc">{{ f.desc }}</div>
                        </div>
                        <div class="form-row-editor">
                          <field-editor :field="f" @save="saveKey" />
                        </div>
                      </div>
                    </div>
                  </div>
                </template>
                <a-empty v-if="!fields.length" description="没有可表单化的键（纯注释或空文件）" />
              </div>

              <!-- ══ 源码模式 ══ -->
              <div v-else class="editor-wrap">
                <textarea v-model="content" class="code-editor" spellcheck="false" />
                <div class="editor-meta">
                  {{ currentSize }} 字节 ｜
                  <span :style="content !== original ? 'color:var(--color-warning-6)' : 'color:var(--color-text-3)'">
                    {{ content !== original ? '有未保存修改' : '与服务器一致' }}
                  </span>
                </div>
              </div>

              <!-- 备份抽屉 -->
              <a-drawer v-model:visible="backupsVisible" title="历史备份" :width="420" unmount-on-close>
                <a-list :data="backups" :bordered="false">
                  <template #item="{ item }">
                    <div class="backup-item">
                      <div>
                        <div class="backup-name">{{ item.file }}</div>
                        <div class="backup-meta">{{ fmtTime(item.mtime) }} · {{ fmtSize(item.size) }}</div>
                      </div>
                      <a-popconfirm content="确认还原到这个备份？当前内容会先被备份" @ok="doRestore(item.file)">
                        <a-button size="mini" type="outline">还原</a-button>
                      </a-popconfirm>
                    </div>
                  </template>
                  <template #empty><a-empty description="还没有备份" /></template>
                </a-list>
              </a-drawer>
            </template>
            <a-empty v-else description="从左侧选择一个配置文件开始编辑" />
          </a-card>
        </a-grid-item>
      </a-grid>
    </div>
  </div>
</template>

<script lang="ts" setup>
  import {
    ref,
    computed,
    onMounted,
    nextTick,
    defineComponent,
    type PropType,
  } from 'vue';
  import { Message, Modal } from '@arco-design/web-vue';
  import Breadcrumb from '@/components/breadcrumb/index.vue';
  import useLoading from '@/hooks/loading';
  import {
    getConfigFiles,
    getConfigEnv,
    getConfigFile,
    writeConfigFile,
    setConfigKey,
    getConfigBackups,
    restoreConfigFile,
    parseTomlForm,
    groupBySection,
    type ConfigFileMeta,
    type BackupItem,
    type EnvItem,
    type FormField,
  } from '@/api/config';

  /**
   * 行内编辑器：按字段类型分发到对应 Arco 控件。
   * 用 h() 渲染（Vite 构建是 runtime-only Vue，不能用字符串 template）。
   */
  import { h, type VNode } from 'vue';
  import {
    Switch as ASwitch,
    InputNumber as AInputNumber,
    Input as AInput,
    InputTag as AInputTag,
    Button as AButton,
  } from '@arco-design/web-vue';

  const FieldEditor = defineComponent({
    name: 'FieldEditor',
    props: {
      field: { type: Object as PropType<FormField>, required: true },
    },
    emits: ['save'],
    setup(props, { emit }) {
      return (): VNode => {
        const f = props.field;
        const save = (v: unknown) => emit('save', f, v);
        if (f.type === 'bool') {
          return h(ASwitch, {
            modelValue: f.value,
            onChange: (v: string | number | boolean) => save(v),
          });
        }
        if (f.type === 'number') {
          return h(AInputNumber, {
            modelValue: f.value,
            style: 'width:160px',
            onChange: (v: number | undefined) => {
              if (v !== undefined) save(v);
            },
          });
        }
        if (f.type === 'string') {
          return h(AInput, {
            modelValue: f.value as string,
            style: 'width:320px',
            'onUpdate:modelValue': (v: string) => {
              f.value = v;
            },
            onPressEnter: () => save(f.value),
          }, {
            append: () =>
              h(AButton, { size: 'small', type: 'text', onClick: () => save(f.value) },
                { default: () => '保存' }),
          });
        }
        if (f.type === 'array' && Array.isArray(f.value)) {
          return h(AInputTag, {
            modelValue: (f.value as unknown[]).map(String),
            style: 'width:320px',
            placeholder: '输入后回车添加',
            onChange: (v: string[]) => save(v),
          });
        }
        return h('span', { class: 'form-row-raw' }, f.line);
      };
    },
  });

  const { loading: filesLoading, setLoading: setFilesLoading } = useLoading();
  const { loading: detailLoading, setLoading: setDetailLoading } = useLoading();
  const { loading: saving, setLoading: setSaving } = useLoading();

  const files = ref<ConfigFileMeta[]>([]);
  const envItems = ref<EnvItem[]>([]);
  const showEnv = ref(false);
  const currentName = ref('');
  const currentLabel = ref('');
  const mode = ref<'form' | 'raw'>('form');
  const content = ref('');
  const original = ref('');
  const currentSize = ref(0);
  const parseError = ref('');
  const backupsVisible = ref(false);
  const backups = ref<BackupItem[]>([]);

  const isToml = computed(() => currentName.value.endsWith('.toml'));
  const fields = ref<FormField[]>([]);
  const groups = computed(() => groupBySection(fields.value));
  // 分层导航：'' = 全部(section 摘要卡片)，否则只渲染该 section
  const curSection = ref('');
  const searchKey = ref('');
  const curGroup = computed(
    () => groups.value.find((g) => g.section === curSection.value)
  );
  /** 跨 section 搜索：键名或描述含关键词 */
  const searchHits = computed(() => {
    const q = searchKey.value.trim().toLowerCase();
    if (!q) return [];
    return fields.value.filter(
      (f) =>
        f.key.toLowerCase().includes(q) ||
        f.path.toLowerCase().includes(q) ||
        (f.desc || '').toLowerCase().includes(q)
    );
  });
  const previewKeys = (fs: FormField[]) =>
    fs.slice(0, 3).map((f) => f.key).join(' / ') + (fs.length > 3 ? ' …' : '');
  const jumpToSection = (f: FormField) => {
    searchKey.value = '';
    curSection.value = f.section || '(顶层)';
  };

  const fmtSize = (bytes: number) => {
    if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
    if (bytes >= 1024) return `${(bytes / 1024).toFixed(0)} KB`;
    return `${bytes} B`;
  };
  const fmtTime = (ts: number) => new Date(ts * 1000).toLocaleString('zh-CN');

  const loadFiles = async () => {
    setFilesLoading(true);
    try {
      const [f, e] = await Promise.all([getConfigFiles(), getConfigEnv()]);
      files.value = f.data.files || [];
      envItems.value = e.data.items || [];
    } finally {
      setFilesLoading(false);
    }
  };

  const selectFile = (name: string) => {
    if (mode.value === 'raw' && content.value !== original.value) {
      Modal.confirm({
        title: '有未保存的修改',
        content: `切换到 ${name} 会丢失当前编辑内容，继续？`,
        onOk: () => doSelect(name),
      });
      return;
    }
    doSelect(name);
  };

  const doSelect = async (name: string) => {
    setDetailLoading(true);
    try {
      const res = await getConfigFile(name);
      const meta = files.value.find((x) => x.name === name);
      currentName.value = name;
      currentLabel.value = meta?.label || name;
      content.value = res.data.raw;
      original.value = res.data.raw;
      currentSize.value = res.data.size;
      parseError.value = res.data.parse_error || '';
      // 实时解析表单（不写死类别，文件里有什么键就渲染什么）
      fields.value = isToml.value ? parseTomlForm(res.data.raw) : [];
      mode.value = isToml.value && fields.value.length ? 'form' : 'raw';
      curSection.value = '';      // 换文件回到全部视图
      searchKey.value = '';
    } finally {
      setDetailLoading(false);
    }
  };

  /** 表单模式：单键保存 */
  const saveKey = async (f: FormField, value: unknown) => {
    setSaving(true);
    try {
      await setConfigKey(currentName.value, f.path, value);
      f.value = value;
      Message.success(`${f.key} 已保存（自动备份+自愈布防）`);
      // 重新拉取原文刷新表单（保证与服务器一致）
      const res = await getConfigFile(currentName.value);
      content.value = res.data.raw;
      original.value = res.data.raw;
      const keepSection = curSection.value;   // 保存后留在原 section
      const keepSearch = searchKey.value;
      fields.value = parseTomlForm(res.data.raw);
      curSection.value = keepSection;
      searchKey.value = keepSearch;
    } catch {
      // 拦截器已提示
    } finally {
      setSaving(false);
    }
  };

  /** 源码模式：整文件保存 */
  const saveRaw = async () => {
    if (!currentName.value) return;
    setSaving(true);
    try {
      const res = await writeConfigFile(currentName.value, content.value);
      original.value = content.value;
      currentSize.value = res.data.new_size ?? content.value.length;
      Message.success(
        `已保存${res.data.armed ? '（自愈已布防 10 分钟）' : ''}${
          res.data.need_restart ? '；需要重启 bot 生效，可在系统页操作' : ''
        }`
      );
    } catch {
      // 拦截器已提示
    } finally {
      setSaving(false);
    }
  };

  const loadBackups = async () => {
    if (!currentName.value) return;
    const res = await getConfigBackups(currentName.value);
    backups.value = res.data.backups || [];
    backupsVisible.value = true;
  };

  const doRestore = async (backup: string) => {
    try {
      await restoreConfigFile(currentName.value, backup);
      Message.success('已还原');
      backupsVisible.value = false;
      await doSelect(currentName.value);
    } catch {
      // 拦截器已提示
    }
  };

  /**
   * AI 助手联动：外部 dispatch 'ai-locate-config-key' 事件（detail = 点分路径，
   * 如 "personality"），本页自动：切到对应文件 → 进表单模式 → 搜索/选段定位
   * 该字段 → 紫色脉冲高亮那一行（精确到输入框级）。
   */
  const locateKey = async (payload: { path: string; file?: string }) => {
    const filePath = payload.file || 'bot_config.toml';
    if (!files.value.length) {
      await loadFiles();   // 页面刚挂载还没加载完，补拉一次
    }
    if (currentName.value !== filePath) {
      const meta = files.value.find((f) => f.name === filePath);
      if (!meta) {
        Message.warning(`助手没找到配置文件 ${filePath}`);
        return;
      }
      await doSelect(filePath);
    }
    await nextTick();
    if (mode.value !== 'form' || !fields.value.length) return;
    const target = fields.value.find((f) => f.path === payload.path);
    if (target) {
      // 精确命中：清空 section，用搜索平铺视图锁定（搜索视图的行也带 data-path）
      searchKey.value = '';
      curSection.value = '';
      await nextTick();
      searchKey.value = target.key;
    } else {
      // 未精确命中：按第一段当 section 兜底
      const sec = payload.path.split('.')[0];
      const hasSec = groups.value.some((g) => g.section === sec);
      if (hasSec) {
        searchKey.value = '';
        curSection.value = sec;
      } else {
        Message.warning(`没找到配置项 ${payload.path}，可试试顶部搜索`);
        return;
      }
    }
    // 触发高亮（等搜索结果渲染完）
    await nextTick();
    setTimeout(() => {
      const el = document.querySelector(
        `.form-row[data-path="${CSS.escape(payload.path)}"]`
      );
      if (el) {
        document
          .querySelectorAll('.ai-highlight')
          .forEach((n) => n.classList.remove('ai-highlight'));
        el.classList.add('ai-highlight');
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        setTimeout(() => el.classList.remove('ai-highlight'), 3000);
      }
    }, 350);
  };

  onMounted(async () => {
    await loadFiles();
    // 默认选中主配置，进入即可编辑
    if (files.value.length) {
      const first =
        files.value.find((f) => f.name === 'bot_config.toml') ||
        files.value.find((f) => f.known) ||
        files.value[0];
      doSelect(first.name);
    }
    // AI 助手联动：监听全局定位事件（ai-assistant 悬浮组件派发）
    window.addEventListener('ai-locate-config-key', (e: Event) => {
      const detail = (e as CustomEvent).detail;
      if (detail && typeof detail.path === 'string') locateKey(detail);
    });
  });
</script>

<script lang="ts">
  export default { name: 'ConfigEditor' };
</script>

<style lang="less" scoped>
  .container {
    padding: 0 20px 20px;
  }
  .layout {
    margin-top: 16px;
  }
  .left-col {
    min-height: 500px;
  }
  .file-item {
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
    &-title {
      display: flex;
      align-items: center;
      gap: 6px;
      font-weight: 500;
    }
    &-desc {
      font-size: 12px;
      color: var(--color-text-3);
      margin-top: 2px;
    }
  }
  .env-entry {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 6px 10px;
    font-size: 13px;
    color: var(--color-text-2);
    cursor: pointer;
    border-radius: 6px;
    &:hover {
      background: var(--color-fill-2);
    }
  }
  .env-list {
    padding: 4px 10px;
  }
  .env-item {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 4px 0;
    font-size: 12px;
  }
  .env-key {
    font-family: monospace;
  }

  /* 表单模式 */
  .form-toolbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 14px;
    flex-wrap: wrap;
  }
  .section-crumb {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 13px;
  }
  .crumb-item {
    padding: 2px 10px;
    border-radius: 4px;
    color: var(--color-text-2);
    cursor: pointer;
    transition: background 0.2s;
    &:hover {
      background: var(--color-fill-2);
    }
    &.active {
      color: rgb(var(--primary-6));
      font-weight: 600;
      background: var(--color-primary-light-1);
    }
    &.search-hint {
      cursor: default;
      color: var(--color-text-3);
    }
  }
  .crumb-sep {
    color: var(--color-text-4);
    font-size: 12px;
  }
  .section-jump {
    cursor: pointer;
  }

  /* section 摘要卡片（全部视图） */
  .section-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 12px;
  }
  .section-card {
    border: 1px solid var(--color-border-2);
    border-radius: 8px;
    padding: 14px;
    cursor: pointer;
    transition: all 0.2s;
    &:hover {
      border-color: rgb(var(--primary-6));
      box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
      transform: translateY(-2px);
    }
    &-head {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 6px;
    }
    &-icon {
      color: rgb(var(--primary-6));
    }
    &-name {
      font-weight: 600;
      font-size: 14px;
      font-family: 'JetBrains Mono', Consolas, monospace;
    }
    &-count {
      font-size: 12px;
      color: var(--color-text-3);
      margin-bottom: 8px;
    }
    &-preview {
      font-size: 12px;
      color: var(--color-text-4);
      font-family: monospace;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
  }

  .form-group {
    margin-bottom: 20px;
    &-title {
      display: flex;
      align-items: center;
      gap: 6px;
      font-weight: 600;
      font-size: 14px;
      padding: 6px 0;
      border-bottom: 1px solid var(--color-border-2);
      margin-bottom: 8px;
    }
    &-count {
      font-weight: 400;
      font-size: 12px;
      color: var(--color-text-3);
    }
  }
  .form-row {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    padding: 10px 4px;
    border-bottom: 1px dashed var(--color-border-1);
    gap: 16px;
    &-info {
      flex: 0 0 40%;
      min-width: 0;
    }
    &-key {
      font-weight: 500;
      font-family: 'JetBrains Mono', Consolas, monospace;
      font-size: 13px;
      display: flex;
      align-items: center;
      gap: 6px;
      flex-wrap: wrap;
    }
    &-desc {
      font-size: 12px;
      color: var(--color-text-3);
      margin-top: 3px;
      white-space: pre-wrap;
    }
    &-editor {
      flex: 1;
      display: flex;
      justify-content: flex-end;
      align-items: center;
    }
    &-editor :deep(.form-row-raw),
    .form-row-raw {
      font-family: monospace;
      font-size: 12px;
      color: var(--color-text-2);
      word-break: break-all;
      text-align: right;
    }
  }

  .editor-wrap {
    border: 1px solid var(--color-border-2);
    border-radius: 6px;
    overflow: hidden;
  }
  .code-editor {
    width: 100%;
    min-height: 480px;
    border: none;
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

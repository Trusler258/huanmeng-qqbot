<template>
  <div class="container">
    <Breadcrumb :items="['menu.features', 'menu.features.list']" />
    <div class="layout">
      <a-card class="general-card" title="实验特性开关" :loading="loading">
        <template #extra>
          <a-button size="small" @click="load">刷新</a-button>
        </template>
        <a-alert
          v-if="data?.registry_error"
          type="warning"
          style="margin-bottom: 12px"
        >{{ data.registry_error }}</a-alert>
        <div v-for="f in features" :key="f.key" class="feature-row">
          <div class="feature-info">
            <div class="feature-key">
              {{ f.key }}
              <a-tag size="small" :color="f.default ? 'green' : 'gray'">
                默认{{ f.default ? '开' : '关' }}
              </a-tag>
            </div>
            <div v-if="f.desc" class="feature-desc">{{ f.desc }}</div>
          </div>
          <div class="feature-actions">
            <a-switch
              :model-value="f.enabled"
              @change="(v: string | number | boolean) => toggle(f, v === true)"
            >
              <template #checked>开</template>
              <template #unchecked>关</template>
            </a-switch>
            <a-popconfirm content="恢复到默认值？" @ok="reset(f)">
              <a-button size="mini" type="text">还原默认</a-button>
            </a-popconfirm>
          </div>
        </div>
        <a-empty v-if="!features.length" description="暂无实验特性" />
        <div v-if="data?.file" class="file-tip">配置文件：{{ data.file }}</div>
      </a-card>

      <!-- ══ 许可码（/~key 授权）══ -->
      <a-card class="general-card" title="许可码（/~key 使用授权）" :loading="licLoading" style="margin-top: 16px">
        <template #extra>
          <a-space>
            <a-radio-group v-model="newType" type="button" size="small">
              <a-radio v-for="t in licTypes" :key="t.key" :value="t.key">{{ t.label }}</a-radio>
            </a-radio-group>
            <a-input-number v-model="newCount" :min="1" :max="50" size="small" style="width: 90px" />
            <a-button size="small" type="primary" :loading="creating" @click="createCode">生成</a-button>
          </a-space>
        </template>
        <div class="lic-tip">
          /~key 现在是全员可用，但非管理员需要许可码。把码发给对方，让他发
          <code>/~key 兑换 &lt;许可码&gt;</code> 即可。
          <span v-for="t in licTypes" :key="t.key" class="lic-type-hint">
            {{ t.label }}：{{ t.desc }}
          </span>
        </div>

        <a-divider style="margin: 12px 0">未使用的码（{{ licStats.unused_codes }} / 共 {{ licStats.total_codes }}）</a-divider>
        <a-table
          :data="unusedCodes"
          :pagination="{ pageSize: 8, hideOnSinglePage: true }"
          size="small"
          :bordered="false"
        >
          <template #columns>
            <a-table-column title="许可码" data-index="code">
              <template #cell="{ record }">
                <span class="lic-code">{{ record.code }}</span>
                <a-button size="mini" type="text" @click="copyCode(record.code)">复制</a-button>
              </template>
            </a-table-column>
            <a-table-column title="类型" data-index="label" :width="90" />
            <a-table-column title="备注" data-index="note" :width="140" />
            <a-table-column title="生成时间" data-index="created_str" :width="140" />
            <a-table-column title="操作" :width="80">
              <template #cell="{ record }">
                <a-popconfirm content="删除这个许可码？" @ok="delCode(record.code)">
                  <a-button size="mini" type="text" status="danger">删除</a-button>
                </a-popconfirm>
              </template>
            </a-table-column>
          </template>
        </a-table>
        <a-empty v-if="!unusedCodes.length" description="没有未使用的码，点右上角生成" />

        <a-divider style="margin: 12px 0">已授权用户（有效 {{ licStats.active_grants }}）</a-divider>
        <a-table
          :data="licGrants"
          :pagination="{ pageSize: 8, hideOnSinglePage: true }"
          size="small"
          :bordered="false"
        >
          <template #columns>
            <a-table-column title="QQ" data-index="qq" :width="130" />
            <a-table-column title="剩余" :width="100">
              <template #cell="{ record }">
                {{ record.uses_left === null ? '不限' : record.uses_left + ' 次' }}
              </template>
            </a-table-column>
            <a-table-column title="到期" :width="150">
              <template #cell="{ record }">
                <a-tag v-if="record.expired" size="small" color="red">已过期</a-tag>
                <span v-else>{{ record.expires_str || '永久' }}</span>
              </template>
            </a-table-column>
            <a-table-column title="来源码" data-index="source" :width="140">
              <template #cell="{ record }">
                <span class="lic-code-sm">{{ record.source }}</span>
              </template>
            </a-table-column>
            <a-table-column title="操作" :width="80">
              <template #cell="{ record }">
                <a-popconfirm content="撤销这个人的使用权？" @ok="revokeGrant(record.qq)">
                  <a-button size="mini" type="text" status="danger">撤销</a-button>
                </a-popconfirm>
              </template>
            </a-table-column>
          </template>
        </a-table>
        <a-empty v-if="!licGrants.length" description="还没有人兑换" />
      </a-card>
    </div>
  </div>
</template>

<script lang="ts" setup>
  import { ref, computed, onMounted } from 'vue';
  import { Message } from '@arco-design/web-vue';
  import Breadcrumb from '@/components/breadcrumb/index.vue';
  import useLoading from '@/hooks/loading';
  import {
    getFeatures,
    toggleFeature,
    resetFeature,
    getLicenses,
    createLicense,
    deleteLicense,
    revokeLicense,
  } from '@/api/panelC';

  const { loading, setLoading } = useLoading();
  const data = ref<any>(null);
  const features = ref<any[]>([]);

  const load = async () => {
    setLoading(true);
    try {
      const res = await getFeatures();
      data.value = res.data;
      features.value = res.data.items || [];
    } finally {
      setLoading(false);
    }
  };

  const toggle = async (f: any, enabled: boolean) => {
    await toggleFeature(f.key, enabled);
    Message.success(`${f.key} 已${enabled ? '开启' : '关闭'}`);
    await load();
  };

  const reset = async (f: any) => {
    await resetFeature(f.key);
    Message.success(`${f.key} 已还原默认`);
    await load();
  };

  // ── 许可码 ──
  const { loading: licLoading, setLoading: setLicLoading } = useLoading();
  const { loading: creating, setLoading: setCreating } = useLoading();
  const licCodes = ref<any[]>([]);
  const licGrants = ref<any[]>([]);
  const licTypes = ref<any[]>([]);
  const licStats = ref<any>({ total_codes: 0, unused_codes: 0, redeemed_codes: 0, active_grants: 0 });
  const newType = ref('once');
  const newCount = ref(1);

  const unusedCodes = computed(() => licCodes.value.filter((c) => !c.redeemed));

  const loadLic = async () => {
    setLicLoading(true);
    try {
      const res = await getLicenses();
      licCodes.value = res.data.codes || [];
      licGrants.value = res.data.grants || [];
      licTypes.value = res.data.types || [];
      licStats.value = res.data.stats || licStats.value;
    } finally {
      setLicLoading(false);
    }
  };

  const createCode = async () => {
    setCreating(true);
    try {
      const res = await createLicense(newType.value, '', newCount.value);
      const codes: string[] = res.data.codes || [];
      Message.success(
        `已生成 ${codes.length} 个${licTypes.value.find((t) => t.key === newType.value)?.label || ''}码：${codes[0]}`
      );
      await loadLic();
    } finally {
      setCreating(false);
    }
  };

  const copyCode = async (code: string) => {
    try {
      await navigator.clipboard.writeText(code);
      Message.success('已复制');
    } catch {
      Message.info(code);
    }
  };

  const delCode = async (code: string) => {
    await deleteLicense(code);
    Message.success('已删除');
    await loadLic();
  };

  const revokeGrant = async (qq: string) => {
    await revokeLicense(qq);
    Message.success('已撤销');
    await loadLic();
  };

  onMounted(() => {
    load();
    loadLic();
  });
</script>

<script lang="ts">
  export default { name: 'FeaturesPage' };
</script>

<style lang="less" scoped>
  .container {
    padding: 0 20px 20px;
  }
  .layout {
    margin-top: 16px;
  }
  .feature-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 12px 4px;
    border-bottom: 1px dashed var(--color-border-1);
  }
  .feature-info {
    flex: 1;
    min-width: 0;
  }
  .feature-key {
    font-weight: 500;
    font-family: monospace;
    font-size: 13px;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .feature-desc {
    font-size: 12px;
    color: var(--color-text-3);
    margin-top: 3px;
  }
  .feature-actions {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .file-tip {
    margin-top: 12px;
    font-size: 12px;
    color: var(--color-text-3);
  }

  /* 许可码区 */
  .lic-tip {
    font-size: 12px;
    color: var(--color-text-2);
    line-height: 1.9;
    background: var(--color-fill-1);
    padding: 8px 12px;
    border-radius: 6px;
    code {
      background: var(--color-fill-3);
      padding: 1px 5px;
      border-radius: 4px;
      font-family: 'JetBrains Mono', Consolas, monospace;
    }
  }
  .lic-type-hint {
    display: inline-block;
    margin-left: 14px;
    color: var(--color-text-3);
  }
  .lic-code {
    font-family: 'JetBrains Mono', Consolas, monospace;
    font-weight: 600;
    letter-spacing: 1px;
    color: rgb(var(--primary-6));
  }
  .lic-code-sm {
    font-family: monospace;
    font-size: 11px;
    color: var(--color-text-3);
  }
</style>

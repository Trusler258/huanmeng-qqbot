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
    </div>
  </div>
</template>

<script lang="ts" setup>
  import { ref, onMounted } from 'vue';
  import { Message } from '@arco-design/web-vue';
  import Breadcrumb from '@/components/breadcrumb/index.vue';
  import useLoading from '@/hooks/loading';
  import { getFeatures, toggleFeature, resetFeature } from '@/api/panelC';

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

  onMounted(load);
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
</style>

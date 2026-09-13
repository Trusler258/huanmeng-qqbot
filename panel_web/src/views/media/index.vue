<template>
  <div class="container">
    <Breadcrumb :items="['menu.media', 'menu.media.browse']" />
    <div class="layout">
      <a-space :size="16" direction="vertical" fill>
        <!-- 分类总览 -->
        <a-card class="general-card" title="图片分类" :loading="catLoading">
          <template #extra>
            <a-button size="small" @click="showTrash">回收目录</a-button>
          </template>
          <a-grid :cols="4" :col-gap="12" :row-gap="12">
            <a-grid-item v-for="c in categories" :key="c.key">
              <div
                class="cat-card"
                :class="{ active: c.key === curCat }"
                @click="selectCat(c.key)"
              >
                <div class="cat-label">{{ c.label }}</div>
                <div class="cat-desc">{{ c.desc }}</div>
                <div class="cat-meta">
                  {{ c.count }} 张 · {{ fmtSize(c.size) }}
                  <a-tag v-if="c.deletable" size="small" color="orange">可清</a-tag>
                </div>
              </div>
            </a-grid-item>
          </a-grid>
        </a-card>

        <!-- 图片列表 -->
        <a-card class="general-card" :title="curLabel ? `${curLabel}（${images.length}/${total}）` : '选择分类'">
          <template #extra>
            <a-space v-if="selected.length">
              <span>已选 {{ selected.length }} 张</span>
              <a-popconfirm
                :content="`删除所选 ${selected.length} 张？（移入回收目录，可找回）`"
                @ok="doDelete"
              >
                <a-button size="small" status="danger">删除所选</a-button>
              </a-popconfirm>
            </a-space>
          </template>

          <a-table
            :data="images"
            :loading="imgLoading"
            :pagination="{
              total,
              pageSize,
              current: page + 1,
              showTotal: true,
            }"
            :row-selection="{ type: 'checkbox', showCheckedAll: true, width: 40 }"
            v-model:selected-keys="selected"
            :row-key="(r: any) => r.name"
            size="small"
            @page-change="onPage"
          >
            <template #columns>
              <a-table-column title="缩略" :width="90">
                <template #cell="{ record }">
                  <a-image
                    :src="thumbUrl(record.name)"
                    :width="60"
                    :height="45"
                    fit="cover"
                    :preview="true"
                  />
                </template>
              </a-table-column>
              <a-table-column title="文件名" data-index="name" />
              <a-table-column title="大小" :width="100">
                <template #cell="{ record }">{{ fmtSize(record.size) }}</template>
              </a-table-column>
              <a-table-column title="格式" :width="70">
                <template #cell="{ record }">{{ record.ext }}</template>
              </a-table-column>
              <a-table-column title="修改时间" :width="170">
                <template #cell="{ record }">{{ fmtTime(record.mtime) }}</template>
              </a-table-column>
            </template>
            <template #empty><a-empty description="该分类暂无图片" /></template>
          </a-table>
        </a-card>
      </a-space>
    </div>

    <!-- 回收目录抽屉 -->
    <a-drawer v-model:visible="trashVisible" title="回收目录（按批次）" :width="480" unmount-on-close>
      <a-list :data="trashBatches" :bordered="false">
        <template #item="{ item }">
          <div class="trash-item">
            <div>
              <div class="trash-name">{{ item.name }}</div>
              <div class="trash-meta">{{ item.count }} 个 · {{ fmtSize(item.size) }} · {{ fmtTime(item.mtime) }}</div>
            </div>
            <a-popconfirm content="彻底清空这批文件？不可恢复！" @ok="doPurge(item.name)">
              <a-button size="mini" status="danger">彻底清空</a-button>
            </a-popconfirm>
          </div>
        </template>
        <template #empty><a-empty description="回收目录是空的" /></template>
      </a-list>
    </a-drawer>
  </div>
</template>

<script lang="ts" setup>
  import { ref, onMounted } from 'vue';
  import { Message } from '@arco-design/web-vue';
  import Breadcrumb from '@/components/breadcrumb/index.vue';
  import useLoading from '@/hooks/loading';
  import {
    getMediaCategories,
    getMediaImages,
    mediaThumbUrl,
    deleteMediaImages,
    getMediaTrash,
    purgeMediaTrash,
    type MediaCategory,
    type MediaImage,
  } from '@/api/panelB';

  const { loading: catLoading, setLoading: setCatLoading } = useLoading();
  const { loading: imgLoading, setLoading: setImgLoading } = useLoading();

  const categories = ref<MediaCategory[]>([]);
  const curCat = ref('');
  const curLabel = ref('');
  const images = ref<MediaImage[]>([]);
  const total = ref(0);
  const page = ref(0);
  const pageSize = 40;
  const selected = ref<string[]>([]);
  const trashVisible = ref(false);
  const trashBatches = ref<any[]>([]);

  const fmtSize = (bytes: number) => {
    if (!bytes) return '0 B';
    if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
    if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
    if (bytes >= 1024) return `${(bytes / 1024).toFixed(0)} KB`;
    return `${bytes} B`;
  };
  const fmtTime = (ts: number) => (ts ? new Date(ts * 1000).toLocaleString('zh-CN') : '-');

  const thumbUrl = (name: string) => mediaThumbUrl(curCat.value, name);

  const loadCategories = async () => {
    setCatLoading(true);
    try {
      const res = await getMediaCategories();
      categories.value = res.data.categories || [];
      if (!curCat.value && categories.value.length) {
        await selectCat(categories.value[0].key);
      }
    } finally {
      setCatLoading(false);
    }
  };

  const selectCat = async (key: string) => {
    curCat.value = key;
    page.value = 0;
    selected.value = [];
    const cat = categories.value.find((c) => c.key === key);
    curLabel.value = cat?.label || key;
    await loadImages();
  };

  const loadImages = async () => {
    if (!curCat.value) return;
    setImgLoading(true);
    try {
      const res = await getMediaImages(curCat.value, {
        limit: pageSize,
        offset: page.value * pageSize,
      });
      images.value = res.data.images || [];
      total.value = res.data.total || 0;
    } finally {
      setImgLoading(false);
    }
  };

  const onPage = (p: number) => {
    page.value = p - 1;
    selected.value = [];
    loadImages();
  };

  const doDelete = async () => {
    if (!selected.value.length) return;
    const res = await deleteMediaImages(
      curCat.value,
      selected.value,
      curCat.value
    );
    Message.success(`已移入回收目录 ${res.data.deleted} 个，释放 ${fmtSize(res.data.freed || 0)}`);
    selected.value = [];
    await Promise.all([loadImages(), loadCategories()]);
  };

  const showTrash = async () => {
    const res = await getMediaTrash();
    trashBatches.value = res.data.batches || [];
    trashVisible.value = true;
  };

  const doPurge = async (batch: string) => {
    await purgeMediaTrash(batch, batch);
    Message.success('已彻底清空');
    await showTrash();
    await loadCategories();
  };

  onMounted(loadCategories);
</script>

<script lang="ts">
  export default { name: 'MediaPage' };
</script>

<style lang="less" scoped>
  .container {
    padding: 0 20px 20px;
  }
  .layout {
    margin-top: 16px;
  }
  .cat-card {
    padding: 12px 14px;
    border: 1px solid var(--color-border-2);
    border-radius: 8px;
    cursor: pointer;
    transition: all 0.2s;
    &:hover {
      border-color: var(--color-primary-light-2);
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
    }
    &.active {
      border-color: rgb(var(--primary-6));
      background: var(--color-primary-light-1);
    }
  }
  .cat-label {
    font-weight: 600;
    font-size: 14px;
  }
  .cat-desc {
    font-size: 12px;
    color: var(--color-text-3);
    margin: 4px 0;
  }
  .cat-meta {
    font-size: 12px;
    color: var(--color-text-2);
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .trash-item {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 8px 4px;
    border-bottom: 1px solid var(--color-border-1);
  }
  .trash-name {
    font-family: monospace;
    font-size: 12px;
  }
  .trash-meta {
    font-size: 12px;
    color: var(--color-text-3);
  }
</style>

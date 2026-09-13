<template>
  <div class="container">
    <Breadcrumb :items="['menu.logs', 'menu.logs.viewer']" />
    <div class="layout">
      <a-card class="general-card" title="日志查看">
        <template #extra>
          <a-space>
            <a-select
              v-model="curFile"
              :options="fileOptions"
              style="width: 260px"
              size="small"
              @change="reload"
            />
            <a-select v-model="level" style="width: 100px" size="small" allow-clear placeholder="级别" @change="reload">
              <a-option value="INFO">INFO</a-option>
              <a-option value="DEBUG">DEBUG</a-option>
              <a-option value="WARNING">WARNING</a-option>
              <a-option value="ERROR">ERROR</a-option>
            </a-select>
            <a-input-search
              v-model="keyword"
              placeholder="关键字过滤"
              style="width: 200px"
              size="small"
              @search="reload"
              @clear="reload"
            />
            <a-switch v-model="live" size="small">
              <template #checked>实时</template>
              <template #unchecked>暂停</template>
            </a-switch>
            <a-button size="small" @click="reload">刷新</a-button>
            <a-button size="small" type="outline" :loading="exporting" @click="exportLog">
              导出
            </a-button>
          </a-space>
        </template>

        <div ref="logBox" class="log-box">
          <div v-for="(l, i) in lines" :key="i" class="log-line" :class="levelClass(l)">
            {{ l }}
          </div>
          <a-empty v-if="!lines.length" description="暂无日志" style="margin-top: 80px" />
        </div>
        <div class="log-meta">
          {{ lines.length }} 行 ｜ 文件：{{ curFile || '-' }}
          <span v-if="live" class="live-dot" /> {{ live ? '实时跟随中' : '已暂停' }}
        </div>
      </a-card>
    </div>
  </div>
</template>

<script lang="ts" setup>
  import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue';
  import { Message } from '@arco-design/web-vue';
  import Breadcrumb from '@/components/breadcrumb/index.vue';
  import { getLogFiles, getLogTail } from '@/api/panel';

  const files = ref<any[]>([]);
  const curFile = ref('');
  const level = ref('');
  const keyword = ref('');
  const live = ref(true);
  const lines = ref<string[]>([]);
  const logBox = ref<HTMLElement>();
  let ws: WebSocket | null = null;
  let pollTimer: number | undefined;
  let lastPollAt = 0;

  const fileOptions = computed(() =>
    files.value.map((f: any) => ({
      label: `${f.name || f.file || f} (${fmtSize(f.size ?? 0)})`,
      value: f.name || f.file || f,
    }))
  );

  const fmtSize = (bytes: number) => {
    if (!bytes) return '';
    if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)}MB`;
    if (bytes >= 1024) return `${(bytes / 1024).toFixed(0)}KB`;
    return `${bytes}B`;
  };

  const levelClass = (line: string) => {
    if (line.includes('ERROR')) return 'lv-error';
    if (line.includes('WARNING') || line.includes('WARN')) return 'lv-warn';
    if (line.includes('DEBUG')) return 'lv-debug';
    return '';
  };

  const scrollBottom = () => {
    nextTick(() => {
      if (logBox.value) logBox.value.scrollTop = logBox.value.scrollHeight;
    });
  };

  const reload = async () => {
    try {
      const res = await getLogTail({
        file: curFile.value || undefined,
        lines: 300,
        keyword: keyword.value || undefined,
        level: level.value || undefined,
      });
      const logs: any[] = res.data.logs || res.data.lines || res.data.rows || [];
      lines.value = logs.map((r) => (typeof r === 'string' ? r : r.msg || r.text || r.line || JSON.stringify(r)));
      scrollBottom();
    } catch {
      // ignore
    }
  };

  // ── 导出：原生导航下载（?token= 认证，与日志 WS 同款先例）。
  // 不用 blob：无头环境截获 blob 下载不稳，且大文件 blob 全量进内存 ──
  const exporting = ref(false);
  const exportLog = () => {
    if (!curFile.value || exporting.value) return;
    exporting.value = true;
    const token = localStorage.getItem('token') || '';
    const base = curFile.value.replace(/\.log(\.\d+)?$/, '').replace(/[^\w.-]/g, '_');
    const stamp = new Date().toISOString().slice(0, 19).replace('T', '_').replace(/:/g, '');
    // 触发浏览器原生下载（服务端 Content-Disposition 带文件名）
    const url = `/api/logs/export?file=${encodeURIComponent(curFile.value)}&token=${encodeURIComponent(token)}&dl=${base}_export_${stamp}`;
    const a = document.createElement('a');
    a.href = url;
    a.download = `${base}_export_${stamp}.log`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    Message.success(`开始导出 ${curFile.value}`);
    window.setTimeout(() => {
      exporting.value = false;
    }, 1500);
  };

  const connectWs = () => {
    if (ws) {
      ws.close();
      ws = null;
    }
    const token = localStorage.getItem('token') || '';
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const fileQ = curFile.value ? `&file=${encodeURIComponent(curFile.value)}` : '';
    ws = new WebSocket(
      `${proto}://${window.location.host}/api/logs/ws?token=${encodeURIComponent(token)}${fileQ}`
    );
    ws.onmessage = (ev) => {
      if (!live.value) return;
      try {
        const data = JSON.parse(ev.data as string);
        // 后端逐条推 {type:'log', msg}；兼容批量 {lines}
        if (data.type === 'log' && data.msg) {
          lines.value.push(data.msg);
          if (lines.value.length > 2000) lines.value.splice(0, lines.value.length - 2000);
          scrollBottom();
        } else if (data.lines?.length) {
          lines.value.push(...data.lines);
          scrollBottom();
        }
      } catch {
        // 非 JSON 忽略
      }
    };
    ws.onclose = () => {
      if (live.value) window.setTimeout(connectWs, 5000); // 断线重连
    };
    ws.onerror = () => ws?.close();
  };

  // WS 不可用时的轮询兜底
  const startPolling = () => {
    pollTimer = window.setInterval(async () => {
      if (!live.value || ws?.readyState === WebSocket.OPEN) return;
      const now = Date.now();
      if (now - lastPollAt < 8000) return;
      lastPollAt = now;
      await reload();
    }, 10_000);
  };

  const loadFiles = async () => {
    try {
      const res = await getLogFiles();
      files.value = res.data.files || [];
      if (!curFile.value && files.value.length) {
        const first: any = files.value[0];
        curFile.value = first.name || first.file || first;
      }
    } catch {
      // ignore
    }
  };

  onMounted(async () => {
    await loadFiles();
    await reload();
    connectWs();
    startPolling();
  });
  onUnmounted(() => {
    live.value = false;
    ws?.close();
    ws = null;
    if (pollTimer) window.clearInterval(pollTimer);
  });
</script>

<script lang="ts">
  export default { name: 'LogViewer' };
</script>

<style lang="less" scoped>
  .container {
    padding: 0 20px 20px;
  }
  .layout {
    margin-top: 16px;
  }
  .log-box {
    height: 560px;
    overflow: auto;
    background: var(--color-fill-1);
    border-radius: 6px;
    padding: 10px 12px;
    font-family: 'JetBrains Mono', Consolas, monospace;
    font-size: 12px;
    line-height: 1.65;
  }
  .log-line {
    white-space: pre-wrap;
    word-break: break-all;
    color: var(--color-text-2);
    &.lv-error {
      color: var(--color-danger-6);
    }
    &.lv-warn {
      color: var(--color-warning-6);
    }
    &.lv-debug {
      color: var(--color-text-3);
    }
  }
  .log-meta {
    margin-top: 8px;
    font-size: 12px;
    color: var(--color-text-3);
  }
  .live-dot {
    display: inline-block;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--color-success-6);
    margin: 0 4px;
    animation: blink 1.2s infinite;
  }
  @keyframes blink {
    50% {
      opacity: 0.3;
    }
  }
</style>

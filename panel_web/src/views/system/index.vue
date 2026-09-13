<template>
  <div class="container">
    <Breadcrumb :items="['menu.system', 'menu.system.status']" />
    <div class="layout">
      <a-space :size="16" direction="vertical" fill>
        <!-- ══ 崩溃自愈 ══ -->
        <a-card class="general-card" title="崩溃自愈" :loading="healLoading">
          <template #extra>
            <a-space>
              <a-switch
                :model-value="heal?.enabled"
                @change="(v: string | number | boolean) => toggleHeal(v === true)"
              >
                <template #checked>开</template>
                <template #unchecked>关</template>
              </a-switch>
              <a-button size="small" @click="doHealTest">演练</a-button>
              <a-popconfirm content="清空自愈事件记录？" @ok="doEventsClear">
                <a-button size="small">清空记录</a-button>
              </a-popconfirm>
            </a-space>
          </template>
          <a-grid :cols="4" :col-gap="16" :row-gap="12" v-if="heal">
            <a-grid-item>
              <stat-cell label="状态" :text="heal.enabled ? '已启用' : '已关闭'"
                :color="heal.enabled ? 'var(--color-success-6)' : 'var(--color-text-3)'" />
            </a-grid-item>
            <a-grid-item>
              <stat-cell label="阶段" :text="heal.phase" :color="phaseColor" />
            </a-grid-item>
            <a-grid-item>
              <stat-cell label="连续失败" :text="`${heal.consecutive_fail} / ${heal.fail_threshold}`"
                :color="heal.consecutive_fail > 0 ? 'var(--color-warning-6)' : 'var(--color-text-1)'" />
            </a-grid-item>
            <a-grid-item>
              <stat-cell label="bot 存活" :text="heal.bot_alive ? '正常' : '探测失败'"
                :color="heal.bot_alive ? 'var(--color-success-6)' : 'var(--color-danger-6)'" />
            </a-grid-item>
            <a-grid-item>
              <stat-cell label="探测间隔" :text="`${heal.probe_interval}s`" />
            </a-grid-item>
            <a-grid-item>
              <stat-cell label="累计探测" :text="String(heal.probe_count)" />
            </a-grid-item>
            <a-grid-item>
              <stat-cell label="崩溃循环" :text="heal.crash_loop ? '是' : '否'"
                :color="heal.crash_loop ? 'var(--color-danger-6)' : 'var(--color-text-1)'" />
            </a-grid-item>
            <a-grid-item>
              <stat-cell label="systemd 状态" :text="`${heal.unit_active_state || '-'} / ${heal.unit_sub_state || '-'}`" />
            </a-grid-item>
          </a-grid>

          <!-- 布防条 -->
          <a-alert v-if="heal?.armed" type="warning" style="margin-top: 12px">
            已布防 {{ heal.armed_age }}s（高频探测 {{ heal.probe_interval }}s）——
            <a-space>
              <a-button size="mini" type="primary" @click="doKeepalive">心跳续档</a-button>
              <a-button size="mini" @click="doDisarm">解除布防</a-button>
            </a-space>
          </a-alert>
          <div v-else style="margin-top: 12px">
            <a-button size="small" type="outline" @click="doArm">手动布防</a-button>
          </div>

          <!-- 事件记录 -->
          <a-table
            v-if="heal?.events?.length"
            :data="heal.events"
            :pagination="false"
            size="small"
            style="margin-top: 12px"
          >
            <template #columns>
              <a-table-column title="时间" data-index="ts" :width="170" />
              <a-table-column title="事件" data-index="kind" :width="120" />
              <a-table-column title="详情" data-index="note" />
            </template>
          </a-table>
          <a-empty v-else description="暂无自愈事件" style="margin-top: 12px" />
        </a-card>

        <!-- ══ 服务管理 ══ -->
        <a-card class="general-card" title="服务管理" :loading="svcLoading">
          <a-table :data="services" :pagination="false" size="small">
            <template #columns>
              <a-table-column title="服务" data-index="name" :width="200" />
              <a-table-column title="状态" :width="100">
                <template #cell="{ record }">
                  <a-tag :color="record.active === 'active' ? 'green' : 'red'" size="small">
                    {{ record.active }}
                  </a-tag>
                </template>
              </a-table-column>
              <a-table-column title="子状态" data-index="sub_state" :width="100" />
              <a-table-column title="运行自" data-index="since" />
              <a-table-column title="操作" :width="220">
                <template #cell="{ record }">
                  <a-space>
                    <a-popconfirm :content="`确认重启 ${record.name}？`" @ok="doService(record.name, 'restart')">
                      <a-button size="mini" type="outline" status="warning">重启</a-button>
                    </a-popconfirm>
                    <a-popconfirm :content="`确认停止 ${record.name}？`" @ok="doService(record.name, 'stop')">
                      <a-button size="mini" type="outline" status="danger">停止</a-button>
                    </a-popconfirm>
                    <a-popconfirm :content="`确认启动 ${record.name}？`" @ok="doService(record.name, 'start')">
                      <a-button size="mini" type="outline" status="success">启动</a-button>
                    </a-popconfirm>
                  </a-space>
                </template>
              </a-table-column>
            </template>
          </a-table>
        </a-card>

        <!-- ══ 端口 + 操作栈 ══ -->
        <a-grid :cols="2" :col-gap="16" :row-gap="16">
          <a-grid-item>
            <a-card class="general-card" title="监听端口" :loading="portsLoading">
              <a-table :data="ports" :pagination="{ pageSize: 10 }" size="small">
                <template #columns>
                  <a-table-column title="端口" :width="90">
                    <template #cell="{ record }">{{ record.port }}</template>
                  </a-table-column>
                  <a-table-column title="进程" data-index="process" :width="150" />
                  <a-table-column title="地址" data-index="addr" />
                </template>
              </a-table>
            </a-card>
          </a-grid-item>
          <a-grid-item>
            <a-card class="general-card" title="操作栈（可回滚）">
              <template #extra>
                <a-space>
                  <a-popconfirm content="回滚最近 1 步写操作？" @ok="doRollback(1)">
                    <a-button size="mini" status="warning">回滚 1 步</a-button>
                  </a-popconfirm>
                  <a-popconfirm content="清空操作栈记录（不回滚）？" @ok="doClearOps">
                    <a-button size="mini">清空</a-button>
                  </a-popconfirm>
                </a-space>
              </template>
              <a-table :data="ops" :pagination="false" size="small" :scroll="{ y: 260 }">
                <template #columns>
                  <a-table-column title="时间" :width="160">
                    <template #cell="{ record }">{{ fmtTs(record.ts) }}</template>
                  </a-table-column>
                  <a-table-column title="类型" data-index="kind" :width="110" />
                  <a-table-column title="说明" data-index="note" />
                </template>
              </a-table>
            </a-card>
          </a-grid-item>
        </a-grid>

        <!-- ══ 审计日志 ══ -->
        <a-card class="general-card" title="审计日志">
          <template #extra>
            <a-button size="small" @click="loadAudit">刷新</a-button>
          </template>
          <a-table :data="audit" :pagination="{ pageSize: 15 }" size="small">
            <template #columns>
              <a-table-column title="时间" :width="170">
                <template #cell="{ record }">{{ record.time }}</template>
              </a-table-column>
              <a-table-column title="用户" data-index="user" :width="100" />
              <a-table-column title="动作" data-index="action" :width="160" />
              <a-table-column title="对象" data-index="target" :width="180" />
              <a-table-column title="详情" data-index="detail" />
            </template>
          </a-table>
        </a-card>
      </a-space>
    </div>
  </div>
</template>

<script lang="ts" setup>
  import { ref, computed, onMounted, onUnmounted } from 'vue';
  import { Message } from '@arco-design/web-vue';
  import Breadcrumb from '@/components/breadcrumb/index.vue';
  import useLoading from '@/hooks/loading';
  import StatCell from '@/components/stat-cell/index.vue';
  import {
    getServices,
    serviceAction,
    getPorts,
    getAudit,
    getOps,
    rollbackOps,
    clearOps,
    getSelfheal,
    selfhealToggle,
    selfhealArm,
    selfhealDisarm,
    selfhealKeepalive,
    selfhealTest,
    selfhealEventsClear,
    type ServiceItem,
    type OpItem,
  } from '@/api/panel';

  const { loading: healLoading, setLoading: setHealLoading } = useLoading();
  const { loading: svcLoading, setLoading: setSvcLoading } = useLoading();
  const { loading: portsLoading, setLoading: setPortsLoading } = useLoading();

  const heal = ref<Record<string, any> | null>(null);
  const services = ref<ServiceItem[]>([]);
  const ports = ref<Record<string, any>[]>([]);
  const ops = ref<OpItem[]>([]);
  const audit = ref<Record<string, any>[]>([]);
  let keepaliveTimer: number | undefined;

  const phaseColor = computed(() => {
    const p = heal.value?.phase;
    if (p === 'armed') return 'var(--color-warning-6)';
    if (p === 'degraded' || p === 'healing') return 'var(--color-danger-6)';
    return 'var(--color-success-6)';
  });

  const fmtTs = (ts: string | number) => {
    const d = new Date(typeof ts === 'number' ? ts * 1000 : ts);
    return isNaN(d.getTime()) ? String(ts) : d.toLocaleString('zh-CN');
  };

  const loadHeal = async () => {
    try {
      const res = await getSelfheal();
      heal.value = res.data;
    } catch {
      // ignore
    }
  };
  const loadServices = async () => {
    setSvcLoading(true);
    try {
      const res = await getServices();
      services.value = res.data.items || res.data.services || [];
    } finally {
      setSvcLoading(false);
    }
  };
  const loadPorts = async () => {
    setPortsLoading(true);
    try {
      const res = await getPorts();
      ports.value = res.data.ports || [];
    } finally {
      setPortsLoading(false);
    }
  };
  const loadOps = async () => {
    try {
      const res = await getOps();
      ops.value = res.data.ops || [];
    } catch {
      // ignore
    }
  };
  const loadAudit = async () => {
    try {
      const res = await getAudit(300);
      audit.value = res.data.items || res.data.rows || res.data || [];
    } catch {
      // ignore
    }
  };

  // ── 自愈操作 ──
  const refreshHeal = async () => {
    await loadHeal();
    await loadOps();
  };
  const toggleHeal = async (on: boolean) => {
    await selfhealToggle(on);
    Message.success(on ? '自愈已开启' : '自愈已关闭');
    await refreshHeal();
  };
  const doArm = async () => {
    await selfhealArm();
    Message.success('已布防，进入高频探测');
    await refreshHeal();
  };
  const doDisarm = async () => {
    await selfhealDisarm();
    Message.success('已解除布防');
    await refreshHeal();
  };
  const doKeepalive = async () => {
    await selfhealKeepalive();
    Message.success('心跳已发');
    await loadHeal();
  };
  const doHealTest = async () => {
    await selfhealTest('TEST');
    Message.success('演练完成，判定链路正常');
    await refreshHeal();
  };
  const doEventsClear = async () => {
    await selfhealEventsClear();
    Message.success('已清空');
    await loadHeal();
  };

  // ── 服务操作 ──
  const doService = async (name: string, action: string) => {
    await serviceAction(name, action, name);
    Message.success(`${name} ${action} 已执行`);
    await loadServices();
  };

  // ── 操作栈 ──
  const doRollback = async (steps: number) => {
    await rollbackOps(steps, String(steps));
    Message.success('回滚完成，bot 可能需要重启');
    await refreshHeal();
  };
  const doClearOps = async () => {
    await clearOps();
    Message.success('已清空');
    await loadOps();
  };

  onMounted(async () => {
    await Promise.all([loadHeal(), loadServices(), loadPorts(), loadOps(), loadAudit()]);
    // 布防期间 30s 心跳，保持高频探测不掉档
    keepaliveTimer = window.setInterval(async () => {
      if (heal.value?.armed) {
        selfhealKeepalive().catch(() => {});
      }
      loadHeal();
    }, 30_000);
  });
  onUnmounted(() => {
    if (keepaliveTimer) window.clearInterval(keepaliveTimer);
  });
</script>

<script lang="ts">
  export default { name: 'SystemStatus' };
</script>

<style lang="less" scoped>
  .container {
    padding: 0 20px 20px;
  }
  .layout {
    margin-top: 16px;
  }
</style>

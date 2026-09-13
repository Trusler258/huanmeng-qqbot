<template>
  <div class="stat-cell">
    <div class="stat-cell-label">{{ label }}</div>
    <div class="stat-cell-value" :style="color ? { color } : undefined">
      {{ text ?? '-' }}
    </div>
  </div>
</template>

<script lang="ts" setup>
  /**
   * 通用统计展示格：label 上 / value 下的纯文本卡格。
   *
   * 为什么不用 <a-statistic>：它的 value 类型是 [Number, Object] 且默认
   * format='HH:mm:ss'——传字符串会被 dayjs 按日期格式化，非日期字符串
   * （'v2.3.0'、'4.4 / 7.7 GB'）全部渲染成 Invalid Date。
   * 本组件纯文本直出，数字/字符串/时间都安全。
   */
  withDefaults(
    defineProps<{
      label: string;
      text?: string | number | null;
      color?: string;
    }>(),
    { text: '-', color: '' }
  );
</script>

<style lang="less" scoped>
  .stat-cell {
    &-label {
      color: var(--color-text-2);
      font-size: 13px;
      margin-bottom: 4px;
    }
    &-value {
      font-size: 22px;
      font-weight: 500;
      color: var(--color-text-1);
      line-height: 1.4;
      word-break: break-all;
    }
  }
</style>

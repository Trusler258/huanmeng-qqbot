import { mergeConfig } from 'vite';
import baseConfig from './vite.config.base';
import configCompressPlugin from './plugin/compress';
import configVisualizerPlugin from './plugin/visualizer';
import configArcoResolverPlugin from './plugin/arcoResolver';
import configImageminPlugin from './plugin/imagemin';

export default mergeConfig(
  {
    mode: 'production',
    plugins: [
      configCompressPlugin('gzip'),
      configVisualizerPlugin(),
      configArcoResolverPlugin(),
      configImageminPlugin(),
    ],
    build: {
      rollupOptions: {
        output: {
          // 对象式 manualChunks 在 vue alias 到 dist/vue.esm-bundler.js 时
          // 会把 vue 运行时错误聚进 arco chunk，导致跨 chunk 命名导出丢失
          // (defineComponent is not defined)。改用 Rollup 自动分包。
          manualChunks(id) {
            if (id.includes('node_modules/echarts') || id.includes('node_modules/vue-echarts') || id.includes('node_modules/zrender')) {
              return 'chart';
            }
            return undefined;
          },
        },
      },
      chunkSizeWarningLimit: 2000,
    },
  },
  baseConfig
);

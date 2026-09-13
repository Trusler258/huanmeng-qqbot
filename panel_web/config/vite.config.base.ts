import { resolve } from 'path';
import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import svgLoader from 'vite-svg-loader';
import configArcoStyleImportPlugin from './plugin/arcoStyleImport';

export default defineConfig({
  plugins: [
    vue(),
    // vueJsx 插件已移除：menu 组件改写为模板写法后项目无 tsx 文件，
    // 且 plugin-vue-jsx 编译链是此前产物丢 import 的直接路径
    svgLoader({ svgoConfig: {} }),
    configArcoStyleImportPlugin(),
  ],
  resolve: {
    alias: [
      {
        find: '@',
        replacement: resolve(__dirname, '../src'),
      },
      {
        find: 'assets',
        replacement: resolve(__dirname, '../src/assets'),
      },
      {
        // ESM 构建（官方推荐 bundler 入口）。此前 alias 到 CJS 构建
        // (vue-i18n.cjs.js) 会让 Rollup 在 commonjs 互操作中摇掉 Vue 的
        // 命名导出绑定，产物出现裸 defineComponent -> ReferenceError
        find: 'vue-i18n',
        replacement: 'vue-i18n/dist/vue-i18n.esm-bundler.js',
      },
      {
        find: 'vue',
        replacement: 'vue/dist/vue.esm-bundler.js', // compile template
      },
    ],
    extensions: ['.ts', '.js'],
  },
  define: {
    'process.env': {},
    // vue-i18n esm-bundler 构建要求的特性标志（否则运行时逐个回落 getGlobalThis 且 dev 打警告）
    __VUE_I18N_FULL_INSTALL__: true,
    __VUE_I18N_LEGACY_API__: false,
    __VUE_I18N_PROD_DEVTOOLS__: false,
    __INTLIFY_PROD_DEVTOOLS__: false,
    __INTLIFY_JIT_COMPILATION__: true,
    __VUE_I18N_BRIDGE__: false,
  },
  css: {
    preprocessorOptions: {
      less: {
        modifyVars: {
          hack: `true; @import (reference) "${resolve(
            'src/assets/style/breakpoint.less'
          )}";`,
        },
        javascriptEnabled: true,
      },
    },
  },
});

import { mergeConfig } from 'vite';
import eslint from 'vite-plugin-eslint';
import baseConfig from './vite.config.base';

export default mergeConfig(
  {
    mode: 'development',
    server: {
      open: false,
      port: 5174,
      fs: {
        strict: true,
      },
      // 开发时代理到服务器上的面板后端（panel.service 只绑 127.0.0.1:59300）。
      // 生产环境由 nginx 同源反代，不需要这个配置。
      proxy: {
        '/api': {
          target: 'http://127.0.0.1:59300',
          changeOrigin: true,
          // 面板后端在远程服务器上，本地开发走 SSH 隧道：
          //   ssh -p 20015 -i ~/.ssh/id_ed25519 -N -L 59300:127.0.0.1:59300 root@01240820.xyz
          // target 写本机 59300 即可；若后端真在本机，也无需改动
          secure: false,
        },
      },
    },
    plugins: [
      eslint({
        cache: false,
        include: ['src/**/*.ts', 'src/**/*.tsx', 'src/**/*.vue'],
        exclude: ['node_modules'],
      }),
    ],
  },
  baseConfig
);

import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// 开发期把 /api 代理到后端，前端代码里只写相对路径：
// 这样不必在后端开跨域，也避免把后端地址硬编码进构建产物。
//
// 端口与代理目标读项目根 `.env`（与 start.py / backend 同一份），改 ANYURL2TEXT_PORT
// 或 FRONTEND_PORT 后重启即可；strictPort 避免 Vite 静默换端口后与 start.py 互杀。
// `npm run dev` 的 cwd 是 frontend/，上一级即项目根。
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '..', '')
  const backendPort = env.ANYURL2TEXT_PORT || '8020'
  const frontendPort = Number(env.FRONTEND_PORT || '5173')

  return {
    plugins: [react()],
    envDir: '..',
    server: {
      port: frontendPort,
      strictPort: true,
      proxy: {
        '/api': {
          target: `http://127.0.0.1:${backendPort}`,
          changeOrigin: true,
          rewrite: (requestPath) => requestPath.replace(/^\/api/, ''),
        },
      },
    },
  }
})

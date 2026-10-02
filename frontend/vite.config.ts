import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 开发期把 /api 代理到后端，前端代码里只写相对路径：
// 这样不必在后端开跨域，也避免把后端地址硬编码进构建产物。
//
// ⚠️ 这里的端口必须与后端 .env 的 ANYURL2TEXT_PORT（默认 8020）一致。
// 改了后端端口却忘了改这里，症状是前端所有请求都失败，且看不出是端口问题。
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8020',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})

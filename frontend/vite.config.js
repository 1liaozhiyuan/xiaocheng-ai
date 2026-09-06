import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// 开发模式：npm run dev 起在 5173，/api 代理到后端 8001（前后端分离，无跨域问题）
// 生产模式：npm run build 产出 dist/，由 FastAPI 直接托管（单个服务即可运行）
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8001",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
  },
});

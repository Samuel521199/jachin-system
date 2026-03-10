import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

// https://vitejs.dev/config/
export default defineConfig(async () => ({
  plugins: [react()],

  // Vite options tailored for Tauri development and only applied in `tauri dev` or `tauri build`
  clearScreen: false,
  
  // Tauri expects a fixed port, fail if that port is not available
  server: {
    port: 31421,
    strictPort: true,
    watch: {
      // Tell Vite to ignore watching `src-tauri`
      ignored: ["**/src-tauri/**"],
    },
    // 开发时代理 L3 技能 API，避免跨域 Failed to fetch
    proxy: {
      "/l3": {
        target: "http://127.0.0.1:18991",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/l3/, ""),
      },
    },
  },

  // 多页入口：main 窗口用 console.html
  build: {
    rollupOptions: {
      input: {
        main: path.resolve(__dirname, "index.html"),
        console: path.resolve(__dirname, "console.html"),
        sprite: path.resolve(__dirname, "sprite.html"),
        chat: path.resolve(__dirname, "chat.html"),
      },
    },
  },

  // Resolve path aliases
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },

  // 确保静态资源正确服务
  publicDir: "public",
}));

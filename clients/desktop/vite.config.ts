import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

// https://vitejs.dev/config/
export default defineConfig(async ({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const l3Port = env.VITE_L3_HTTP_PORT || "18991";
  return {
    plugins: [react()],
    clearScreen: false,
    server: {
      port: 31421,
      strictPort: true,
      watch: {
        ignored: ["**/src-tauri/**"],
      },
      proxy: {
        "/l3": {
          target: `http://127.0.0.1:${l3Port}`,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/l3/, ""),
        },
      },
    },
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
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "./src"),
      },
    },
    publicDir: "public",
  };
});

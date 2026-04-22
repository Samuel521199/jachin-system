import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";
import { Readable } from "stream";

/** L3 HTTP 端口回退（与 l3_node/http_server.py 一致） */
const L3_PORTS = [18991, 18990, 18992, 18993, 18994, 18995, 18996, 18997, 18998, 18999];

const HOP_BY_HOP = new Set([
  "connection",
  "keep-alive",
  "transfer-encoding",
  "proxy-connection",
  "host",
  "content-length",
]);

function readIncomingBody(req: any): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    req.on("data", (c: Buffer | string) => {
      chunks.push(Buffer.isBuffer(c) ? c : Buffer.from(c, "utf8"));
    });
    req.on("end", () => resolve(Buffer.concat(chunks)));
    req.on("error", reject);
  });
}

/** Vite 插件：/l3 多端口回退代理，L3 可能在 18990 等端口启动，支持 SSE 流式响应 */
function viteL3Proxy() {
  return {
    name: "vite-l3-proxy",
    configureServer(server: any) {
      server.middlewares.use(async (req: any, res: any, next: () => void) => {
        if (!req.url?.startsWith("/l3")) {
          next();
          return;
        }
        if (req.aborted) {
          next();
          return;
        }
        const pathname = req.url.replace(/^\/l3/, "");
        const method = String(req.method || "GET").toUpperCase();

        // Node fetch 不能把 IncomingMessage 直接当 body；POST（如安全锁审批）会全端口失败 → 502「L3 不可达」
        let forwardBody: Buffer | undefined;
        if (!["GET", "HEAD"].includes(method)) {
          try {
            const buf = await readIncomingBody(req);
            if (buf.length > 0) forwardBody = buf;
          } catch {
            res.statusCode = 400;
            res.setHeader("Content-Type", "application/json");
            res.end(JSON.stringify({ error: "读取请求体失败" }));
            return;
          }
        }

        for (const port of L3_PORTS) {
          try {
            const target = `http://127.0.0.1:${port}${pathname}`;
            const src = (req.headers || {}) as Record<string, string | string[] | undefined>;
            const headers: Record<string, string> = {};
            for (const [k, v] of Object.entries(src)) {
              if (v == null || HOP_BY_HOP.has(k.toLowerCase())) continue;
              headers[k] = Array.isArray(v) ? v.join(", ") : String(v);
            }
            headers["host"] = `127.0.0.1:${port}`;
            if (forwardBody && forwardBody.length > 0) {
              headers["content-length"] = String(forwardBody.length);
            }
            const resp = await fetch(target, {
              method,
              headers,
              body: forwardBody,
            });
            res.statusCode = resp.status;
            resp.headers.forEach((v, k) => {
              if (!/^content-encoding$/i.test(k)) res.setHeader(k, v);
            });
            res.setHeader("Access-Control-Allow-Origin", "*");
            const body = resp.body;
            if (body && resp.headers.get("content-type")?.includes("text/event-stream")) {
              const stream = Readable.fromWeb(body as import("stream").WebReadableStream<any>);
              stream.on("error", () => {}); // L3 断开或客户端断开时 pipe 可能抛错，避免 ECONNRESET 导致 Vite 崩溃
              res.on("close", () => stream.destroy()); // 客户端断开时销毁源流，避免泄漏
              stream.pipe(res);
            } else if (body) {
              const buf = await resp.arrayBuffer();
              res.end(Buffer.from(buf));
            } else {
              res.end();
            }
            return;
          } catch {
            continue;
          }
        }
        res.statusCode = 502;
        res.setHeader("Content-Type", "application/json");
        res.end(JSON.stringify({ error: "L3 不可达，请确认 L3 已启动 (端口 18991 等)" }));
      });
    },
  };
}

// https://vitejs.dev/config/
export default defineConfig(async () => ({
  plugins: [react(), viteL3Proxy()],

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
    // L3 代理由 viteL3Proxy 处理（多端口回退 18991/18990/18992...）
  },

  // 多页入口：main 窗口用 console.html
  build: {
    // 控制台含 Mermaid/Cytoscape/大屏块等，500kB 默认阈值会持续误报；桌面 Tauri 可接受较大初始 chunk
    chunkSizeWarningLimit: 1600,
    rollupOptions: {
      input: {
        main: path.resolve(__dirname, "index.html"),
        console: path.resolve(__dirname, "console.html"),
        sprite: path.resolve(__dirname, "sprite.html"),
        chat: path.resolve(__dirname, "chat.html"),
        notification: path.resolve(__dirname, "notification.html"),
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

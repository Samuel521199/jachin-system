import path from "node:path";

/** @type {import('next').NextConfig} */
const nextConfig = {
  // Linux/容器生产部署：产出 .next/standalone，便于拷贝到服务器直接 node server.js
  output: "standalone",

  /**
   * Windows：Watchpack 在初始扫描时可能对盘符根目录做 lstat，触及
   * `System Volume Information`（系统保护目录）触发 EINVAL。
   * 仅 glob 有时无法拦住「根目录下列举」，需同时加入当前盘符下的绝对路径字符串。
   */
  webpack: (config, { dev }) => {
    if (dev) {
      // Webpack 5 schema：ignored 为数组时每项须为非空字符串（不可混 RegExp，且 Next 可能带入 ""）。
      const prev = config.watchOptions?.ignored;
      const strings = [];
      if (Array.isArray(prev)) {
        for (const x of prev) {
          if (typeof x === "string" && x.length > 0) strings.push(x);
        }
      } else if (typeof prev === "string" && prev.length > 0) {
        strings.push(prev);
      }

      const extras = [
        "**/System Volume Information",
        "**/System Volume Information/**",
        "**/$Recycle.Bin",
        "**/$Recycle.Bin/**",
      ];
      try {
        const root = path.parse(process.cwd()).root;
        if (root && root.length >= 2) {
          const svi = path.join(root, "System Volume Information");
          const recycle = path.join(root, "$Recycle.Bin");
          extras.push(svi, `${svi}/**`, recycle, `${recycle}/**`);
          const sviFwd = svi.replace(/\\/g, "/");
          const recFwd = recycle.replace(/\\/g, "/");
          extras.push(sviFwd, `${sviFwd}/**`, recFwd, `${recFwd}/**`);
        }
      } catch {
        /* ignore */
      }
      for (const e of extras) {
        if (e && !strings.includes(e)) strings.push(e);
      }

      config.watchOptions = {
        ...config.watchOptions,
        ignored: strings.length > 0 ? strings : extras.filter(Boolean),
        followSymlinks: false,
      };

      /**
       * 仅靠 ignored 无法阻止 Watchpack 在「初始扫描」时对盘符根目录做 lstat；
       * Windows 上 `D:\System Volume Information` 等会返回 EINVAL 并刷屏。
       * 开发态在 win32 下启用短间隔轮询，可走另一套路径、避免该错误。
       * 若需恢复原生监听（并接受可能出现的告警），设环境变量：NEXUS_WIN_WEBPACK_POLL=0
       */
      if (
        process.platform === "win32" &&
        process.env.NEXUS_WIN_WEBPACK_POLL !== "0" &&
        process.env.WATCHPACK_POLLING !== "false"
      ) {
        config.watchOptions.poll = 1000;
        config.watchOptions.aggregateTimeout = 300;
      }
    }
    return config;
  },
};

export default nextConfig;

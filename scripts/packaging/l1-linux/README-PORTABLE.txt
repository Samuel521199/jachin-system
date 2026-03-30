Jachin L1 (Nexus) — Linux 便携运行包（类 Windows 绿色版）
============================================================

解压后在本目录执行：

  chmod +x start.sh
  ./start.sh

- 已内含 Linux x64 官方 Node 运行时（runtime/node/），服务器无需 apt/yum 安装 Node，也无需 Docker。
- 需配置同目录下的 .env.production.local（至少 DATABASE_URL），见 DEPLOY.md。
- 适用于常见 glibc 发行版（Ubuntu / Debian / CentOS / RHEL 等）；Alpine（musl）请另行构建。

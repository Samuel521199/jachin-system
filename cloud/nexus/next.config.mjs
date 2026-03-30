/** @type {import('next').NextConfig} */
const nextConfig = {
  // Linux/容器生产部署：产出 .next/standalone，便于拷贝到服务器直接 node server.js
  output: "standalone",
};

export default nextConfig;

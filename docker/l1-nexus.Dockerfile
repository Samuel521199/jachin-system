# Jachin Layer 1 (Nexus) — Linux 镜像（数据库建议装在宿主机，见 docker/compose.l1.yml 与 docs/L1_LINUX_CLOUD_DEPLOY.md）
# 构建：docker build --platform linux/amd64 -f docker/l1-nexus.Dockerfile -t jachin-l1:latest .
# 默认 NODE_IMAGE 为 DaoCloud 同步的 node（国内直连 docker.io 易超时）；要改用官方：
#   docker build --build-arg NODE_IMAGE=node:20-bookworm-slim ...
# npm ci 访问 registry.npmjs.org 易 ECONNRESET：默认用 npmmirror；海外或需官方源时：
#   docker build --build-arg NPM_REGISTRY=https://registry.npmjs.org ...
# 单机运行：docker run --rm -p 3000:3000 --add-host=host.docker.internal:host-gateway --env-file docker/l1.env jachin-l1:latest
#
ARG NODE_IMAGE=docker.m.daocloud.io/library/node:20-bookworm-slim
FROM ${NODE_IMAGE} AS builder
ARG NPM_REGISTRY=https://registry.npmmirror.com
WORKDIR /src
COPY cloud/nexus ./nexus
COPY scripts/packaging/l1-linux/start.sh ./nexus/start.sh
WORKDIR /src/nexus
ENV NPM_CONFIG_REGISTRY=${NPM_REGISTRY}
RUN npm config set fetch-retries 8 \
    && npm config set fetch-retry-mintimeout 20000 \
    && npm config set fetch-retry-maxtimeout 120000 \
    && npm ci
ENV NODE_ENV=production
RUN npm run build

FROM ${NODE_IMAGE} AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV PORT=3000
# 不显式设 HOSTNAME：Next standalone 的 server.js 在未设置时仍 listen 0.0.0.0；
# 避免把「监听地址」写进 process.env.HOSTNAME，减少与对外 URL（NEXUS_PUBLIC_URL / AUTH_URL）混淆。

COPY --from=builder /src/nexus/.next/standalone ./
COPY --from=builder /src/nexus/.next/static ./.next/static
COPY --from=builder /src/nexus/start.sh ./start.sh
COPY --from=builder /src/nexus/drizzle ./drizzle

# 与 savePackageLocally 一致；standalone 默认不保证运行时写入的 public 可被静态层命中
RUN mkdir -p /app/public/packages

# Windows 检出 CRLF 时 shebang 会变成 bash\r，容器内启动即 127；构建时在 Linux 层统一去掉 \r
RUN sed -i 's/\r$//' /app/start.sh && chmod +x /app/start.sh

EXPOSE 3000
CMD ["./start.sh"]

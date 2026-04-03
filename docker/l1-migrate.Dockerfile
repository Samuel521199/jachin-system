# Jachin L1 — 仅用于在服务器上对空 public schema 执行 drizzle-kit push + init-store（无需宿主机 Node / 无需 git clone）
# 构建（在仓库根目录）:
#   docker build --platform linux/amd64 -f docker/l1-migrate.Dockerfile -t jachin-l1-migrate:latest .
#   docker save jachin-l1-migrate:latest -o jachin-l1-migrate.tar
# 服务器:
#   docker load -i jachin-l1-migrate.tar
#   docker run --rm --network host -e DATABASE_URL="postgres://..." jachin-l1-migrate:latest
#
ARG NODE_IMAGE=docker.m.daocloud.io/library/node:20-bookworm-slim
FROM ${NODE_IMAGE}
WORKDIR /nexus
COPY cloud/nexus/package.json cloud/nexus/package-lock.json ./
RUN npm ci
COPY cloud/nexus/ ./
# 避免镜像内残留构建机 .env 覆盖运行时 -e DATABASE_URL
RUN rm -f .env .env.local .env.production .env.development 2>/dev/null || true
# push：按 schema.ts 在空库上建全表；init-store：幂等补齐
CMD ["sh", "-lc", "set -e; npx drizzle-kit push --force; npx tsx scripts/init-store-schema.ts"]

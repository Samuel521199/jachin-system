L1 ECS 部署包（当前示例服务器 47.86.39.173）
============================================

服务器地址与访问（与 docs/L1_LINUX_CLOUD_DEPLOY.md §0 一致）
------------------------------------------------------------
  公网 IP（示例）     47.86.39.173
  L1 浏览器入口       http://47.86.39.173:3000
  服务器部署目录      /opt/jachin-l1
  SSH（示例）         ssh root@47.86.39.173

  勿在浏览器使用 http://0.0.0.0:3000（不是站点地址）。

  l1.env 中必填（生产）：
    NEXUS_PUBLIC_URL=http://47.86.39.173:3000   （与浏览器地址一致，无尾部 /）
    AUTH_SECRET=...                             （openssl rand -base64 32）
  可选 AUTH_URL= 同上，见 docker/l1.env.example。

  换机器或换 IP 时：改 l1.env、本 README、docs/L1_LINUX_CLOUD_DEPLOY.md §0、
  scripts/scp-l1-docker-artifacts-to-server.ps1 等中的地址。

本目录内容
----------
- compose.l1.runtime.yml   Docker 仅镜像启动（与 docker/ 下同名文件一致）
- l1.env                   已填 DATABASE_URL（同机 Postgres）、NEXUS_PUBLIC_URL（勿提交 Git）
- l1.env.example           无敏感信息的模板
- server-load-and-up.sh    服务器上：docker load + compose 启动 nexus-host
- server-l1-db-sql-only.sh 服务器仅 SQL 重建库（无 Node/无 git）：见下文「纯 SQL」
- l1_reset_public_schema.sql   DROP public + 空 schema
- l1_nexus_full_schema.sql     全量 DDL（与当前 Nexus schema 一致，大版本升级随仓库更新）
- l1_hotfix_p1_p2_schema.sql   旧库/旧全量 SQL 补列补表（is_personal_default、device_groups 等）
- l1_migrate_architecture_p1_p2_slug.sql  升级到当前架构文档版：P1 SSOT + P2 Fleet + organizations.slug（含 0012 数据回填；幂等）
- _strip_pg_restrict.py    开发机再生成全量 SQL 时用（去掉 pg_dump \\restrict 行）
- db-migrate-remote.ps1    Windows 本机：对远端库执行 npm run db:migrate / db:init-store（可选）

镜像包 jachin-l1-latest.tar.gz
------------------------------
本机构建后复制到此目录，或放在仓库根目录（上传脚本两处都会找）：

  docker build --platform linux/amd64 -f docker/l1-nexus.Dockerfile -t jachin-l1:latest .
  docker save jachin-l1:latest | gzip > jachin-l1-latest.tar.gz
  copy jachin-l1-latest.tar.gz deploy\l1-ecs-bundle\

本机一键上传
------------
在仓库根目录：

  .\scripts\scp-l1-docker-artifacts-to-server.ps1

服务器首次启动
--------------
  ssh root@47.86.39.173
  cd /opt/jachin-l1
  chmod +x server-load-and-up.sh
  ./server-load-and-up.sh

发版更新镜像后
--------------
  docker compose -f compose.l1.runtime.yml --profile host down
  ./server-load-and-up.sh

若 docker load 报 docker-import .../repositories
------------------------------------------
目录里同时有旧 .tar.gz 与新 .tar 时，旧脚本会优先读 .gz，损坏的 gzip 会触发该错误。
已改为「两者并存时用修改时间较新的文件」；或临时移走旧包：
  mv jachin-l1-latest.tar.gz jachin-l1-latest.tar.gz.bak
或强制：
  export JACHIN_L1_LOAD_FILE=/opt/jachin-l1/jachin-l1-latest.tar
  ./server-load-and-up.sh
仍失败时检查 /tmp 与 /var/lib/docker 磁盘空间：df -h

数据库
------
首次部署前在本机执行（需能连 47.86.39.173:5432）：

  .\deploy\l1-ecs-bundle\db-migrate-remote.ps1

表结构大版本升级（清空 public 后按 Drizzle 重建）
------------------------------------------------
说明：drizzle/*.sql 多为增量补丁，空库必须用「drizzle-kit push」按 schema.ts 建全表，
      不能只拼 SQL 文件。

推荐 A — 无需在服务器 git clone（迁移专用镜像）：
  在仓库根目录构建并上传 jachin-l1-migrate.tar：
    docker build --platform linux/amd64 -f docker/l1-migrate.Dockerfile -t jachin-l1-migrate:latest .
    docker save jachin-l1-migrate:latest -o jachin-l1-migrate.tar
  服务器：docker load -i jachin-l1-migrate.tar
  cd /opt/jachin-l1 && chmod +x server-l1-db-reset-and-migrate.sh
  ./server-l1-db-reset-and-migrate.sh --yes

推荐 B — 服务器上已有完整仓库 cloud/nexus：
  ./server-l1-db-reset-and-migrate.sh --yes /opt/jachin-system-main/cloud/nexus

纯 SQL（服务器无代码目录时，与 /opt 仅有 jachin-l1 一致）
--------------------------------------------------------
  cd /opt/jachin-l1
  chmod +x server-l1-db-sql-only.sh
  ./server-l1-db-sql-only.sh --yes
  依赖：本目录下 l1_nexus_full_schema.sql（与发版 Nexus 同步；升级表结构后需换新版文件）

再生成全量 SQL（开发机、需 Docker，在仓库根目录）
------------------------------------------------
  docker rm -f jachin-l1-gen-pg 2>/dev/null; docker network rm jachin-l1-gen-net 2>/dev/null
  docker network create jachin-l1-gen-net
  docker run -d --name jachin-l1-gen-pg --network jachin-l1-gen-net -e POSTGRES_PASSWORD=genpass -e POSTGRES_DB=jachin_nexus postgres:16-bookworm
  sleep 4
  docker build --platform linux/amd64 -f docker/l1-migrate.Dockerfile -t jachin-l1-migrate:latest .
  docker run --rm --network jachin-l1-gen-net -e DATABASE_URL=postgresql://postgres:genpass@jachin-l1-gen-pg:5432/jachin_nexus jachin-l1-migrate:latest
  docker exec jachin-l1-gen-pg pg_dump -U postgres -d jachin_nexus --schema-only --no-owner --no-privileges > deploy/l1-ecs-bundle/l1_nexus_full_schema.sql
  python deploy/l1-ecs-bundle/_strip_pg_restrict.py
  docker rm -f jachin-l1-gen-pg; docker network rm jachin-l1-gen-net

详见 docs/L1_LINUX_CLOUD_DEPLOY.md

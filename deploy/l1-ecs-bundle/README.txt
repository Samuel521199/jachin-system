L1 ECS 部署包（当前服务器 47.86.39.173）
========================================

本目录内容
----------
- compose.l1.runtime.yml   Docker 仅镜像启动（与 docker/ 下同名文件一致）
- l1.env                   已填 DATABASE_URL（同机 Postgres）、NEXUS_PUBLIC_URL（勿提交 Git）
- l1.env.example           无敏感信息的模板
- server-load-and-up.sh    服务器上：docker load + compose 启动 nexus-host
- db-migrate-remote.ps1    Windows 本机：对远端库执行 npm run db:migrate / db:init-store

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

数据库
------
首次部署前在本机执行（需能连 47.86.39.173:5432）：

  .\deploy\l1-ecs-bundle\db-migrate-remote.ps1

详见 docs/L1_LINUX_CLOUD_DEPLOY.md

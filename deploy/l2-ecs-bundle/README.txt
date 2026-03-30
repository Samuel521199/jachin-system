L2 ECS Docker 部署包（与 L1 同机示例：Nexus 宿主机 3000）
=====================================================

本目录
------
- compose.l2.runtime.yml   单节点 L2 + Redis（入口 18888）
- l2.env                   含 NEXUS_BASE_URL / BRAIN_BASE_URL（勿提交 Git）
- l2.env.example           模板
- server-l2-up.sh         服务器：pull redis + compose up（需 LF 行尾）

本机构建镜像（仓库根目录）
--------------------------
  docker build --platform linux/amd64 -f deploy/Dockerfile.l2 -t jachin-l2:latest .
  docker save jachin-l2:latest -o jachin-l2-latest.tar
  （可选 gzip；上传后若双层 tar 请先 tar xf 再 load）

上传到服务器 /opt/jachin-l2/
----------------------------
  compose.l2.runtime.yml, l2.env, jachin-l2-latest.tar（或 .tar.gz）, server-l2-up.sh

服务器（示例）
--------------
  mkdir -p /opt/jachin-l2 && cd /opt/jachin-l2
  docker load -i jachin-l2-latest.tar
  sed -i 's/\r$//' server-l2-up.sh && chmod +x server-l2-up.sh
  ./server-l2-up.sh

阿里云安全组：入方向放行 TCP 18888（及已有 3000、22）。

与 L1 关系
----------
l2.env 中 NEXUS_BASE_URL=http://host.docker.internal:3000 要求 L1 以宿主机网络
监听 3000（当前 compose.l1.runtime.yml 的 nexus-host 即如此）。

配对顺序（推荐）
----------------
1. L1 已启动且浏览器可打开 NEXUS_PUBLIC_URL（如 http://公网IP:3000）
2. 本目录执行 docker load + server-l2-up.sh，L2 容器起来
3. 再配对（在容器内写 nexus_config.json，已挂卷 l2_jachin_config 可持久化）：

   cd /opt/jachin-l2
   docker compose -f compose.l2.runtime.yml exec l2 bash
   python -m core.cli pair --base-url http://host.docker.internal:3000

   若 L2 与 L1 不同机，把 --base-url 换成 L1 公网地址。

4. 浏览器按 CLI 提示打开 L1 控制台确认配对；成功后无需改 l2.env 里的 NEXUS_BASE_URL 也可同步（凭证内带 nexus_base_url）

说明：先启动 L2 再 exec 配对即可；不必「未启动先配对」（除非你在本机 pair 再把 ~/.jachin 拷进卷，一般不推荐）。

详见 docker/l2.env.example、docker-compose.l2-cluster.yml（多副本+Nginx 集群另用）。

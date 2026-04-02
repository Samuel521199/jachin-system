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

与 L1 建立信任（nexus_config，推荐顺序）
----------------------------------------
完整说明与架构图：仓库 docs/L1_L2_PAIRING_AND_WEB_BRIDGE.md

1. L1 配置 L2_BRIDGE_ALLOWED_RETURN_PREFIXES（须覆盖 l2.env 里 BRAIN_BASE_URL 前缀，Web Bridge 回跳用）。
2. L1 已启动，浏览器可打开 NEXUS_PUBLIC_URL；l2.env 中 NEXUS_BASE_URL 须指向该 L1（容器内可解析）。
3. docker load + server-l2-up.sh 启动 L2。
4. **主路径（推荐，无跳转）**：浏览器打开 http://<公网IP>:18888/gateway/ ，用户名填 L1 注册邮箱、密码填 L1 密码登录；写入卷内 ~/.jachin/nexus_config.json 并热启同步。
5. **主路径（OAuth / 偏好跳转）**：同页点「使用 Nexus 账号登录」，在 L1 确认后回跳兑换。
6. **辅助路径（仅 SSH / 无 Web）**：

   docker compose -f compose.l2.runtime.yml exec l2 bash
   python -m core.cli pair --base-url http://host.docker.internal:3000
   （L1 不同机则换为 L1 公网基址；再在 L1 /console/pair 输入 6 位码。）

凭证内带 nexus_base_url，一般无需改 l2.env 的 NEXUS_BASE_URL。

详见 docker/l2.env.example、docker-compose.l2-cluster.yml（多副本+Nginx 集群另用）。

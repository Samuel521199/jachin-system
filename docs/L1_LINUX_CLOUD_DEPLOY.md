# Layer 1（Nexus）Linux 云端部署指南

面向在海外 **Linux x86_64** 服务器上运行 Jachin Nexus（Next.js 14 + Drizzle + PostgreSQL）。

**交付形态（非 Docker）**：`tar.gz` 解压后是一个**便携运行目录**——类似 Windows 上的「绿色版 / 免安装文件夹」：内含 **`runtime/node`**（官方 Linux x64 Node 二进制）、`server.js` 与追踪依赖。服务器上 **不需要安装 Docker**，默认也 **不需要 `apt/yum install node`**，执行 `./start.sh` 即可。

---

## 0. 服务器与环境一览（示例 ECS）

以下为本仓库脚本与示例配置里沿用的 **当前示例机**；**更换 IP、域名或目录后，请同步修改** `deploy/l1-ecs-bundle/l1.env`（及本机 `l1.env`）、`scripts/scp-l1-docker-artifacts-to-server.ps1`、`scripts/scp-l2-docker-artifacts-to-server.ps1`，并全文替换本文档与 `deploy/l1-ecs-bundle/README.txt` 中的旧地址。

| 项 | 示例值 | 说明 |
|----|--------|------|
| **ECS 公网 IPv4** | `47.86.39.173` | 阿里云等控制台「公网 IP」 |
| **L1 Nexus（浏览器访问）** | `http://47.86.39.173:3000` | 控制台 / 注册 / 登录入口；安全组需放行 **TCP 3000**（或经 Nginx 反代 443） |
| **L1 Docker 部署目录（示例）** | `/opt/jachin-l1` | `compose.l1.runtime.yml`、`l1.env`、`server-load-and-up.sh` 所在目录 |
| **便携包部署目录（示例）** | `/opt/jachin/jachin-l1-linux-amd64-v*` | 非 Docker 解压路径，见 §4 |
| **SSH 登录（示例）** | `ssh root@47.86.39.173` | 以你实际用户与密钥为准 |
| **PostgreSQL（同机、容器连宿主机）** | `127.0.0.1:5432` / 库 `jachin_nexus` | `nexus-host` 网络时常用；桥接网络见 §9.1 |
| **本机迁移连库（示例）** | `postgres://jachin:…@47.86.39.173:5432/jachin_nexus` | 需安全组放行 **5432** 或 SSH 隧道 |
| **L2（若同机 Docker）** | `http://47.86.39.173:18888` | 见 §11.1；`l2.env` 中 `BRAIN_BASE_URL` 等需指向可达地址 |

### 0.1 公网地址、`NEXUS_PUBLIC_URL` 与浏览器

- **日志里的 `0.0.0.0:3000` 不是「网站地址」**，只表示进程在容器/机器上 **监听所有网卡**。**浏览器地址栏请始终使用公网 IP 或域名**，例如 `http://47.86.39.173:3000`，**不要**输入 `http://0.0.0.0:3000`（易出现 503、跳转与登录回调异常）。
- **Docker / 生产环境**在 `docker/l1.env` 或 `deploy/l1-ecs-bundle/l1.env` 中必须配置：
  - **`NEXUS_PUBLIC_URL`**：与用户在浏览器里访问的基址一致，例如 `http://47.86.39.173:3000`（无尾部 `/`）。
  - **`AUTH_SECRET`**：生产必填，否则 Auth.js 报 `MissingSecret`。
  - **`AUTH_URL`（可选）**：与 `NEXUS_PUBLIC_URL` 一致即可，用于规范绝对 URL（见 Auth.js 文档）。
- 若已配置 `NEXUS_PUBLIC_URL`，Nexus 中间件会将 **Host 为 `0.0.0.0` / `::` 的误访请求** 重定向到 `NEXUS_PUBLIC_URL`（需使用包含该改动的镜像版本）。

模板文件：`docker/l1.env.example`、`deploy/l1-ecs-bundle/l1.env.example`。

---

## 1. 架构说明

| 组件 | 说明 |
|------|------|
| **应用** | Next.js `output: "standalone"`：`server.js` + 追踪 `node_modules` |
| **运行时** | 官方 **Node.js linux-x64** 解压至 `runtime/node/`（打包脚本自动下载嵌入） |
| **数据库** | PostgreSQL，连接串 `DATABASE_URL` |
| **日志** | `./start.sh` → `logs/`（控制台同步可见） |

**构建注意**：`npm run build` 必须在 **Linux（或 linux/amd64 容器内）** 执行，才能保证依赖为 Linux 版。不要在 Windows 上直接 `npm run build` 后把产物拷到服务器。

**发行版说明**：glibc 系发行版（Ubuntu / Debian / CentOS / RHEL 等）可直接用内置 `runtime/node`。**Alpine（musl）** 不适用该二进制，需自行换 musl 版 Node 或改用 glibc 系统。

---

## 2. 在 Windows 本机打包（服务器不放源码；产物非镜像）

适用：**只有 Windows 开发机 + Linux 服务器**，服务器上**不要**求有源码或 Docker。

Docker Desktop **仅作本机的「Linux 构建环境」**（在容器里跑 `npm run build`，产出 Linux 二进制），**不是**让你在服务器上 `docker run` 部署。

1. 安装并启动 **Docker Desktop**。
2. 仓库根目录执行：

```powershell
cd D:\path\to\jachin-system-main
.\scripts\build-l1-linux-via-docker.ps1
```

3. 本机 `dist/` 下得到 **可直接给 Linux 解压运行的目录 + `tar.gz`**（已内含 `runtime/node`）：

- `dist/jachin-l1-linux-amd64-v0.1.0/`
- `dist/jachin-l1-linux-amd64-v0.1.0.tar.gz`

4. 只上传 **`tar.gz`** 到服务器即可。

若 **完全不想在本机装 Docker**，可在 **WSL2（Ubuntu）** 里克隆仓库后执行 `./scripts/build-l1-linux-release.sh`（同样会下载嵌入 Node）。

---

## 3. 在 Linux / WSL / CI 本机打包

```bash
cd /path/to/jachin-system-main
chmod +x scripts/build-l1-linux-release.sh
./scripts/build-l1-linux-release.sh
```

产物同上。

---

## 4. 上传到服务器（示例：47.86.39.173）

```bash
scp dist/jachin-l1-linux-amd64-v*.tar.gz root@47.86.39.173:/opt/jachin/
ssh root@47.86.39.173
cd /opt/jachin
tar xzf jachin-l1-linux-amd64-v*.tar.gz
cd jachin-l1-linux-amd64-v*
```

解压后目录内已有 **`runtime/node`**，**无需**在服务器再装 Node（除非你用 `JACHIN_L1_SKIP_BUNDLE_NODE=1` 自己打了精简包）。

---

## 5. 数据库与迁移

1. 在服务器或托管 RDS 上准备 PostgreSQL，得到 `DATABASE_URL`，例如：  
   `postgres://user:pass@127.0.0.1:5432/jachin_nexus`

2. **首次部署**需执行 Drizzle 迁移。任选其一：

   - **方式 A（推荐）**：在能访问该库的机器上（可为本机），克隆仓库后执行：
     ```bash
     cd cloud/nexus
     export DATABASE_URL="postgres://..."
     npm ci
     npm run db:migrate
     npm run db:init-store
     ```
   - **方式 B**：在服务器临时克隆同版本仓库，配置 `.env.local` 后执行同上命令，再删除仓库仅保留 standalone 包。

打包目录内附带 `drizzle/` SQL 仅供参考；**仍需用项目内 `npm run db:migrate`** 以保证 journal 一致。

---

## 6. 环境变量与启动

在 **standalone 包根目录**（与 `server.js`、`start.sh` 同级）创建 `.env.production.local`：

```bash
# 最小示例
DATABASE_URL=postgres://user:pass@127.0.0.1:5432/jachin_nexus
SKIP_AUTH=true
# 生产请关闭 SKIP_AUTH，并配置 Auth / NEXUS_ADMIN_SECRET 等，见 cloud/nexus/.env.example
```

**生产（`SKIP_AUTH=false`）** 建议同时设置（与 §0.1 一致）：

```bash
NEXUS_PUBLIC_URL=http://你的公网IP或域名:3000
AUTH_SECRET=openssl-rand-base64-32-生成的值
# AUTH_URL=http://你的公网IP或域名:3000   # 可选，与上一行一致即可
```

启动（**先打日志再检查文件**，再启动 Node）：

```bash
chmod +x ./start.sh
./start.sh
```

指定端口（**默认已监听 `0.0.0.0`**，一般无需再设 `HOSTNAME`）：

```bash
PORT=3000 ./start.sh
# 仅当需要只绑本机时再设，例如：HOSTNAME=127.0.0.1 PORT=3000 ./start.sh
```

日志位置：

- `logs/l1-boot.log` — 启动阶段轨迹（含「即将检查某路径」）
- `logs/l1-YYYYMMDD.log` — 运行期 stdout/stderr（`tee`）

---

## 7. systemd（可选，开机自启）

`/etc/systemd/system/jachin-l1.service`：

```ini
[Unit]
Description=Jachin Nexus Layer 1
After=network.target postgresql.service

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/jachin/jachin-l1-linux-amd64-v0.1.0
Environment=NODE_ENV=production
Environment=PORT=3000
# 不设 HOSTNAME 时 Next standalone 仍监听 0.0.0.0；对外 URL 用 NEXUS_PUBLIC_URL
ExecStart=/opt/jachin/jachin-l1-linux-amd64-v0.1.0/start.sh
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now jachin-l1
sudo journalctl -u jachin-l1 -f
```

---

## 8. 防火墙与安全组

在云平台安全组与 `iptables`/`firewalld` 放行 **3000**（或你设置的 `PORT`）。示例：

```bash
# firewalld
sudo firewall-cmd --permanent --add-port=3000/tcp
sudo firewall-cmd --reload
```

浏览器访问：`http://47.86.39.173:3000`（生产建议 Nginx HTTPS 反代）。

---

## 9. Docker 部署 L1 + 数据库在宿主机（推荐与你当前方案一致）

**架构**：仅把 **Nexus 应用**放进容器；**PostgreSQL 用 apt/yum 装在宿主机**。容器挂了或重建，数据仍在 `/var/lib/postgresql/`，由 systemd 管的 Postgres 服务不受影响。

### 9.1 宿主机 PostgreSQL（简要）

```bash
sudo apt install -y postgresql postgresql-contrib
sudo systemctl enable --now postgresql
sudo -u postgres psql -c "CREATE USER jachin WITH PASSWORD '强密码';"
sudo -u postgres psql -c "CREATE DATABASE jachin_nexus OWNER jachin;"
```

让 **Docker 容器能连上**宿主机上的库，任选其一：

**方式 A — 桥接网络 + `host.docker.internal`（compose 默认）**

- `DATABASE_URL` 主机名写 **`host.docker.internal`**（compose 已配 `extra_hosts: host-gateway`）。
- Postgres 需监听来自 Docker 网桥的连接，例如在 `postgresql.conf`：`listen_addresses = '*'` 或至少包含宿主机在 `docker0` 上的地址。
- 在 `pg_hba.conf` 增加一行允许 Docker 网段（常见为 `172.17.0.0/16`，以 `docker network inspect bridge` 为准），例如：  
  `host all all 172.17.0.0/16 scram-sha-256`  
  然后 `sudo systemctl reload postgresql`。

**方式 B — `network_mode: host`（更简单，少配 pg_hba）**

- 容器与宿主机共用网络栈，`DATABASE_URL` 里用 **`127.0.0.1:5432`** 即可（Postgres 只监听 `127.0.0.1` 也行）。
- 使用 compose 的 **`host` profile** 启动 `nexus-host` 服务（见下）。

### 9.2 Compose（仅 L1，不含数据库）

在**仓库根目录**：

```bash
cp docker/l1.env.example docker/l1.env
# 编辑 docker/l1.env 中的 DATABASE_URL 等

docker compose -f docker/compose.l1.yml build
docker compose -f docker/compose.l1.yml up -d
```

默认启动 **`nexus`**（端口映射 `3000:3000`，连 `host.docker.internal` 上的库）。

若要用 **宿主机网络** 跑 L1：

```bash
docker compose -f docker/compose.l1.yml --profile host up -d nexus-host
```

此时请在 `l1.env` 里把 `DATABASE_URL` 改为 `...@127.0.0.1:5432/...`，并停止冲突的 `nexus` 服务。

### 9.3 仅用 docker run（不用 compose）

```bash
docker build -f docker/l1-nexus.Dockerfile -t jachin-l1:latest .
docker run -d --name jachin-l1 --restart unless-stopped \
  -p 3000:3000 \
  --add-host=host.docker.internal:host-gateway \
  --env-file docker/l1.env \
  jachin-l1:latest
```

`docker/l1.env` 从 `docker/l1.env.example` 复制；**勿将 `l1.env` 提交到 Git**（已在 `.gitignore` 中忽略）。

### 9.4 方式 B：本机构镜像，服务器不放源码（修代码只在本机）

适合：**服务器上不克隆仓库、不在线改代码**；改 bug 在本机完成后重新 `build` → `save` → 上传 → `load` → 重启容器。

**推荐：使用一键部署目录 `deploy/l1-ecs-bundle/`**（compose、`l1.env` 模板、`server-load-and-up.sh`、本机远程迁移脚本 `db-migrate-remote.ps1`；说明见同目录 `README.txt`）。其中 **`l1.env` 含数据库口令，已加入 `.gitignore`，新克隆仓库请从 `l1.env.example` 复制并填写。

**本机（仓库根目录，Windows 用 Docker Desktop 即可，镜像为 linux/amd64）：**

```bash
docker build --platform linux/amd64 -f docker/l1-nexus.Dockerfile -t jachin-l1:latest .
docker save jachin-l1:latest | gzip > jachin-l1-latest.tar.gz
# 建议将 tar.gz 放进 deploy/l1-ecs-bundle/，与 compose、l1.env 同目录，便于打包上传
```

上传到服务器（**本机 PowerShell**：`.\scripts\scp-l1-docker-artifacts-to-server.ps1`，优先上传 `deploy/l1-ecs-bundle/` 内文件；或手动 scp）：

```bash
scp deploy/l1-ecs-bundle/jachin-l1-latest.tar.gz root@47.86.39.173:/opt/jachin-l1/
scp deploy/l1-ecs-bundle/compose.l1.runtime.yml root@47.86.39.173:/opt/jachin-l1/
scp deploy/l1-ecs-bundle/l1.env root@47.86.39.173:/opt/jachin-l1/
scp deploy/l1-ecs-bundle/server-load-and-up.sh root@47.86.39.173:/opt/jachin-l1/
```

**服务器**（仅需一个目录里放镜像包 + compose + `l1.env`）：

```bash
mkdir -p /opt/jachin-l1 && cd /opt/jachin-l1
# l1.env 中 nexus-host 推荐：DATABASE_URL=postgres://jachin:postgres@127.0.0.1:5432/jachin_nexus

chmod +x server-load-and-up.sh
./server-load-and-up.sh
# 或手动：gunzip -c jachin-l1-latest.tar.gz | docker load
# docker compose -f compose.l1.runtime.yml --profile host up -d nexus-host
```

**以后发版**：本机重新 `build` + `save` + `scp`，服务器：

```bash
docker compose -f compose.l1.runtime.yml --profile host down
gunzip -c jachin-l1-latest.tar.gz | docker load
docker compose -f compose.l1.runtime.yml --profile host up -d nexus-host
```

**数据库迁移**：仍在**本机**（或任意能访问服务器 Postgres 的机器）用仓库里 `cloud/nexus` 执行 `npm run db:migrate`（需安全组/防火墙放行 **5432** 或 SSH 隧道）。示例（PowerShell，IP 已写死为当前 ECS）：

```powershell
cd <仓库根目录>\cloud\nexus
$env:DATABASE_URL="postgres://jachin:postgres@47.86.39.173:5432/jachin_nexus"
npm ci
npm run db:migrate
npm run db:init-store
```

详见 **§5**。

### 9.5 迁移（与方式 A/B 通用）

数据库在宿主机上，仍在能访问 `DATABASE_URL` 的机器执行 `cloud/nexus` 下的 `npm run db:migrate` / `db:init-store`。详见上文 **§5**。

---

## 10. 便携包与 Docker 二选一说明

- **tar.gz 便携目录**：不装 Docker，解压 `./start.sh`。
- **Docker 镜像**：只容器化 L1，**数据库仍建议宿主机原生安装**（本节）。

---

## 11. 与仓库脚本对应关系

| 脚本 | 用途 |
|------|------|
| `scripts/build-l1-linux-via-docker.ps1` | **Windows 本机**：Docker linux/amd64 容器内构建，产出 `dist/*.tar.gz` |
| `scripts/build-l1-linux-release.sh` | Linux/WSL/容器内：生成 standalone 目录 + tar.gz |
| `scripts/docker-l1-inner-build.sh` | 供 Docker 入口调用（`exec` 打包脚本） |
| `scripts/packaging/l1-linux/start.sh` | 拷贝进包内的生产启动脚本（日志优先） |
| `docker/compose.l1.yml` | 含 `build`；在**有源码**的机器上构建并启动 |
| `docker/compose.l1.runtime.yml` | **仅 image**，服务器方式 B：`docker load` 后用此文件启动 |
| `docker/l1.env.example` | 复制为 `docker/l1.env` 供 compose / docker run |
| `deploy/l1-ecs-bundle/` | ECS 方式 B 一键目录：`compose`、`l1.env`（勿提交）、`server-load-and-up.sh`、`db-migrate-remote.ps1`、`README.txt` |
| `docker/l1-nexus.Dockerfile` | 多阶段构建 Linux 镜像 |
| `scripts/scp-l1-docker-artifacts-to-server.ps1` | 本机 scp：优先 `deploy/l1-ecs-bundle/`，否则 `docker/`；镜像包支持 bundle 或仓库根 |
| `deploy/Dockerfile.l2` | L2 FastAPI 镜像（`jachin-l2:latest`） |
| `docker/compose.l2.runtime.yml` | 服务器：**单节点 L2 + Redis**，入口 **18888**（与 `deploy/l2-ecs-bundle/` 同步维护） |
| `scripts/scp-l2-docker-artifacts-to-server.ps1` | 上传 L2 镜像 + compose + `l2.env` 到 **`/opt/jachin-l2`** |
| `scripts/start-cloud.sh` | 开发模式（`npm run dev`），非生产包 |

### 11.1 同机部署 L2（Docker，与 L1 nexus-host 搭配）

L1 使用 **`nexus-host`（宿主机网络、3000）** 时，L2 容器在 bridge 网络内需通过 **`host.docker.internal:3000`** 访问 Nexus（`docker/compose.l2.runtime.yml` 已加 `extra_hosts`）。

1. **本机构建**（仓库根）：`docker build --platform linux/amd64 -f deploy/Dockerfile.l2 -t jachin-l2:latest .`  
2. **导出**：`docker save jachin-l2:latest -o jachin-l2-latest.tar`（勿再套一层外层 tar）  
3. **上传**：`.\scripts\scp-l2-docker-artifacts-to-server.ps1` 或手动放到服务器 **`/opt/jachin-l2/`**  
4. **服务器**：`docker load -i jachin-l2-latest.tar`，`docker compose -f compose.l2.runtime.yml pull redis`，`docker compose -f compose.l2.runtime.yml up -d`（或 `deploy/l2-ecs-bundle/server-l2-up.sh`）  
5. **安全组**：放行 **TCP 18888**；`l2.env` 中 **`BRAIN_BASE_URL`** 改为公网可访问的 L2 地址（如 `http://47.86.39.173:18888`）。  

L3 默认通过 L1 Profile 直连当前 L1 地址；L2 仅作为可选企业桥接扩展，不再是 L1 部署的必需步骤。

多副本负载均衡仍用仓库根 **`docker-compose.l2-cluster.yml`**（3×L2 + Nginx + Redis）。

---

## 12. 故障排查

1. **启动无样式**：确认已复制 `.next/static` 到包内 `.next/static`（打包脚本已处理）。
2. **数据库连接失败**：检查 `DATABASE_URL`、PostgreSQL 监听、`pg_hba.conf`、安全组。
3. **先看日志**：`logs/l1-boot.log` 是否已打印「即将检查 .env…」；若无，说明 `start.sh` 未执行或权限不足。
4. **本机 `docker build` 拉 `node` 超时 / `dockerproxy.com` TLS 失败**：Docker Desktop → **Settings → Docker Engine**，检查 `registry-mirrors`；去掉不可用源或改用可用镜像。也可不显式配镜像，直接用构建参数：  
   `docker build --build-arg NODE_IMAGE=docker.m.daocloud.io/library/node:20-bookworm-slim ...`（见 `docker/l1-nexus.Dockerfile` 头部注释）。

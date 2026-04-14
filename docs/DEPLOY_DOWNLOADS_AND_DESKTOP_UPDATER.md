# 桌面端下载站与热更新（OTA）部署指南

本文面向运维与后端同事，说明 **下载网站**、**Tauri 热更新** 的架构、依赖、环境变量与上线步骤，便于在测试/生产环境一次性部署成功。

---

## 1. 架构概览

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         PostgreSQL（单一事实源）                          │
│  表：desktop_app_releases（版本、notes、pub_date、artifacts JSONB）        │
└─────────────────────────────────────────────────────────────────────────┘
                    ▲ 读写                          ▲ 只读列表 / 管理登记
                    │                               │
┌───────────────────┴──────────────┐   ┌────────────┴──────────────────────┐
│  Jachin Nexus（Layer 1）          │   │  jachin-downloads（可选独立站）    │
│  • /desktop-downloads 登录后下载   │   │  默认端口 3001，与 Nexus 共用 DB    │
│  • GET /api/v1/update/desktop     │   │  同表、同 MinIO、同热更新 API 语义   │
│  • POST /api/v1/admin/desktop-*   │   │  适合「下载站单独域名/进程」场景      │
└───────────────────┬──────────────┘   └──────────────────────────────────┘
                    │
                    │ 预签名 GET（私有桶内对象）
                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  MinIO 或 AWS S3 兼容存储（对象存储，不是数据库）                           │
│  Bucket：安装包 .exe/.msi + 路径需含版本号段，供预签名 URL 下载            │
└─────────────────────────────────────────────────────────────────────────┘
                    ▲ HTTPS + Bearer
                    │
┌───────────────────┴───────────────────────────────────────────────────┐
│  Jachin Desktop（Tauri）                                               │
│  • tauri-plugin-updater：轮询更新 JSON（含 signature + 预签名 url）       │
│  • 可选：jachin-updater-helper 下载/校验/ staged 替换（见客户端 README）   │
│  • 本地 ~/.jachin/nexus_config.json → desktop_update_token 与 DESKTOP_* 对齐│
└─────────────────────────────────────────────────────────────────────────┘
```

**要点：**

- **关系型数据库只需一个：PostgreSQL**。桌面发行元数据存在表 `desktop_app_releases`，**不要求**单独再建 MySQL/Redis 才能跑下载或热更新（除非你们其它业务本来就要用）。
- **MinIO/S3 是对象存储**，用于存放安装包二进制与生成**限时预签名下载链接**，不是 SQL 数据库。

---

## 2. 下载网站有两种部署形态

| 形态 | 路径/入口 | 说明 |
|------|-----------|------|
| **A. Nexus 内置** | `https://<nexus域名>/desktop-downloads` | 与 L1 控制台同一 Next 应用；需登录（Auth.js）。推荐与现有 Nexus 一起部署。 |
| **B. 独立站 jachin-downloads** | 默认 `http://localhost:3001`（`next dev/start -p 3001`） | 独立 Next 应用，**与 Nexus 共用同一 `DATABASE_URL` 与同一套 `DESKTOP_RELEASES_S3_*`**，数据一致。适合下载站单独域名、单独扩缩容。 |

两者读写的都是 **同一张表** `desktop_app_releases`，任选其一或同时开（注意 OAuth/AUTH_SECRET 与回调 URL 配置）。

---

## 3. 必须先准备的前置条件

### 3.1 PostgreSQL

- **版本**：建议 14+（仓库 `docker-compose.postgres.yml` 使用 16-alpine）。
- **仅需一个库/一个连接串**：例如 `postgres://postgres:postgres@localhost:5432/postgres`。
- **表结构**：在 `cloud/nexus` 下用 Drizzle 推表（与团队现有流程一致）：
  - `npm run db:push`  
  - 或按项目迁移流程执行；确保存在表 **`desktop_app_releases`**（定义见 `cloud/nexus/src/db/schema.ts`）。

> **注意**：`docker compose down -v` 会删掉 Postgres 数据卷，**发行记录会清空**，勿在生产随意执行。

### 3.2 对象存储 MinIO（或 S3）

热更新与「生成下载链接」都依赖 **私有桶 + Access Key**，未配置时：

- 下载页可能能看到版本列表，但 **无法生成预签名链接**；
- 热更新接口对 Tauri 会返回 **204 No Content**（等价于暂无可用 OTA，避免客户端刷 503）。

需要准备：

| 变量 | 含义 |
|------|------|
| `DESKTOP_RELEASES_S3_ENDPOINT` | 如 `http://127.0.0.1:9000`（MinIO）或 AWS 区域端点 |
| `DESKTOP_RELEASES_S3_REGION` | 如 `us-east-1` |
| `DESKTOP_RELEASES_S3_BUCKET` | 桶名，如 `jachin-desktop-releases` |
| `DESKTOP_RELEASES_S3_ACCESS_KEY` / `SECRET_KEY` | 有读权限即可（预签名 GET） |
| `DESKTOP_RELEASES_S3_FORCE_PATH_STYLE` | MinIO 一般为 `true`；原生 AWS 常为 `false` |

在 MinIO 控制台创建 **Bucket**，并保证发布脚本上传的对象路径与登记到 DB 的 `objectKey` 一致（发布脚本会校验路径中含 **版本号目录段**）。

### 3.3 密钥与鉴权

- **`AUTH_SECRET`**（NextAuth）：Nexus 与 jachin-downloads **生产必须设置**（开发与文档说明可能允许占位，生产勿用默认值）。
- **`NEXUS_ADMIN_SECRET`**：登记新版本 `POST /api/v1/admin/desktop-releases` 时使用。服务端校验见 `cloud/nexus/src/lib/admin-auth.ts`：请求头 **`X-Admin-Token`**、**`Authorization: Bearer <secret>`**，或 Cookie **`nexus_admin_token=<secret>`** 三者之一与 `NEXUS_ADMIN_SECRET` 完全一致即视为 root。`scripts/publish_desktop_release.py` 使用的 **`NEXUS_ADMIN_SECRET`** 须与此相同。
- **`DESKTOP_UPDATE_BEARER`**（可选但生产强烈建议）：与桌面端 **`~/.jachin/nexus_config.json` → `desktop_update_token`** 一致，用于 `GET /api/v1/update/desktop` 的 **Bearer**。  
  - 另一种合法身份是 **edge_agents** 表中的 Bearer（舰队场景）；普通桌面用户用共享 secret 即可。

### 3.4 构建与签名（发布安装包时）

- Windows 安装包需 **minisign** 签名，公钥写入 `clients/desktop/src-tauri/tauri.conf.json` → `plugins.updater.pubkey`。
- 一键发布：`python scripts/publish_desktop_release.py`（仓库根），依赖 `NEXUS_BASE_URL`、`NEXUS_ADMIN_SECRET`、全套 `DESKTOP_RELEASES_S3_*`。详见脚本文件头注释。

---

## 4. 热更新如何实现（给非客户端同事的版本）

1. **登记**：发布脚本把构建产物上传到 S3，并调用 Nexus **Admin API** 写入 `desktop_app_releases`（每平台 `artifacts[platformKey] = { objectKey, signature }`）。
2. **检查更新**：桌面端 Tauri Updater 请求  
   `GET /api/v1/update/desktop?target=...&arch=...&current_version=...`  
   请求头：`Authorization: Bearer <desktop_update_token 或 edge token>`。
3. **服务端逻辑**（`cloud/nexus/src/app/api/v1/update/desktop/route.ts`）：
   - 校验 Bearer；
   - 从 DB 取最新 semver **大于** 当前版本的记录；
   - 按平台从 `artifacts` 取 `objectKey`、`signature`；
   - 对 `objectKey` 做 **S3 预签名 GET**，把 `url` + Base64 格式的 `signature` 填入 Tauri 期望的 JSON；
   - 无新版本返回 **204**；桶未配置也 **204**（避免开发环境刷错误）。
4. **客户端**：下载后用 **minisign 公钥**（`tauri.conf.json`）校验安装包；可选 **jachin-updater-helper** 负责下载落盘与用户确认后重启替换。

---

## 5. 开启「下载站」— 分步清单

### 5.1 仅使用 Nexus 内置 `/desktop-downloads`

1. 启动 **PostgreSQL**，配置 `cloud/nexus/.env.local` 中 **`DATABASE_URL`**。
2. 执行 **`npm run db:push`**（在 `cloud/nexus`）。
3. 配置 **`DESKTOP_RELEASES_S3_*`** 与 **`AUTH_SECRET`**。
4. 可选：配置 **`NEXT_PUBLIC_DESKTOP_DOWNLOAD_HALL_URL`**（若要从 Nexus 顶栏跳到**独立下载站**）；若只用内置页可留空。
5. 启动 Nexus：`npm run dev` 或 `npm run build && npm start`。
6. 用户访问 **`/login`** 登录后访问 **`/desktop-downloads`**。
7. 若列表为空：先通过 **`publish_desktop_release.py`** 或 Admin API 写入至少一条 `desktop_app_releases`。

### 5.2 独立部署 jachin-downloads（端口 3001）

1. 使用 **与 Nexus 相同** 的 `DATABASE_URL`（同一 Postgres 实例即可）。
2. 复制 `cloud/jachin-downloads/.env.example` → `.env.local`，填写：
   - `DATABASE_URL`
   - `AUTH_SECRET`（可与 Nexus 相同便于运维）
   - 全套 `DESKTOP_RELEASES_S3_*`
   - 生产设置 **`JACHIN_DOWNLOADS_DEV_LOGIN_BYPASS=0`**，关闭开发态任意密码登录。
   - 若用 GitHub OAuth：配置 `AUTH_GITHUB_*`，并在 GitHub OAuth App 中增加回调 `https://<下载站域名>/api/auth/callback/github`。
   - `NEXTAUTH_URL` 设为下载站对外 URL。
3. `cd cloud/jachin-downloads && npm ci && npm run build && npm start`（或 PM2/systemd）。
4. 测试账号：可用 `npm run seed:test-user`（见该目录 `.env.example` 说明），**生产勿依赖**。
5. 热更新 API：独立站同样提供 **`/api/v1/update/desktop`**，客户端把 `tauri.conf.json` 里 **endpoints** 改成下载站或 Nexus 的公网地址即可。

---

## 6. 环境变量速查（Nexus）

完整注释见 **`cloud/nexus/.env.example`**，与下载/热更新强相关：

```env
DATABASE_URL=postgresql://...

AUTH_SECRET=...

# MinIO / S3
DESKTOP_RELEASES_S3_ENDPOINT=http://127.0.0.1:9000
DESKTOP_RELEASES_S3_REGION=us-east-1
DESKTOP_RELEASES_S3_BUCKET=jachin-desktop-releases
DESKTOP_RELEASES_S3_ACCESS_KEY=
DESKTOP_RELEASES_S3_SECRET_KEY=
DESKTOP_RELEASES_S3_FORCE_PATH_STYLE=true

# 桌面热更新 Bearer（与客户端 nexus_config desktop_update_token 一致）
DESKTOP_UPDATE_BEARER=

# 管理端登记发行
NEXUS_ADMIN_SECRET=
```

jachin-downloads 额外见 **`cloud/jachin-downloads/.env.example`**（`JACHIN_DOWNLOADS_DEV_LOGIN_BYPASS`、`NEXTAUTH_URL` 等）。

---

## 7. 客户端（桌面）侧配置（部署验收用）

- **`clients/desktop/src-tauri/tauri.conf.json`**  
  - `plugins.updater.endpoints`：指向线上 **`.../api/v1/update/desktop?target={{target}}&arch={{arch}}&current_version={{current_version}}`**。  
  - 生产必须 **HTTPS**；开发可用 `dangerousInsecureTransportProtocol`（仅本地）。
- **用户机器**：`~/.jachin/nexus_config.json` 中 **`desktop_update_token`** 与 **`DESKTOP_UPDATE_BEARER`** 一致（或由运维下发 edge token）。

---

## 8. 发布一条新版本（运维/发版同学）

1. 构建 Tauri 安装包并完成 **minisign**（见 `scripts/publish_desktop_release.py` 头注释）。
2. 在仓库根执行发布脚本（自动上传 S3 + 调 Admin API）：  
   `python scripts/publish_desktop_release.py`  
   或 `clients/desktop` 下 `npm run publish-desktop-release`。
3. 确认 Nexus（或独立站）`/desktop-downloads` 能看到新版本；桌面端触发检查更新后能拉到 JSON。

---

## 9. 常见问题（排障）

| 现象 | 可能原因 |
|------|----------|
| 下载页无版本 / 空 | `DATABASE_URL` 未生效、未 `db:push`、或库被 `down -v` 清空 |
| 有版本但点下载 503/无链接 | 未配置 `DESKTOP_RELEASES_S3_*` 或 AK/SK 无桶权限 |
| 热更新一直无反应（204） | 当前已是最新；或桶未配置；或 DB 无对应平台 `artifacts` |
| 热更新 401 | Bearer 与 `DESKTOP_UPDATE_BEARER` / edge token 不一致 |
| 校验签名失败 | 安装包与 `.sig` 不对应、或 pubkey 与签名私钥不成对 |

---

## 10. 部署检查清单（可复制到工单）

- [ ] PostgreSQL 已启动，`DATABASE_URL` 可达  
- [ ] 已执行 schema 同步，存在 `desktop_app_releases`  
- [ ] MinIO/S3 Bucket 已创建，`DESKTOP_RELEASES_S3_*` 已配置  
- [ ] `AUTH_SECRET`、`NEXUS_ADMIN_SECRET`、`DESKTOP_UPDATE_BEARER` 已设（生产）  
- [ ] Nexus 或 jachin-downloads 已构建并监听正确端口  
- [ ] 至少一次成功执行 `publish_desktop_release.py` 或 Admin 登记  
- [ ] 桌面 `tauri.conf.json` endpoints 与 `desktop_update_token` 与线上一致  
- [ ] HTTPS、防火墙、反向代理已放行 `/api/v1/update/desktop` 与登录回调  

---

## 11. 相关代码路径（供开发二次排查）

| 模块 | 路径 |
|------|------|
| 表定义 | `cloud/nexus/src/db/schema.ts` → `desktopAppReleases` |
| 热更新 API | `cloud/nexus/src/app/api/v1/update/desktop/route.ts` |
| 管理登记 | `cloud/nexus/src/app/api/v1/admin/desktop-releases/route.ts` |
| S3 预签名 | `cloud/nexus/src/lib/desktop-releases-s3.ts` |
| 签名/平台键 | `cloud/nexus/src/lib/desktop-releases-common.ts` |
| Nexus 下载页 | `cloud/nexus/src/app/desktop-downloads/page.tsx` |
| 独立下载站首页 | `cloud/jachin-downloads/src/app/page.tsx` |
| 发布脚本 | `scripts/publish_desktop_release.py` |
| 客户端更新横幅 | `clients/desktop/src/components/DesktopUpdateBanner.tsx` |

---

**文档版本**：与仓库 `jachin-system-main` 当前实现一致；若接口路径或环境变量名变更，请以 `.env.example` 与源码为准并更新本节。

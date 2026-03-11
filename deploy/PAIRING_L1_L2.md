# L1 与 L2 分机部署时如何配对

当 L1 和 L2 部署在不同机器上时，L2 需要与 L1 配对才能获取授权、同步技能等。

## 一、deploy-bundle-l2 与 start-layer2 选 [3] Gateway 的关系

**deploy-bundle-l2 的 `启动.ps1` 等价于 `start-layer2.ps1` 选 [3] Gateway：**

- 均启动 `core.main`（FastAPI 18888）
- 提供武库大盘 `/admin/`、审批 L3 `/gateway/`
- `JACHIN_L2_ADMIN_TOKEN` 会在首次启动时自动生成并写入 `.env`

无需在 deploy-bundle 中再执行 start-layer2，直接运行 `.\启动.ps1` 即可。

## 二、配对方式：run-pair.ps1

**run-pair.ps1 需要 Python 和项目源码**，无法在无代码的 deploy-bundle 内直接执行。

### 推荐流程（deploy-bundle-l2 分机部署）

1. **在有源码的机器上**（开发机或任意已安装 Python 的机器）执行配对：
   ```powershell
   cd 项目根目录
   .\scripts\run-pair.ps1 -BaseUrl http://L1机器IP:3000
   ```
2. 配对成功后，CLI 会输出 `instance_id`、`access_token`、`l1_user_id`，并写入 `~/.jachin/nexus_config.json`
3. **将凭证填入 L2 部署包的 .env**（在 deploy-bundle-l2 目录）：
   ```
   NEXUS_BASE_URL=http://L1机器IP:3000
   NEXUS_INSTANCE_ID=<配对输出的 instance_id>
   NEXUS_ACCESS_TOKEN=<配对输出的 access_token>
   NEXUS_L1_USER_ID=<配对输出的 l1_user_id>
   ```
4. 在 L2 机器上执行 `.\启动.ps1`，Docker 会通过环境变量将凭证传入容器

### 若 L2 机器有 Python 和源码

可直接在 L2 机器上执行 `.\scripts\run-pair.ps1`，但凭证会写入 `~/.jachin/nexus_config.json`，**Docker 容器无法读取**。仍需将 `instance_id`、`access_token`、`l1_user_id` 填入 deploy-bundle-l2 的 `.env`，供 docker-compose 透传。

## 三、配置 L2 指向 L1

在 L2 所在机器上，启动前设置 `NEXUS_BASE_URL` 为 L1 的访问地址：

**PowerShell:**
```powershell
$env:NEXUS_BASE_URL="http://L1机器IP:3000"
.\启动.ps1
```

**或持久化：** 在 `deploy-bundle-l2` 目录创建 `.env` 文件：
```
NEXUS_BASE_URL=http://L1机器IP:3000
```

## 四、配对流程（6 位码）

1. **L1 已启动**：访问 http://L1机器IP:3000
2. **L2 已启动**：访问 http://L2机器IP:18888
3. **执行 run-pair.ps1**：获取 6 位配对码
4. **在 L1 控制台输入**：在 L1 控制台（如 http://L1机器IP:3000/pair）输入该 6 位码
5. **配对完成**：CLI 输出凭证，将其填入 L2 的 `.env`

## 五、网络要求

- L2 机器必须能访问 L1 的 3000 端口
- L3/桌面端必须能访问 L2 的 18888 端口
- 若跨公网，需确保防火墙/安全组放行

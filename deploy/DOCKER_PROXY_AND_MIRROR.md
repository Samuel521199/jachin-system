# Docker 代理与国内镜像配置

## 一、代理配置（端口 8800）

### 方式 A：Docker Desktop 图形界面（推荐）

1. 打开 **Docker Desktop**
2. 点击右上角 **Settings**（齿轮图标）
3. 左侧选择 **Resources** → **Proxies**
4. 勾选 **Manual proxy configuration**
5. 填写：
   - **Web Server (HTTP):** `http://127.0.0.1:8800`
   - **Secure Web Server (HTTPS):** `http://127.0.0.1:8800`
6. 若有账号密码，在下方填写
7. 点击 **Apply & Restart**

### 方式 B：环境变量（系统级）

以管理员身份打开 PowerShell，执行：

```powershell
[System.Environment]::SetEnvironmentVariable("HTTP_PROXY", "http://127.0.0.1:8800", [System.EnvironmentVariableTarget]::Machine)
[System.Environment]::SetEnvironmentVariable("HTTPS_PROXY", "http://127.0.0.1:8800", [System.EnvironmentVariableTarget]::Machine)
[System.Environment]::SetEnvironmentVariable("NO_PROXY", "localhost,127.0.0.1", [System.EnvironmentVariableTarget]::Machine)
```

执行后**重启 Docker Desktop**。

---

## 二、国内镜像源配置

若代理不稳定，可改用国内镜像加速拉取。

### Docker Desktop 配置

1. 打开 **Docker Desktop** → **Settings** → **Docker Engine**
2. 在 JSON 中增加或修改 `registry-mirrors`：

```json
{
  "builder": {
    "gc": {
      "defaultKeepStorage": "20GB",
      "enabled": true
    }
  },
  "experimental": false,
  "registry-mirrors": [
    "https://docker.1ms.run",
    "https://docker.xuanyuan.me",
    "https://docker.rainbond.cc"
  ]
}
```

3. 点击 **Apply & Restart**

### 移除 dockerproxy.com（若 TLS handshake timeout）

Docker Engine 中若配置了 dockerproxy.com 且超时，请删除该镜像源，仅保留其他可用源。

### 常用国内镜像地址

| 镜像源 | 地址 |
|--------|------|
| 1ms（Dockerfile 已使用） | https://docker.1ms.run |
| 玄原 | https://docker.xuanyuan.me |
| Rainbond | https://docker.rainbond.cc |
| 阿里云（需登录获取） | https://xxx.mirror.aliyuncs.com |
| 腾讯云 | https://mirror.ccs.tencentyun.com |

---

## 三、代理 + 镜像同时使用

可先配置代理，再配置镜像。拉取失败时 Docker 会尝试镜像源。

---

## 四、代理导致构建失败时（TLS connect to 127.0.0.1:8800: EOF）

代理 8800 会导致所有镜像拉取失败，需**先移除代理**再构建：

**步骤 1：移除环境变量代理**
```powershell
.\deploy\unset-docker-proxy.ps1
```

**步骤 2：关闭 Docker Desktop 代理**
- Docker Desktop → Settings → Resources → Proxies
- 取消勾选 Manual proxy configuration
- Apply & Restart

**步骤 3：配置国内镜像源**（见上文第二节），然后执行 `.\deploy\pack.ps1`

---

## 五、验证配置

```powershell
# 测试拉取（会走 registry-mirrors）
docker pull node:20-alpine
docker pull python:3.11-slim
```

若成功，再执行 `.\deploy\pack.ps1` 构建部署包。

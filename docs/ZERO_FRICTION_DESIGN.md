# 零摩擦体验设计 — Zero-Friction & Out-of-the-Box

**版本**: 1.0  
**创建日期**: 2026-02-28  
**定位**: 从「极客玩具」到「魔法师与企业家买单」的体验降维打击  
**原则**: CLI 征服前 100 个极客，零摩擦征服成千上万的魔法师与传统企业主

---

## 设计哲学

> **任何需要用户手动修改 .json 配置文件或敲击冗长命令的设计，都是反人性的。**

- **零摩擦 (Zero-Friction)**：用户无需理解 API Base URL、Token、公钥
- **开箱即用 (Out-of-the-Box)**：通电/开机即完成配置，无需人工干预（企业场景）
- **隐形化 (Invisible)**：`nexus_config.json` 对用户绝对隐藏，配置由云端推送

---

## 一、Layer 1 云端：彻底消灭密码 + 数字孪生大盘

### 1.1 极简免密登录 (Passwordless Onboarding)

**摒弃**：传统账号密码注册（太老派）

**界面**：极简赛博风页面，唯一输入框：

```
┌─────────────────────────────────────────────────────────┐
│  输入邮箱，获取魔法链接 (Magic Link)                      │
│  ┌─────────────────────────────────────────────────────┐ │
│  │ your@email.com                                      │ │
│  └─────────────────────────────────────────────────────┘ │
│                                                          │
│  或                                                       │
│  [ 通过 GitHub 登入 ]  [ 通过 Google 登入 ]              │
│                                                          │
│  [ 发送魔法链接 ]                                        │
└─────────────────────────────────────────────────────────┘
```

**体验**：
- 用户无需记住密码
- 点击邮箱里的链接，瞬间进入 The Console
- 技术栈：Supabase Auth Magic Link / OAuth

**实现要点**：
- 复用 `auth.users`，无需新建用户表
- 登录态持久化：`nexus_users` 与 `auth.users` 映射
- 首次登录自动创建 `nexus_users` 记录

---

### 1.2 数字孪生控制台 (Digital Twin Dashboard)

**现状痛点**：配对后，用户不知道设备在干嘛。

**傻瓜化设计**：

| 元素 | 形态 | 说明 |
|------|------|------|
| **设备卡片** | 网格 / 3D 节点 | 每个已配对的边缘智能体一张卡片 |
| **在线状态** | 绿色呼吸灯 | 心跳正常时显示 |
| **设备信息** | 卡片内展示 | CPU / RAM、版本、已装载插件 |

**意图驱动 (Intent-based Control)**：

| 操作 | 用户行为 | 系统行为 |
|------|----------|----------|
| **部署蓝图** | 在商城买了「傲娇女仆蓝图」，拖拽到设备卡片上 | 云端通过 WebSocket/MQTT 向边缘端下发热更新指令 |
| **切换形态** | 拖拽完成 | 边缘智能体瞬间切换形态，无需敲命令 |
| **卸载** | 从卡片拖出或点击卸载 | 云端下发卸载指令 |

**技术要点**：
- WebSocket 长连接：`/api/v1/ws/edge` 或 MQTT
- 拖拽目标：设备卡片 `data-drop-target="instance_id"`
- 部署指令：复用 `deploy_commands` + `poll`，或实时推送

---

## 二、Layer 2/3 边缘端：三种傻瓜化配对协议

### 2.1 方案 A：桌面客户端 (Layer 3) — 扫码即连

**适用**：Windows / Mac / Linux 桌面级应用

**展现形式**：
- 用户打开 Jachin Nexus 桌面端
- 屏幕中央弹出 **动态二维码**（附带 6 位配对码备用）

**操作流**：
1. 用户拿起手机（已登录 Layer 1 控制台）
2. 打开摄像头扫码
3. 手机端弹出：「是否将此设备接入星图？” 点击确认
4. 桌面端瞬间亮起绿灯，完成配置下发

**参考**：微信 PC 端、Apple TV 配对

**技术要点**：
- 二维码内容：`https://nexus.jachin/console/pair?session_id=xxx` 或短链
- 手机端：扫码后跳转 `/console/pair?session_id=xxx`，自动预填并确认
- 桌面端：轮询 `pairing/status` 或 WebSocket 收到推送

---

### 2.2 方案 B：无头物联网设备 — 智能家居式配对

**适用**：Raspberry Pi、旧手机、无屏幕的纯 Layer 2 设备

**展现形式**：
- 边缘智能体首次开机时，自动释放一个名为 **Jachin-Nexus-Setup** 的 Wi-Fi 热点（Captive Portal）

**操作流**：
1. 用户用手机连上 `Jachin-Nexus-Setup` Wi-Fi
2. 手机自动弹出极简本地网页（类似配置智能灯泡）
3. 用户在网页输入：家里 Wi-Fi 密码 + Layer 1 账号邮箱
4. 设备自动联网、向云端注册并完成配对
5. 设备关闭热点，隐身运行

**技术要点**：
- 热点：`hostapd` + `dnsmasq`（Linux）或 Android `WifiManager.startLocalOnlyHotspot`
- Captive Portal：`iptables` 重定向 HTTP 到本地 80 端口
- 本地网页：静态 HTML + 表单提交到设备本地 API
- 设备侧：收到 Wi-Fi 凭据后，连接网络，调用 `POST /api/v1/pairing/request`，等待用户邮箱确认

**安全**：热点仅用于配置，不暴露任何外部网络；配对完成后立即关闭

---

### 2.3 方案 C：企业 B 端 — 零触控部署 (ZTP)

**适用**：医疗机构、酒店等需一次性部署 500 台边缘智能体的场景

**展现形式**：
- 企业根本不需要配对
- 出厂或打包镜像时，提前将 **Enterprise ID** 烧录进系统镜像

**操作流**：
1. 酒店服务员只需把设备插上电源和网线
2. 设备通电开机后，自动带着 MAC 地址去云端报到
3. 云端根据 `enterprise_id` 自动挂载到该酒店的「舰队指挥大屏」
4. 全程无需人工干预

**技术要点**：
- 镜像预置：`/etc/jachin/enterprise_id` 或环境变量 `JACHIN_ENTERPRISE_ID`
- 设备启动：`POST /api/v1/ztp/register`，携带 `enterprise_id`、`mac_address`、`device_fingerprint`
- 云端：校验 `enterprise_id` 有效性，自动创建 `layer2_instances`，下发 `access_token`
- 可选：企业管理员在 Console 预置「设备白名单」（MAC 列表）

**ZTP API 契约**（待实装）：

```
POST /api/v1/ztp/register
Body: {
  "enterprise_id": "uuid",
  "mac_address": "AA:BB:CC:DD:EE:FF",
  "device_fingerprint": "sha256:...",
  "core_version": "1.0.0"
}
Response 200: {
  "instance_id": "jachin-xxx",
  "access_token": "jch-...",
  "nexus_base_url": "https://..."
}
```

---

## 三、配置管理的隐形化

### 3.1 告别本地改配置

| 原则 | 说明 |
|------|------|
| **用户不可见** | `nexus_config.json` 对用户绝对隐藏，不暴露在设置界面 |
| **用户不可编辑** | 不提供「API Base URL」「Token」等输入框 |
| **云端为源** | 所有配置由云端下发，边缘端只读 |

### 3.2 自愈与热更新

| 机制 | 说明 |
|------|------|
| **状态同步** | nexus_daemon 定期与云端进行状态同步（心跳扩展） |
| **配置推送** | 云端发现配置有变，通过加密隧道将新配置全量推送 |
| **热重载** | 边缘端在内存中热重载，旧进程优雅退出，新进程无缝接管 |
| **用户体验** | 无服务中断感 |

**技术要点**：
- 心跳响应扩展：`GET /api/v1/instances/heartbeat` 返回 `config_version`，若与本地不一致则拉取全量配置
- 或：WebSocket 推送 `config_update` 事件
- 热重载：`nexus_daemon` 收到新配置后，fork 新进程加载新配置，旧进程退出

---

## 四、实施路线图

### Phase 1：Layer 1 免密登录 + 数字孪生大盘（P1）

| 任务 | 优先级 | 依赖 |
|------|--------|------|
| Supabase Auth Magic Link / OAuth 集成 | P1 | auth.users |
| 登录页 + 受保护路由 | P1 | - |
| 数字孪生大盘：设备卡片网格化 | P1 | layer2_instances |
| 拖拽部署蓝图到设备 | P1+ | deploy_commands、WebSocket |

### Phase 2：配对协议升级（P1）

| 任务 | 优先级 | 依赖 |
|------|--------|------|
| 方案 A：扫码即连（Layer 3 桌面端） | P1 | 配对 API、二维码生成 |
| 方案 B：Wi-Fi 热点 + Captive Portal | P1+ | 嵌入式/Linux 设备 |
| 方案 C：ZTP 预烧录 + 自动注册 | P1+ | enterprise 表、ztp API |

### Phase 3：配置隐形化 + 热更新（P1）

| 任务 | 优先级 | 依赖 |
|------|--------|------|
| 心跳返回 config_version | P1 | heartbeat API |
| 配置拉取 API | P1 | - |
| 边缘端热重载逻辑 | P1 | nexus_daemon |

---

## 五、与现有文档的衔接

| 文档 | 衔接点 |
|------|--------|
| [PAIRING_PROTOCOL_SPEC.md](./PAIRING_PROTOCOL_SPEC.md) | 6 位码为方案 A 的备用；方案 B/C 为扩展协议 |
| [INVISIBLE_SECURITY_UX.md](./INVISIBLE_SECURITY_UX.md) | 权限大白话、一键授权，与本设计互补 |
| [MICROKERNEL_ECOSYSTEM_BATTLE_PLAN.md](./MICROKERNEL_ECOSYSTEM_BATTLE_PLAN.md) | Phase 1–3 对应 P1 级战役 |
| [P0_TRUST_AND_HEARTBEAT_SPEC.md](./P0_TRUST_AND_HEARTBEAT_SPEC.md) | 心跳扩展 config_version |

---

**相关文档**:
- [ECOSYSTEM_AND_COMMERCIALIZATION_WHITEPAPER.md](./ECOSYSTEM_AND_COMMERCIALIZATION_WHITEPAPER.md) - 生态与商业化白皮书
- [LAYER1_ARCHITECTURE_AND_DESIGN.md](./LAYER1_ARCHITECTURE_AND_DESIGN.md) - Layer 1 架构总览

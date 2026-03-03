# 战役 C —— 扫码即连桌面端 UI

**版本**: 1.0  
**创建日期**: 2026-03-02  
**定位**: 用 Electron/Tauri 打造极其优雅的「扫码即连」桌面端，把硬核技术彻底隐藏在极简体验之下

---

## 一、设计目标

> **参考**：微信 PC 端、Apple TV 配对 —— 用户无需理解 CLI、6 位码、API、配置文件。

| 现状 | 目标 |
|------|------|
| 极客在终端跑 `python -m core.cli pair`，复制 6 位码，打开浏览器输入 | 桌面端打开 → 屏幕中央动态二维码 → 手机一扫 → 瞬间完成 |
| 配置写入 `~/.jachin/nexus_config.json`，用户不可见 | 同上，完全隐形 |
| Layer 2 daemon 与 Layer 3 桌面端分离 | 桌面端可触发/代理 Layer 2 配对，或作为独立「扫码即连」入口 |

---

## 二、用户流程（极简）

```
┌─────────────────────────────────────────────────────────────────┐
│  用户打开 Jachin 桌面端（首次 / 未配对）                           │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                                                          │   │
│  │            ┌─────────────────────────┐                    │   │
│  │            │                         │                    │   │
│  │            │    [ 动态二维码 ]       │                    │   │
│  │            │                         │                    │   │
│  │            └─────────────────────────┘                    │   │
│  │                                                          │   │
│  │              或输入备用码: J8K-2X9                        │   │
│  │                                                          │   │
│  │  「用手机扫描二维码，或访问 nexus 控制台输入上方码」       │   │
│  │                                                          │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                  │
│  用户拿起手机（已登录 Layer 1）→ 扫码 → 弹出「是否接入？」→ 确认  │
│                                                                  │
│  桌面端：轮询到 success → 显示 ✅ 已连接 → 进入主界面             │
└─────────────────────────────────────────────────────────────────┘
```

---

## 三、技术实现

### 3.1 复用现有配对 API

| 阶段 | API | 桌面端行为 |
|------|-----|------------|
| 1. 请求配对码 | `POST /api/v1/pairing/request` | 启动时或点击「连接」时调用，获取 `short_code`、`session_id`、`pair_url` |
| 2. 展示二维码 | - | 二维码内容：`${pair_url}?code=${short_code}`，手机扫码后跳转预填页面 |
| 3. 轮询状态 | `GET /api/v1/pairing/status?session_id=xxx` | 每 2 秒轮询，收到 `status: "success"` 时保存配置并进入主界面 |
| 4. 保存配置 | - | 写入 `~/.jachin/nexus_config.json`：`instance_id`、`access_token`、`nexus_base_url` |

### 3.2 二维码内容

```
https://nexus.jachin/console/pair?code=J8K2X9
```

或本地开发：
```
http://localhost:3000/console/pair?code=J8K2X9
```

**Layer 1 配对页增强**：当 URL 带 `?code=XXX` 时，自动预填 6 位码，用户只需点击「确认」即可完成授权。

### 3.3 配置持久化

桌面端（Tauri）需写入与 Layer 2 相同的配置路径，以便 daemon 读取：

- **Windows**: `%USERPROFILE%\.jachin\nexus_config.json`
- **macOS/Linux**: `~/.jachin/nexus_config.json`

```json
{
  "instance_id": "uuid-from-pairing",
  "access_token": "jch-xxx",
  "nexus_base_url": "http://localhost:3000"
}
```

### 3.4 技术栈

| 组件 | 选型 |
|------|------|
| 桌面框架 | Tauri v2（已有 `clients/desktop`） |
| 前端 | React + TypeScript |
| 二维码 | `qrcode.react` 或 `qrcode` |
| HTTP | `fetch` 或 Tauri 侧 Rust `reqwest` |
| 配置写入 | Tauri `fs` + `path`，或 `tauri-plugin-store` |

---

## 四、实施步骤

### Phase 1：配对页支持 URL 预填（Layer 1）

- [ ] `cloud/nexus/src/app/console/pair/page.tsx`：读取 `?code=XXX`，自动填入输入框
- [ ] 可选：`?code=XXX&auto=1` 时自动调用 confirm，一键完成（需登录态）

### Phase 2：桌面端配对入口

- [ ] 新增 `PairingScreen` 组件：二维码 + 6 位码展示 + 轮询逻辑
- [ ] 应用启动时检测 `~/.jachin/nexus_config.json`，未配对则显示 `PairingScreen`
- [ ] Tauri command：`pairing_request`、`pairing_status`、`write_nexus_config`

### Phase 3：极简视觉

- [ ] 深色赛博风背景，中央大二维码，底部小字「或输入备用码」
- [ ] 配对成功：绿色呼吸灯 + 「已连接」动画，1 秒后淡出进入主界面
- [ ] 无任何技术术语（无 API、session_id、token）

### Phase 4：与 Layer 2 联动 ✅

- [x] 配对成功后，Rust 侧静默拉起 `run-daemon.ps1` / `run-daemon.sh`
- [x] 界面显示「边缘守护进程已在后台静默运行」
- [x] 用户无需打开终端

### Phase 5：自定义 Base URL ✅

- [x] 齿轮图标展开「自定义指挥中枢 (Nexus Base URL)」
- [x] 保存至 `~/.jachin/desktop_config.json`
- [x] 支持私有化部署、企业自建 Layer 1

---

## 五、文件映射

| 功能 | 文件 |
|------|------|
| 战役规格 | `docs/BATTLE_C_DESKTOP_SCAN_TO_CONNECT.md`（本文档） |
| 配对页预填 | `cloud/nexus/src/app/console/pair/page.tsx` |
| 桌面端配对屏 | `clients/desktop/src/components/PairingScreen.tsx` |
| Tauri 配对命令 | `clients/desktop/src-tauri/src/commands/pairing.rs` |
| spawn_daemon | 静默拉起 run-daemon.ps1/sh |
| read/write_nexus_base_url | 自定义指挥中枢 URL |
| 应用入口逻辑 | `clients/desktop/src/App.tsx` 或 `consoleEntry.tsx` |

---

## 六、参考文档

- [ZERO_FRICTION_DESIGN.md](./ZERO_FRICTION_DESIGN.md) - 方案 A：扫码即连
- [PAIRING_PROTOCOL_SPEC.md](./PAIRING_PROTOCOL_SPEC.md) - 配对协议
- [MICROKERNEL_ECOSYSTEM_BATTLE_PLAN.md](./MICROKERNEL_ECOSYSTEM_BATTLE_PLAN.md) - 战役规划

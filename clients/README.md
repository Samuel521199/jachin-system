# Clients (Layer 3 / Terminal)

多形态客户端代码，负责数据采集和指令执行。属于 **Jachin 三层架构** 中的 **Layer 3（灵动终端）**。

## 三层架构定位

| Layer | 名称 | 职责 | 本仓库 |
|-------|------|------|--------|
| **Layer 1** | Jachin Nexus (灵界枢纽) | 智慧分发、协议标准、神经元商城 | `cloud/`（待建） |
| **Layer 2** | Jachin Hive (私有大脑) | 本地推理、记忆、技能运行时 | `core/` |
| **Layer 3** | Jachin Terminal (灵动终端) | 用户交互、I/O、边缘反射 | **`clients/`** |

> Layer 1 设计理念：**轻量化、协议化、去中心化**。不做传统 SaaS 或 Web2.0 商城，而是「分发智慧」的枢纽。详见 [docs/LAYER1_ARCHITECTURE_AND_DESIGN.md](../docs/LAYER1_ARCHITECTURE_AND_DESIGN.md)。

## 目录结构

```
clients/
├── desktop/          # 桌面客户端 (Tauri/Electron)
│   ├── src/         # 前端代码 (React)
│   ├── src-tauri/   # Rust 后端 (Tauri)
│   └── package.json
├── mobile/          # 移动端 (Flutter/React Native)
│   ├── lib/         # Flutter 代码
│   └── pubspec.yaml
└── iot/             # IoT 客户端
    ├── raspberry_pi/ # 树莓派客户端
    ├── esp32/       # ESP32 客户端
    └── jetson/      # Jetson 客户端
```

## 开发指南

### Desktop Client

```bash
cd clients/desktop
npm install
npm run dev
```

### Mobile Client (Flutter)

```bash
cd clients/mobile
flutter pub get
flutter run
```

### IoT Client

```bash
cd clients/iot/raspberry_pi
pip install -r requirements.txt
python main.py
```

## 插件开发

参考 `.cursor/rules/020-client-plugins.mdc` 了解插件开发规范。

## 能力注册

客户端启动时会自动向服务端注册自身能力（Capabilities），包括：
- 可执行的动作（Actions）
- 可上报的传感器数据（Sensors）

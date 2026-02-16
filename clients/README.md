# Clients (Limbs Layer)

多形态客户端代码，负责数据采集和指令执行。

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

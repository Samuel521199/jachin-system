# L3 Sidecar 二进制目录

Tauri 从此目录加载 `l3_node` Sidecar 引擎。

## 首次使用

1. **创建占位符**（若目录为空）：
   ```bash
   python scripts/create_l3_stub.py
   ```

2. **打包真实 L3 引擎**（替换占位符）：
   ```bash
   pip install pyinstaller
   python scripts/build_l3_sidecar.py
   ```

3. 启动桌面端：
   ```bash
   cd clients/desktop && npm run tauri dev
   ```

## 文件命名

- Windows: `l3_node-x86_64-pc-windows-msvc.exe`
- macOS: `l3_node-aarch64-apple-darwin` 或 `l3_node-x86_64-apple-darwin`
- Linux: `l3_node-x86_64-unknown-linux-gnu`

`tauri.conf.json` 的 `bundle.externalBin` 为 `bin/l3_node`，与 `l3_node-<triple>` 文件名对应。热更新助手 `jachin-updater-helper.exe` 不在此外部二进制列表中；开发与发布时请用 `npm run ensure-updater-helper` 或从 `target/release` 与主程序同目录拷贝。

## 注意

- 占位符仅用于通过构建，不提供 L3 功能
- 运行 `build_l3_sidecar.py` 后需设置 `OPENAI_API_KEY` 环境变量

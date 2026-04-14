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

- Windows: `l3_node-x86_64-pc-windows-msvc.exe`（**必须**放在 **`bin/`** 下；Tauri `externalBin` 与 `l3_spawn` 均按此路径解析）
- **不要**把侧车改名为 `l3_node.exe` 放在安装根目录：桌面端无法按侧车方式启动，Omni 会卡在「等待 L3」。
- macOS: `l3_node-aarch64-apple-darwin` 或 `l3_node-x86_64-apple-darwin`
- Linux: `l3_node-x86_64-unknown-linux-gnu`

`tauri.conf.json` 的 `bundle.externalBin` 为 `bin/l3_node`，与 `l3_node-<triple>` 文件名对应。热更新助手 `jachin-updater-helper.exe` 不在此外部二进制列表中。日常 `npm run tauri:dev` 不自动编译助手；若要本机测「立即更新」，请先 `npm run ensure-updater-helper` 或使用 `npm run tauri:dev:with-updater`。发布流程仍用 `npm run publish-desktop-release`（仓库根）或从 `target/release` 拷贝助手与主程序同目录。

## 注意

- 占位符仅用于通过构建，不提供 L3 功能
- 运行 `build_l3_sidecar.py` 后需设置 `OPENAI_API_KEY` 环境变量
- **安装后 Omni 提示「等待 L3 或 L2」且 `l3_debug.log` 报 `bin\\l3_node-…exe` 不存在**：安装包构建时未打入真实侧车。请重新执行 `npm run tauri:build`（`beforeBundle` 会校验侧车），必要时先 `python scripts/build_l3_sidecar.py`；若杀软删了 `bin` 下 exe，需加白名单或重装。

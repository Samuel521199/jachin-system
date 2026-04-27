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

### 只编译热更新助手（不跑完整 `tauri build`）

在仓库根或 `clients/desktop` 下：

```bash
cargo build --release --manifest-path clients/desktop/src-tauri/Cargo.toml --bin jachin-updater-helper
```

产物：`clients/desktop/src-tauri/target/release/jachin-updater-helper.exe`（与主程序同目录分发）。

助手下载安装包时：**先直连**（`reqwest` 显式 `no_proxy()`，不受系统 HTTP 代理干扰）；失败则自动经 **`http://127.0.0.1:8800`** 重试（常见本机 Clash HTTP 端口）。可用环境变量：`JACHIN_UPDATER_HELPER_HTTP_PROXY`（默认 `http://127.0.0.1:8800`）、`JACHIN_UPDATER_HELPER_NO_PROXY_FALLBACK=1`（禁用代理回退）。

## Windows：侧车「一打开就闪退」

PyInstaller 打出来的侧车带 **`--noconsole`**，在资源管理器里**双击**时，若进程在**弹出控制台窗口之前**就崩溃（常见：缺运行库、杀软拦截、DLL 加载失败），你会看到**窗口闪一下或直接没反应**。

1. **请用命令行跑**（能看到报错或至少确认进程码）：
   ```powershell
   cd "$env:LOCALAPPDATA\Jachin Desktop Sprite\bin"
   .\l3_node-x86_64-pc-windows-msvc.exe --ws-only
   ```
2. **看日志**：`%USERPROFILE%\.jachin\l3_debug.log`，或安装目录下的 `logs\l3_debug.log` / 根目录 `l3_debug.log`（见 `l3_node/early_log.py` 的解析顺序）。
3. **安装 [VC++ 2015–2022 x64 可再发行组件](https://learn.microsoft.com/zh-cn/cpp/windows/latest-supported-vc-redist)**：onefile 依赖与 Python 扩展模块常与 `vcruntime140.dll`、`api-ms-win-crt-*.dll` 等绑定；新机未装时会在启动极早阶段失败。
4. **杀毒/防护**：将 `Jachin Desktop Sprite` 安装目录加入白名单；隔离区里的 `l3_node-*.exe` 需恢复。
5. **根目录的 `l3_node.exe`**：正规安装只应在 **`bin\l3_node-x86_64-pc-windows-msvc.exe`**。根目录若多了一个 `l3_node.exe`，多半是误拷、旧资源或第三方工具生成，**不作为受支持入口**；请以 `bin` 下带 triplet 文件名为准。

## 注意

- 占位符仅用于通过构建，不提供 L3 功能
- 运行 `build_l3_sidecar.py` 后需设置 `OPENAI_API_KEY` 环境变量
- **安装后 Omni 提示「等待 L3 或 L2」且 `l3_debug.log` 报 `bin\\l3_node-…exe` 不存在**：安装包构建时未打入真实侧车。请重新执行 `npm run tauri:build`（`beforeBundle` 会校验侧车），必要时先 `python scripts/build_l3_sidecar.py`；若杀软删了 `bin` 下 exe，需加白名单或重装。

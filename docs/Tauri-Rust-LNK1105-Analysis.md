# Tauri/Rust LNK1105 深度分析

## 一、错误现象

```
error: linking with `link.exe` failed: exit code: 1105
LINK : fatal error LNK1105: 无法关闭文件
"D:\...\proc_macro_hack-51d70a37c357b76f.dll"
错误代码 1224
```

- **LNK1105**：MSVC 链接器无法关闭/写入输出文件
- **错误 1224**：`ERROR_USER_MAPPED_FILE` — 文件被用户映射段占用

---

## 二、根本原因

### 2.1 错误 1224 的含义

| 项目 | 说明 |
|------|------|
| 错误码 | 1224 (0x4C8) |
| 名称 | ERROR_USER_MAPPED_FILE |
| 含义 | 无法对带有用户映射段（memory-mapped）打开的文件执行该操作 |

当 `link.exe` 尝试写入/关闭 `.dll` 或 `.exe` 时，该文件已被其他进程以**内存映射**方式打开，导致无法覆盖或关闭。

### 2.2 典型触发场景

1. **杀毒软件**：实时扫描新建的 exe/dll，会短暂锁定
2. **OneDrive / 云同步**：同步目录中的文件被索引或上传时锁定
3. **Windows 搜索索引**：索引服务读取新文件
4. **IDE / 调试器**：VS Code、Visual Studio 可能持有文件句柄
5. **Cargo 并行编译**：多个 rustc 进程同时链接，竞争同一输出路径
6. **上次构建残留**：进程未完全退出，仍持有旧 exe 句柄

### 2.3 为何出现在 proc_macro、markup5ever 等不同 crate？

- **proc_macro-hack**：生成 proc-macro 的 `.dll`，由 rustc 加载
- **markup5ever**：build script 生成 `build_script_build-*.exe` 并执行

共同点：都是**中间产物**（dll/exe），在 `target\debug\deps\` 或 `target\debug\build\` 下，被 link.exe 写入时遭遇锁定。

---

## 三、Rust 官方已知问题

[Rust #127883](https://github.com/rust-lang/rust/issues/127883) 追踪了 Windows MSVC CI 上类似问题：

- 自 2024-06 起，MSVC Windows 构建失败率显著升高（约 15%）
- 表现为：`Access is denied`、`used by another process`、`cannot open file`、LNK1104/LNK1105
- 官方尝试过 `handle.exe`、RestartManager API 排查，未找到稳定根因
- 可能与 Windows 镜像更新、杀毒策略、文件系统行为有关

说明：这是 **Windows + MSVC 工具链** 的已知环境问题，而非 Tauri 或项目代码本身。

---

## 四、排查步骤

### 4.1 确认文件被谁占用

```powershell
# 需安装 Sysinternals Handle
handle.exe "proc_macro_hack" "D:\Projects\jachi\jachin-system-main\clients\desktop\src-tauri\target"
```

或使用 **Process Explorer**：`Ctrl+F` 搜索路径，查看占用进程。

### 4.2 检查项目路径

- 是否在 **OneDrive**、**Dropbox** 等同步目录？
- 路径是否过长（> 260 字符）？
- 是否在**网络驱动器**或**映射盘**上？

### 4.3 检查杀毒与安全软件

- Windows Defender 实时保护
- 第三方杀毒（卡巴、诺顿、360 等）
- 企业 EDR / 终端安全软件

---

## 五、解决方案（按优先级）

### 5.1 排除杀毒扫描（推荐）

**Windows Defender：**

1. 设置 → 隐私和安全 → Windows 安全中心 → 病毒和威胁防护
2. 病毒和威胁防护设置 → 排除项 → 添加排除项 → 文件夹
3. 添加：`D:\Projects\jachi\jachin-system-main\clients\desktop\src-tauri\target`

### 5.2 项目移出云同步目录

若项目在 OneDrive 等目录，移到本地非同步路径（如 `D:\Projects\`）。

### 5.3 使用 LLD 替代 link.exe（推荐，从根本规避）

**LLD**（LLVM 链接器）与 MSVC 的 `link.exe` 实现不同，通常可避免 LNK1105。Rust 自带 `rust-lld.exe`。

在 `clients/desktop/src-tauri/.cargo/config.toml` 中添加：

```toml
[target.x86_64-pc-windows-msvc]
linker = "rust-lld"
```

或使用完整路径（若 rust-lld 不在 PATH）：

```toml
[target.x86_64-pc-windows-msvc]
linker = "rust-lld"
# 或: linker = "C:/Users/<用户名>/.rustup/toolchains/stable-x86_64-pc-windows-msvc/lib/rustlib/x86_64-pc-windows-msvc/bin/rust-lld.exe"
```

**注意**：若启用 thin LTO，部分项目曾报告 LLD 与 LTO 的兼容性问题（如 STATUS_ACCESS_VIOLATION）。Tauri 默认通常不启用 thin LTO，一般可安全使用。

### 5.4 限制并行度（折中方案）

`CARGO_BUILD_JOBS` **仅影响构建时间**，不影响**最终程序的运行时性能**。编译出的二进制与多线程构建完全相同。

单线程是权宜之计，可尝试折中：

```powershell
# 限制为 2–4 个并行任务，减少 link.exe 竞争，同时保留一定并行
$env:CARGO_BUILD_JOBS = "2"
cargo build
```

若仍失败，再降至 `1`。

### 5.5 构建前清理

```powershell
cd D:\Projects\jachi\jachin-system-main\clients\desktop\src-tauri
cargo clean
cargo build
```

清理可消除残留锁定与损坏的中间文件。

### 5.6 使用 x64 Native Tools 命令行

1. 开始菜单 → "x64 Native Tools Command Prompt for VS 2022"
2. 进入项目目录后执行 `cargo build`

避免 PowerShell 环境变量或编码干扰。

### 5.7 临时关闭实时保护（慎用）

仅用于验证是否为杀毒导致，验证后请恢复：

- Windows 安全中心 → 病毒和威胁防护 → 实时保护 → 关闭

### 5.8 使用 Vite 模式绕过 Tauri 构建

```powershell
.\scripts\start-layer3.ps1 -ViteOnly
```

在浏览器中运行前端，不构建 Rust 桌面壳。

---

## 六、技术细节：为何是“无法关闭”而非“无法打开”

| 阶段 | 行为 |
|------|------|
| 1 | link.exe 创建/打开输出文件（如 proc_macro_hack.dll） |
| 2 | 写入链接后的二进制内容 |
| 3 | 调用 `CloseHandle` / `SetEndOfFile` 等关闭文件 |
| 4 | **此时若文件被其他进程 memory-map，CloseHandle 失败 → 1224** |

因此表现为 LNK1105「无法关闭文件」，本质是关闭时发现文件仍被占用。

---

## 七、参考

- [Rust #127883 - Windows MSVC filesystem errors](https://github.com/rust-lang/rust/issues/127883)
- [Stack Overflow - LNK1105 cannot close file](https://stackoverflow.com/questions/41105114/1link-fatal-error-lnk1105-cannot-close-file)
- [Windows Error 1224 - ERROR_USER_MAPPED_FILE](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-erref/18d8fbe8-a967-4f1c-ae50-99ca8e143d14)

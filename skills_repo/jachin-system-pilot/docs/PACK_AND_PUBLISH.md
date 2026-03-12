# jachin-system-pilot 打包与发布指南

本文档说明如何使用 jachin-cli 完成 Skill 的 pack 与 publish 全流程。

---

## 前置条件

1. **编译 main.wasm**

   ```bash
   cd skills_repo/jachin-system-pilot
   rustup target add wasm32-unknown-unknown
   cargo build --target wasm32-unknown-unknown --release
   cp target/wasm32-unknown-unknown/release/jachin_system_pilot.wasm main.wasm
   ```

   Windows PowerShell 用户（或直接运行 `.\build.ps1`）：

   ```powershell
   cd skills_repo\jachin-system-pilot
   .\build.ps1
   # 或手动：
   rustup target add wasm32-unknown-unknown
   cargo build --target wasm32-unknown-unknown --release
   Copy-Item target\wasm32-unknown-unknown\release\jachin_system_pilot.wasm main.wasm
   ```

2. **安装 jachin-cli**

   ```bash
   cd tools/jachin-cli
   pip install -e .
   ```

3. **配置开发者 Token**（publish 时需要）

   - 环境变量：`export JACHIN_DEV_TOKEN=your_token`
   - 或配置文件 `~/.jachin-cli/config.json`：
     ```json
     { "token": "xxx", "nexus_url": "http://localhost:3000" }
     ```

---

## 步骤 1：jachin pack

在 Skill 项目根目录执行：

```bash
cd skills_repo/jachin-system-pilot
jachin pack
```

**作用：**

- 校验 `plugin.json`（ID 格式、必填字段、入口文件 `main.wasm` 存在性）
- 打包为 `dist/com.jachin.system.pilot_v1.0.0.zip`

**Windows 若遇 UnicodeEncodeError：**

jachin-cli 已内置 UTF-8 修复。若仍报错，可先执行：

```powershell
$env:PYTHONIOENCODING = "utf-8"
jachin pack
```

---

## 步骤 2：jachin publish

在同一目录执行：

```bash
jachin publish
```

**交互提示：**

1. **可见性**：`PUBLIC` 或 `PRIVATE`
   - **PRIVATE**：影子上传，仅登记 `plugin.json` 元数据，不传 zip 包。适合开发调试，实体包需侧载到 L2 `~/.jachin/inventory/`
   - **PUBLIC**：完整上传，将 `dist/` 下最新 zip 上传至 Nexus 商城

2. **月付价格（分）**：输入 `0` 表示免费

**Nexus 地址：**

- 默认：`http://localhost:3000`
- 可通过 `JACHIN_NEXUS_URL` 或 `~/.jachin-cli/config.json` 的 `nexus_url` 覆盖

---

## 流程概览

```
编译 main.wasm  →  jachin pack  →  dist/*.zip  →  jachin publish  →  Nexus 商城
```

| 步骤       | 命令           | 输出                          |
|------------|----------------|-------------------------------|
| 编译       | `cargo build`  | `main.wasm`                   |
| 打包       | `jachin pack`  | `dist/com.jachin.system.pilot_v1.0.0.zip` |
| 发布(PRIVATE) | `jachin publish` | 仅元数据登记，无 zip 上传   |
| 发布(PUBLIC)  | `jachin publish` | 完整 zip 上传至 Nexus       |

---

## 侧载（PRIVATE 模式）

PRIVATE 发布后，实体包需手动放入 L2 节点：

```bash
# 将 zip 复制到 L2 的 inventory 目录
cp dist/com.jachin.system.pilot_v1.0.0.zip ~/.jachin/inventory/
```

L2 会从 `inventory/` 加载已登记的 Skill。

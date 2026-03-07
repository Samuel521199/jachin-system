# Jachin CLI

Jachin Nexus 开发者生态命令行工具 - 插件脚手架、打包与上云。

## 安装

```bash
cd tools/jachin-cli
pip install -e .
```

安装后全局可用 `jachin` 命令。

## 命令

### jachin init

交互式创建插件项目脚手架。

```bash
jachin init
```

- 输入 Plugin ID（如 `com.example.hello`）
- 输入 Plugin Name、Description
- 选择 Item Type：SKILL 或 MCP
- 自动生成 `plugin.json`、`src/main.rs` / `main.c`（SKILL）、`main.wasm` 占位符

### jachin pack

严苛校验并打包。

```bash
jachin pack
```

- 校验 plugin.json（ID 格式、必填字段、入口文件存在性）
- 打包为 `dist/{plugin_id}_v{version}.zip`

### jachin publish

一键上云发布到 Nexus 商城。

```bash
export JACHIN_DEV_TOKEN=your_token
jachin publish
```

- 从环境变量或 `~/.jachin-cli/config.json` 读取 token
- 交互式选择可见性（PUBLIC/PRIVATE）、定价
- 将 dist/ 下最新 zip 上传至 L1 Nexus

## 配置

- `JACHIN_DEV_TOKEN`: 开发者 Token
- `JACHIN_NEXUS_URL`: Nexus 地址，默认 `http://localhost:3000`
- 或写入 `~/.jachin-cli/config.json`:
  ```json
  { "token": "xxx", "nexus_url": "http://localhost:3000" }
  ```

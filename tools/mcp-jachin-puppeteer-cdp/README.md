# Jachin · Puppeteer MCP（CDP 附加版）

基于 [@modelcontextprotocol/server-puppeteer](https://www.npmjs.com/package/@modelcontextprotocol/server-puppeteer)（MIT）：工具名与行为一致，**增加**在设置 `PUPPETEER_BROWSER_URL`（例如 `http://127.0.0.1:9222`）时用 `puppeteer.connect` 附加到**已启动**的 Chrome，而不是 `puppeteer.launch` 再开一只浏览器。

## 安装

在**本目录**执行一次。仅使用 **CDP 连接**（`PUPPETEER_BROWSER_URL`）时不必下载 Puppeteer 自带的 Chromium，可加快安装：

**Windows (PowerShell)**

```powershell
$env:PUPPETEER_SKIP_DOWNLOAD='true'
npm install
```

**Unix**

```bash
PUPPETEER_SKIP_DOWNLOAD=true npm install
```

若未设置该变量，`npm install` 会下载 Chromium，耗时较长。

## MCP 配置

见仓库根 `config/mcp_servers.json.example` 中的 **`jachin-puppeteer-cdp`** 条目：使用占位符 `__JACHIN_REPO_ROOT__` 指向 `index.mjs`。`env` 中 **`PUPPETEER_BROWSER_URL` 推荐 `${KALAROKO_CDP_ENDPOINT}`**（与 Kalaroko E2E 同源）。L3 解析配置后若仍为空，会按 `core/mcp_embedded_runtime` 从 `KALAROKO_CDP_ENDPOINT` / `K11_CDP_HTTP` / attach JSON 等回填。

配置变更后需**重启 L3**。

## 与官方包的区别

官方包仅支持 `launch` + `PUPPETEER_LAUNCH_OPTIONS`，**不能**连接已有远程调试端口；本目录为 Jachin 维护的少量分叉代码。

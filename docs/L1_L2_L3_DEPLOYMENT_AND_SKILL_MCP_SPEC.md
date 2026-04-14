# L1/L2/L3 部署拓扑与 Skill/MCP 解耦规范 — 深度分析

**版本**: 1.0  
**日期**: 2026-03  
**定位**: 回答「L1+L2 同机、L3 异地、仅 exe 部署、Skill/MCP 订阅下载」的架构满足度与规范缺口

**术语**：文中 **Skill / MCP / Wasm** 与 **Tools / MCP / Skills / Agent Tasks** 四原语对照见 **[Jachin 视角的「四大原语」终极架构规范.md](./Jachin%20视角的「四大原语」终极架构规范.md)**。

**运维侧（示例服务器公网 IP、端口、`NEXUS_PUBLIC_URL` / `AUTH_SECRET`、Docker 目录等）**：见 [`docs/L1_LINUX_CLOUD_DEPLOY.md`](L1_LINUX_CLOUD_DEPLOY.md) **§0**。

---

## 一、部署拓扑满足度

### 1.1 目标拓扑

```
┌─────────────────────────────────────────────────────────────────┐
│  机器 A（中心）                                                  │
│  ├── L1 (Nexus)     — 平台、商城、manifest、订阅                 │
│  └── L2 (Core)      — 控制面、子账号、Key、inventory 同步         │
└─────────────────────────────────────────────────────────────────┘
                              │ HTTP (manifest, heartbeat)
                              │
┌─────────────────────────────────────────────────────────────────┐
│  机器 B（边缘）                                                  │
│  └── L3 (exe only)  — 仅 l3_node.exe + 日志，无 Python/Node       │
│      ├── 订阅 L2 → 下载 Skill/MCP 到 ~/.jachin/l3_skill_cache    │
│      └── 执行 Wasm 技能、L3_LOCAL MCP                            │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 当前满足度

| 能力 | 状态 | 说明 |
|------|------|------|
| L1+L2 同机 | ✅ | L1 (Nexus) 与 L2 (Core) 可同机，L2 通过 nexus_base_url 连接 L1 |
| L3 异地 | ✅ | L3 通过 L2_BASE_URL 连接 L2，仅需网络可达 |
| L3 仅 exe 部署 | ✅ | PyInstaller 打包 l3_node.exe，内嵌 Python 运行时，目标机无需安装 Python |
| 无 Python 环境 | ⚠️ | exe 内嵌 Python，可运行。但 **L3_LOCAL MCP 若为 Python 源码**，需 exe 能 `import` — exe 内嵌的 Python 可执行，但 MCP 包必须是纯 Python 源码（无 C 扩展等） |
| Skill/MCP 订阅下载 | ⚠️ | 部分满足，见下文 |

---

## 二、Skill 与 MCP 解耦现状

### 2.1 数据流

```
L1 (manifest)  →  L2 (CloudSyncDaemon)  →  ~/.jachin/inventory/
                      ↓
               L3 (skill_sync / mcp_sync)  →  ~/.jachin/l3_skill_cache / l3_mcp_cache
```

### 2.2 Skill（JPP Wasm）— 已解耦 ✅

| 环节 | 路径 | 说明 |
|------|------|------|
| L1 manifest | `plugins_registry` + `user_licenses` | item_type=SKILL, package_url 指向 zip |
| L2 同步 | `inventory/skills/{item_id}/` | 解压 main.wasm + plugin.json |
| L3 拉取 | `l3_skill_cache/{item_id}/` | 下载 main.wasm + plugin.json |
| L3 执行 | Wasm 运行时（内嵌） | 无需额外环境 |

**结论**：Skill 完全解耦，本地测试 → 打包上传 → 订阅下载 → 直接可用。

### 2.3 MCP — 部分解耦 ⚠️

#### 类型 A：L3 内置 MCP（未解耦）

| 工具 | 位置 | 说明 |
|------|------|------|
| mcp:atom_web_scraper | `l3_node/primitives/mcp/mcp_tools/bi/` | 硬编码在 L3_LOCAL_MCP_TOOLS，随 exe 打包 |
| mcp:atom_lark_notifier | 同上 | 同上 |
| mcp:atom_email_sender | 同上 | 同上 |
| mcp:read_file | `mcp_registry.py` | 同上 |

**结论**：这些 MCP **不能**通过 L1 订阅下载，只能随 L3 版本更新。

#### 类型 B：L3_LOCAL MCP（从 L1 订阅）— 已支持 ✅

| 环节 | 路径 | 说明 |
|------|------|------|
| L1 manifest | runtime_tier=L3_LOCAL, item_type=MCP | package_url 指向 zip |
| L2 同步 | `inventory/l3_mcps/{item_id}/` | 解压 plugin.json + Python 源码 |
| L3 拉取 | `l3_mcp_cache/{item_id}/` | 下载 zip 并解压 |
| L3 加载 | `_load_tools_from_l3_mcp_cache()` | `sys.path.insert(cache_dir)` + `__import__(module_path)` |

**plugin.json 格式**（L3_LOCAL MCP）：

```json
{
  "id": "com.example.my-mcp",
  "name": "My MCP",
  "item_type": "MCP",
  "runtime_tier": "L3_LOCAL",
  "tools": [
    {
      "id": "mcp:my_tool",
      "module": "tools.my_module",
      "function": "my_func",
      "params": ["input"],
      "desc": "描述"
    }
  ]
}
```

**包结构**：zip 根目录需含 `plugin.json`，以及 `tools/my_module.py`（与 module 路径对应）。

**结论**：L3_LOCAL MCP 可订阅下载，但必须是 **纯 Python 源码**，exe 内嵌的 Python 能 `import`。

#### 类型 C：L2_GATEWAY MCP（stdio）— **默认在 L3 执行**（L2 仅同步与委托）

| 说明 | 路径 |
|------|------|
| **默认运行位置** | **L3 所在用户机**（`l3_node/mcp_stdio_bootstrap.py` 内嵌 `MCPManager`，读 `~/.jachin/mcp_servers.json` 与 `inventory/mcps/`） |
| 配置落盘 | L2 仍同步 `inventory/mcps/`（及用户 `mcp_servers.json`），与旧侧载布局一致，便于 L3 与本机命令行环境对齐 |
| 依赖 | 执行 **stdio 子进程** 的机器需具备对应 Python/Node/**npx**（通常是 **L3 笔记本/边缘机**）。**无系统 Node 时**：将官方 Node 便携包解压到 `~/.jachin/runtime/node/`（须含 `npx.cmd`），或由安装器/Sidecar 随包附带；详见 **[L3_EMBEDDED_RUNTIME.md](./L3_EMBEDDED_RUNTIME.md)**、`core/mcp_embedded_runtime.py`。 |
| **回滚** | 环境变量 **`JACHIN_L2_STDIO_MCP=1`** 时，L2 进程再次侧载 stdio（兼容旧部署/排障） |

**结论**：清单类型仍可为 L2_GATEWAY，但**长期默认**不在 L2 宿主机上起 MCP 子进程；L3 本机执行，缺能力时 `POST /api/v2/mcp/invoke` 走 L2 **TaskManager**。**跨 L3**：Pull + 带 Task Token 的 HTTP 降级；见 `docs/ARCHITECTURE_L3_MCP_HOST_AND_L2_TASK_MANAGER.md` v0.4、`docs/MCP_EXECUTION_MODEL.md` v2.2。

---

## 三、本地测试 → 云端 → 目标机 一致性

### 3.1 理想流程

```
1. 本地开发：在 ~/.jachin/inventory/ 或 skills_repo 中测试
2. 打包：jachin pack 产出 zip（含 plugin.json + wasm/源码）
3. 上传：jachin publish 到 L1
4. 审核：L1 管理员审核通过

5. L2 订阅：CloudSyncDaemon 拉 manifest → 下载到 inventory
6. L3 拉取：skill_sync / mcp_sync 从 L2 下载到 l3_skill_cache / l3_mcp_cache
7. 目标机：仅 exe + 网络，订阅后即可使用
```

### 3.2 当前缺口

| 缺口 | 原因 | 建议 |
|------|------|------|
| **MCP 包规范不完整** | 无统一文档规定 plugin.json 的 tools 结构、module/function 约定 | 见下文「规范」 |
| **L3_LOCAL MCP 依赖 Python** | 需纯 Python，无 C 扩展；目标机 exe 内嵌 Python 可 import | 文档中明确约束 |
| **L2 侧载 vs L1 订阅** | 本地可侧载到 inventory，但 L1 订阅需 tenant 有 license | 需确保 publish 后 license 正确 |
| **配置分离** | Skill/MCP 的 config（如 config.json）需随包或单独下发 | 包内可含 config.json，L2 解压时保留 |

---

## 四、Skill/MCP 上传与发布规范

### 4.1 已有规范（L1 publish）

| 项目 | 要求 |
|------|------|
| 包格式 | ZIP，根目录含 plugin.json |
| plugin.json | id（反向域名）、name、version（语义化）、item_type、runtime_tier |
| Skill | 需 entry（默认 main.wasm） |
| MCP | item_type=MCP，runtime_tier=L3_LOCAL 或 L2_GATEWAY |

### 4.2 需补充的 L3_LOCAL MCP 规范

```yaml
# plugin.json 扩展（L3_LOCAL MCP）

tools:  # 数组，每项含：
  - id: "mcp:tool_id"      # 可选 mcp: 前缀
  - module: "tools.xxx"   # Python 模块路径，相对 zip 根
  - function: "func_name"  # 可调用函数名
  - params: ["input"]      # 参数列表
  - desc: "描述"

# 包结构示例
zip/
├── plugin.json
├── tools/
│   ├── __init__.py
│   └── my_module.py   # 含 func_name
└── config.json        # 可选，运行时读取
```

### 4.3 本地测试验证清单

| 步骤 | 验证方式 |
|------|----------|
| 1. 目录结构 | 符合 plugin.json 的 module 路径 |
| 2. 侧载 L2 | 放入 `~/.jachin/inventory/l3_mcps/{id}/`，L2 reload |
| 3. L3 拉取 | L3 启动后 mcp_sync 拉取到 l3_mcp_cache |
| 4. 执行 | 通过 L3 Agent 或 API 调用 mcp:xxx |
| 5. 打包上传 | jachin pack && jachin publish |
| 6. 订阅 | L2 已与 L1 建立信任（Web Bridge 或 CLI），CloudSyncDaemon 拉 manifest 并下载 |
| 7. 目标机 | 仅 exe，L3 拉取后应能直接使用 |

---

## 五、架构满足度总结

| 问题 | 答案 |
|------|------|
| L1+L2 同机、L3 异地？ | ✅ 支持 |
| L3 仅 exe 部署？ | ✅ 支持（PyInstaller 单文件） |
| 目标机无 Python？ | ✅ exe 内嵌 Python，可运行。L3_LOCAL MCP 需为纯 Python 源码 |
| MCP/Skill 解耦？ | ⚠️ Skill 完全解耦；MCP 分内置（未解耦）与 L3_LOCAL（可订阅） |
| 订阅后直接可用？ | ✅ Skill 是；L3_LOCAL MCP 若符合规范则可用 |
| 配置与代码一起下载？ | ✅ 包内可含 config/，L2/L3 解压后按 manifest.yaml 写出到 ~/.jachin/config/ |
| 上传规范？ | ⚠️ 有基础规范，L3_LOCAL MCP 的 tools 结构需文档化 |
| 本地测试 → 云端 → 目标机 一致？ | ⚠️ 需遵循规范：包结构、plugin.json、无外部依赖 |

---

## 六、建议行动

**已实现**：L2 sync_daemon 与 L3 mcp_sync 在解压包后按 `config/manifest.yaml` 将配置写出到 `~/.jachin/config/`（`l3_node/config_writeout.py`，规范 075）。

1. **补充 MCP 规范文档**：在 `docs/` 或 `tools/jachin-cli` 中明确 L3_LOCAL MCP 的 plugin.json 与包结构。
2. **内置 MCP 可插拔化**：若希望 atom_web_scraper 等也可订阅，可将其改为 L3_LOCAL 包，从 L1 下发；或保留内置作为默认实现。
3. **CI 验证**：本地测试通过后，自动 pack → 上传到测试环境 → 触发 L2 同步 → L3 拉取 → 执行验证。

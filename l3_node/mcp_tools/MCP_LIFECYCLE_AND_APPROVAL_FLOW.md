# MCP 生命周期与审批流程说明

**版本**: 1.1  
**定位**: 回答「l3_node/mcp_tools 下的 MCP 能否满足 L1 上传→审核→L2 下载→L3 的完整流程，以及开发期本地可用、后期审批形成闭环」

---

## 双模式设计目标（必达）

为满足**开发**与**测试/生产**两类场景，需同时满足：

| 需求 | 说明 |
|------|------|
| **1. 本机开发即用** | L3 本地开发的 Skill 和 MCP 在本机即可使用，**不经过 L1 审批** |
| **2. 测试/生产闭环** | 同一套 Skill/MCP 可通过打包等方式，进入「上传→审批→下载→使用」的完整闭环 |

**原则**：同一份代码，开发期走本地路径，测试/生产期走 L1→L2→L3 流程，二者互不阻塞。

### 实现规范

#### 需求 1：本机开发即用（不经过 L1 审批）

| 条件 | L3 行为 |
|------|---------|
| 未连接 L2 | 本地 MCP（`l3_node/mcp_tools/`）与本地 Wasm（`wasm_plugins/`、`l3_skill_cache/`）**全部可用** |
| `allowed_skills` 为 `None` | 同上，视为开发模式，不做白名单过滤 |
| `allowed_skills` 为 `[]` | 空列表表示「无分配」时，本地 MCP 仍可用；若表示「显式禁止」则按策略实现 |

**实现要点**：
- L3 在 `allowed_skills is None` 时，`mcp_tools` 与 `load_tools` 均不做过滤。
- 本地 MCP 由 `mcp_registry` 直接 import，不依赖 L2 inventory。
- 开发期：仅启动 L3 即可，无需 L1/L2。

#### 需求 2：测试/生产闭环（上传→审批→下载→使用）

| 阶段 | 行为 |
|------|------|
| **打包** | 将 `l3_node/mcp_tools/` 或 `skills_repo/plugin/bi-daily-report-mcp/` 按 L1 要求的格式打包（见路径 2 或 3） |
| **上传** | `jachin publish` 或等价方式上传到 L1 Store |
| **审批** | L1 管理员审核，通过后进入 `plugins_registry` |
| **订阅** | 租户通过 `user_licenses` 订阅 |
| **同步** | L2 CloudSyncDaemon 拉取到 `~/.jachin/inventory/mcps/` 或 `l3_mcp_cache/` |
| **分配** | 管理员在角色中勾选 `mcp:atom_web_scraper` 等，`allowed_skills` 下发 |
| **使用** | L3 按 `allowed_skills` 过滤后加载并执行 |

**实现方式**：采用 **路径 3（L3_LOCAL 扩展）**，与 [MCP_EXECUTION_MODEL.md](../../docs/MCP_EXECUTION_MODEL.md) 一致：L3 优先执行，本机无则 L2 委托其他 L3。

---

## 〇、深度分析：审批流程与 L3 本地 MCP 的冲突

**结论**：当前实现存在 **权限 bypass 冲突** —— L3 本地 MCP 工具（含 atom_web_scraper、atom_lark_notifier、atom_email_sender）**完全绕过** L2 的 `allowed_skills` 白名单，与「上传→审核→分配→执行」的审批闭环不一致。

### 冲突点 1：工具列表合并时未过滤

```text
agent_core.run_agent (约 1110–1118 行):
  tools = load_tools(allowed_skills=allowed)     # ✅ 受 allowed_skills 过滤
  mcp_tools = await mcp_registry.fetch_tools_from_l2()  # ❌ 未传 allowed_skills
  tools = list(tools) + mcp_tools               # ❌ MCP 直接追加，未过滤
```

- `load_tools` 返回的 Native + Wasm 工具会按 `allowed_skills` 过滤。
- `fetch_tools_from_l2` 返回的 MCP 工具（含 L3_LOCAL_MCP_TOOLS）**未做任何过滤**。
- 合并后，Agent 可见的工具集包含「未审批」的 MCP 工具。

### 冲突点 2：执行时 MCP 路径不校验 allowed_skills

```text
agent_core._run_react_core (约 1022–1026 行):
  if tool in mcp_registry.known_mcp_tools:
      observation = await mcp_registry.invoke(tool, inp)   # ❌ 无 allowed_skills 校验
  else:
      observation = run_tool(tool, inp, allowed_skills=allowed_skills)  # ✅ 有校验
```

- 走 `mcp_registry.invoke` 的 MCP 工具**不接收、不校验** `allowed_skills`。
- 走 `run_tool` 的 Native/Wasm 工具会调用 `is_tool_allowed()` 做二次校验。
- 因此 MCP 工具在执行层也绕过了审批白名单。

### 冲突点 3：L3 本地 MCP 不在 L2 物资大盘

- L2 的 `role_permissions` 来自 `inventory`（L1 同步 + 侧载）。
- L3 本地 MCP（atom_web_scraper 等）**不在 L2 inventory**，管理员无法在角色中勾选。
- 即使想通过 `allowed_skills` 控制，也**无法将** `mcp:atom_web_scraper` 加入白名单。

### 影响

| 场景 | 预期 | 实际 |
|------|------|------|
| L2 仅分配 `jpp:com.jachin.hr.analyzer4` | 仅 HR 透析镜可用 | Agent 仍可见并可调用 atom_web_scraper 等 |
| L1 全局封禁某技能 | 该技能不可用 | 仅对 `allowed_skills` 内项生效，MCP 不受影响 |
| 多租户/子账号隔离 | 按角色限制工具 | MCP 工具对所有子账号开放 |

### 修复方向（已实现）

1. **工具列表**：对 `mcp_tools` 按 `allowed_skills` 过滤后再合并（`agent_core.run_agent` 约 1113–1120 行）。`allowed=None` 时不过滤，开发即用。
2. **执行层**：`mcp_registry.invoke` 已增加 `allowed_skills` 参数，执行前调用 `is_tool_allowed()`。
3. **物资来源**：将 L3 本地 MCP 登记到 L2 inventory（如 builtin 或 side-load），使管理员可在角色中分配。—— **待实现**，需按路径 1/2/3 择一接入。

### 与审批闭环的冲突小结

| 流程环节 | 设计预期 | 当前实现 |
|----------|----------|----------|
| L1 上传/审核 | 技能需登记、审核 | L3 本地 MCP 不经过 L1 |
| L2 同步/分配 | L2 从 L1 拉取，按角色分配 item_id | L3 本地 MCP 不在 L2 inventory |
| L3 工具列表 | 仅展示 allowed_skills 内工具 | MCP 直接追加，未过滤 |
| L3 执行 | 执行前校验 allowed_skills | mcp_registry.invoke 无校验 |

**结论**：审批与上传下载闭环本身无逻辑冲突，但 **L3 本地 MCP 未接入该闭环**，形成事实上的权限 bypass。

---

## 一、当前实现状态

### 1.1 BI MCP 工具（本目录）

| 工具 | 文件 | 注册方式 | 执行位置 |
|------|------|----------|----------|
| mcp:atom_web_scraper | tool_web_scraper.py | L3_LOCAL_MCP_TOOLS | L3 本地直接执行 |
| mcp:atom_lark_notifier | tool_broadcaster.py | L3_LOCAL_MCP_TOOLS | L3 本地直接执行 |
| mcp:atom_email_sender | tool_broadcaster.py | L3_LOCAL_MCP_TOOLS | L3 本地直接执行 |

**特点**：
- 代码在 `l3_node/mcp_tools/`，由 `mcp_registry.py` 直接 `import` 调用
- 不依赖 L2、不经过 L1
- 随 L3 进程/二进制一起分发，**不走 L1 上传→审核→下载流程**

### 1.2 与 L1→L2→L3 流程的关系

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  L1 上传→审核→下载 流程（适用于）                                              │
├─────────────────────────────────────────────────────────────────────────────┤
│  • Wasm 技能 (.wasm + plugin.json) → jachin pack → jachin publish            │
│  • L1 Store 审核 → plugins_registry → user_licenses 订阅                      │
│  • L2 CloudSyncDaemon 拉取 → ~/.jachin/inventory/skills/                     │
│  • L3 skill_sync 从 L2 下载 → ~/.jachin/l3_skill_cache/                       │
│  • L3 loader 扫描 wasm_plugins/ + l3_skill_cache/ → 加载技能                   │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│  l3_node/mcp_tools（当前）                                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│  • Python 代码，直接 import，无打包、无 L1 登记                                 │
│  • 不经过 L1、L2 的 manifest/sync 流程                                        │
│  • 始终可用（L3 启动即生效）                                                   │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 二、需求对照

| 需求 | 当前是否满足 | 说明 |
|------|--------------|------|
| **1. 本机开发即用（不经过 L1 审批）** | ✅ 满足 | L3 本地执行，无需 L1/L2，启动即可用；`allowed_skills=None` 时不做过滤 |
| **2. 测试/生产：上传→审批→下载→使用闭环** | ⚠️ 需扩展 | 需将 MCP 打包为可发布形态，按路径 1/2/3 择一接入 L1 流程 |

---

## 三、满足完整闭环的两种路径

### 路径 A：保持 L3 本地（现状，适合内部/快速迭代）

**适用**：内部使用、快速迭代、不要求走正式审批流程。

| 阶段 | 行为 |
|------|------|
| 开发期 | 直接修改 `l3_node/mcp_tools/`，L3 重启即生效，本地可用 |
| 分发 | 随 L3 代码/二进制一起部署，无单独审批 |
| 权限 | 由 L2 `allowed_skills` 控制是否暴露给子账号（若 L3 与 L2 对接） |

**优点**：实现简单，开发即用。  
**缺点**：不走 L1 审批流程，无法通过 Store 订阅/下发。

---

### 路径 B：接入 L1 审批流程（需扩展实现）

**适用**：需要正式审批、多租户订阅、通过 Store 分发的场景。

#### 3.1 目标流程

```
开发 (l3_node/mcp_tools 或 plugin 目录)
    → 打包 (Python + manifest，类似 jachin pack)
    → 上传 L1 Store
    → 管理员审核
    → 租户订阅 (user_licenses)
    → L2 CloudSyncDaemon 拉取 → ~/.jachin/inventory/mcps/ 或 skills/
    → L3 从 L2 同步 或 从 inventory 加载
    → 审批后可用
```

#### 3.2 需补齐的能力

| 环节 | 当前状态 | 需补充 |
|------|----------|--------|
| **打包格式** | 无 | MCP 用 Python，需定义 manifest + 目录结构，支持 `jachin pack` 或等价打包 |
| **L1 登记** | 仅支持 Wasm/Skill | L1 需支持 `item_type: MCP` 的登记与审核 |
| **L2 同步** | 主要同步 skills | L2 sync 需支持 MCP 类型，下载到 `inventory/mcps/` 等 |
| **L3 加载** | 仅 L3_LOCAL 硬编码 | L3 需能从 `inventory` 或 `l3_skill_cache` 动态加载 Python MCP |

#### 3.3 推荐目录结构（便于后续接入）

为便于将来接入 L1 流程，建议采用「可插拔」结构：

```
skills_repo/plugin/bi-daily-report-mcp/     # 可选：插件形态，便于打包
├── plugin.json                             # id, name, type: mcp
├── tools/
│   ├── tool_web_scraper.py
│   └── tool_broadcaster.py
└── requirements.txt

l3_node/mcp_tools/                          # 当前：开发期主战场
├── tool_web_scraper.py
├── tool_broadcaster.py
└── README.md
```

**开发期**：继续在 `l3_node/mcp_tools/` 开发，`mcp_registry` 直接 import，本地即用。  
**发布期**：将代码同步到 `skills_repo/plugin/bi-daily-report-mcp/`，按 L1 要求的格式打包、上传。

---

## 四、开发期本地使用（当前已支持）

在审批前，本地可直接使用本目录 MCP：

1. **启动 L3**：`python -m l3_node` 或桌面端
2. **调用方式**：
   - Agent 自然语言触发
   - 或直接 `mcp_registry.invoke("mcp:atom_web_scraper", {...})`
3. **无需**：L1 启动、L2 审批、Store 订阅

---

## 五、总结与建议

| 问题 | 结论 |
|------|------|
| **能否满足「上传→审核→下载」完整流程？** | 当前 **不能**。本目录 MCP 为 L3 本地实现，未接入 L1 流程。 |
| **能否满足「审批前本地可用」？** | **能**。L3 本地执行，开发期即可使用。 |
| **能否后期审批形成闭环？** | 需扩展：定义 MCP 打包格式、L1 登记、L2 同步、L3 动态加载。 |

**建议**：
- **短期**：沿用路径 A，在 `l3_node/mcp_tools/` 开发，本地使用，随 L3 分发。
- **中期**：若需审批闭环，按路径 B 设计 MCP 插件格式，并推动 L1/L2/L3 支持 MCP 类型的发布与同步。

---

## 六、深度分析：L3 MCP 不在 L2 Inventory 时，如何实现上传→审核→闭环

### 6.1 问题本质

L3 本地 MCP 与 L2 Inventory 的 MCP 是**两种不同形态**：

| 维度 | L2 Inventory MCP | L3 本地 MCP |
|------|------------------|-------------|
| **形态** | MCP Server（stdio 进程，`command` 拉起） | Python 模块（L3 直接 import 执行） |
| **存储** | `~/.jachin/inventory/mcps/{item_id}/` | `l3_node/mcp_tools/*.py` |
| **执行** | L2 MCPManager 拉起 → L3 经 HTTP 代理调用 | L3 进程内直接调用 |
| **来源** | L1 同步 或 侧载（config.json + command） | 代码硬编码 |

L3 本地 MCP **天然不在** L2 inventory，因为：
1. 它不是 MCP Server，没有 `command` 配置
2. 它不经过 L2 代理，L2 无法「挂载」它
3. L2 inventory 扫描的是 `mcps/*.json` 和 `mcps/*/config.json`，要求 `command` 字段

### 6.2 实现闭环的三种架构路径

#### 路径 1：L2 Builtin 登记（最短路径，不经过 L1）

**思路**：L2 启动时或首次扫描时，将 L3 本地 MCP 的元数据**登记到 inventory**，作为 builtin 来源，使管理员可在角色中分配。

```
L2 启动 / reload
    → 扫描「L3 本地 MCP 清单」（来自配置或 L3 上报）
    → 写入 inventory 或内存 registry，item_id = atom_web_scraper 等
    → 物资大盘展示，管理员可勾选
    → role_permissions 写入 mcp:atom_web_scraper
    → auth/poll 下发 allowed_skills 含 mcp:atom_web_scraper
    → L3 按 allowed_skills 过滤工具列表与执行
```

**需改动**：
- L2：新增 builtin MCP 登记逻辑（如 `config/l3_builtin_mcps.json` 或由 L3 注册）
- L2 inventory API：合并 builtin 与 L1 同步的 MCP
- L3：工具列表与 invoke 按 allowed_skills 过滤（见前文修复方向）

**优点**：不改 L1，不改打包格式，快速闭环。  
**缺点**：不走 L1 审核，无法多租户订阅、Store 分发。

---

#### 路径 2：L3 MCP 打包为 L2 MCP Server（复用现有 L1 流程）

**思路**：将 Python 逻辑包装成 MCP Server（stdio 进程），按现有 MCP 流程发布。

```
l3_node/mcp_tools/bi/tool_web_scraper.py
    → 封装为 MCP Server：python -m l3_node.mcp_tools.scraper_server
    → 实现 MCP 协议：tools/list、tools/call
    → 打包：plugin.json (item_type: MCP) + config.json (command: python ...)
    → jachin pack → L1 publish → 审核 → 订阅
    → L2 sync 下载到 mcps/{item_id}/
    → L2 inventory_scanner 注入 MCPManager
    → L3 经 L2 POST /api/v2/mcp/invoke 调用
```

**包结构示例**：

```
com.jachin.bi.atom_web_scraper_v1.0.0.zip
├── plugin.json          # id, name, item_type: MCP
└── config.json          # { "command": "python", "args": ["-m", "l3_node.mcp_tools.scraper_server"] }
```

或子目录结构：

```
mcps/
└── atom-web-scraper/
    ├── plugin.json
    ├── config.json
    └── tools/
        └── tool_web_scraper.py   # 需能作为模块被 server 导入
```

**需新增**：`scraper_server.py` 作为 MCP Server 入口，通过 stdio 与 MCP 协议通信。

**优点**：完全复用 L1→L2 现有流程，审核、订阅、同步、分配均可用。  
**缺点**：需为每个 L3 本地 MCP 写 MCP Server 包装，执行路径变为 L3→L2→MCP Server。

---

#### 路径 3：新增 L3_LOCAL 运行时类型（扩展 L1/L2/L3）

**思路**：定义 `runtime_tier: L3_LOCAL` 的 MCP 包，L2 同步后存到 L3 专用目录，L3 动态加载 Python 模块。

```
打包：plugin.json (item_type: MCP, runtime_tier: L3_LOCAL) + Python 源码
    → L1 publish → 审核 → 订阅
    → L2 sync 下载到 ~/.jachin/inventory/l3_mcps/{item_id}/
    → L2 不注入 MCPManager（L3_LOCAL 不经过 L2 执行）
    → L3 skill_sync 或等效逻辑从 L2 拉取到 ~/.jachin/l3_mcp_cache/
    → L3 动态 import 或 exec 加载，注册到 mcp_registry
    → 按 allowed_skills 过滤
```

**包结构**：

```
com.jachin.bi.atom_web_scraper_v1.0.0.zip
├── plugin.json          # item_type: MCP, runtime_tier: L3_LOCAL
├── tools/
│   ├── tool_web_scraper.py
│   └── tool_broadcaster.py
└── requirements.txt
```

**需改动**：
- L1：manifest 已支持 item_type、runtime_tier，需确保 MCP + L3_LOCAL 可发布
- L2 sync：MCP + L3_LOCAL 时下载到 `inventory/l3_mcps/` 或 `l3_mcp_cache/`
- L2 inventory：展示 l3_mcps，供角色分配，item_id 格式与 L3 一致（如 `mcp:atom_web_scraper`）
- L3：新增「L3 MCP 同步」逻辑，从 L2 拉取到本地缓存，动态加载并注册
- L3：`_build_allowed_ids` 支持 `mcp:` 前缀，role_permissions 的 item_id 与 L3 工具 id 对齐

**优点**：保留 L3 本地执行、低延迟，同时走 L1 审核与 Store 分发。  
**缺点**：需扩展 L1/L2/L3 多处，工作量最大。

### 6.3 架构对比

| 路径 | 经过 L1 | 审核 | L2 分配 | L3 执行位置 | 实现成本 |
|------|---------|------|---------|-------------|----------|
| 1. L2 Builtin | ❌ | ❌ | ✅ | L3 本地 | 低 |
| 2. MCP Server 包装 | ✅ | ✅ | ✅ | L2 代理→Server | 中 |
| 3. L3_LOCAL 扩展 | ✅ | ✅ | ✅ | L3 本地 | 高 |

### 6.4 闭环实现建议（采用路径 3）

| 阶段 | 建议 |
|------|------|
| **设计规范** | 采用路径 3：L3 优先执行，本机无则 L2 委托其他 L3。详见 [MCP_EXECUTION_MODEL.md](../../docs/MCP_EXECUTION_MODEL.md) |
| **短期** | 路径 1：L2 登记 L3 builtin MCP，修复 L3 的 allowed_skills 过滤，先形成「分配→执行」闭环 |
| **中期** | 路径 3：L2 sync 支持 L3_LOCAL MCP，L3 从 L2 拉取到 l3_mcp_cache 动态加载；L2 委托 fallback 待实现 |

### 6.5 关键命名对齐

无论选哪条路径，**item_id 与 L3 工具 id 必须一致**，否则 allowed_skills 无法匹配：

- L2 role_permissions.item_id：`mcp:atom_web_scraper`
- L3 工具 id：`mcp:atom_web_scraper`
- L1 plugin_id：可不同（如 `com.jachin.bi.atom_web_scraper`），但 L2 分配给角色时需映射为 `mcp:atom_web_scraper`

L1 manifest 返回的 id 是 plugins_registry.id（UUID），L2 下载目录用该 UUID。L2 需在 inventory 中维护 `plugin_id → mcp:atom_web_scraper` 的映射，供角色分配与 allowed_skills 下发使用。

---

## 七、相关文档

- [L1_L2_L3_END_TO_END_FLOW.md](../../docs/L1_L2_L3_END_TO_END_FLOW.md) — 端到端流程
- [SKILL_MCP_FLOW_AND_RECENT_CHANGES.md](../../docs/SKILL_MCP_FLOW_AND_RECENT_CHANGES.md) — Skill/MCP 流转
- [docs/bi_daily_report/](../../docs/bi_daily_report/) — BI 战报设计
- [JMP_SPEC.md](../../docs/JMP_SPEC.md) — JMP 包格式

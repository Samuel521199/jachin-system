# L3 轻量分发与订阅制品（Skill / MCP）

**Cursor 规则（核心提炼）**：`.cursor/rules/086-l3-slim-distribution-subscribed-artifacts.mdc`

本文约定：**目标机器上的 L3 本体下载保持轻量**（核心运行时 + IM/Lark 等通道能力）；**Skill 与 MCP 在上架（L1）并经订阅同步后，下载到本机缓存目录，在运行时即视为 L3 能力的一部分**。

---

## 1. 首先要明确的三件事

### 1.1 Skill / MCP 上架后，下载即可用

- 制品在 **L1（市场/制品库）** 上架后，经 **L2** 完成订阅、授权与同步。
- L3 将对应内容拉取到用户目录下的缓存（见下文路径）。
- 拉取完成后，**Agent 工具列表、Wasm 技能扫描、MCP 进程启动** 均从这些目录加载，**无需把业务技能打进 L3 安装包**。
- 因此：**下载后的 Skill/MCP 在运行语义上就是「当前这台 L3 的一部分」**——只是物理上与「侧车 exe」分离，由缓存统一挂载。

### 1.2 L3 本体的「轻量」指什么

- **便携包 / 安装包**侧：以 `scripts/build_full.ps1` 组装的 `dist_jachin_desktop` 为例，**不复制** 仓库内的 `skills_repo` 业务插件目录；脚本注释已写明：MCP/Skill 走 **L1 订阅 → L2 同步 → L3 拉取**。
- **PyInstaller 侧车（`l3_node-*.exe`）**：定位为 **agent + IM 核心**；**Wasm 技能在 frozen 模式下不扫描仓库内 `l3_node/skills/wasm_plugins/`**，只认 **`~/.jachin/l3_skill_cache/`**（与 `l3_node/skills/loader.py` 中 `_scan_wasm_plugins` 行为一致）。
- **Lark / IM**：作为 L3 的**通道与集成能力**，与「可订阅的业务 Skill/MCP」分层；侧车依赖中包含 `lark_oapi` 及 `l3_node/channels/lark`、`l3_node/im_channels` 等，属于 **L3 轻量本体** 应覆盖的范围。

### 1.3 「变成 L3 的一部分」的边界

| 层面 | 说明 |
|------|------|
| **运行时能力** | 订阅并缓存成功后，工具调用、MCP 子进程、Wasm 执行路径与「内置」一致，对用户与 Agent 无差别。 |
| **磁盘布局** | 能力文件在 `~/.jachin/l3_mcp_cache`、`l3_skill_cache` 等，与侧车 exe 分离，可单独更新、卸载。 |
| **二进制体积** | 侧车 exe 仍可能包含 **加载器、路由、Agent 里针对各域的胶水代码**（例如动态 `import` HR 调度模块的路径解析），这与「业务包不进安装 zip」不矛盾。 |

---

## 2. 当前实现是否满足上述目标

**结论：在「目标机器轻量安装 + 订阅后下载即用 + 运行时视为 L3 一部分」这一产品定义下，当前架构可以满足。**

依据摘要：

1. **便携包不随包分发 MCP/Skill 目录**（`build_full.ps1` 第 4 步明确不复制 MCP/Skill 仓库树）。
2. **frozen 下 Wasm 仅从 skill 缓存扫描**，避免把大型 wasm 树绑进 exe 开发目录扫描路径。
3. **MCP 插件**通过 `hr_loader` 等解析 **`l3_mcp_cache` 下带 `plugin.json` 的包目录**（及开发时的 `skills_repo` 覆盖策略），与「下载即用」一致。
4. **IM/Lark** 在侧车与 `config/im_channels.yaml` 等配置中集成，属于轻量本体能力。

**需要知晓的差异（与理想「二进制零业务代码」不完全等同）：**

- 侧车 **Python 包** 中仍包含 **按域拆分的胶水逻辑**（如招聘域的 Agent 分支、dispatcher 路由），体积与代码耦合大于零；若未来要 **exe 内零某业务域**，需单独做特性开关或分包，不在本文「轻量分发」的默认承诺内。
- PyInstaller 可能 **附带** `docs/L3_CAPABILITY_CATALOG.md` 及 `docs/capability_domains/*` 等数据文件，用于能力说明；与「是否携带 HR 业务包」无关，若需极致瘦身可再裁剪打包参数。

---

## 3. 目标机器上的目录角色（订阅后 = L3 扩展层）

以下路径以 `JACHIN_HOME` 默认为 `~/.jachin` 为例。

| 路径 | 内容 | 与 L3 的关系 |
|------|------|----------------|
| `~/.jachin/l3_mcp_cache/<item_id>/` | 已同步的 MCP 插件（含 `plugin.json`、`server.py`、`tools/` 等） | 运行时由 L3 发现并启动/代理，**即 L3 的 MCP 能力扩展** |
| `~/.jachin/l3_skill_cache/` | 已同步的 Wasm 技能包等 | frozen 下 **唯一** Wasm 扫描源之一，**即 L3 的技能扩展** |
| `~/.jachin/inventory/` 等 | L2 下发的清单、MCP 配置副本（视部署而定） | 与订阅与挂载策略相关 |
| 便携包目录下的 `bin/l3_node-*.exe` | 侧车本体 | **轻量核心 + Lark/IM** |
| 便携包目录下的 `config/*.yaml.example` | 通道/技能等示例配置 | 用户复制到 `~/.jachin/config/` 后生效 |

订阅前：上述 cache 可为空，L3 仍可启动（仅通用能力与 IM，视配置而定）；订阅并同步成功后，**同一进程内**即可加载新工具，无需重装侧车。

---

## 4. 端到端流程（运维 / 产品对齐用）

```text
L1 上架 Skill、MCP（及可选 Wasm 技能制品）
        ↓
L2：子账号订阅、策略授权、向节点同步清单与制品元数据
        ↓
L3：拉取到 ~/.jachin/l3_mcp_cache / l3_skill_cache
        ↓
Agent / HTTP / IM 路由按 registry 加载 → 行为上等同「L3 已安装该能力」
```

---

## 5. 与构建脚本的对应关系

| 脚本 | 作用 |
|------|------|
| `scripts/build_l3_sidecar.py` | 构建 **轻量侧车** `l3_node-*.exe`（核心 + 依赖；不含随包 MCP/Skill 目录） |
| `scripts/build_full.ps1` | 组装 **便携目录**：主程序 + `bin/l3_node` + 脚本 + **少量 config 示例**，**不复制** 仓库内 MCP/Skill 树 |

---

## 6. 验收建议（目标机器）

1. 干净用户：仅安装便携包 / 侧车，**无** `l3_mcp_cache` / `l3_skill_cache` 中对应业务包时，L3 与 Lark（按配置）可启动。
2. 在 L2 完成某 MCP/Skill 订阅并同步后，对应目录出现且 `plugin.json` / wasm 完整。
3. 重启或刷新技能列表后，**工具可见、可调用**，无需替换 `l3_node` exe。

---

## 7. 相关文档与配置

- 招聘域实施摘要、命令等：`docs/HR_RECRUITMENT.md`、`docs/HR_LARK_COMMANDS.md`（业务操作面）。
- IM 示例：`config/im_channels.yaml.example`。
- 能力总览（打包进侧车数据时）：`docs/L3_CAPABILITY_CATALOG.md`。

---

*文档版本：与仓库 `build_full.ps1` / `build_l3_sidecar.py` 及 `l3_node/skills/loader.py` 行为对齐；若打包策略变更，请同步更新本节与第 5 节。*

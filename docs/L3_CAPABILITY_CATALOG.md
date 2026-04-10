# L3 能力总目录（Jachin 执行节点）

**定位**：与具体业务域**解耦**的元层文档——说明系统身份、软/硬路由分工、**如何注册新 MCP/Skill 域**，供人工与 Agent 查阅。  
**各域细节**（工具映射、飞书硬指令、注入正文）放在 **`docs/capability_domains/*.md`**，由 `l3_node/capability_catalog.py` 按当前「可用工具」自动拼接。

**打包 L3（PyInstaller Sidecar）**：`scripts/build_l3_sidecar.py` 会将本文件与 `capability_domains/` 一并打入 `sys._MEIPASS/docs/`，与源码仓库路径一致；任意机器上的 frozen L3 均可读取，大模型仍能注入「是谁、会什么」。详见 Cursor 规则 **079-l3-capability-catalog.mdc**。

**四大原语**：本目录与 `capability_domains/*.md` 属于 **Skills（领域知识注入层）**，与 **Tools/MCP** 工具 id 列表互补；术语 SSOT 见 **[Jachin 视角的「四大原语」终极架构规范.md](./Jachin%20视角的「四大原语」终极架构规范.md)**。

| 资源 | 说明 |
|------|------|
| 技能格式 | [SKILL_MD_SPEC.md](./SKILL_MD_SPEC.md) |
| 域切片示例 | [capability_domains/hr_recruitment.md](./capability_domains/hr_recruitment.md)（招聘）；后续可增 `bi_daily_report.md` 等 |

---

## 1. 系统身份

| 项 | 说明 |
|----|------|
| **角色** | Layer 3 执行节点：ReAct Agent + 本地/订阅 MCP + Wasm 技能 +（可选）L2 记忆与协同 |
| **典型通道** | 飞书 IM、WebSocket 控制台、HTTP `agent/run` |
| **边界** | 不臆测外部系统已登录/已连接；以工具返回值与配置为准 |

---

## 2. 软路由 vs 硬约束（与域无关）

| 层级 | 职责 |
|------|------|
| **软路由（模型）** | 理解自然语言、在**当前可见工具列表**中选 MCP/技能、填参、多轮澄清 |
| **硬约束（代码）** | 短指令、秒停、调度与互斥、**不经 LLM** 的入站处理，保证可预测、可验收 |

**原则**：若某意图已被硬入口处理（例如 IM 侧拦截器直接回复并触发后台任务），模型在**同一管道**上不应再「用工具重复实现同一按钮」。在**未走硬入口**的通道（如纯 WebSocket/HTTP 长对话），仍应通过**相应 MCP** 落实等价意图。

硬入口实现位置随产品演进；飞书招聘类短指令当前集中在 `l3_node/lark_workflow_command_interceptor.py`。**新增硬指令时**：在对应域的 `capability_domains/<域>.md` 中登记，避免文档漂移。

---

## 3. 域注册（扩展 MCP / Skill 时）

每增加一类「希望模型自检」的领域，建议四步（**不修改本总目录正文结构**）：

1. **新建** `docs/capability_domains/<domain_id>.md`：写工具 id 片段映射、硬路径说明、并用 HTML 注释包一层 **`PROMPT_INJECT_<DOMAIN>_START` / `END`**（与 `capability_catalog.py` 中常量一致）。
2. **在** `l3_node/capability_catalog.py` 的 **`DOMAIN_REGISTRY`** 追加一条：`(domain_id, tool_markers_tuple, 相对 docs 的 md 路径, inject 锚点名前缀)`。
3. **（可选）** 为该域维护 `SKILL.md` / `plugin.json`，与 [SKILL_MD_SPEC.md](./SKILL_MD_SPEC.md) 一致。
4. **（可选）** 若该域需要独立 SOP 注入（如招聘），在 `agent_core._build_system_prompt` 中**仅按域 id 或 markers** 挂载 SKILL 正文，**勿**把域逻辑写进本文件。

---

## 4. 已登记域索引（人工维护）

| 域 id | 文档 | 说明 |
|--------|------|------|
| `hr_recruitment` | [capability_domains/hr_recruitment.md](./capability_domains/hr_recruitment.md) | Boss/Lark 招聘、无人值守、透析 MCP |
| `office_powerpoint_mcp` | [capability_domains/office_powerpoint_mcp.md](./capability_domains/office_powerpoint_mcp.md) | PPTX 创建/编辑（com.jachin.mcp.office_powerpoint） |

> 新域请在此表增加一行，并在 `DOMAIN_REGISTRY` 注册。

---

<!-- PROMPT_INJECT_CORE_START -->

### 【注入用 · 总目录核心】（凡加载工具列表时进入 system prompt）

你是 **Jachin L3 执行节点**上的助手。请根据上方 **「可用工具」中的 id** 判断当前已启用哪些能力；**不要假设**未出现在列表中的 MCP 或 Wasm 可用。

1. **分层**：长对话、多轮澄清、参数填写由你完成；**短指令、秒停、部分 IM 遥控**可能由代码硬路径处理——若用户说明来自飞书且极短，可能已被拦截；在 WebSocket/HTTP 等场景仍应对等价意图调用工具。

2. **多域**：若 prompt 中出现多个「域自检」段落，各自仅在该域工具可见时注入；只使用与当前用户意图相关的工具。

3. **细节**：各域的 MCP 对照表与典型调用顺序见 **紧随其后的域摘要**（若有）；逐步话术与分支以各域 **SKILL.md**（若有注入）为准。

<!-- PROMPT_INJECT_CORE_END -->

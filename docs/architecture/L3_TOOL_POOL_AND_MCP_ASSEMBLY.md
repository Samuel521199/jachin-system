# L3 工具池与 MCP 组装

**版本**: 1.0  
**定位**: 说明 L3 如何将 **Native / Wasm（jpp）** 与 **MCP（含 L2 下发）** 合并为送入 LLM 的 `tools[]`，并与 Claude Code 风格「全量定义 → 上下文过滤 → 组装池」概念对齐。  
**相关 SSOT**: `docs/Jachin 视角的「四大原语」终极架构规范.md`（`docs/FOUR_PRIMITIVES.md`）、`docs/architecture/JACHIN_HYBRID_AGENT_ARCHITECTURE.md`（L3 单主轴 ReAct 与工具派发链）、`l3_node/primitives/mcp/mcp_tools/MCP_LIFECYCLE_AND_APPROVAL_FLOW.md`。

**stdio MCP 与 npm**：`~/.jachin/mcp_servers.json` 中 `npx -y` 的包名**必须**与 npm 一致，合并示例前须核验，见 **`docs/MCP_SPEC.md` §3.5**（规则 **088-mcp-npm-package-verification**）。

---

## 1. 与 Claude Code 概念对照（简表）

| Claude 风格 | Jachin 对应 | 说明 |
|-------------|-------------|------|
| `getAllBaseTools` | `load_tools` + `NATIVE_TOOLS` / Wasm 扫描 | 宿主侧「可注册」工具定义的权威来源在 `l3_node/primitives/tools/loader.py`。 |
| `getTools(context)` | `load_tools(allowed_skills=…)` + `is_tool_allowed` | 按 Skill 白名单与 loader 规则裁剪 **内置与 jpp**；MCP 列表另由 registry 提供后再过滤。 |
| `assembleToolPool` | `assemble_tool_pool` | 实现于 `l3_node/primitives/tools/tool_pool.py`；`run_agent` await 调用（见下文锚点）。 |
| `filterToolsForAgent` | `allowed_skills` / RBAC 预检 / 通道特化 | 白名单：`is_tool_allowed`；RBAC：可跳过 MCP 合并；后台通道可剔除特定工具。 |
| 子 Agent 内层循环 | `SubAgent.run_once` → `run_agent` | 子 Agent 通过独立 system 与 `load_tools` 起步，仍进入同一套 `run_agent` 合并与 ReAct 路径。 |

---

## 2. 组装流水线（三阶段）

### 阶段 A — 内置池（`load_tools`）

- **输入**: `allowed_skills`（`None` = 开发态不过滤；非 `None` = 白名单语义以 loader 为准）。
- **输出**: 与 LiteLLM 对齐的 dict 列表，字段含 `id`、`label`、`desc`、`params` 等。
- **组成**: `core:*`（与 `core/native_tools.py` 对齐的清单）、`jpp:*`（Wasm 扫描与缓存技能）。
- **代码**: `l3_node/primitives/tools/loader.py` 中 `load_tools`。

### 阶段 B — MCP 池（`fetch_tools_from_l2`）

- **行为**: 从 L2/缓存拉取 MCP 工具定义，格式与 `load_tools` 一致（见 `l3_node/primitives/mcp/registry.py` 内说明）。
- **容错**: L2 不可用或异常时记录 debug 日志，**不阻断**主流程；本阶段可得到空列表。
- **代码**: `get_mcp_registry().fetch_tools_from_l2()`。

### 阶段 C — 合并与上下文过滤

由 `assemble_tool_pool` 串起上述阶段，顺序为：

1. `load_tools(allowed_skills=…)`  
2. **RBAC 预检**失败时**跳过** MCP 合并（仍保留内置池）。  
3. 否则 `await fetch_tools_from_l2()`；若 `allowed_skills` 非 `None`，对 MCP 项执行 `is_tool_allowed`。  
4. 内置列表与 MCP 列表拼接（MCP **追加**在末尾）。  
5. 若 `bg_channel == "background_task"`，剔除 `core:submit_background_task`。

**实现锚点** — `assemble_tool_pool`：

```17:63:l3_node/primitives/tools/tool_pool.py
async def assemble_tool_pool(
    *,
    allowed_skills: list[str] | None,
    gateway_bundle: Any = None,
    bg_channel: str | None = None,
    mcp_registry: MCPToolRegistry | None = None,
    logger: logging.Logger | None = None,
) -> list[dict[str, Any]]:
    """
    阶段 A: load_tools；阶段 B: fetch_tools_from_l2（可因 RBAC 跳过）；阶段 C: 白名单过滤 MCP、追加、通道剔除。
    """
    log = logger or logging.getLogger(__name__)
    tools = load_tools(allowed_skills=allowed_skills)
    skip_mcp_for_rbac = False
    if gateway_bundle is not None:
        try:
            from l3_node.intent_gateway.rbac_precheck import precheck_l2_subintent_allowed

            loc = "prefer_l2" if gateway_bundle.extra.get("attachment_forced_l2_routing") else "local_only"
            ok_rbac, rbac_reason = precheck_l2_subintent_allowed(gateway_bundle, locality=loc)
            if not ok_rbac:
                skip_mcp_for_rbac = True
                log.warning(
                    "[L3 Agent] RBAC 预检拒绝合并 L2 MCP locality=%s reason=%s",
                    loc,
                    rbac_reason,
                )
        except Exception as e:
            log.debug("[L3 Agent] RBAC MCP 预检跳过: %s", e)

    try:
        if not skip_mcp_for_rbac:
            from l3_node.primitives.mcp.registry import get_mcp_registry

            reg = mcp_registry if mcp_registry is not None else get_mcp_registry()
            mcp_tools = await reg.fetch_tools_from_l2()
            if mcp_tools:
                if allowed_skills is not None:
                    mcp_tools = [t for t in mcp_tools if is_tool_allowed(t["id"], allowed_skills)]
                tools = list(tools) + mcp_tools
                log.info("[L3 Agent] 已合并 %d 个 MCP 工具，总计 %d", len(mcp_tools), len(tools))
    except Exception as e:
        log.debug("[L3 Agent] MCP 工具拉取跳过（L2 可能未启动）: %s", e)

    if bg_channel == "background_task":
        tools = [t for t in tools if (t.get("id") or "").strip().lower() != "core:submit_background_task"]
    return tools
```

主 Agent 入口调用（`run_agent`）：

```3998:4003:l3_node/agent_core.py
    tools = await assemble_tool_pool(
        allowed_skills=allowed,
        gateway_bundle=_gateway_bundle,
        bg_channel=_bg_channel or None,
        logger=logger,
    )
```

**稳定排序**: 主路径合并后**未**统一调用 `sort_tools_by_id`；`coordinate` 等路径会对列表做按 `id` 排序（`l3_node/prompt_compose.py`）。四大原语规则中「MCP 工具在描述中相对靠后」由 **追加合并** 自然满足。若未来改为全局排序，须在规则与 prompt 策略中同步说明。

---

## 3. 不变量与策略（修改代码时请保持）

1. **内置先于 MCP**: 合并结果应为「`load_tools` 段 + MCP 段」，除非有明确产品理由改为可配置排序。  
2. **白名单一致**: MCP 与内置在「`allowed` 非 None」时应使用同一套 `is_tool_allowed`，避免双轨权限。  
3. **部分成功**: MCP 拉取失败不得让整次 ReAct 因工具列表而崩溃；与执行韧性规范一致（见 `.cursor/rules/080-jachin-execution-resilience.mdc`）。  
4. **单点组装**: 新增通道或子 Agent 特化过滤时，优先在**一处**集中处理或明确调用链，避免多处复制合并逻辑。  
5. **与 MCP 生命周期文档一致**: 开发态 `allowed=None`、生产白名单、审批与缓存行为以 `MCP_LIFECYCLE_AND_APPROVAL_FLOW.md` 为准。

---

## 4. 能力总目录与 Skills

- 工具 **id** 出现在 `tools[]` 中后，`capability_catalog` 才可能按域注入 prompt（见 `079-l3-capability-catalog.mdc`）。  
- 新增面向模型的 MCP 或域工具时，除 loader/registry 注册外，按 `docs/L3_CAPABILITY_CATALOG.md` 与 `docs/capability_domains/` 更新域切片。

---

## 5. 新工具接入 Checklist

- [ ] **内置**: 在 `loader.py` 的 `NATIVE_TOOLS`（或等价扫描路径）登记；`core/native_tools.py` 若有运行入口则同步。  
- [ ] **Wasm**: 放入约定目录并确保扫描与安全策略覆盖。  
- [ ] **MCP**: `registry` / L2 清单可发现；本地实现见 `l3_node/primitives/mcp/`。  
- [ ] **权限**: `is_tool_allowed` 与 Skill 白名单可解释；需要 L2 时核对 RBAC 与 `MCP_LIFECYCLE_AND_APPROVAL_FLOW.md`。  
- [ ] **Prompt**: 若属某能力域，更新 `capability_domains/*.md` 与 `DOMAIN_REGISTRY`。  
- [ ] **通道**: 后台任务、子 Agent、direct bypass 等路径下是否应隐藏或降级该工具——在 `agent_core` 相关分支中显式处理并在此文档「阶段 C」补一句说明。

---

## 6. 文档索引

| 主题 | 路径 |
|------|------|
| MCP 审批与双模式 | `l3_node/primitives/mcp/mcp_tools/MCP_LIFECYCLE_AND_APPROVAL_FLOW.md` |
| 四大原语 | `docs/Jachin 视角的「四大原语」终极架构规范.md` |
| MCP 协议与执行 | `docs/MCP_SPEC.md`、`docs/MCP_EXECUTION_MODEL.md` |
| L3 执行面总览 | `docs/ARCHITECTURE_V2_LAYER3_STANDALONE.md` |

# 08 — JPP 与技能生态（四大原语）

**文档类型**: 白皮书 · 技能生态规范  
**版本**: V2.3  
**更新日期**: 2026-06  
**术语 SSOT**：[Jachin 视角的「四大原语」终极架构规范.md](../Jachin%20视角的「四大原语」终极架构规范.md)

---

## 一、定位

**商品形态**（L1 商城）与 **执行原语**（L3 运行时）的映射：

| 商城商品 | 执行原语 | 运行时 |
|----------|----------|--------|
| MCP 包 | **MCP** | L3 stdio Host |
| Skill 包（含 SKILL.md） | **Skills** + 可能含 Wasm | Prompt/SOP + 可选 **Tools(jpp)** |
| Wasm 插件 | **Tools · jpp** | L3 `wasm_runner` 沙箱 |
| — | **Agent Tasks** | 非商城商品；运行时能力 |

**Write Once, Run Everywhere**：发布到 L1 → L2 sync → L3 cache 执行。

---

## 二、MCP（原语）

- **id 前缀**：`mcp:*`
- **Host**：L3 `core/mcp_client.py`（MCPManager）
- **配置**：`~/.jachin/mcp_servers.json`、`inventory/mcps/`、`l3_mcp_cache/`
- **跨节点**：L2 TaskManager 委托（见 MCP_EXECUTION_MODEL）
- **npm 包名**：须与 registry 一致（规则 088）
- SSOT：[MCP_SPEC.md](../MCP_SPEC.md)、[MCP_EXECUTION_MODEL.md](../MCP_EXECUTION_MODEL.md)

---

## 三、Skills（原语）

- **形态**：`SKILL.md`（YAML frontmatter + SOP 正文）
- **位置**：`skills_repo/**/SKILL.md`、商城 Skill zip、`docs/capability_domains/`
- **作用**：声明 Persona、工具/MCP 白名单、领域 SOP — **非**可执行二进制本体
- **加载**：Prompt 注入、域路由、`capability_catalog.py`
- SSOT：[SKILL_MD_SPEC.md](../SKILL_MD_SPEC.md)

---

## 四、Tools · jpp（Wasm）

**不受信任的第三方付费插件**，L3 沙箱执行。

### 4.1 执行边界

- **通信**：JSON stdin/stdout（WASI）
- **宿主**：`core/wasm_runner.py`（L3 路径；L2 仅 legacy）
- **燃料熔断**：死循环/OOM 时实例终止

### 4.2 plugin.json 示例

```json
{
  "name": "crypto-oracle",
  "version": "1.0.0",
  "item_type": "SKILL",
  "entry_point": "plugin.wasm",
  "royalty_fee": "0.01 USDC"
}
```

L1 统计调用 → `developer/earnings`、分润账本。

### 4.3 发布流程

```bash
# tools/jachin-cli/
jachin-cli pack
jachin-cli publish   # → POST /api/v1/store/publish
```

- PUBLIC：`status=pending` → Admin 审核  
- PRIVATE：`shadow_only` 仅 metadata，实体侧载 L2 inventory

### 4.4 SDK

- `jachin-plugin-sdk-python/` — `@jachin_plugin` → Wasm
- `jachin-plugin-sdk/` — Rust 侧

---

## 五、Agent Tasks（原语）

| 入口 | 说明 |
|------|------|
| `delegate` / SubAgent | 同步多轮子 Agent |
| `core:submit_background_task` | 异步队列 |
| `coordinate` | L2 多 L3 协同 |

非商城 SKU；见 [前台闲聊与后台重负荷任务的物理隔离与背压熔断.md](../前台闲聊与后台重负荷任务的物理隔离与背压熔断.md)。

---

## 六、能力登记与 Sidecar

- 新域须登记：[L3_CAPABILITY_CATALOG.md](../L3_CAPABILITY_CATALOG.md) + `l3_node/capability_catalog.py`
- Sidecar 打包：`scripts/build_l3_sidecar.py`（规则 079）
- 订阅制品路径：[L3_SLIM_DISTRIBUTION_AND_SUBSCRIBED_ARTIFACTS.md](../L3_SLIM_DISTRIBUTION_AND_SUBSCRIBED_ARTIFACTS.md)

---

## 七、废弃声明

1. ~~万物皆 Wasm~~ → 四大原语并存  
2. ~~轨道 A/B/C~~ → 统一术语  
3. ~~L2 作为 Wasm 主宿主~~ → **L3 执行**  
4. ~~Docker 技能容器~~ → Wasm + stdio MCP

---

## 八、参考

- [SKILL_MCP_UPLOAD_SPEC.md](../SKILL_MCP_UPLOAD_SPEC.md)
- [PLUGIN_SECURITY_SANDBOX.md](../PLUGIN_SECURITY_SANDBOX.md)
- [08 对应商城 Admin API](../ADMIN_PLUGIN_MANAGEMENT_API.md)

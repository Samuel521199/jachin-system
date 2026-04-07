# 08 — JPP 与技能生态（四大原语）

**文档类型**: 白皮书 · 技能生态规范  
**版本**: v8.0 (The Singularity OS)  
**术语 SSOT**：[Jachin 视角的「四大原语」终极架构规范.md](../Jachin%20视角的「四大原语」终极架构规范.md)（**已废弃**「轨道 A/B/C」命名）

---

## 一、 定位与愿景 (The Ecosystem Vision)

**“Write Once, Run Everywhere, Earn Crypto.”**

Jachin Nexus v8.0 以 **四大原语** 组织执行面：**MCP**、**Skills**、**Tools**（含 `core:*` 与 `jpp:*` Wasm）、**Agent Tasks**（多轮/后台/编排）。本文侧重 **Tools · jpp** 与生态；MCP / Skills 见专文。

| 原语 | 形态 | 信任级别 | 用途 |
|------|------|----------|------|
| **MCP** | Model Context Protocol 外挂 | 高信任（本机托管） | 开箱工具、系统控制 |
| **Skills** | SKILL.md 声明式 | 用户可控 | `skills_repo/` 热加载 |
| **Tools · jpp** | JPP Wasm 沙箱 | 零信任 | 商城第三方付费插件 |
| **Agent Tasks** | delegate / 后台 / coordinate | 独立预算与生命周期 | 多轮子运行时 |

---

## 二、 MCP（原语）

* **划时代意义**: 瞬间继承全球最大的 AI 工具生态。
* **实现**: L2 `core/mcp_client.py` / L3 `mcp_registry` 连接 MCP 服务器，发现 `mcp:*` 工具。
* **参考**: `docs/MCP_SPEC.md`

---

## 三、 Skills（原语）：SKILL.md 声明式技能

* **划时代意义**: 极低门槛。用户只需在 `skills_repo/` 丢一个 Markdown 文件。
* **格式**: YAML Frontmatter (name, description, persona, mcp_tools) + 自然语言指令正文。
* **热加载**: 保存文件瞬间，智能体立刻掌握新技能。
* **参考**: `docs/SKILL_MD_SPEC.md`

---

## 四、 Tools · jpp：JPP 插件协议 (Jachin Plugin Protocol)

**专供从“神经元商城”下载的、不信任的第三方付费插件使用**，确保商业生态的安全变现。

### 4.1 物理沙箱与 WASI 交互

* **通信媒介**: Layer 2 与 `.wasm` 之间，严格通过 JSON 格式的 `stdin` 和 `stdout` 通信。
* **执行边界**: `core/wasm_runner.py` 注入燃料，写入 Action Input，截获 stdout 作为 Observation。
* **燃料熔断**: 死循环或恶意占用时，燃料耗尽，Wasm 实例当场物理超度。

### 4.2 神经元清单 (`plugin.json`)

```json
{
  "name": "crypto-oracle",
  "version": "1.0.0",
  "description": "获取实时加密货币价格",
  "author": "0xYourWalletAddress",
  "royalty_fee": "0.01 USDC",
  "entry_point": "plugin.wasm",
  "schema": {
    "input": { "ticker": "string" },
    "output": { "price": "float", "trend": "string" }
  }
}
```

`royalty_fee` 是 JPP 的灵魂。Layer 1 统计调用次数，向开发者地址分润。

### 4.3 官方脚手架

* **jachin-plugin-sdk-python**: `@jachin_plugin` 装饰器，`make build` 一键编译为 Wasm。
* **上传**: 将 `plugin.wasm` + `plugin.json` 上传至云端商城。

### 4.4 去中心化编译器技能 (The Forge Compiler)

**`core:forge_compiler`** 是一个特殊的官方重型技能，允许边缘节点**消耗本地 CPU** 将 Python 脚本一键编译为 Wasm，减轻 Layer 1 云端压力。

* **定位**: 边缘侧编译，无需云端算力。
* **输入**: Python 源码路径（位于 `~/.jachin/workspace/` 内）。
* **输出**: 编译后的 `.wasm` 与 `plugin.json`，可直接写入 `skills_repo/`。

企业部署时，可在边缘节点本地完成技能编译与热加载，无需依赖云端 Forge 服务。

---

## 五、 v8.0 废弃声明

1. **❌ 废弃“万物皆 Wasm”**: **MCP + Skills + Tools(jpp)** 并存。
2. **❌ 废弃原生脚本裸跑**: 不受信任的第三方代码仍必须通过 Wasm 沙箱；MCP 与 SKILL.md 为用户可控的高信任扩展。
3. **❌ 废弃 Docker 技能容器**: Wasm 极速冷启动、极低内存占用，完美契合边缘端。
4. **❌ 废弃「轨道 A/B/C」文案**: 统一使用 **四大原语**（见仓库 SSOT 文档）。

# 安全策略 (Security Policy)

Jachin Nexus 将安全视为核心设计原则。我们极其骄傲的 **Wasm 物理沙箱** 与 **燃料熔断机制** 是抵御恶意插件的第一道防线。

---

## 🛡️ 沙箱与熔断机制

### The Abyss Wasm Sandbox

- **零信任执行**：所有第三方技能插件一律编译为 WebAssembly，在隔离沙箱中运行
- **燃料熔断 (Fuel Limit)**：每个插件有算力上限，死循环或恶意消耗会在燃料耗尽时**立即熔断**，宿主进程毫发无损
- **内存隔离**：Wasm 模块无法直接访问宿主内存或文件系统
- **WASI 可控**：Python (py2wasm) 插件通过 WASI stdin/stdout 与宿主通信，无任意文件/网络访问

### 实现位置

- `core/wasm_runner.py` — JachinWasmSandbox，run_plugin / run_plugin_wasi
- `core/plugin/validator.py` — Python 代码静态分析（AST 黑名单）
- `core/plugin/sandbox.py` — 受限 __builtins__ 执行（过渡方案）

详见 [docs/PLUGIN_SECURITY_SANDBOX.md](./docs/PLUGIN_SECURITY_SANDBOX.md)、[docs/HYBRID_SANDBOX_ARCHITECTURE.md](./docs/HYBRID_SANDBOX_ARCHITECTURE.md)。

---

## 🚨 漏洞报告

如果你发现安全漏洞，**请不要**在公开 Issue 中披露。

### 报告方式

- **邮箱**：security@jachin-nexus.dev（或项目维护者 GitHub 邮箱）
- **内容**：漏洞描述、复现步骤、影响范围、建议修复方向

### 响应承诺

- 我们会在 **72 小时内**确认收到报告
- 在修复完成前，不会公开披露漏洞细节
- 对于负责任的披露，我们会在修复公告中致谢（若你同意）

---

## 📋 支持的版本

| 版本 | 支持状态 |
|------|----------|
| v0.5.x | ✅ 当前支持，接收安全更新 |
| v0.4.x 及更早 | ⚠️ 仅重大漏洞，建议升级 |

---

感谢你帮助 Jachin Nexus 更加安全。

# 贡献指南 (Contributing to Jachin Nexus)

感谢你对 Jachin Nexus 的关注！我们欢迎所有形式的贡献。

---

## 提交规范

### 代码风格

| 语言 | 工具 | 要求 |
|------|------|------|
| **Python** | Black | 必须通过 `black --check .` |
| **Rust** | Clippy | 必须通过 `cargo clippy`，无 warning |
| **TypeScript/JavaScript** | ESLint + Prettier | 遵循项目现有配置 |

### 提交前检查

```bash
# Python
cd core && black . && pytest

# Rust（插件 SDK）
cd jachin-plugin-sdk && cargo clippy && make build
```

### Commit Message 规范

- 格式：`<type>: <description>`
- 类型：`feat` | `fix` | `docs` | `refactor` | `test` | `chore`

---

## 分支与 PR

1. Fork 本仓库
2. 从 `main` 拉取最新代码，创建功能分支
3. 提交更改，创建 Pull Request
4. 等待 Review

---

## Bug 报告

创建 Issue 时选择 **Bug 报告** 模板，提供环境信息、复现步骤、预期 vs 实际行为。

---

## 新技能需求 (Bounty Request)

有想要的插件但自己不会写？创建 Issue 时选择 **新技能需求 (Bounty Request)** 模板许愿，开发者可接单开发。

# SKILL.md 声明式技能规范

**版本**: v6.0  
**定位**: Layer 2 双轨制引擎 — 轨道 B

---

## 一、 划时代意义

**极低门槛的扩展**。用户只需在 `skills_repo/` 丢一个 Markdown 文件，定义 Persona (人设) 和关联的 MCP 工具链。引擎支持**热加载 (Hot-reloading)**，保存文件的瞬间，智能体立刻掌握新技能。

---

## 二、 文件结构

```
skills_repo/
├── github-pr-reviewer/
│   └── SKILL.md      # 技能定义
├── email-briefing/
│   └── SKILL.md
└── debugger/
    └── SKILL.md      # 高级：分析报错并自我修改代码
```

---

## 三、 SKILL.md 格式

### 3.1 前置 YAML Frontmatter

```yaml
---
name: github-pr-reviewer
description: Review GitHub pull requests and post feedback
persona: 专业、 constructive、区分 blocking 与 suggestion
mcp_tools: ["web_fetch", "github_api"]   # 关联的 MCP 工具
---
```

### 3.2 正文：自然语言指令

```markdown
# GitHub PR Reviewer

当被要求审查 Pull Request 时：

1. 使用 web_fetch 工具从 GitHub URL 获取 PR diff
2. 分析 diff 的正确性、安全性、代码风格
3. 结构化输出：Summary、Issues Found、Suggestions
4. 若被要求发布 review，使用 github_api 工具提交

始终保持建设性。将 blocking 问题与建议分开标注。
```

### 3.3 可选：示例对话

```markdown
## 示例

**用户**: 帮我 review 这个 PR: https://github.com/org/repo/pull/123

**Agent**: [使用 web_fetch 获取] → [分析] → [输出结构化 review]
```

---

## 四、 热加载机制

- **文件监听**: `core/skill_loader.py` 监听 `skills_repo/**/SKILL.md` 变化
- **解析**: 读取 YAML + Markdown，提取 name、description、persona、mcp_tools、指令正文
- **注入**: 将技能摘要（name、description、path）加入 Agent 的可用技能列表
- **按需加载**: Agent 决定调用某技能时，读取完整 `SKILL.md` 内容注入当前 Prompt

---

## 五、 与 MCP 的关系

- SKILL.md 通过 `mcp_tools` 声明依赖的 MCP 工具
- 若 MCP 未启用或工具不存在，该技能在注册时标记为不可用
- Agent 可组合多个 SKILL.md + MCP 工具完成复杂任务

---

## 六、 参考

- OpenClaw SKILL.md 设计（借鉴）
- `docs/whitepaper/06_LAYER2_EDGE.md` — 轨道 B 实现
- `docs/MCP_SPEC.md` — MCP 工具接入

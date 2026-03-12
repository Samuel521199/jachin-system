# SKILL.md 声明式技能规范

**版本**: v8.0 (The Singularity OS)  
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
tools:                                    # Native Core Fallback（防止 MCP 瘫痪导致技能失效）
  - prefer: "mcp:web_fetch"
    fallback: "core:fs_read"
  - prefer: "mcp:github_api"
    fallback: "core:shell_exec"
---
```

**Native Core Fallback (原生降级路由)**：为防止 MCP 瘫痪导致技能失效，系统内置安全的宿主原生标准库（如 `core:fs_read`、`core:shell_exec`），权限死锁在 `~/.jachin/workspace/` 目录下。当 `prefer` 工具调用失败时，自动无缝降级至 `fallback`。

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

- **文件监听**: `core/runtime/skill_loader.py` 监听 `skills_repo/**/SKILL.md` 变化
- **解析**: 读取 YAML + Markdown，提取 name、description、persona、mcp_tools、指令正文
- **注入**: 将技能摘要（name、description、path）加入 Agent 的可用技能列表
- **按需加载**: Agent 决定调用某技能时，读取完整 `SKILL.md` 内容注入当前 Prompt

---

## 五、 与 MCP 的关系

- SKILL.md 通过 `mcp_tools` 声明依赖的 MCP 工具
- 若 MCP 未启用或工具不存在，该技能在注册时标记为不可用
- Agent 可组合多个 SKILL.md + MCP 工具完成复杂任务

## 六、 Native Core Fallback (原生降级路由)

| 工具标识 | 说明 | 权限边界 |
|----------|------|----------|
| `core:fs_read` | 文件读取 | 仅限 `~/.jachin/workspace/` |
| `core:fs_write` | 文件写入 | 仅限 `~/.jachin/workspace/` |
| `core:shell_exec` | Shell 执行 | 工作目录死锁在 `~/.jachin/workspace/` |

当 MCP 工具调用失败（超时、连接断开、服务不可用）时，Agent 自动 catch 异常并路由至 `fallback` 指定的 Native Core 工具，确保技能不因 MCP 瘫痪而失效。

---

## 七、 参考

- OpenClaw SKILL.md 设计（借鉴）
- `docs/whitepaper/06_LAYER2_EDGE.md` — 轨道 B 实现
- `docs/MCP_SPEC.md` — MCP 工具接入

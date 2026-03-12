# HR 功能 MCP 测试说明

## 前置条件

1. **Chrome 调试模式**：用 `scripts\launch_chrome_debug.ps1` 启动 Chrome
2. **登录 Boss 直聘**：在 Chrome 中登录招聘端（招聘方账号，非求职端）
3. **JD 配置**：编辑 `data\jd_to_publish.json` 填写要发布的职位

## 一键验证（推荐）

```powershell
cd skills_repo\plugin

# 完整验证（需 Chrome + Boss 已登录）
python scripts\verify_all_recruitment_scripts.py

# 仅验证调度器等不依赖 Chrome 的部分
python scripts\verify_all_recruitment_scripts.py --dry

# 通过 L3 MCP 调用验证发布
python scripts\verify_all_recruitment_scripts.py --l3
```

## 测试脚本

### 1. 发布 JD (atom_post_job_boss)

```powershell
cd skills_repo\plugin

# MCP 模式（推荐）
python scripts\test_mcp_post_job_boss.py
python scripts\test_mcp_post_job_boss.py --config "data\jd_to_publish.json"

# 直接调用（不经过 MCP）
python scripts\test_mcp_post_job_boss.py --direct --config "data\jd_to_publish.json"
```

### 2. 推荐牛人打招呼 (atom_greet_recommend_boss)

**重要**：需先在浏览器中打开「推荐牛人」页面，再运行脚本。

```powershell
cd skills_repo\plugin

# MCP 模式（推荐）
python scripts\test_mcp_greet_recommend_boss.py
python scripts\test_mcp_greet_recommend_boss.py --config "data\jd_to_publish.json"

# 可选：跳过 brain_filter API，仅用规则兜底
$env:GREET_USE_RULE_ONLY="1"
python scripts\test_mcp_greet_recommend_boss.py

# 直接调用（不经过 MCP）
python scripts\test_mcp_greet_recommend_boss.py --direct --config "data\jd_to_publish.json"
```

### 3. 统一运行（两个测试一起跑）

```powershell
cd skills_repo\plugin

# 运行两个 MCP 测试
python scripts\run_mcp_hr_tests.py

# 仅发布 JD
python scripts\run_mcp_hr_tests.py --post-only

# 仅打招呼（需先在浏览器打开推荐牛人页面）
python scripts\run_mcp_hr_tests.py --greet-only

# 打招呼时用规则模式
python scripts\run_mcp_hr_tests.py --greet-rule-only
```

### 4. 列出 MCP 工具

```powershell
cd skills_repo\plugin
python scripts\test_mcp_hr_tools.py --list

# 执行指定工具测试
python scripts\test_mcp_hr_tools.py --test post_job
python scripts\test_mcp_hr_tools.py --test greet
```

## 常见问题

| 错误 | 原因 | 解决 |
|-----|------|------|
| 未找到浏览器上下文 | Chrome 未以调试模式启动 | 用 `launch_chrome_debug.ps1` 启动 |
| 未找到推荐牛人候选人卡片 | 当前在聊天页而非推荐牛人页 | 在 Boss 中打开「推荐牛人」页面后再运行 |
| 配置文件不存在 | 未创建 jd_to_publish.json | 复制 `jd_to_publish.example.json` 并编辑 |

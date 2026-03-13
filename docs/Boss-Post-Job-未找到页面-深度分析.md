# Boss 直聘「未找到页面」错误深度分析

## 一、错误现象

```json
{"success": false, "posted": false, "error": "未找到页面"}
```

调用 `mcp:atom_post_job_boss` 时返回，JD 配置已正确保存，但自动化发布失败。

---

## 二、错误触发位置

**文件**: `skills_repo/plugin/2-track-a-atomic-mcp/tools/atom_post_job_boss.py`  
**行号**: 123-125

```python
browser = pw.chromium.connect_over_cdp(cdp_url, timeout=5000)
contexts = browser.contexts
if not contexts:
    return {"success": False, "posted": False, "error": "未找到浏览器上下文"}
context = contexts[0]
pages = context.pages
if not pages:
    return {"success": False, "posted": False, "error": "未找到页面"}  # ← 此处
```

**含义**：CDP 已连接，浏览器上下文存在，但 `context.pages` 为空，即**当前上下文下没有任何标签页**。

---

## 三、根本原因分析

### 3.1 调用链

| 阶段 | 行为 |
|------|------|
| 1 | L3 Agent 收到 HR「同意」→ 调用 `atom_post_job_boss` |
| 2 | `mcp_registry._invoke_atom_post_job_boss_local` 执行 |
| 3 | `_check_chrome_cdp(9222)` 检测 Chrome 是否可连 |
| 4 | 若未连接：`_launch_chrome_with_boss_login` 启动 Chrome，并打开 Boss 登录页 |
| 5 | 等待最多 12 秒直到 CDP 可连 |
| 6 | 调用 `atom_post_job_boss(cdp_url, jd_config_path)` |
| 7 | Playwright 连接 CDP → 取 `context.pages` → **若为空则返回「未找到页面」** |

### 3.2 为何 `context.pages` 会为空？

| 原因 | 说明 |
|------|------|
| **A. 启动时序** | Chrome 刚启动时，CDP 先可用，但首屏页面尚未加入 `context.pages`，存在短暂空窗期 |
| **B. 连接错实例** | 9222 上已有其他 Chrome 进程（非本次启动），该实例可能无标签页或已关闭所有标签 |
| **C. 用户关闭标签** | 用户手动关闭了唯一标签，导致 `pages` 为空 |
| **D. 多用户数据目录** | 不同启动方式使用不同 `--user-data-dir`，连接到的实例与预期不一致 |
| **E. 启动参数未带 URL** | 若通过 `launch_chrome_debug.ps1` 无参数启动，Chrome 可能只开空白页，且创建有延迟 |

### 3.3 与「未找到浏览器上下文」的区别

- **未找到浏览器上下文**：`browser.contexts` 为空，通常表示 CDP 连接异常或浏览器状态异常
- **未找到页面**：`contexts` 存在，但 `context.pages` 为空，表示**有上下文但无标签页**

---

## 四、解决方案

### 4.1 立即可做的操作

1. **确认 Chrome 已打开且至少有一个标签**
   - 检查 9222 对应的 Chrome 窗口
   - 至少保留一个标签（建议为 Boss 直聘页面）

2. **使用正确脚本启动 Chrome**
   ```powershell
   .\scripts\launch_chrome_debug.ps1 "https://www.zhipin.com/web/user/?ka=header-login"
   ```
   - 启动后等待 Boss 页面完全加载
   - 登录完成后再执行发布

3. **避免多实例冲突**
   - 关闭其他使用 9222 的 Chrome 进程
   - 确保只保留一个调试模式 Chrome

### 4.2 代码层面改进（建议）

在 `atom_post_job_boss` 中，当 `pages` 为空时增加兜底逻辑：

1. **短时重试**：等待 2–5 秒后再次检查 `context.pages`
2. **新建页面**：若仍为空，则 `page = context.new_page()`，并 `page.goto(Boss 职位管理页)`
3. **错误信息增强**：将「未找到页面」改为更具体的提示，例如「Chrome 已连接但无标签页，请确保至少打开一个 Boss 直聘页面后重试」

### 4.3 推荐操作流程

```
1. 运行 .\scripts\launch_chrome_debug.ps1 "https://www.zhipin.com/web/user/?ka=header-login"
2. 在 Chrome 中扫码登录 Boss 直聘
3. 保持至少一个 Boss 直聘标签页打开（职位管理或沟通页均可）
4. 回复「已登录」或「继续发布」
5. 系统再次调用 atom_post_job_boss
```

---

## 五、技术细节

### 5.1 CDP 检测与页面检测的差异

`_check_chrome_cdp` 仅请求 `http://127.0.0.1:9222/json/version`，能说明：
- Chrome 进程在运行
- 调试端口可访问

**不能说明**：
- 是否存在浏览器上下文
- 是否存在标签页

因此可能出现「CDP 检测通过」但「未找到页面」的情况。

### 5.2 Playwright 与 CDP 的关系

- `connect_over_cdp` 连接到已有 Chrome
- `browser.contexts` 来自 Chrome 的 Browser 上下文
- `context.pages` 为该上下文下的所有标签页
- 若 Chrome 刚启动或所有标签已关闭，`pages` 可能为空

---

## 六、参考

- `atom_post_job_boss.py` 第 116-125 行
- `mcp_registry.py` 中 `_launch_chrome_with_boss_login`、`_invoke_atom_post_job_boss_local`
- `scripts/launch_chrome_debug.ps1`

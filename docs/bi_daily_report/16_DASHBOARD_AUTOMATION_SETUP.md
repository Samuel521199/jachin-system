# 仪表盘自动化配置指南（Step 4）

在 BI 每日战报流程的数据同步、Lark 推送、邮件发送完成后，Step 4 会对每个仪表盘执行：
1. 调用 LLM 基于对应 CSV 数据生成图表分析
2. 将分析保存为 MD 文档到 `~/.jachin/client_volumes/bi_data/output/统计分析/仪表盘分析_xxx.md`（子目录可由 `dashboard_automation.analysis_output_subdir` 配置）
3. 打开 Lark 仪表盘 → 点击「设置自动化发送」→「更多配置」→ 在时间框设置定时、在内容框填入 MD 分析 →「保存并启用」

通过 Lark 的「定时发送」功能，在设定时间自动将仪表盘图表+分析推送到群，而非直接发消息。

## 前置条件

- **浏览器**（二选一）：
  - **自动模式**（`cdp_url` 留空）：Playwright 自动启动 Chromium，首次运行会弹出浏览器，需手动登录 Lark，登录态保存在 `~/.jachin/lark_automation_browser`
  - **CDP 模式**（`cdp_url` 填 `http://127.0.0.1:9222`）：连接已以调试模式启动的 Chrome，需事先登录 Lark
- **仪表盘 URL**：在 Lark 多维表格中打开每个仪表盘，从地址栏复制完整 URL 到 `dashboards[].url`

## 配置步骤

### 1. 获取仪表盘 URL

在 Lark 多维表格左侧栏依次点击三个仪表盘：
- 仪表盘_用户登录活跃情况
- 仪表盘_平台留存情况
- 仪表盘_平台消耗情况

每次点击后，从浏览器地址栏复制完整 URL（形如 `https://xxx.feishu.cn/base/AppToken?view=blkXXX` 或 `https://xxx.sg.larksuite.com/base/...`）。

### 2. 配置 URL

在 `config/skills/com.jachin.bi.daily_report/bi_daily_report.yaml` 中：

**方式 A：直接写 URL**
```yaml
dashboard_automation:
  enabled: true
  scheduled_time: "18:05"
  dashboards:
    - name: "仪表盘_用户登录活跃情况"
      url: "https://xxx.feishu.cn/base/HCvubvsubak2WPs2GbClYq68gjh?view=blkXXX"
    - name: "仪表盘_平台留存情况"
      url: "https://xxx.feishu.cn/base/HCvubvsubak2WPs2GbClYq68gjh?view=blkYYY"
    - name: "仪表盘_平台消耗情况"
      url: "https://xxx.feishu.cn/base/HCvubvsubak2WPs2GbClYq68gjh?view=blkZZZ"
```

**方式 B：环境变量**
在 `.env` 中设置：
```
BI_LARK_DASHBOARD_1_URL=https://xxx.feishu.cn/base/...
BI_LARK_DASHBOARD_2_URL=https://xxx.feishu.cn/base/...
BI_LARK_DASHBOARD_3_URL=https://xxx.feishu.cn/base/...
```

配置中保持：
```yaml
  dashboards:
    - name: "仪表盘_用户登录活跃情况"
      url: "${BI_LARK_DASHBOARD_1_URL}"
    ...
```

### 3. 定时时间

`scheduled_time` 在 `dashboard_automation.scheduled_time` 中配置，格式为 `HH:MM`（如 `18:08`），可手动修改。

### 4. 独立测试

仅测试「分析仪表盘 + 配置 Lark 定时发送」：

```bash
python scripts/run_bi_dashboard_automation.py              # 用 config 中的 scheduled_time
python scripts/run_bi_dashboard_automation.py --time 18:05 # 覆盖定时为 18:05
python scripts/run_bi_dashboard_automation.py --dry-run    # 只生成分析并保存，不打开 Lark
```

前置：output 目录需有 CSV（可先跑一次 `run_bi_analysis`）。cdp_url 留空时 Playwright 会启动浏览器，首次需登录 Lark。

### 5. 禁用 Step 4

若暂不需要仪表盘自动化，设置 `dashboard_automation.enabled: false`。
image.png
## Lark 自动化 UI 流程（Playwright 自动执行）

Step 4b 的 Playwright 操作顺序：
1. 打开仪表盘 URL
2. 点击「设置自动化发送」按钮
3. 点击「更多配置」链接
4. 在「编辑自动化流程」弹窗中：填入分析文本（图4 红框）、设置定时为 `scheduled_time`（图5 红框）
5. 点击「保存并启用」（图6）

若 Lark 界面改版导致选择器失效，可在 `dashboard_automation.py` 的 `setup_lark_dashboard_automation_via_browser` 中调整选择器。

## 输出文件

分析结果保存为 MD 文档到 `output/统计分析/` 目录：
- `仪表盘分析_仪表盘_用户登录活跃情况.md`
- `仪表盘分析_仪表盘_平台留存情况.md`
- `仪表盘分析_仪表盘_平台消耗情况.md`

## 与 L3 Agent 的关系

Step 4 由 `main_skill` 在 `run_bi_daily_report` 流程中**同步执行**，无需 L3 Agent 单独触发。当用户发起「BI 分析」等意图时，L3 Agent 调用 `run_bi_daily_report`，整个流程（含 Step 4）自动运行。Playwright 在各仪表盘配置「设置自动化发送」后，Lark 会在设定时间自动推送仪表盘图表+分析到群。

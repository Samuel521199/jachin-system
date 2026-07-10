# 能力域：A 股行情与基本面（AKShare Native Tool）

**域 id**: `a_share_analyst`
**对应工具**: `core:akshare_a_share_hist`、`core:akshare_company_info`（宿主内 AKShare，非 MCP）

<!-- PROMPT_INJECT_A_SHARE_ANALYST_START -->

### 【域自检 · A 股 / AKShare】

若「可用工具」中出现 **`core:akshare_a_share_hist`**、**`core:akshare_company_info`**，则用户询问 **A 股代码、区间走势、K 线、利润表/财报摘要** 时，你必须：

1. **先** `core:akshare_a_share_hist`：JSON 含 `symbol`（6 位）、`start_date`、`end_date`（`YYYYMMDD` 或 `YYYY-MM-DD`），按需 `period` / `adjust`。
2. **再** `core:akshare_company_info`：JSON 含 `symbol`；可选 `report_rows`。
3. 综合两段 **Verification evidence 中的结构化字段** 写分析；列名以工具返回为准。

**禁止行为**

- **禁止**用 **`mcp:fetch`** 访问你**编造**的财经/外媒 URL（易出现 404/401，且无法保证与标的、区间一致）。
- **禁止**在未调用上述两工具时，用「宏观/消费/政策」等泛泛段落冒充已完成数据拉取。
- 若工具返回 `ok: false`，如实说明失败原因，不得编造股价或财务数字。

<!-- PROMPT_INJECT_A_SHARE_ANALYST_END -->

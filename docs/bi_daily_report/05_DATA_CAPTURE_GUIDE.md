# BI 数据抓取使用指南

**适用**: A 系统（mcp:atom_web_scraper）  
**契约**: [01_PARALLEL_DEVELOPMENT_GUIDE.md](./01_PARALLEL_DEVELOPMENT_GUIDE.md)

---

## 一、两种抓取模式

| 模式 | 适用场景 | 前置条件 |
|------|----------|----------|
| **API 模式** | 数据源提供 REST API 返回 JSON | 无需浏览器，直接 HTTP 请求 |
| **SPA 模式** | 自研后台（如 bi-admin-web）需登录后查看表格 | Chrome 调试模式 + 已登录 |

---

## 二、重要：首页 ≠ 数据页

Heron-Bi-Admin 左侧菜单结构：

| 菜单项 | 说明 | 抓取目标 |
|--------|------|----------|
| 仪表盘 / 首页 | 个人信息、数据统计卡片 | ❌ 非业务数据 |
| **平台数据** | 子菜单，含具体数据表 | ✅ 需抓取 |
| **数据统计分析** | 子菜单，含统计报表 | ✅ 需抓取 |
| **数据明细** | 子菜单，含明细列表 | ✅ 需抓取 |

**`#/layout/person` 是首页/个人信息页，不含平台数据、统计分析、明细表格。** 抓取时需使用各数据页的实际 URL。

### 全自动 vs 手动

| 方式 | 说明 |
|------|------|
| **全自动** | 配置 `automation` 后，工具自动：点击菜单 → 填写日期筛选 → 点击查询 → 抓取表格，**无需手点** |
| 手动 | 仅传 URL，需手动在浏览器中点击到数据页再执行抓取 |

---

## 三、SPA 模式：抓取 BI 后台表格

### 3.1 前置步骤

1. **启动 Chrome 调试模式**（任选其一）：
   - Windows: `chrome.exe --remote-debugging-port=9222`
   - macOS: `/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222`
   - 项目脚本: `.\scripts\launch_chrome_debug.ps1 "https://bi-admin-web.heronpro.xin/#/layout/person"`（打开登录入口）

2. **在 Chrome 中完成登录**：
   - 访问上述地址，使用账号密码登录
   - 登录后工具会自动执行配置的操作（点击菜单、填写筛选等），无需手动切换

### 3.2 全自动抓取（推荐）

配置 `automation` 后，工具自动完成：点击菜单进入数据页 → 填写日期范围 → 点击查询 → 抓取表格。

```python
from l3_node.mcp_tools.bi.tool_web_scraper import harvest_table_data
from l3_node.mcp_tools.bi.paths import get_bi_raw_dir, ensure_bi_dirs
from datetime import datetime, timedelta

ensure_bi_dirs()
# 计算昨日与今日日期
today = datetime.now().strftime("%Y-%m-%d")
yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

result = harvest_table_data(
    url="https://bi-admin-web.heronpro.xin/#/layout/person",
    output_path=str(get_bi_raw_dir() / "platform_data.csv"),
    config={
        "cdp_url": "http://127.0.0.1:9222",
        "output_format": "csv",
        "timeout": 30,
        "automation": {
            "start_url": "https://bi-admin-web.heronpro.xin/#/layout/person",
            "actions": [
                {"type": "click", "selector": "text=平台数据"},
                {"type": "wait", "ms": 500},
                {"type": "click", "selector": "text=某子菜单"},  # 替换为实际子菜单
                {"type": "wait", "selector": ".el-date-editor", "timeout": 5},
            ],
            "filters": {
                "date_range": [yesterday, today],  # 日期范围筛选
                "date_range_selectors": {
                    "start": ".el-date-editor input:first-of-type",
                    "end": ".el-date-editor input:last-of-type",
                },
                "query_selector": "button:has-text('查询')",
                "wait_for_loading_hidden": ".el-loading-mask",  # 等待加载遮罩消失
                "wait_after_query_ms": 5000,          # 弱网可调大，如 10000
                "wait_for_data_timeout": 30,          # 最大等待秒数
            },
        },
    },
)
print(result)
```

**如何获取选择器**：在 Chrome 中右键目标元素 → 检查 → 在 Elements 面板中查看 class、id 或文本，用 `text=平台数据`、`.el-menu-item` 等 Playwright 选择器。

### 3.3 调用方式（单页 / 多页）

**单页抓取**（传入具体数据页 URL，无 automation）：

```python
from l3_node.mcp_tools.bi.tool_web_scraper import harvest_table_data
from l3_node.mcp_tools.bi.paths import get_bi_raw_dir, ensure_bi_dirs

ensure_bi_dirs()
# 使用平台数据/统计分析/明细页的实际 URL，而非 #/layout/person
result = harvest_table_data(
    url="https://bi-admin-web.heronpro.xin/#/layout/platform-data/xxx",  # 替换为实际数据页 URL
    output_path=str(get_bi_raw_dir() / "platform_data_20260316.csv"),
    config={
        "cdp_url": "http://127.0.0.1:9222",
        "output_format": "csv",
        "timeout": 15,
    },
)
```

**多页抓取**（平台数据 + 统计分析 + 明细）：

```python
from l3_node.mcp_tools.bi.tool_web_scraper import harvest_table_data
from l3_node.mcp_tools.bi.paths import get_bi_raw_dir, ensure_bi_dirs
from datetime import datetime

ensure_bi_dirs()
date_str = datetime.now().strftime("%Y%m%d")
urls = [
    ("https://bi-admin-web.heronpro.xin/#/layout/platform-data", "platform"),
    ("https://bi-admin-web.heronpro.xin/#/layout/statistics", "statistics"),
    ("https://bi-admin-web.heronpro.xin/#/layout/data-detail", "detail"),
]
cfg = {"cdp_url": "http://127.0.0.1:9222", "output_format": "csv", "timeout": 15}
for url, name in urls:
    r = harvest_table_data(url, str(get_bi_raw_dir() / f"{name}_{date_str}.csv"), cfg)
    print(f"{name}: {r}")
```

**MCP 调用**（同样传入数据页 URL）：

```json
{
  "tool_id": "mcp:atom_web_scraper",
  "arguments": {
    "url": "https://bi-admin-web.heronpro.xin/#/layout/platform-data",
    "output_path": "~/.jachin/client_volumes/bi_data/raw/platform_20260316.csv",
    "cdp_url": "http://127.0.0.1:9222",
    "config": { "output_format": "csv", "timeout": 15 }
  }
}
```

### 3.3 本地快速测试

```bash
# 1. 启动 Chrome 并登录
# 2. 点击「平台数据」等菜单，复制当前页 URL
# 3. 将下方 URL 替换为实际数据页地址后执行
python -c "
from l3_node.mcp_tools.bi.tool_web_scraper import harvest_table_data
from l3_node.mcp_tools.bi.paths import get_bi_raw_dir, ensure_bi_dirs
ensure_bi_dirs()
r = harvest_table_data(
    'https://bi-admin-web.heronpro.xin/#/layout/xxx',  # 替换为点击「平台数据」后地址栏的实际路径
    str(get_bi_raw_dir() / 'test.csv'),
    {'cdp_url': 'http://127.0.0.1:9222', 'output_format': 'csv', 'timeout': 15}
)
print(r)
"
```

---

## 四、配置说明（config）

| 参数 | 类型 | 说明 |
|------|------|------|
| `cdp_url` | string | Chrome 调试地址，默认 `http://127.0.0.1:9222` |
| `output_format` | string | `json` \| `csv`，默认 `json` |
| `extract_rules` | string | 表格 CSS 选择器，如 `table, .el-table, .ant-table` |
| `headers` | object | API 模式下的 HTTP 请求头 |
| `timeout` | int | 超时秒数，默认 30 |
| `automation` | object | 全自动配置，见下 |

### automation 配置

| 字段 | 类型 | 说明 |
|------|------|------|
| `start_url` | string | 入口页 URL，默认与 url 相同 |
| `actions` | array | 操作序列：`{type, selector?, value?, ms?, timeout?}` |
| `filters` | object | 筛选条件：`date_range`, `query_selector`, `wait_for_loading_hidden` 等 |
| `expand_table_rows` | bool | 抓取前展开所有树形行，纳入子项（渠道明细、各游戏数据等）。默认 false |
| `expand_selector` | string | 未展开图标选择器，默认 Element UI：`.el-table__expand-icon:not(.el-table__expand-icon--expanded)` |
| `expand_wait_ms` | int | 每次展开后等待毫秒，默认 600，弱网可调大至 800 |
| `split_merged_cells` | bool | 拆分「当前值 (+X%) 上期值」合并单元格为「当前值 \| 环比 \| 上期值」。默认 true |

**filters 等待相关**（确保数据完全加载，弱网环境可调大）：
- `wait_for_loading_hidden`：加载遮罩选择器（如 `.el-loading-mask`），等待其隐藏表示数据就绪
- `wait_after_query_ms`：无遮罩时的固定等待毫秒，默认 5000，弱网可调至 10000~15000
- `wait_for_data_timeout`：等待数据就绪的最大秒数，默认 30

---

## 五、输出契约

| 成功 | `{"status": "success", "file_path": "...", "rows_count": N}` |
|------|-------------------------------------------------------------|
| 失败 | `{"status": "error", "error": "错误描述"}` |

数据存储路径：`~/.jachin/client_volumes/bi_data/raw/YYYYMMDD.csv` 或 `.json`

---

## 六、常见问题

| 问题 | 处理 |
|------|------|
| `connect` / `Target` 错误 | 确认 Chrome 以 `--remote-debugging-port=9222` 启动 |
| 未找到浏览器上下文 | 检查 Chrome 是否已打开至少一个标签页 |
| 未提取到表格数据 | 检查页面是否已加载完成；可调整 `extract_rules` 或 `table_selector` |
| 日期填写无效 | Element UI 日期选择器若为弹窗日历，`fill` 可能无效；可改用 `actions` 中先 `click` 打开面板再 `click` 选择日期 |
| 抓取到部分数据 | 增大 `wait_after_query_ms`（如 10000）、`wait_for_data_timeout`（如 30）；配置 `wait_for_loading_hidden` 等待加载遮罩消失 |
| 渠道/汇总只有 ALL，缺子项 | 配置 `expand_table_rows: true`，抓取前自动展开树形行；弱网可调大 `expand_wait_ms`（如 600） |
| `playwright 未安装` | 执行 `pip install playwright && playwright install chromium` |

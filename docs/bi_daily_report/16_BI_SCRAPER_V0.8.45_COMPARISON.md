# BI 抓数据部分：当前项目 vs 云端 v0.8.45 对比

## 结论

| 模块 | 文件 | 与 v0.8.45 是否一致 |
|------|------|---------------------|
| **抓数据** | `l3_node/primitives/mcp/mcp_tools/bi/spa_collector.py` | ❌ **有差异** |
| **抓数据** | `l3_node/primitives/mcp/mcp_tools/bi/tool_web_scraper.py` | ❌ **有差异** |
| **存数据库** | `l3_node/primitives/mcp/mcp_tools/bi/data_store.py` | ✅ 一致 |
| **存数据库** | `l3_node/primitives/mcp/mcp_tools/bi/paths.py` | ✅ 一致 |
| **脚本** | `scripts/run_bi_scraper_spa.py` | ✅ 一致 |
| **脚本** | `scripts/import_raw_to_duckdb.py` | ✅ 一致 |

**存数据库部分（data_store、paths）已与 v0.8.45 一致。**
**抓数据部分（spa_collector、tool_web_scraper）当前项目有本地修改，与 v0.8.45 不同。**

---

## 差异详情

### 1. spa_collector.py

| 位置 | v0.8.45（云端） | 当前项目 |
|------|-----------------|----------|
| 首步 action | `wait_visible` | `expand_sidebar_if_collapsed` + `wait_attached` |
| 循环内等待 | `wait_visible` | `wait_attached` |
| 点击叶子菜单 | `click`（无 force） | `click` + `force: True` |

### 2. tool_web_scraper.py

| 功能 | v0.8.45（云端） | 当前项目 |
|------|-----------------|----------|
| `click` 默认 force | `False` | `True` |
| `click_if_exists` 默认 force | `False` | `True` |
| `click` / `click_if_exists` | 直接 `page.locator(sel).first` | 优先 `filter(state="visible")` |
| `click_expand` | 有 `scroll_into_view_if_needed`，复杂 fallback | 移除 scroll，直接 force 点击 |
| `wait_attached` | 无 | 新增 |
| `expand_sidebar_if_collapsed` | 无 | 新增（侧栏折叠时展开/强制显示） |
| `click_expand_first_row` | timeout 3000 | timeout 5000，force=True |
| SPA 挂载缓冲 | 1000ms | 2000ms + 等待 `.el-menu` attached |

---

## 侧栏折叠兼容（已加回）

v0.8.45 在同事电脑（侧栏默认展开）可正常运行，但若本地 BI 侧栏**默认折叠**，`wait_visible` 会因 `locator resolved to hidden` 超时失败。

已加回最小改动以兼容两种环境：
- `expand_sidebar_if_collapsed`：检测折叠时点击展开或注入 CSS 强制显示菜单文字
- `wait_attached`：在展开前仅等待元素挂载，不要求可见
- `click` 加 `force: True`：确保折叠状态下仍可点击

同事侧栏展开时，`expand_sidebar_if_collapsed` 无实际效果，行为与 v0.8.45 一致。

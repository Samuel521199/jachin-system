# BI 抓数据 "element is not visible" 深度根因分析

## 一、现象与关键线索

### 1.1 错误信息

```
action[3] click_expand failed: Locator.scroll_into_view_if_needed: Timeout 5000ms exceeded.
Call log:
  - attempting scroll into view action
    2 × waiting for element to be stable
      - element is not visible
```

选择器：`.el-menu >> text=平台数据`
失败阶段：`scroll_into_view_if_needed`（在执行 `click_expand` 时，先滚动元素到视口）

### 1.2 矛盾现象：首项成功，后续全部失败

| Slug | 结果 | 说明 |
|------|------|------|
| 1/34 daily_ops_summary | ✅ OK (10 rows) | 平台数据 → 日常报表 → 每日运营数据汇总，全流程正常 |
| 2/34 daily_ops_compare | ❌ FAIL | action[3] click_expand 平台数据 失败 |
| 3/34 daily_acquisition | ❌ FAIL | 同上 |
| 4/34 prod_sales | ❌ FAIL | 同上 |

同一套自动化步骤、同一选择器，第一个 slug 完全成功，后续全部在同一位置失败。

### 1.3 执行顺序

每次 `harvest_table_data` 调用都会：

1. `with sync_playwright()` 新建 Playwright 连接
2. `connect_over_cdp(cdp_url)` 连到已有 Chrome
3. 选择 `target_page`（优先 URL 匹配，否则 `pages[0]`）
4. `target_page.goto(base_url)` 导航到 `#/layout/person`
5. `wait_for_load_state("networkidle")` + `wait_for_timeout(1000)`
6. 执行 automation：expand_sidebar → wait_attached → click_expand → ...

---

## 二、根因分析

### 根因 1：每次 slug 独立连接 + 独立 goto，导致 SPA 状态不一致

每个 slug 都会单独执行一次 `goto(base_url)`。对于 Vue Router 等 SPA：

- **首次加载**：完整 HTML 加载 + 应用初始化，侧栏 DOM 为初始状态
- **后续加载**：可能走 client-side 导航，不触发完整 reload，DOM 可能复用或部分更新

结果：第二次及以后的 `goto` 后，侧栏 DOM 可能与前一次页面留下的状态混在一起（例如仍保留上次展开/折叠），或路由缓存导致渲染与首屏不同。

### 根因 2：`wait_attached` 与 `element is not visible` 的区别

当前流程：

- `wait_attached`：只要求元素在 DOM 中
- `scroll_into_view_if_needed`：要求元素可见（在视口内、未被隐藏）

`attach` 成功而 `visible` 失败，说明：元素在 DOM 中存在，但被判定为不可见。常见原因：

1. `visibility: hidden`（如 Element UI 折叠时的菜单文字）
2. 在 `overflow: hidden` 的可滚动容器中，且不在当前可视区
3. 被其他元素遮挡或 `opacity: 0`
4. `display: none`（一般不会 attach，但某些框架可能用其他方式挂载）

结合 18_SCRAPER_HIDDEN_ROOT_CAUSE_ANALYSIS.md：`.el-aside .el-menu` 为 0，说明侧栏可能并未使用 `.el-aside`，结构可能与预期不符。

### 根因 3：17 个 `.el-menu` 导致匹配错误实例

文档指出：页面上存在 **17 个** `.el-menu`。

选择器 `.el-menu >> text=平台数据` 的 `.first` 会匹配文档中**第一个**符合的 `.el-menu` 内的「平台数据」。这个 `.el-menu` 可能是：

- 侧栏主导航
- 下拉菜单、弹窗、表格内菜单等

首次加载时，侧栏可能最先出现在 DOM 中，`.first` 正好命中侧栏。第二次及以后，DOM 顺序或渲染时机变化，`.first` 可能命中弹层、下拉等不可见菜单，从而触发 “element is not visible”。

### 根因 4：`expand_sidebar_if_collapsed` 未真正展开侧栏

当前逻辑大致为：

- 检测 `.el-menu.el-menu--collapse`
- 点击 `el-icon-s-unfold` 或类似按钮
- 注入 CSS 让折叠态下的 `span` 可见

若 BI 后台侧栏的折叠方式与此不同（例如用别的 class、别的图标），则：

- 折叠检测不到
- 不会执行展开
- 菜单文字仍 `visibility: hidden`
- `scroll_into_view_if_needed` 会因 “element is not visible” 超时

### 根因 5：target_page 选择可能不稳定

选页逻辑：

```python
for p in pages:
    if url in (p.url or "") or start_url in (p.url or ""):
        target_page = p
        break
if not target_page:
    target_page = pages[0]
```

若用户有多个 Tab：

- slug 1 运行后，当前 Tab 可能是 BI 数据页
- slug 2 重新连接时，`pages` 顺序可能变化，`pages[0]` 可能不是 BI Tab
- 或 URL 匹配到非激活 Tab，导致对错误 Tab 执行 `goto` 和 automation

### 根因 6：首次成功的偶然性

首次成功可能来自：

- 冷启动：完整加载，DOM 结构最稳定
- 初始路由：`#/layout/person` 与侧栏默认状态匹配
- 菜单顺序：侧栏的 `.el-menu` 恰好在 DOM 中排第一

之后每次 `goto` 可能都是“软导航”，DOM 和渲染状态与首次不同，从而暴露出选择器、折叠逻辑、页面选择等问题。

---

## 三、执行链路与失败点

```
run_full_spa_collect (for each slug)
  └─ harvest_table_data(automation={...})
       └─ _harvest_via_playwright()
            ├─ sync_playwright()  ← 每个 slug 新建连接
            ├─ connect_over_cdp()
            ├─ target_page = pages[0] 或 url 匹配页
            ├─ target_page.goto(base_url)  ← 每次都重新导航
            ├─ wait_for_load_state("networkidle")
            ├─ wait_for_timeout(1000)
            └─ _run_automation_actions()
                 ├─ [0] expand_sidebar_if_collapsed  ← 可能未真正展开
                 ├─ [1] wait_attached ".el-menu >> text=平台数据"  ← 通过
                 ├─ [2] wait_ms 500
                 └─ [3] click_expand ".el-menu >> text=平台数据"  ← 失败
                      └─ loc.scroll_into_view_if_needed()  ← "element is not visible"
```

失败点：`scroll_into_view_if_needed`，说明元素在 DOM 中但 Playwright 认为不可见。

---

## 四、小结

| 根因 | 权重 | 说明 |
|------|------|------|
| SPA 二次 goto 导致状态/渲染不同 | 高 | 首 slug 完整加载，后续可能是软导航，DOM/状态不一致 |
| `.first` 命中非侧栏的 `.el-menu` | 高 | 17 个 `.el-menu` 时，顺序变化会让 `.first` 匹配到不可见菜单 |
| 侧栏折叠逻辑与 BI 实际实现不符 | 中 | `expand_sidebar_if_collapsed` 可能未识别或未展开当前侧栏 |
| `wait_attached` 不保证可见 | 中 | 折叠或隐藏状态下元素仍在 DOM，但不可见 |
| target_page 选择可能错误 | 中 | 多 Tab 时可能操作非 BI 页面 |
| 首次成功依赖初始加载与 DOM 顺序 | 低 | 冷启动时恰好满足当前逻辑，后续不再满足 |

---

## 五、建议排查方向（不实施，仅作参考）

1. 限定侧栏：使用更精确选择器，如 `.el-aside .el-menu` 或侧栏容器的实际选择器，避免命中弹层/下拉菜单。
2. 校验 `expand_sidebar_if_collapsed`：在 BI 实际页面上确认折叠 class、展开按钮，确保能正确展开。
3. 复用同一页面：改为一次连接、一次 goto，在同一个 page 上顺序执行多个 slug 的 automation，减少 SPA 状态混乱。
4. 加强 target_page 选择：明确按 BI 域名或 hash 选 Tab，并在操作前 `bring_to_front()`。
5. 将 `wait_attached` 替换或补充为 `wait_visible`，并结合重试或更长超时，确认元素确实可见后再操作。

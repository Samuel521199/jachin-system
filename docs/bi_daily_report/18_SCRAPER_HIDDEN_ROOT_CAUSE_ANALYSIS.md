# BI 抓数据 "locator resolved to hidden" 根因分析报告

## 一、错误现象

```
action[0] wait_visible 失败: Locator.wait_for: Timeout 15000ms exceeded.
Call log:
  - waiting for locator(".el-menu").locator("text=平台数据").first to be visible
    34 × locator resolved to hidden <span>平台数据</span>
```

**关键信息**：选择器找到 34 个匹配元素，但**全部处于 hidden 状态**。

---

## 二、调试脚本输出（用户侧栏已展开时的矛盾现象）

用户确认**侧栏和「平台数据」一直保持展开**，且页面可见。但 `debug_bi_menu_selectors.py` 输出：

| 检测项 | 结果 | 含义 |
|--------|------|------|
| `.el-menu` 数量 | 17 | 页面存在 17 个菜单容器 |
| **`.el-aside .el-menu`** | **0** | **侧栏内没有任何 `.el-menu`** |
| `平台数据` sub-menu__title | count=1 | 能找到唯一匹配 |
| `日常报表` is_visible | False | Playwright 认为不可见 |
| `平台数据` aria-expanded | false | DOM 显示未展开 |
| 点击 `平台数据` | `Element is not visible` | 无法点击，滚动后仍不可见 |

### 矛盾点

- **用户肉眼**：侧栏展开、「平台数据」展开、「日常报表」可见、「每日运营数据汇总」高亮。
- **Playwright**：`aria-expanded=false`，`日常报表` 不可见，无法点击「平台数据」。

说明：**Playwright 操作的对象与用户当前看到的 UI 很可能不是同一套 DOM / 同一页面状态**。

---

## 三、根因分析（基于调试结果）

### 根因 1：侧栏可能不放在 `.el-aside` 下

`.el-aside .el-menu count: 0` 表明：

- BI 后台布局**很可能未使用** Element Plus 的 `.el-aside`
- 17 个 `.el-menu` 可能分布在布局、弹层、下拉等不同区域
- 使用 `.el-menu` 而未限定父级时，会优先匹配到文档中**最先出现**的 `.el-menu`，这个不一定是侧栏菜单

当前选择器 `.el-menu >> text=平台数据` 可能在匹配：下拉菜单、弹层中的菜单、表格内嵌菜单等，而这些在视口中通常是 hidden。

### 根因 2：调试脚本的 `page.goto` 改变了页面状态

`debug_bi_menu_selectors.py` 会执行：

```python
page.goto(base_url)  # #/layout/person
page.wait_for_load_state("networkidle")
page.wait_for_timeout(2000)
```

即：**强制导航到 `#/layout/person`（仪表盘）**。

- 用户截图里是「每日运营数据汇总」页面，说明用户之前已经通过菜单点到该页
- 脚本运行后会**重新进入** person 页，此时 SPA 重新挂载
- 初始加载时，侧栏的展开/折叠状态可能和用户手动操作后的状态不同
- 因此 Playwright 检测到的 `aria-expanded`、`is_visible` 可能与用户当前看到的不一致

### 根因 3：17 个 `.el-menu` 导致匹配到错误实例

存在 17 个 `.el-menu` 时，`.el-menu >> text=平台数据` 的 `.first` 可能命中：

- 折叠的二级菜单
- 弹层中的菜单副本
- 其他非侧栏的菜单

这些元素在 DOM 中存在，但可能处于 `visibility: hidden`、`overflow: hidden` 或不可见区域，导致 Playwright 认为“不可见”。

### 根因 4：`page.goto` 可能影响活跃 Tab

`connect_over_cdp` 后使用 `ctx.pages[0]`，若有多个 Tab，可能选中的不是用户正在看的 Tab。再执行 `page.goto` 会作用于该 Tab，用户若在看其他 Tab，会出现“脚本在一个页面上操作、用户在另一个页面上观察”的情况。

---

## 四、文档与代码的脱节（原有结论仍成立）

`docs/bi_daily_report/16_BI_SCRAPER_V0.8.45_COMPARISON.md` 中明确写道：

> v0.8.45 在同事电脑（侧栏默认展开）可正常运行，但若本地 BI 侧栏**默认折叠**，`wait_visible` 会因 `locator resolved to hidden` 超时失败。
> 已加回最小改动以兼容两种环境：
> - `expand_sidebar_if_collapsed`：检测折叠时点击展开或注入 CSS 强制显示菜单
> - `wait_attached`：在展开前仅等待元素挂载，不要求可见

但**当前代码库中并未实现**这两种能力，侧栏折叠场景下的兼容逻辑缺失。

---

## 三、执行链路与失败点

```
run_bi_scraper_spa.py
  └─ run_full_spa_collect(use_discover=False)  # Discovery 失败，用 MENU_ITEMS
       └─ harvest_table_data(automation={actions: [...]})
            └─ _harvest_via_playwright()
                 ├─ goto(base_url)  # #/layout/person 仪表盘
                 ├─ wait_for_timeout(1000)   # SPA 挂载
                 └─ _run_automation_actions()
                      └─ action[0]: wait_visible ".el-menu >> text=平台数据"  ← 在此失败
```

`_build_leaf_actions(["平台数据", "日常报表"], "每日运营数据汇总")` 生成的首个 action 是：

```python
{"type": "wait_visible", "selector": ".el-menu >> text=平台数据", "timeout": 15}
```

在侧栏折叠的情况下，这个 `wait_visible` 会一直等到超时。

---

## 四、为何会出现「34 × hidden」

1. **多个 `.el-menu`**
   页面可能存在多个菜单（侧栏、顶部导航、下拉菜单等），`.el-menu` 会匹配到多处，其中部分可能被隐藏。

2. **侧栏折叠**
   折叠后仅显示图标，文本常被隐藏（如 `overflow:hidden`），DOM 里仍有 30+ 个「平台数据」相关节点，但都不可见。

3. **父级「BI平台管理」未展开**
   菜单层级类似：
   ```
   BI平台管理（顶层 sub-menu）
     └─ 平台数据
          └─ 日常报表、平台产销、平台充值...
   ```
   若「BI平台管理」未展开，其子菜单「平台数据」等虽然在 DOM 中存在，但会被视为 hidden。

---

## 五、选择器范围过宽

当前使用 `.el-menu >> text=平台数据`，未限定在侧栏内：

- 若有 header 中另一个 `.el-menu`，可能优先匹配到隐藏下拉
- 文档建议使用 `.el-aside .el-menu` 或 `aside .el-menu` 限定侧栏

---

## 六、可行的解决方向（需改代码）

### 1. 实现 `expand_sidebar_if_collapsed`

- 在首步 `wait_visible` 前，增加 `expand_sidebar_if_collapsed`
- 逻辑：检测侧栏是否折叠 → 若折叠则点击展开按钮，或注入 CSS 强制显示菜单内容

### 2. 使用 `wait_attached` 替代首步 `wait_visible`

- `wait_attached`：只等元素挂载到 DOM，不要求 visible
- 等挂载后再执行 `click_expand` 等操作，由点击展开后再用 `wait_visible` 等子菜单

### 3. 缩小选择器范围

- 将 `.el-menu` 改为 `.el-aside .el-menu` 或 `aside .el-menu`
- 避免匹配到 header 等非侧栏的隐藏菜单

### 4. 先展开「BI平台管理」

- 若「平台数据」在「BI平台管理」下，需在 actions 前增加一步展开「BI平台管理」
- 或在 `expand_sidebar_if_collapsed` 中一并处理

---

## 七、临时绕过方式（无需改代码）

### 方式一：手动展开侧栏后再跑脚本

1. 启动 `launch_chrome_debug_bi.ps1`
2. 在 Chrome 中登录 BI 后台
3. 手动点击侧栏展开按钮（如有），并展开「BI平台管理」和「平台数据」
4. 保持页面不要刷新或切页
5. 再运行 `python scripts/run_bi_scraper_spa.py`

若能保持侧栏及子菜单展开，`wait_visible` 有机会通过。

### 方式二：使用 `--direct` 直接打开目标页（单页测试）

对已知直接 URL 的页面，可跳过菜单导航：

```bash
python scripts/test_bi_prod_sales_compare.py --direct
```

该脚本会直接打开 `#/layout/BIManager/PlatformData/PlatformAsset/biGameDailyAssetSummaryCompare`，`actions=[]`，不依赖菜单点击。

若要批量抓取，需为每个 slug 配置对应的直接 URL，并在 `run_full_spa_collect` 中支持「直接 URL 模式」。

### 方式三：运行调试脚本查看 DOM 状态

```bash
python scripts/debug_bi_menu_selectors.py
```

可查看：
- `.el-menu` 数量
- 侧栏内菜单数量
- 「平台数据」相关选择器的匹配情况
- 点击「平台数据」后「日常报表」是否变为可见

有助于进一步确认折叠 / 父子展开逻辑。

---

## 九、建议的下一步诊断（不改抓取逻辑）

1. **确认侧栏实际 DOM 结构**
   - 在 Chrome DevTools Console 执行：`document.querySelectorAll('.el-aside')`
   - 若返回空，说明侧栏未使用 `.el-aside`
   - 再查：`document.querySelectorAll('aside')`、`document.querySelector('[class*="sidebar"]')` 等，定位侧栏根容器

2. **确认「平台数据」在哪个 `.el-menu` 下**
   - 在 Console：`document.querySelector('.el-sub-menu__title')?.closest('.el-menu')?.getBoundingClientRect()`
   - 查看该 `.el-menu` 的 class 和父元素，确定侧栏菜单的真实选择器

3. **修改调试脚本，避免 `goto` 覆盖当前页**
   - 注释掉 `page.goto(base_url)`，改为只使用当前已打开的页面
   - 确认用户已停在实际要抓的页面（如「每日运营数据汇总」）
   - 再跑一次，对比 `aria-expanded`、`is_visible` 与有无 `goto` 的差异

4. **检查多 Tab 情况**
   - 在脚本中打印 `len(ctx.pages)`、`[p.url for p in ctx.pages]`
   - 确认 `pages[0]` 是否为用户正在查看的 BI Tab

5. **尝试限定到侧栏菜单**
   - 若找到侧栏容器（如 `aside .el-menu` 或 `.sidebar .el-menu`），在 `spa_collector` 中把 `MENU` 从 `.el-menu` 改为该选择器，避免匹配到隐藏的菜单副本

---

## 十、用户提供的 DOM 结构（2026-03 实测）

### 菜单元素结构（Element Plus）

| 菜单项 | 元素 | 展开状态（箭头） |
|--------|------|------------------|
| BI平台管理 | `div.el-sub-menu__title` | `transform: rotateZ(180deg)` = 已展开 |
| 平台数据 | `div.el-sub-menu__title` | 同上 |
| 日常报表 | `div.el-sub-menu__title` | `transform: none` = 折叠 |
| 平台产销 | `div.el-sub-menu__title` | 同上 |
| 叶子项（如仪表盘） | `li.el-menu-item` | — |

### 前端 BUG（需 BI 团队修复）

用户抓取的实际 HTML 中：
- **平台充值** 按钮的 `<span>` 显示为「平台产销」
- **平台预警信息** 按钮的 `<span>` 显示为「平台产销」
- **数据统计分析** 与 **数据明细** 的文案可能混淆

若按 `text=平台充值` 或 `text=平台预警信息` 查找会失败，需改用顺序/位置选择器或等前端修复。

---

## 十一、总结与已实施的修复

| 项目 | 说明 |
|------|------|
| 直接诱因 | 选择器 `.el-menu` 未限定范围，在 17 个菜单中可能命中隐藏实例 |
| 结构性原因 | `.el-aside .el-menu` 为 0，侧栏很可能不用 `.el-aside` |
| 脚本干扰 | 调试脚本的 `page.goto` 会改变页面状态 |
| **已实施修复** | 1）首步 `expand_top_menu` 展开「BI平台管理」 2）`wait_attached` 替代首步 `wait_visible` 3）使用 `div.el-sub-menu__title` 精确选择器 4）用箭头 `transform: rotateZ(180deg)` 检测展开状态 |
| 潜在风险 | 若 BI 前端存在文案 BUG（平台充值/平台预警信息 显示为 平台产销），对应菜单可能仍失败 |

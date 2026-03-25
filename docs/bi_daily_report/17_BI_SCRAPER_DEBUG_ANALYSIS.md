# BI 抓数据脚本深度排查报告

## 一、执行链路

```
run_bi_scraper_spa.py
  └─ run_full_spa_collect(use_discover=True, ...)
       ├─ discover_menu_items() → 失败，fallback MENU_ITEMS
       └─ for slug in all_items:
            └─ harvest_table_data(url, config={automation: {actions}})
                 └─ _harvest_via_playwright()
                      ├─ connect_over_cdp(cdp_url)
                      ├─ target_page = pages[0]
                      ├─ goto(base_url)
                      ├─ _run_automation_actions(page, actions)
                      ├─ 提取表格 / 展开行等
                      └─ browser.close()
```

**关键点**：每个 slug 都会重新 `connect_over_cdp` → `goto` → 执行 actions → `browser.close()`，即每次都是「新连接 + 重新导航」。

---

## 二、daily_ops_summary 的 action 序列

```python
# _build_leaf_actions(["平台数据", "日常报表"], "每日运营数据汇总") 生成：
[
  {"type": "wait_visible", "selector": ".el-menu >> text=平台数据", "timeout": 15},
  {"type": "wait_ms", "ms": 500},
  {"type": "click_expand", "selector": ".el-menu >> text=平台数据", "text": "平台数据"},
  {"type": "wait_visible", "selector": ".el-menu >> text=日常报表", "timeout": 5},  # ← 失败处
  {"type": "wait_ms", "ms": 400},
  {"type": "click_expand", "selector": ".el-menu >> text=日常报表", "text": "日常报表"},
  ...
]
```

**失败现象**：`action[3] wait_visible selector='.el-menu >> text=日常报表'` 超时，`locator resolved to hidden <span>日常报表</span>`。

**含义**：元素在 DOM 中存在，但处于 hidden 状态，说明「平台数据」子菜单未展开。

---

## 三、菜单层级（抓元素.txt + 截图）

| 层级 | 元素 | 类名 | 说明 |
|------|------|------|------|
| 0 | BI平台管理 | el-sub-menu__title | 顶层，需先展开 |
| 1 | 仪表盘 | el-menu-item | 直接子项 |
| 1 | 平台数据 | el-sub-menu__title | 子菜单，折叠时子项 hidden |
| 1 | 数据统计分析 | el-sub-menu__title | 同上 |
| 1 | 数据明细 | el-sub-menu__title | 同上 |
| 2 | 日常报表 | el-sub-menu__title | 平台数据 的子项 |
| 2 | 平台产销 | el-sub-menu__title | 平台数据 的子项 |
| ... | ... | ... | ... |

**结论**：BI 使用 **Element Plus**（`el-sub-menu__title`），不是 Element UI（`el-submenu__title`）。

---

## 四、问题点汇总

### 1. MENU_ITEMS 缺少「BI平台管理」

- 当前：`parents=["平台数据", "日常报表"]`，首步 `wait_visible "平台数据"`
- 实际：若「BI平台管理」未展开，则「平台数据」可能不可见或不可点
- 截图：侧栏已展开，能看到「平台数据」，说明「BI平台管理」已展开，此项可能不是根因

### 2. click_expand 可能未真正展开「平台数据」

- 现象：`click_expand "平台数据"` 执行后，`wait_visible "日常报表"` 仍为 hidden
- 可能原因：
  - **A. 选择器不匹配**：已加 `div[class*='sub-menu__title']`，理论上能匹配 Element Plus
  - **B. is_expanded 误判**：Element Plus 可能不用 `aria-expanded`，`closest('li[aria-expanded]')` 可能拿不到正确节点
  - **C. 点击目标错误**：`title_loc` 可能点到别的菜单（如 header 里的 .el-menu）
  - **D. 点击未生效**：Vue 事件未触发，或动画未完成就进入下一步

### 3. discover_menu_items 与 Element Plus 不兼容

```python
# spa_collector.py 第 161-164 行
submenus = menu_locator.locator(":scope > .el-submenu")   # Element UI
title_el = sub.locator(".el-submenu__title").first       # Element UI
```

- Element Plus 使用 `el-sub-menu`、`el-sub-menu__title`
- 导致 Discovery 始终失败，只能 fallback 到 MENU_ITEMS

### 4. 每次 slug 都重新 goto

- 每个 slug：`goto(base_url)` → 执行 actions
- 若 base_url 为 `#/layout/person`（仪表盘），每次都会回到仪表盘
- 理论上每次起点一致，问题应集中在 actions 本身

### 5. run_bi_scraper_spa.py 本身

- 逻辑简单：检查 CDP、调用 `run_full_spa_collect`、打印进度
- 未发现明显问题

---

## 五、需要验证的假设

1. **click_expand 是否真的点到「平台数据」**
   - 在 click 前后加 `page.screenshot()` 或 `page.locator(...).count()` 日志
   - 确认 `title_loc` 是否唯一、是否在侧栏内

2. **Element Plus 的展开状态如何表示**
   - 检查 DOM：`li.el-sub-menu` 是否有 `aria-expanded`、`is-opened` 等
   - 若没有 `aria-expanded`，`is_expanded` 可能恒为 false，导致逻辑异常

3. **页面内是否有多个 .el-menu**
   - 若 header、dropdown 也有 `.el-menu`，`.el-menu div[...]:has-text('平台数据')` 可能点到错误菜单
   - 需限定在侧栏：`.el-aside .el-menu` 或 `aside .el-menu`

4. **展开动画时长**
   - 当前 `page.wait_for_timeout(350)` 可能不足
   - Element Plus 默认 transition 可能更长

---

## 六、建议的调试步骤（不改业务逻辑）

1. **单 slug 调试脚本**：只跑 `daily_ops_summary`，在 `click_expand` 前后打日志、截图
2. **在浏览器控制台验证选择器**：`document.querySelectorAll(".el-menu div[class*='sub-menu__title']")` 的数量和位置
3. **检查 Element Plus 菜单 DOM**：展开/折叠时 `li` 的 class、`aria-expanded` 等属性变化
4. **确认侧栏选择器**：用 `.el-aside .el-menu` 或更具体的选择器，避免点到其他菜单

---

## 七、抓元素.txt 中的异常

- 第 35 行：「数据统计分析」的 span 写成了「数据明细」，应为笔误
- 第 21、23、25 行：「平台充值」「平台预警信息」的 span 都写成「平台产销」，可能影响 `:has-text()` 匹配

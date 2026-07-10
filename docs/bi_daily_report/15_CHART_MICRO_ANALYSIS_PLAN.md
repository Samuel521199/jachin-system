# BI 图表级小分析方案规划

> 背景：领导要求每个 BI 统计图附带「小分析」（区别于 strategic_report.py 的宏观大分析）。
> Lark 仪表盘自带「智能分析」按钮，但需评估如何与图一起发送到群消息卡片。

---

## 一、核心结论

### 1.1 飞书「智能分析」API 现状

**飞书开放平台目前未提供「智能分析」相关接口**：

| 能力 | 是否有 API | 说明 |
|------|------------|------|
| 列出仪表盘 | ✅ 有 | `GET /bitable/v1/apps/{app_token}/dashboards`，仅返回 block_id、name |
| 获取图表组件 | ❌ 无 | 无法通过 API 获取单个图表组件、截图或元数据 |
| 触发智能分析 | ❌ 无 | 无法通过 API 调用「智能分析」功能 |
| 获取分析文本 | ❌ 无 | 无法通过 API 读取智能分析弹窗中的 AI 文案 |

因此，**无法通过飞书 API 直接获取 Lark 智能分析的结果**。

---

## 二、可行方案对比

### 方案 A：自建 LLM 小分析（推荐，只改 main_skill）

**思路**：利用已有 CSV 与 Lark 仪表盘图表的一一对应（见 `11_LARK_TABLE_SCHEMA.md`），为每个图表对应的数据调用 LLM 生成 1～3 句 micro-analysis。

| 项目 | 说明 |
|------|------|
| 改动范围 | 仅 `main_skill.py`（+ 配置） |
| 数据来源 | 已有的 `output/*.csv` 提纯数据 |
| 分析生成 | 调用 LLM，prompt 限定「针对单图表、1～3 句、聚焦洞察」 |
| 图的来源 | Lark「设置自动发送」继续负责发图；我们发「图表名 + 小分析」文本卡片 |
| 优点 | 无额外 MCP、无浏览器依赖、易维护、可复用 LLM 能力 |
| 缺点 | 非 Lark 原版智能分析，风格可能略有差异 |

### 方案 B：浏览器自动化抓 Lark 智能分析（需新建 MCP）

**思路**：用 Playwright 打开已登录的 Lark 仪表盘页面，对每个图表组件：点击「智能分析」→ 等待弹窗 → 抓取弹窗内文本 → 可选截图表区域图。

| 项目 | 说明 |
|------|------|
| 改动范围 | 新建 MCP（如 `atom_lark_dashboard_scraper`） |
| 依赖 | Playwright、已登录 Chrome（与 spa_collector 类似） |
| 输出 | 每个图表的智能分析文本 + 可选图表截图 |
| 优点 | 使用 Lark 原版智能分析，体验一致 |
| 缺点 | DOM 选择器易受 Lark 迭代影响、需维护、需用户保持浏览器已登录 |

### 方案 C：混合（A 为主 + B 可选）

- 默认用方案 A 生成小分析，满足「每个图带小分析」的主流需求；
- 若后续 Lark 开放 API 或需要「原版智能分析」，再接入方案 B 作为补充。

---

## 三、推荐实现：方案 A（只改 main_skill）

### 3.1 架构设计

```
main_skill 流程（新增 Step 3.4）
                                    ┌─────────────────────────────────────────┐
                                    │  Step 3.4: 图表级小分析                    │
                                    │  - 配置: chart_micro_analysis.enabled     │
                                    │  - 映射: 图表名 ↔ CSV 文件                │
                                    │  - 对每个映射: 读 CSV → LLM 生成 1-3 句   │
                                    │  - 输出: [{chart_name, analysis}]         │
                                    └─────────────────────────────────────────┘
                                                    │
                                                    ▼
                                    ┌─────────────────────────────────────────┐
                                    │  推送 Lark                                │
                                    │  - 大战略报告（现有 Step 3.5）             │
                                    │  - 小分析卡片（新增）：每条 = 图表名+分析  │
                                    └─────────────────────────────────────────┘
```

### 3.2 配置结构（bi_daily_report.yaml）

```yaml
# 图表级小分析（与 strategic_report 大分析互补）
chart_micro_analysis:
  enabled: true
  push_to_lark: true
  # 图表名 → CSV 文件（相对于 output 目录）
  charts:
    - name: "DAU和DNU"
      csv: "01_用户活跃_增幅表.csv"
      prompt_hint: "关注 DAU/DNU 占比与增幅变化"
    - name: "DAU渠道来源"
      csv: "03a_用户活跃_DAU渠道来源.csv"
      prompt_hint: "关注各渠道占比与头部渠道"
    - name: "周统计DAU和DNU数量"
      csv: "02_用户活跃_日期数量表.csv"
      prompt_hint: "关注周内趋势与拐点"
    - name: "DNU渠道来源"
      csv: "03b_用户活跃_DNU渠道来源.csv"
      prompt_hint: "关注新增用户渠道分布"
    - name: "次留表"
      csv: "04_留存_次留表.csv"
    - name: "周环比"
      csv: "06_留存_周环比表.csv"
    - name: "每日金币产出消耗"
      csv: "08_消耗_每日表.csv"
    - name: "按游戏消耗"
      csv: "09_消耗_按游戏表.csv"
    - name: "付费人数"
      csv: "10_充值_付费人数按SKU.csv"
    - name: "付费金额"
      csv: "11_充值_付费金额按SKU.csv"
```

### 3.3 实现要点

1. **`generate_chart_micro_analyses(output_dir, charts_config) -> list[dict]`**
   - 遍历 `charts` 配置，读取对应 CSV；
   - 构造精简 prompt：`你是 BI 分析师。下面是「{图表名}」的数据（最近几条），用 1～3 句话给出洞察，不解释概念。`;
   - 调用现有 LLM 通路（与 strategic_report 相同）；
   - 返回 `[{"chart_name": "DAU和DNU", "analysis": "DAU 占 54.49%，为核心用户群..."}]`。

2. **L3 Agent 职责**
   - 在 Step 3.4 调用 `generate_chart_micro_analyses`；
   - 将结果写入 `result["chart_micro_analyses"]`；
   - Step 3.5 推送时，除战略报告外，再发一条（或分批）小分析卡片，格式例如：
     ```
     ## 📊 图表洞察
     - **DAU和DNU**：DAU 占 54.49%，建议...
     - **DAU渠道来源**：...
     ```

3. **与 Lark 自动发送的图配合**
   - Lark「设置自动发送」已定时发图到 BI 群；
   - 我们的小分析卡片可：
     - **方式 A**：在 Lark 自动发送之后立即发送，作为「图下面的解读」；
     - **方式 B**：在 Lark 自动发送之前发送，作为「今日图表解读预告」；
   - 图表顺序与 `charts` 配置顺序一致，便于人工对应。

4. **图 + 分析同卡（若必须）**
   - 若领导要求「图和分析必须在同一条消息里」：
     - 需要截图能力：Playwright 打开 Lark 仪表盘，按图表区域截屏；
     - 需要图片上传：飞书 API 上传图片获得 `img_key`，卡片用 `image` 组件引用；
   - 这部分需新建 MCP（方案 B），无法仅在 main_skill 内完成。

---

## 四、若必须用 Lark 原版智能分析：方案 B（需新 MCP）

### 4.1 新建 MCP：`atom_lark_dashboard_scraper`

**职责**：

1. 接收 `dashboard_url`（Lark 仪表盘链接）、`cdp_url`（Chrome 调试地址）；
2. 用 Playwright 连接已登录 Chrome，打开仪表盘；
3. 遍历每个图表组件（需 DOM 选择器，如 `.chart-widget` 或类似类名）；
4. 对每个图表：
   - 点击「智能分析」按钮（选择器需实测）；
   - 等待弹窗出现；
   - 抓取弹窗内分析文本；
   - 可选：截取图表区域为图片（供上传飞书）；
5. 返回 `[{chart_name, analysis_text, screenshot_path?}]`。

### 4.2 实现难点

- Lark 多维表格仪表盘为复杂 SPA，选择器可能随版本变化；
- 需要用户提前用 Chrome 登录 Lark，并开启远程调试；
- 智能分析为异步生成，需合理设置等待时间与重试。

### 4.3 与 main_skill 集成

- main_skill 中增加可选分支：若配置 `chart_micro_analysis.use_lark_native: true`，则调用该 MCP 而非自建 LLM；
- 若 MCP 不可用或超时，回退到方案 A（自建 LLM）。

---

## 五、总结与建议

| 需求 | 方案 | 改动 |
|------|------|------|
| 每个图带小分析，可接受非 Lark 原版 | **方案 A** | 只改 `main_skill.py` + 配置 |
| 必须用 Lark 智能分析 | **方案 B** | 新建 MCP `atom_lark_dashboard_scraper` |
| 图 + 分析必须在同一条消息卡片 | **方案 B**（含截图） | MCP + 飞书图片上传 API |

**推荐路径**：优先实现方案 A，满足「每个图带小分析」的核心需求，全部在 main_skill 内完成，由 L3 agent 调度。
若后续确认必须用 Lark 原版智能分析或必须「图+分析同卡」，再补充方案 B 的 MCP。

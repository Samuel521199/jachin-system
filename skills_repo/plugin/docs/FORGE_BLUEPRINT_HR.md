# HR 插件 Forge 蓝图编排说明

本文档描述如何在 Jachin Layer 1 的 **The Forge** 中，通过 React Flow 画布编排 HR 模拟插件的完整工作流。

---

## 一、整体架构（AST 蓝图）

```
                    ┌─────────────────┐
                    │   HR 用户请求    │
                    │ job_title + desc │
                    └────────┬────────┘
                             │
                             ▼
┌────────────────────────────────────────────────────────────────────────────┐
│  Node 1: The Retriever                                                       │
│  com.jachin.retriever-boss.fetch_resumes_by_job                             │
│  输入: job_title, job_desc, max_count                                        │
│  输出: resumes[]                                                             │
└────────────────────────────────────────────────────────────────────────────┘
                             │
                             ▼
┌────────────────────────────────────────────────────────────────────────────┐
│  Node 2: 解析 + 提取 (可并行)                                                 │
│  - pdf-to-text / docx-parser (若为文件)                                       │
│  - resume-extractor.extract_resume (每份简历)                                │
│  输出: resume_struct[]                                                       │
└────────────────────────────────────────────────────────────────────────────┘
                             │
                             ▼
┌────────────────────────────────────────────────────────────────────────────┐
│  Node 3: RAG 检索 (图4 - 记忆与 RAG 深度融合)                                 │
│  com.jachin.resume-memory.rag_retrieve_success_profile                      │
│  输入: candidate_text, department                                            │
│  输出: profiles[] (历史成功画像)                                              │
│  目的: Agent 辩论前先检索「根据当前部门的历史成功画像，评估此人」                 │
└────────────────────────────────────────────────────────────────────────────┘
                             │
                             ▼
┌────────────────────────────────────────────────────────────────────────────┐
│  Node 4: The Tribunal (图3 - 三权分立)                                       │
│  com.jachin.tribunal.screen_resume_debate                                   │
│  Persona: Agent A (Tech) + Agent B (Culture) + Agent C (Judge)              │
│  Round 1 → [若分歧] Round 2 辩论 → Round 3 裁决                              │
│  输出: verdict, brief, agent_a_opinion, agent_b_opinion                     │
└────────────────────────────────────────────────────────────────────────────┘
                             │
                             ▼
┌────────────────────────────────────────────────────────────────────────────┐
│  Node 5: 汇总输出 → Lark 多维表                                                │
│  过滤 verdict=Pass，以表格形式写入 Lark，从上至下按推荐程度排名；                 │
│  含推荐理由、PDF 附件链接；Top 10 实时更新；HR 手动选取面试候选人后，AI 安排面试      │
└────────────────────────────────────────────────────────────────────────────┘
```

---

## 二、Persona 节点 (Neural Market)

在 Forge 或 Neural Market 中需定义三个 Persona 节点，供 Tribunal 编排使用：

| Persona | 角色 | 关注点 |
|---------|------|--------|
| **Agent A (Tech Assessor)** | 左脑能力者、技术专家 | 项目含金量、技术栈匹配、工作年限 |
| **Agent B (Culture Fit)** | 右脑灵魂者、HR BP | 稳定性、沟通能力暗示、自我评价逻辑 |
| **Agent C (The Judge)** | 裁决者、招聘总监 | 综合 A、B 意见，最终 Pass/Reject + 简报 |

---

## 三、辩论工作流设计 (Round 1/2/3)

1. **Round 1 - 各自表态**
   - 将简历 + RAG 检索结果 作为 Context 分别喂给 Agent A 和 Agent B
   - 各自独立输出 `{ "verdict": "Pass"|"Reject", "reason": "..." }`
   - 互不沟通

2. **Round 2 - 碰撞与质询**
   - **一致**：A、B 均为 Pass 或均为 Reject → 直接交 Agent C
   - **分歧**：如 A=Pass、B=Reject → 触发辩论
     - C 将 A 观点发给 B，将 B 观点发给 A
     - A、B 各自反驳/论证

3. **Round 3 - 最终裁决**
   - Agent C 综合辩论记录
   - 输出 `{ "verdict", "brief": "含双方争议点的结构化简报" }`
   - 供 HR 参考

---

## 四、连线与数据流

| 源节点 | 目标节点 | 传递数据 |
|--------|----------|----------|
| Retriever | 解析/提取 | `resumes[].raw` |
| 解析/提取 | RAG | `resume_text`, `resume_struct` |
| RAG | Tribunal | `rag_context` (profiles 文本) |
| Tribunal | 汇总 | `verdict`, `brief`, `agent_a_opinion`, `agent_b_opinion` |

---

## 五、核心记忆 (图4)

- 将优秀员工简历标记 `is_core=True` 存入 `com.jachin.resume-memory` 的 LanceDB
- RAG 检索 Query：「根据当前部门的历史成功画像，评估此人」
- 筛选标准随公司发展**有机进化**

---

## 六、Driver 与权限

- `com.jachin.retriever-boss` 需声明 `execution_model: heavy_process`
- 权限：`network`, `sandbox.execute`（调用宿主机 Playwright）
- Cookie 仅存 `~/.jachin/core/config/`，零密码上云

---

## 七、新增方案：三层异步 + 双触发引擎（2025）

基于招聘网站简历实时更新、需周期收集的特点，新增三层架构：

### 7.1 第一层：雷达粗筛与撒网（在线简历）

- **触发**：cron_thinker 每 30 分钟
- **数据源**：推荐牛人、搜索人才库、主动打招呼列表
- **动作**：atom_radar_scraper → brain_filter（小模型底线过滤）→ atom_auto_greeter

### 7.2 第二层：意向确认与收网归档（PDF 附件）

- **触发**：与第一层并行，cron 每次检查
- **数据源**：消息/沟通列表
- **动作**：atom_inbox_harvester → local_archiver → 更新 recruitment_status.json

### 7.3 第三层：诸神黄昏（Wasm 虫群）

- **触发**：双触发引擎（满载 50 份 或 每日 08:30）
- **动作**：pending PDFs → 脱敏 → Wasm 虫群 → processed → lark_bitable_sync
- **Lark 输出**：以表格形式输出，从上至下按推荐程度排名，含推荐理由和 PDF 附件链接；Top 10 最终推荐候选人信息实时更新；HR 手动选取要面试的候选人后，由 AI 安排面试

### 7.4 MCP 新增原子工具

| 工具 | 职责 |
|------|------|
| atom_radar_scraper | 抓取推荐牛人/搜索的在线文本简历 |
| atom_auto_greeter | 自动发送打招呼话术 |
| atom_inbox_harvester | 扫描消息，下载 PDF 附件 |
| local_archiver | 将 PDF 保存到 pending/ |
| brain_filter | 小模型底线过滤（学历、年限） |
| nat_lang_to_jd | 自然语言解析为 JD 与评审规则 |
| atom_post_job | 发布岗位 |
| atom_lark_notifier | 向飞书发送进度汇报卡片 |
| atom_get_progress | 获取招聘进度，供 HR 被动查询 |

### 7.5 零阶漏斗与全天候雷达

- **零阶漏斗**：HR 在飞书发自然语言 → nat_lang_to_jd 解析 → 规则烙印至 recruitment_status → 多 Agent 据此筛选
- **全天候雷达**：里程碑（10/20/30 份 PDF）或 18:00 主动推送；HR 问「现在几个了？」被动查询

详见 [docs/HR_PLUGIN_NEW_SCHEME.md](HR_PLUGIN_NEW_SCHEME.md)。

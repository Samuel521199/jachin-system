# 近期版本开发摘要（v0.9.12 → v0.9.23）

按版本列出主要变更，格式与对内 Release 清单一致（`[ ]` 可转为任务或对外勾选）。内容依据仓库 **Git 标签说明与 `git diff --stat` 范围** 归纳；细节以对应提交为准。

**说明**：仓库存在 **`v0.9.18` 提交但未打 `v0.9.18` 标签**，下文从 **v0.9.17 直接到 v0.9.19**；若需严格逐号 Release，可补打标签或忽略该中间提交。

---

## v0.9.12 — 交互与代理、大模型截断（约 5 项）

1. [ ] `clients/desktop/src/chat.tsx` + `clients/desktop/src/utils/reasoningStreamSplit.ts` — 交互与推理流展示调整，大模型输出截断逻辑优化  
2. [ ] `.env.example` / `dist_jachin_desktop/.env.example` / `cloud/nexus/.env.example` — 默认**不走代理**等环境示例与注释对齐  
3. [ ] `clients/desktop/src-tauri/src/updater_common.rs` + `prepare-installer-payload.mjs` — 安装包/热更新相关脚本与逻辑  
4. [ ] `scripts/build_full.ps1` + `scripts/publish_desktop_release.py` — 全量构建与桌面发布流水线  
5. [ ] `l3_node/terminal_turn_debug_log.py` — 终端轮次调试日志小幅调整  

---

## v0.9.13 — 部署热更新（约 3 项）

1. [ ] `clients/desktop/src-tauri/windows/installer_hooks.nsh` — Windows 安装钩子，修复**部署场景下的热更新逻辑**  
2. [ ] `clients/desktop/src-tauri/tauri.conf.json` + `Cargo.toml` — 桌面包版本对齐  
3. [ ] `skills_repo/.../youtube.transcript` 等 MCP stub 版本号/元数据随发版对齐  

---

## v0.9.14 — 日志 / WebSocket / MCP 防窒息（约 5 项）

1. [ ] `l3_node/agent_core.py` — ReAct 与日志/截断策略，避免长输出拖死链路  
2. [ ] `l3_node/ws_server.py` + `l3_node/log_broadcaster.py` — WebSocket / 广播侧**防窒息截断**  
3. [ ] `l3_node/primitives/mcp/registry.py` — **MCP fetch 默认 `max_length`**、**Observation 上限** 等，防止超大工具回包撑爆上下文  
4. [ ] 桌面与核心版本号与 **v0.9.14** 对齐（`package.json` / `tauri` / `VERSION` 等）  

---

## v0.9.15 — 卸载、启动、东南亚与热更新公钥（约 4 项）

    1. [ ] `docs/USER_GUIDE_NEXUS_PUBLIC.md` — 用户侧指南大段补充（对外文档）  
    2. [ ] `l3_node/agent_core.py` + `l3_node/primitives/mcp/registry.py` — 行为与 MCP 注册表持续收紧  
    3. [ ] 桌面侧：**卸载**、**应用启动后跳出**、**东南亚区域限制**、**热更新公钥配对** 等（见提交说明；涉及 Tauri/更新链路与配置）  
    4. [ ] `scripts/start-layer3.ps1` — Layer3 启动脚本小改  

---

## v0.9.16 — LLM 超时、长文流式、后台任务 Pulse（约 4 项）

1. [ ] `l3_node/llm_client.py` — **LLM 超时**与**长文流式**行为（大响应不卡死）  
2. [ ] `l3_node/primitives/agent_tasks/background_task_service.py` — **后台任务 pulse** 与僵尸/状态补推相关能力  
3. [ ] `l3_node/primitives/tools/core_util_tools.py` — 与后台任务/工具侧协同的小幅扩展  
4. [ ] `core/sync_daemon.py` 等 — 同步守护与版本对齐  

---

## v0.9.17 — Jachin Omni 战术层与动效（约 5 项）

1. [ ] `clients/desktop/src/components/Omni/OmniTacticalVoidDecor.tsx` — **战术虚空层**装饰与动效  
2. [ ] `OmniDynamicHud`、**command-deck meteor ring**、**JachinCore** 数字核心等 Omni 组件（见提交长说明）  
3. [ ] `clients/desktop/src/styles/globals.css` — 全息角标、神经脉络、技能画布 chrome 等全局样式  
4. [ ] `clients/desktop/src/skills-ui/SkillCanvasPane.tsx` — Skill 画布布局与交互  
5. [ ] `l3_node/agent_core.py` — 与发版对齐的极小改动（若有）  

---

## v0.9.19 — 控制台「星图室」与 Dashboard（约 4 项）

*（中间存在 **v0.9.18 相关提交**，未单独打 `v0.9.18` 标签。）*

1. [ ] `clients/desktop/src/console/pages/Dashboard.tsx` — **星图室 / HUD** 视觉与中区布局；**Quick Actions** 与 **VAD** 分区  
2. [ ] `clients/desktop/src/styles/globals.css` — 控制台与全息纤维等样式大改  
3. [ ] `clients/desktop/src/utils/desktopUiI18n.ts` — 控制台文案 **i18n** 扩展  
4. [ ] `clients/desktop/src/skills-ui/skillCanvasWindow.ts` — 技能画布窗口行为微调  

---

## v0.9.20 — 控制台拓扑紧凑化与仪表盘（3 项）

1. [ ] `clients/desktop/src/console/components/ComputeTopology.tsx` — **ComputeTopology** **compact** 模式、拓扑与中区高度约 **3/5** 紧凑带  
2. [ ] `clients/desktop/src/console/pages/Dashboard.tsx` — Quick Actions / VAD 排布与中区布局再调  
3. [ ] `clients/desktop/src/styles/globals.css` — `dashboard-holo-fiber` 裁切修复等  

---

## v0.9.21 — 读取黑名单，写入白名单（6 项）

1. [ ] `clients/desktop/src-tauri/src/commands/native_fs_policy.rs` + `main.rs` — Tauri 侧本机路径策略命令，与 L3 策略存储对齐  
2. [ ] `l3_node/primitives/fs_path_blacklist.py` + `l3_node/primitives/native_fs_policy_store.py` — L3 **可读黑名单** / **策略落盘**与加载  
3. [ ] `l3_node/primitives/native_write_allowlist.py` + `core/native_tools.py` — Native **写路径白名单**校验衔接  
4. [ ] `clients/desktop/src/console/pages/SettingsPanel.tsx` + `clients/desktop/src/lib/api.ts` — 控制台**设置页**与 HTTP API，配置黑白名单  
5. [ ] `l3_node/http_server.py` — 策略读写相关 **HTTP** 接口（与桌面联动）  
6. [ ] `l3_node/primitives/tools/loader.py` — 工具加载与路径策略协同  

---

## v0.9.22 — 读文件阻塞问题修复（7 项 + 子项）

1. [ ] `clients/desktop/src/workers/attachment.worker.ts` + `clients/desktop/src/utils/attachmentPayloadCore.ts` — 附件「读文件 → Base64 → 组装 `attachments_metadata`」在 **Web Worker** 执行，主线程不随大文件掉帧  
2. [ ] `clients/desktop/src/utils/attachmentPayload.ts` — Promise 封装 Worker；对外 API 与发往 L3 的结构不变  
3. [ ] `clients/desktop/src/chat.tsx` — 有附件时在编码前进入 **loading/thinking**  
4. [ ] `clients/desktop/src/utils/reasoningStreamSplit.ts` + `clients/desktop/src/components/Omni/JachinOmniCyberProtocol.tsx` — 心跳 / trace 与主文拆分，Omni 展示收敛  
5. [ ] `l3_node/ws_server.py` — 超大 WebSocket JSON 帧的 `json.loads` 放入**线程池**，减轻 asyncio **假死**  
6. [ ] `l3_node/intent_gateway/multimodal_attachments.py` — **xlsx / docx / PDF / txt·md·csv·log** 等提取前**字节/字符/页数**预算，避免截断给模型前无上限遍历  
7. [ ] `core/llm_provider.py` — 通义模型名末尾 **`-YYYY-MM-DD` 快照后缀剥离**；`tests/unit/test_llm_qwen_snapshot_normalization.py` 覆盖  

**文档与配置示例**

- [ ] `docs/architecture/DESKTOP_OMNI_MULTIMODAL_ATTACHMENT_PERFORMANCE.md` — 多模态附件卡顿根因与修复 **SSOT**  
- [ ] `.env.example` / `dist_jachin_desktop/.env.example` — `qwen3.5-flash` 等**稳定模型名**；`core/compaction_hook.py`、`l3_node/llm_client.py`、`l3_node/bootstrap.py` 等经济型默认与之一致  

---

## v0.9.23 — 修复上下文记忆错乱

1. [ ] `l3_node/intent_gateway/bundle.py` — `classification_text` **仅**由本轮 `routing_utterance` / `user_input` 生成并截断，**不再**与 `short_memory_context` 拼接，避免路由/缓存/意图面被旧任务污染  
2. [ ] `l3_node/intent_gateway/semantic_cache.py` + `l3_node/agent_core.py` — 语义缓存 Key 增加可选 **`session_id`** 隔离；Experience RAG 对**极短意图**（少于 6 个字符）短路跳过检索  
3. [ ] `l3_node/experience_memory.py` — `should_bypass_experience_rag_for_intent` 门控  
4. [ ] `l3_node/agent_core.py` — ReAct：`Final Answer` **行首锚定**解析、裸答案 `Thought:` 剥离、多模态经验库与 chief_advisor 等既有收敛逻辑保留  
5. [ ] `l3_node/intent_gateway/execution_inject.py` — 多模态「本轮锚定」与 `Final Answer` 输出提示  
6. [ ] `l3_node/react_ui_sanitize.py` — `strip_leading_thought_tag` 等 UI 侧脚手架剥离  
7. [ ] `core/llm_provider.py` + `core/compaction_hook.py` — fallback / memory_flush 模型名规范化（快照后缀）  

---

## 版本标签对照（Git）

| 标签 | 提交主题摘要 |
|------|----------------|
| `v0.9.12` | 改交互与 env 代理示例、大模型截断逻辑 |
| `v0.9.13` | 修复部署时热更新逻辑 |
| `v0.9.14` | 日志/WS 防窒息、MCP fetch/Observation 上限 |
| `v0.9.15` | 卸载/启动/东南亚/热更新公钥等 |
| `v0.9.16` | LLM 超时与长文流式、后台任务 pulse |
| `v0.9.17` | Jachin Omni 战术层与动效大改版 |
| `v0.9.19` | 控制台星图室 / Dashboard / i18n（「天上」发版说明） |
| `v0.9.20` | 控制台拓扑 compact、仪表盘与样式 |
| `v0.9.21` | 读取黑名单，写入白名单 |
| `v0.9.22` | 读文件阻塞问题修复 |
| `v0.9.23` | 修复上下文记忆错乱 |

---

## 横向主题速览（跨版本）

| 主题 | 涉及版本（侧重） |
|------|------------------|
| **桌面 Omni / 控制台 UI** | v0.9.17、v0.9.19、v0.9.20 |
| **L3 / MCP / WS 稳定性** | v0.9.14、v0.9.16、v0.9.22 |
| **安装、更新、发布** | v0.9.12、v0.9.13、v0.9.15 |
| **本机文件策略（黑白名单）** | v0.9.21 |
| **多模态附件与通义模型名** | v0.9.22 |
| **网关意图面 / 上下文与经验库** | v0.9.23 |

---

*本文档随发版迭代维护；复制到 GitHub Releases 时可按版本节拆成多段正文。*

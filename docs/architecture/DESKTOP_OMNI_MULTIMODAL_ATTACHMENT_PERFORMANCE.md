# 桌面 Omni 多模态附件：卡顿原因与修复说明

本文说明 **Tauri + React/Vite 桌面端** 在「一条消息 + 附件（尤其 xlsx / docx）」场景下曾出现的 **界面掉帧、整节点假死、Ctrl+C 后似乎恢复** 等现象的 **根因** 与 **已落地解决办法**，便于排障与后续演进。

---

## 1. 现象（用户侧）

- 选择附件并发送后，**短时间内 UI 掉帧或像无响应**。
- 带大附件时，Layer 3（Sensory WebSocket）侧 **长时间无 chunk / 像挂住**；在终端运行 L3 时，**Ctrl+C** 后有时又「能继续」——容易误判为「写文件把管道锁死」。

---

## 2. 根因（核心：三类阻塞叠加）

问题 **不是** 简单的「磁盘写入阻塞管道」。本质是 **主线程 / asyncio 事件循环 / 后台线程** 被 **大体积附件 + 同步重 CPU** 占满。

### 2.1 客户端（JS 主线程）

- 附件需 **读文件 → Base64 → 组装 `attachments_metadata`**，再经 `sendInput` 做 **`JSON.stringify` 后 `ws.send`**。
- 若 **FileReader、并行 Base64、大字符串拼接** 全部在 **主线程** 执行，会与 React 渲染、动画竞争，表现为 **发送瞬间卡顿**。
- 若 **编码阶段未先进入加载态**，用户会感觉「点了发送却毫无反馈」，加重「死机」观感。

**代码锚点（修复后）**

- Worker：`clients/desktop/src/workers/attachment.worker.ts`
- 与主线程共用的纯逻辑：`clients/desktop/src/utils/attachmentPayloadCore.ts`
- 对外 API 不变：`clients/desktop/src/utils/attachmentPayload.ts` 中 `buildAttachmentsMetadataPayload` 改为 **通过 Worker** 执行重活；`mergePendingAttachmentFiles` 等 **未改语义**。
- 发送前 UI：`clients/desktop/src/chat.tsx` 在 **有附件时于 `await buildAttachmentsMetadataPayload` 之前** 进入 loading/thinking，避免编码期零反馈。

### 2.2 L3 WebSocket 服务端（asyncio 事件循环）

- 单条消息内联 **数 MB 级 Base64** 时，若对整帧做 **同步 `json.loads`**，会 **长时间占用 asyncio 唯一事件循环**，其它协程（心跳、其它连接、同连接后续步骤）无法推进 → **整节点「假死」**。
- 终端 **Ctrl+C** 往往打断的正是这类 **长时间同步段**，故表现为「中断后事件环又能动一下」，**并非**「写文件解锁」。

**代码锚点**

- `l3_node/ws_server.py`：对 **较大帧**（阈值约 150KB 字符串）使用 `asyncio.to_thread(json.loads, …)`，避免大附件帧阻塞事件环。

### 2.3 多模态 xlsx 正文提取（线程池 / 耗时）

- `build_openai_user_content` 在 `agent_core` 中经 **`asyncio.to_thread`** 调用附件组装，不阻塞事件循环本身。
- 但若 **在截断为模型可见长度之前**，对超大表 **扫描过多行、拼接过长字符串**，仍会 **长时间占用线程池**，用户侧表现为 **首轮提示之后迟迟无输出**。

**代码锚点**

- `l3_node/intent_gateway/multimodal_attachments.py` 中 `_extract_xlsx_text`：增加 **字符预算提前停止**、收紧 **单表行数 / 工作表数量** 等，避免无意义全表扫描后再截断。

### 2.4 多模态 docx 正文提取（线程池 / 耗时）

- 与 xlsx 相同：`extracted = _truncate_doc(_extract_docx_text(...))` 若 **先全文遍历再截断**，战略类长文档（大量段落、复杂表格）会让 **python-docx** 在 `to_thread` 内运行很久，表现为 **网关跑完 → ReAct 首 token 前长时间无 chunk**。
- 日志里若同时出现 **IntentGateway** 的 `realtime_knowledge`、`domain_experts` 等小模型调用，会与附件解析 **串行叠加**，进一步拉长「像阻塞」的体感。

**代码锚点**

- `multimodal_attachments._extract_docx_text`：对段落 / 表格 **字符预算与数量上限** 早停（与 xlsx 同类策略）。

### 2.5 纯文本（txt / md / csv / log）与 PDF

- **纯文本**：原先 `read_text` / `bytes.decode` 会 **整文件进内存**，大日志 / CSV 在截断给模型前已消耗大量时间与内存。
- **PDF**：原先 **逐页抽全文无上限**，千页 PDF 在 PyMuPDF/pypdf 中同样会长时间占用 `to_thread`。

**代码锚点**

- `_extract_plain_text`：先 **按字节上限读取**（`MAX_PLAIN_TEXT_READ_BYTES`），再 **`_budget_unicode_text` 字符上限**。
- `_extract_pdf_text`：**页数上限**（`MAX_PDF_PAGES`）+ **字符预算**（`MAX_PDF_PRE_TRUNCATE_CHARS`），最后再过 `_budget_unicode_text`。

---

## 3. 解决办法汇总（与 §2 一一对应）

| 层级 | 办法 | 目的 |
|------|------|------|
| 桌面端 | Web Worker 内完成「读文件 + Base64 + `items` 组装」 | 解放主线程，减少掉帧 |
| 桌面端 | 有附件时先发 loading/thinking 再 `await` | 编码期仍有明确反馈 |
| L3 WS | 大帧 `json.loads` 放到线程池 | 不阻塞 asyncio，避免全节点假死 |
| L3 多模态 | xlsx / docx / 纯文本 / PDF 提取早停 + 限额 | 降低线程占用与首包延迟 |

**未改变的内容（刻意保持）**

- 发往 L3 的 **`attachments_metadata` 结构**（含 `name`、`mime`、`size_bytes`、`has_image`、`base64`）与业务流程（`pendingFiles`、`setIsLoading` 等语义）保持一致；仅 **执行线程/解析位置** 调整。

---

## 4. 排障提示

- 若仍觉得 **发送瞬间** 卡顿，可关注 **`sendInput` 路径上的 `JSON.stringify`（主线程）**——与附件 Worker 解耦，必要时可再评估分块上传或侧车协议（属后续优化）。
- 超大表分析建议：优先将文件放入 **工作区** 用工具链读取，或拆分上传，避免单帧过大。

---

## 5. 相关文件索引

| 文件 | 说明 |
|------|------|
| `clients/desktop/src/workers/attachment.worker.ts` | 附件 Worker 入口 |
| `clients/desktop/src/utils/attachmentPayloadCore.ts` | 附件载荷核心逻辑（Worker/类型共用） |
| `clients/desktop/src/utils/attachmentPayload.ts` | 对外 `buildAttachmentsMetadataPayload`、Worker 桥接 |
| `clients/desktop/src/chat.tsx` | 发送流程与附件 loading |
| `clients/desktop/src/hooks/useSensoryWebSocket.ts` | `sendInput` → `JSON.stringify` + `ws.send` |
| `l3_node/ws_server.py` | WebSocket 收包与超大 JSON 解析 |
| `l3_node/intent_gateway/multimodal_attachments.py` | xlsx/pdf/docx 等提取与 OpenAI user 内容组装 |
| `l3_node/agent_core.py` | `asyncio.to_thread(build_openai_user_content, …)` 调用点 |

---

*文档版本：与多模态附件 Worker / WS 大帧解析 / xlsx 提取限额等改动同步维护。*

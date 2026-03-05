# Jachin Nexus v8.0 — The Singularity OS 架构升维白皮书

**文档类型**: 架构宪法 · v8.0 升维设计（历史参考）  
**版本**: v8.0 (The Singularity OS)  
**更新日期**: 2026-02  
**状态**: ⚠️ **历史文档** — V2 架构已迁移：执行引擎在 **Layer 3**，L2 为控制面。详见 [ARCHITECTURE_V2_LAYER3_STANDALONE.md](../ARCHITECTURE_V2_LAYER3_STANDALONE.md)

---

## 一、 升维愿景

从「单体智能 Agent」彻底升维成「**分布式数字生命操作系统**」。

v8.0 七大核心设计：

1. **Session Multiplexing** — 会话多路复用，Actor 隔离模型
2. **Nexus Hook Pipeline** — 洋葱中间件体系，无损插件介入
3. **Dream Weaver Consolidation** — 梦境重塑与记忆自愈
4. **Capability Negotiation** — Layer 3 能力协商，设备泛化
5. **Edge Mesh Swarm** — 边缘网格计算，多设备算力协同
6. **全链路 runId 追踪 (Distributed Tracing)** — 贯穿总线与沙箱，企业级可观测性
7. **流式神经 (Streaming Chunk)** — LLM 脑电波毫秒级实时投射

---

## 二、 Session Multiplexing（会话多路复用）

### 2.1 当前缺陷

事件总线虽有多 worker 协程，但对同一来源的连续对话缺乏**上下文隔离**。张三问天气、李四问代码，可能共享同一 brain_worker 上下文，导致串话。

### 2.2 划时代设计

在 **OmniSensoryBus** 与 **Agent Loop** 之间增加 **Session Manager（会话管理器）**：

- 按 `session_id`（如 Telegram Chat ID、设备 UUID、CLI 会话 ID）动态拉起**独立的 Agent Actor 协程**
- 每个 session 拥有独立的记忆上下文、工具调用栈、HITL 挂起状态
- 支持同一用户开多个并行任务线程，互不干扰
- 实现**千万级并发下的记忆隔离**

### 2.3 实现约束

- `SensoryInputEvent` 必须携带 `session_id`（可选，缺省时使用 `source` 作为 fallback）
- `SessionManager` 维护 `session_id → Actor` 映射，空闲超时后回收
- brain_worker 从队列取任务时，按 `session_id` 路由到对应 Actor

---

## 三、 Nexus Hook Pipeline（洋葱中间件体系）

### 3.1 当前缺陷

Agent 的思考、调用工具是写死的线性流程，无法无损插入「前置鉴权」「后置审计」或「第三方插件」。

### 3.2 划时代设计

重构 `agent_loop`，采用类似 **Koa.js 的洋葱中间件模型**：

```
on_intent_received → before_llm_think → [LLM] → before_tool_exec → [Tool] → after_tool_exec → before_response
```

**标准生命周期 Hook**：

| Hook | 时机 | 用途 |
|------|------|------|
| `on_intent_received` | 输入入队后 | 鉴权、限流、意图预过滤 |
| `before_llm_think` | LLM 调用前 | 注入上下文、修改 prompt |
| `before_tool_exec` | 工具执行前 | 拦截危险操作（如 rm -rf）、参数校验 |
| `after_tool_exec` | 工具执行后 | 审计日志、结果后处理 |
| `before_response` | 最终回复前 | Markdown 转语音、格式转换 |

### 3.3 实现约束

- 开发者通过编写 Python 函数，注册到 Hook 上
- Hook 支持 `next()` 调用链，洋葱模型：进入时执行前半段，`await next()` 后执行后半段
- 任一 Hook 可 `raise` 中断流程（如鉴权失败）

---

## 四、 Dream Weaver Consolidation（梦境重塑与记忆自愈）

### 4.1 当前缺陷

向量数据库（LanceDB）只会越写越大，旧记忆干扰新记忆，甚至产生**事实冲突**（昨天说喜欢苹果，今天说喜欢香蕉）。

### 4.2 划时代设计

在 `cron_thinker` 基础上，增加 **Memory GC（记忆垃圾回收）** 守护进程：

- **设备空闲时**（如凌晨），自动唤醒**小脑模型**（边缘轻量级 LLM）
- 对 LanceDB 中的近期记忆进行：
  - **聚类**：相似记忆合并
  - **去重**：重复事实压缩
  - **时间线衰减**：忘却机制，旧记忆权重降低
  - **逻辑压缩**：提炼高密度 Tag 写入 core_memory
- **冲突消解**：若发现记忆冲突，主动打上「需用户澄清」标签，待下次交互时提示

### 4.3 实现约束

- **数据层**：`core/memory_store.py`，LanceDB `memories` 表含 `is_consolidated` 字段
- **引擎**：`core/dream_weaver.py`，`DreamWeaver.weave_dreams()` 聚类/去重/融合
- **触发**：`core/daemon.py` 的 `dream_scheduler_loop`，凌晨 3 点 + 空闲 30 分钟
- 与 `dreamer.py` 协同：dreamer 处理 short_term→core_memory；dream_weaver 处理 LanceDB 记忆碎片
- 小脑模型可选：Ollama 本地、Qwen-Turbo 等轻量模型

---

## 五、 Capability Negotiation（能力协商）

### 5.1 当前缺陷

系统默认认为接入的 Layer 3 是「能弹窗、能发光的桌面精灵」。树莓派、手机、无屏设备无法按能力差异化接入。

### 5.2 划时代设计

Layer 3 客户端连接 WebSocket (`ws://localhost:8080/sensory`) 时，**必须先发送 Manifest（能力清单）**：

```json
{"device": "pc", "caps": ["ui_render", "hitl_popup"]}
{"device": "rpi", "caps": ["gpio_control", "audio_play"]}
{"device": "phone", "caps": ["push_notification"]}
```

**Layer 2 总线根据客户端能力标签，动态决定推送什么消息**：

- UI 动画只推给 `ui_render`
- HITL 弹窗只推给 `hitl_popup`
- 语音合成只推给 `audio_play`
- 桌面精灵降级为一种普通的「视觉展示 Skill/Client」

---

## 六、 Edge Mesh Swarm（边缘网格计算）

### 6.1 划时代设计

同一 Layer 2 局域网下，所有 Layer 3 设备不仅是**感官器官**，还是**算力节点 (Worker Nodes)**。

**P2P 任务分发**：

1. 用户在手机下达「帮我把这段 4K 视频压缩一下」
2. Layer 2 收到后，识别为高负载任务
3. 通过 WebSocket 询问局域网内设备：「谁现在 CPU 空闲？」
4. Mac Studio (Layer 3 节点) 回复：「我空闲」
5. Layer 2 将 FFmpeg WASM 技能包和任务参数派发给 Mac Studio
6. 处理完成后回传结果

**效果**：Jachin 变成家庭/企业级**私有云计算集群**。

### 6.2 实现约束

- 需定义 Worker 注册协议、任务认领协议、结果回传协议
- 与现有 Jachin Mesh 扩展，或独立 `edge_mesh` 模块

---

## 七、 全链路 runId 追踪 (Distributed Tracing)

### 7.1 划时代设计

在 Edge Mesh Swarm 等高并发、分布式场景下，若无贯穿始终的 `run_id`，日志将如乱麻。v8.0 为每次用户请求注入独一无二的「基因序列」，从输入总线 → 洋葱模型 → 大模型 → 输出总线全程染色追踪。

**数据流**：

```
emit_omni_input / publish_input
  → _persist_omni_input_sync（无 run_id 则 uuid.uuid4().hex 生成）
  → SQLite omni_input_queue (metadata_json 含 run_id)
  → _process_single_task(run_id=metadata["run_id"])
  → agent_run(run_id=run_id)
  → PipelineContext(run_id=run_id)
  → SensoryOutputEvent.metadata["run_id"]
  → publish_output → Layer 3 广播
```

**实现约束**：

- `SensoryInputEvent` / `SensoryOutputEvent` 的 metadata 强制支持 `run_id`
- `PipelineContext` 新增 `run_id: str` 属性
- 所有 `on_step`、`on_hitl_request`、chunk 广播的 payload/metadata 携带 `run_id`
- 日志输出带 `[RunID: {run_id[:8]}]` 短前缀

---

## 八、 流式神经 (Streaming Chunk)

### 8.1 划时代设计

LLM 推理由「一次性憋大招」改为**逐 token 流式输出**，实现极低延迟的视觉反馈。支持流式的 UI 可实时拼接 chunk 显示，或等完整 thought 后再覆盖。

**实现约束**：

- `core/llm_provider.py`：`LiteLLMEngine.generate_response_stream(messages, chunk_callback)`，`stream=True` 调用 litellm.acompletion，每 chunk 调用 `await chunk_callback(chunk_text)`，内存拼接后返回完整字符串供 ReAct 解析
- `core/agent_loop.py`：`_get_llm_response` 支持 `chunk_callback`，传入 `_on_chunk` 时使用流式
- `_on_chunk`：`bus.publish_output(SensoryOutputEvent(action_type="chunk", metadata={run_id, ...}))`
- **能力协商**：`_should_send_to_client` 仅当客户端 caps 含 `stream_chunk` 时转发 `step_type="chunk"` 事件
- 向后兼容：`execute_blueprint` 等非总线路径不传 `on_chunk`，使用非流式；原有 thought/action/observation/answer 广播保持不变

**Manifest 示例**（支持流式）：

```json
{"type": "manifest", "caps": ["ui_render", "hitl_popup", "stream_chunk"]}
```

---

## 九、 与 v7.0 的兼容性

| 设计 | v7.0 状态 | v8.0 升级路径 |
|------|-----------|---------------|
| Session Multiplexing | 无 session_id | SensoryInputEvent 增加 session_id，SessionManager 可选 |
| Nexus Hook Pipeline | 无 Hook | agent_loop 重构，Hook 注册表可选启用 |
| Dream Weaver | 梦境仅提纯 | 扩展 dreamer.py，增加聚类/去重/冲突消解 |
| Capability Negotiation | 无 | WebSocket 握手增加 Manifest 交换 |
| Edge Mesh Swarm | 无 | 新增模块，与 daemon 并行 |
| 全链路 runId 追踪 | 无 | emit_omni_input/PipelineContext/SensoryOutputEvent 贯穿 run_id |
| 流式神经 (Streaming Chunk) | 无 | generate_response_stream + on_chunk + stream_chunk cap |

---

## 十、 参考文档

- [02_FRAMEWORK.md](./02_FRAMEWORK.md) — 框架架构
- [06_LAYER2_EDGE.md](./06_LAYER2_EDGE.md) — Layer 2 控制面 (V2)
- [07_LAYER3_TERMINAL.md](./07_LAYER3_TERMINAL.md) — Layer 3 灵动终端
- [OMNI_SENSORY_BUS.md](./OMNI_SENSORY_BUS.md) — 全息感官总线

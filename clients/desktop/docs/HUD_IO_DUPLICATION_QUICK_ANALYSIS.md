# HUD 临时交互窗 — 输入/输出重复与乱序快速分析

> **状态**：问题分析稿 v2（不含代码实现）  
> **关联**：`VOICE_COMPANION_MODULE_PLAN.md`（目标态设计）、`HUDMessagePanel.tsx`（现状实现）  
> **现象参考**：同一句用户话（如「我不跟你说了」）出现 3 条；助手回复出现结巴式重复 + 多条几乎相同的全文气泡。  
> **v2 补充要点**：sendQuick 发出后 user 气泡永远不显示、`registerUserInputHandler` 触发时机误区、`voiceSessionActive` 门锁造成流式输出完全沉默、`voiceSessionStore` 与 HUD React state 双头维护、`activeRunId` 陈旧闭包、HUD WS 被 chat WS 的 register 系列覆盖等共 6 处新漏洞。

---

## 1. 问题概述

HUD（`hud_panel` / `SYSTEM.HUD.V2`）的设计目标是 **临时、轻量、单轮对话可见**——用户说一句，助手回一句，最多保留最近几条历史（`MAX_MESSAGES = 8`）。

当前实际表现与目标严重偏离：

| 异常类型 | 用户可见表现 | 严重程度 |
|---|---|---|
| **用户输入重复** | 同一句 user 气泡堆叠 2～3 条 | 高 |
| **助手输出重复** | 多条内容几乎相同的 assistant 气泡 | 高 |
| **流式结巴** | 单条气泡内出现「我明白，我明白啦啦～～」类重复片段 | 高 |
| **通路不一致** | 底部输入框发送 vs 语音/STT 路径行为不同 | 中 |
| **会话割裂** | HUD 与主 Omni 聊天窗各走各的 L3 session | 中 |

这些问题 **不是单一 bug**，而是「多入口写入 + 无去重 + 流式状态机不完整 + 语音模块半落地」叠加的结果；因此「修一处、漏一处」会表现为 **修了很久仍离谱**。

---

## 2. 现状架构：消息从哪来、到哪去

### 2.1 HUD 侧所有「写入 messages[]」的入口

`HUDMessagePanel.tsx` 里，任何一条可见气泡都来自以下 **彼此独立、无统一账本** 的路径：

```
┌─────────────────────────────────────────────────────────────────┐
│                     HUD messages[] 写入入口                      │
├─────────────────────────────────────────────────────────────────┤
│ A. Tauri 事件  hud-panel-user-message   → appendMessage("user") │
│ B. Tauri 事件  hud-panel-message        → appendMessage("assistant") │
│ C. WS 回调     registerUserInputHandler → appendMessage("user") │  ← sendInput 内部触发
│ D. WS 回调     registerChunkHandler     → 流式拼 assistant 气泡 │
│ E. WS 回调     registerAnswerHandler    → appendMessage("assistant") │  ← 仅无 chunk 时
│ F. 本地        sendQuick 失败           → appendMessage("system") │
└─────────────────────────────────────────────────────────────────┘
```

**没有任何一层做「本轮 runId / utteranceId 去重」**；`appendMessage` 只是无脑 `push`。

### 2.2 Rust 侧向 HUD 投喂的事件

`main.rs` 当前会向 `hud_panel` 发送：

| 事件 | 典型来源 | 写入 HUD 角色 |
|---|---|---|
| `hud-panel-user-message` | `--jachin-voice-sim` 用户角色模拟 | user |
| `hud-panel-message` | `--jachin-voice-sim` 助手角色模拟 | assistant（带 title 前缀） |
| `hud-voice-session` | 语音会话开始/结束 | 仅改 `voiceSessionActive`，不直接写气泡 |

哨兵通知（`show_sentry_toast_inner`）**已刻意与 HUD 解耦**（注释写明避免「实时陪伴/回复完成」回灌）。  
因此 **当前 assistant 重复主要不是哨兵路径**，而是 **B + D + E 的组合**，以及 **listener 重复触发**。

### 2.3 与主聊天窗（chat）的并行关系

语音模拟用户话时，Rust **同时**做两件事：

1. 向 HUD 发 `hud-panel-user-message`（HUD 显示 user）
2. 向 chat 发 `voice-sim-user-input` → `chat.tsx` 调 `doActualSend` → **L3 用 chat 的 session_id**

而 HUD 自己还维护 **独立的** `useSensoryWebSocket` 实例：

```typescript
const sessionRef = useRef(`hud-panel-${Date.now()}`);
const sensory = useSensoryWebSocket({ desktopSessionIdRef: sessionRef });
```

结果是：

- **L3 回复** 跟着 **chat session** 走；
- **HUD 的 WS** 挂着另一个 session id，默认 **收不到** 这次 run 的 chunk/answer；
- 若 HUD 又想显示助手回复，只能再走 **Rust 事件灌入** 或 **HUD 自己 sendInput**——两条链路极易重复或漏消息。

这与 `VOICE_COMPANION_MODULE_PLAN.md` 要求的「桌面端缝合、单 utterance 单账本」不一致，属于 **架构层分裂**，不是 UI 小修能根治的。

---

## 3. 根因分析（按优先级）

### 根因 A：同一 utterance 多入口写入 user（高概率 → 截图 3 条 user）

**设计层重复（已在 JVS 方案中预警）**

`VOICE_COMPANION_MODULE_PLAN.md` §6.1 要求：

> STT 后 **立刻上屏 HUD**，再 `sendInput` 进 L3

若实现时 **既** 通过 Tauri 事件上屏 **又** 在 `sendInput` 里触发 `registerUserInputHandler` 上屏，**同一句 user 必然双份**。  
再叠加 listener 重复（见根因 C），就会出现 **3 条**。

**现状代码对应关系**

| 步骤 | 路径 |
|---|---|
| 1 | Rust `--jachin-voice-sim user …` → `hud-panel-user-message` → `appendMessage("user")` |
| 2 | 同一脚本 → `voice-sim-user-input` → chat `doActualSend`（chat 侧再加 user，HUD 不直接收，但可能触发其它回灌） |
| 3 | 若 HUD `sendQuick` / 未来 STT 再调 `sendInput` → `onUserInputRef` → `registerUserInputHandler` → 再 `appendMessage("user")` |

**`sendQuick` 本身不写 user 气泡**，完全依赖路径 C；而路径 A/C 可能同时生效。

---

### 根因 B：`streamingAssistantId` 闭包陈旧 → 每个 chunk 新建一条 assistant 气泡（高概率 → 多条 assistant + 结巴）

`registerChunkHandler` 的 effect 依赖项包含 **`streamingAssistantId`（state）**：

```typescript
useEffect(() => {
  registerChunkHandler((chunk, runId, meta) => {
    // ...
    setMessages((prev) => {
      if (streamingAssistantId) { /* 追加到已有气泡 */ }
      else {
        const id = ...;
        setStreamingAssistantId(id);  // 异步更新
        return clippedPush(prev, { id, role: "assistant", content: delta, ... });
      }
    });
  });
  return () => registerChunkHandler(null);
}, [..., streamingAssistantId]);  // ← 每次 streamingAssistantId 变化就卸载/重挂 handler
```

这是典型的 React 流式 UI 反模式，会导致：

1. **第一个 chunk 到达时** `streamingAssistantId === null` → 创建气泡 #1  
2. `setStreamingAssistantId` 尚未 commit → **第二个 chunk 仍看到 null** → 创建气泡 #2  
3. effect 因 state 变化反复重注册 handler → 与 `streamAccRef` 累加器交错 → **单条内结巴**（`mergeStreamChunk` 的 prev 与 UI 气泡内容不同步）

`streamChunkMerge.ts` 的注释已说明误用会出「用户用户用户说」式结巴；HUD 当前用法 **正好触发该注释描述的场景**。

**正确模式（文档建议，非代码）**：`streamingAssistantId` 应放在 **`useRef`**，handler effect **不应**依赖它；每轮 run 用 `runId` 重置 ref，而不是靠 state 驱动 effect 重挂载。

---

### 根因 C：Tauri `listen` 异步注册 + React StrictMode → 事件监听器叠加（中～高概率 → 同 payload 触发 2～3 次）

`hud.tsx` 使用：

```tsx
<React.StrictMode>
  <HUDMessagePanel />
</React.StrictMode>
```

HUD 内多个 `useEffect` 采用「`listen().then(fn => unlisten = fn)` + `disposed` 标志」模式。  
在 **开发模式 StrictMode 双挂载** 或 **依赖项频繁变化**（如 `voiceSessionActive` 切换导致 handler effect 重建）时，存在竞态：

- cleanup 执行时 `unlisten` 仍为 `undefined`（listen 尚未 resolve）
- 新 effect 已再次 `listen`
- 旧 listen resolve 后若未正确 dispose，会 **留下多个活跃监听器**

**同一 `hud-panel-user-message` / `hud-panel-message` 事件被处理 N 次 → N 条相同气泡。**

生产环境 StrictMode 不双挂载，但若用户在语音会话中频繁切换 `voiceSessionActive`，仍可能偶发重复。

---

### 根因 D：`voiceSessionActive` 门槛不一致 → 行为随机（中概率）

HUD 对 WS 流式输出加了门闩：

```typescript
if (!voiceSessionActive) return;  // chunk / answer / userInput handler 均如此
```

但：

- `hud-panel-message` / `hud-panel-user-message` **不受** 此门闩影响，且会 **`setVoiceSessionActive(true)`**
- `sendQuick`（底部输入框）**不**设置 `voiceSessionActive`，也不本地 append user

导致：

| 用户操作 | user 是否上屏 | assistant 是否流式 |
|---|---|---|
| 语音模拟 / 事件驱动 | 是（事件） | 取决于后续 WS 是否同 session |
| 底部输入框 `sendQuick` | 仅当 `voiceSessionActive` 已为 true | 同上 |
| 关闭 HUD 后再开 | 状态可能不同步 | 表现「有时好有时坏」 |

用户感知为 **「输入接受很离谱」**——同一块 UI，不同入口规则不同。

---

### 根因 E：双 session / 双 WebSocket 导致「该显示的没显示、不该重复的重复」（架构级）

| 组件 | session_id | WebSocket |
|---|---|---|
| chat (`chat.tsx`) | 用户会话 UUID（持久化） | 实例 #1 |
| HUD (`HUDMessagePanel`) | `hud-panel-${Date.now()}`（每次加载新建） | 实例 #2 |

L3 `ws_server.py` 按 `chat_id` / `session_id` 分区会话。  
**一次只在 chat 发起的 run，其 chunk/answer 不会自动出现在 HUD 的 session 上。**

于是出现两种「离谱」极端：

1. **HUD 空回复 / 只有 user**：话是从 chat 发的，HUD 等 WS 但等不到  
2. **HUD 重复回复**：Rust 事件灌一条 + HUD 自己又 `sendInput` 收到一条 + chunk 乱流再多条

这与 JVS 方案 §1.3「桌面端负责缝合」冲突——**缝合层尚未实现**，HUD 与 chat 各写各的。

---

### 根因 F：缺少「轮次」抽象与去重键（系统性）

目标态（`VOICE_COMPANION_MODULE_PLAN.md` §7.2、验收 §597-598）要求：

- 单条 assistant 气泡内流式增长  
- **无重复全文、无哨兵标题污染**

现状缺失：

| 应有 | 现状 |
|---|---|
| 每轮 `utteranceId` / `runId` 唯一气泡 | 无；`appendMessage` 每次新建 id |
| 同一 run 的 answer 不再 append（已有 chunk 时） | 有 `hadStreamChunks` 判断，但被根因 B 绕过 |
| 同一文本 500ms 内 dedupe | 无 |
| 单一 orchestrator 决定「谁写 HUD」 | 无；5 条路径并行 |

---

## 4. 为何「修了很久仍修不好」

| 常见修法 | 实际效果 | 原因 |
|---|---|---|
| 只改 `appendMessage` 去重 | 稍缓解 | 未解决 chunk 多气泡与 session 分裂 |
| 只关 StrictMode | 生产略好 | 开发仍复现；listener 竞态仍在 |
| 只修 capability / 关闭 HUD | 关闭问题消失 | 与 IO 重复无关 |
| 在 HUD 再挂一条 WS 监听 | 更乱 | 双 session 加剧重复 |
| 把哨兵通知灌回 HUD | assistant 刷屏 | 已在 Rust 注释中标记并部分回滚 |

**本质**：团队在 **表现层**（多写少写、hide/show）打补丁，但 **数据平面**（单 utterance 单 writer、单 session、单流式状态机）没有建立。

---

## 5. 推荐解决路线（分阶段，仍不写代码）

### Phase 0 — 先定规则（1 天内可共识）

1. **Single Writer 原则**：每一轮对话 **只有一个模块** 有权往 HUD `messages[]` 写 user/assistant（建议：`voiceOrchestrator` 或等价中枢，见 JVS 方案 §2 目录结构）。  
2. **Single Session 原则**：HUD 不单独维护 `hud-panel-*` session；语音轮次 **复用 chat 当前 session_id**，或 run 级 id 由 orchestrator 显式下发。  
3. **禁止双写 user**：STT/模拟事件 **要么** 只通知 orchestrator **要么** 只触发一次 `sendInput`，**不可两者各 append 一次**。  
4. **流式 state 用 ref 不用 state 驱动 effect**（根因 B 的对策）。

### Phase 1 — 止血（最小改动面，优先消除截图级问题）

| 动作 | 预期消除 |
|---|---|
| 所有 Tauri `listen` 改为同步 unlisten 或 ref 持有单例 listener | 根因 C：3 条相同 user/assistant |
| `registerChunkHandler` 去掉对 `streamingAssistantId` state 的 effect 依赖；用 ref 跟踪当前流式气泡 id | 根因 B：多条 assistant + 结巴 |
| `sendQuick` 与 STT 路径统一走 orchestrator 的 `beginUtterance(text)` | 根因 A/D：输入行为一致 |
| `appendMessage` 增加 `(role, content, runId?)` 去重：同 runId 同 role 不重复 push 全文 | 根因 F |

### Phase 2 — 架构对齐 JVS（根治 session 分裂）

按 `VOICE_COMPANION_MODULE_PLAN.md` 落地：

1. `voiceOrchestrator.ts` 成为 **唯一** STT → HUD 展示 → `sendInput(L3)` → chunk → HUD 流式 → TTS 的编排点  
2. Rust 仅发 **控制面** 事件（`hud-voice-session`、PTT 状态），**不再** 直接 `hud-panel-message` 灌业务正文（模拟脚本改为调 orchestrator API）  
3. HUD 组件 **退化为纯展示**：订阅 orchestrator store，不再自己 `useSensoryWebSocket`  
4. chat 历史与 HUD 显示 **同源**（方案 §6.1：「主聊天历史与 HUD 共用同一 user 消息，避免双份」）

### Phase 3 — 验收（对应 JVS 文档 §597+）

- [ ] 用户说/输入一次 → HUD **仅 1 条** user 气泡  
- [ ] L3 回复 → HUD **仅 1 条** assistant 气泡内流式增长，无结巴、无第二遍全文  
- [ ] 连续 3 轮对话，`messages.length` 符合预期（≤ `MAX_MESSAGES`，无幽灵重复）  
- [ ] 开发模式 StrictMode 下重复次数 = 生产模式  
- [ ] chat 与 HUD 内容一致，session 查询 L3 只有一条 run  

---

## 6. 快速定位手册（调试时不写代码也能查）

### 6.1 用户气泡重复

1. DevTools → HUD webview → 数 `listen("hud-panel-user-message")` 注册次数（应在日志中打 `[HUD] listener registered` 一类标记，**当前无**，建议 Phase 1 加）  
2. 发一句话，看 L3 log / `l3_debug.log` 有几次 `intent` 且 `session_id` 是否多个  
3. 若 3 次 intent、1 个 session → 前端 listener 或 handler 三重触发  
4. 若 1 次 intent、3 个 session → chat/HUD 各发 + 模拟脚本重复

### 6.2 助手气泡重复 / 结巴

1. 观察重复气泡内容是否 **完全相同**（全文重复 → append/事件重复）还是 **递进片段**（流式多气泡 → 根因 B）  
2. 单条内结巴 → 查 chunk 是 delta 还是 cumulative；对照 `streamChunkMerge.ts`  
3. 查 L3 WS 帧是否 `hadStreamChunks` 与 HUD answer handler 是否仍 append  

### 6.3 建议日志锚点（实现 Phase 1 时）

| 位置 | 记录 |
|---|---|
| 每次 `appendMessage` | `role, contentLen, source=A\|B\|C\|D\|E, runId` |
| `registerChunkHandler` | `runId, deltaLen, streamingBubbleId` |
| Rust emit HUD 事件 | `event, contentLen`（已有部分 `l3_debug.log` 可扩展） |

---

## 7. 与截图现象的对应关系（小结）

```
用户「我不跟你说了」×3
  ├─ 可能1: hud-panel-user-message listener ×3    （根因 C）
  ├─ 可能2: 事件 + sendInput handler 双写          （根因 A）
  └─ 可能3: 模拟脚本/ STT 多次 finalize            （流程层）

助手结巴 + 多条相似回复
  ├─ 结巴: streamingAssistantId 闭包 + merge 失步  （根因 B）
  ├─ 多条全文: appendMessage 无去重 + 事件/answer 叠加 （根因 F + A）
  └─ chat/HUD 双 session 乱流                       （根因 E）
```

---

## 9. v2 补充——v1 说漏的六处关键细节

以下六点在 v1 中被遗漏，但实际上是造成「修了很久仍离谱」的直接参与者。

---

### 补充根因 G：`sendQuick` 发出后 user 气泡永远不出现（高概率复现）

`sendQuick` 调用 `sendInput(text)`，而 `sendInput` 内部会触发 `onUserInputRef.current?.(intentTrim, { source: "local" })`。  
HUD 的 `registerUserInputHandler` 会接到这次回调，**但回调内的第一行是**：

```typescript
if (!voiceSessionActive) return;
```

**问题**：用户从底部输入框打字发送时，`voiceSessionActive` 通常为 `false`（HUD 刚打开、或上次语音会话已结束）。此时 `registerUserInputHandler` 的回调直接 `return`，user 气泡**永远不显示**。

`sendQuick` 本身也不调 `appendMessage("user")`（只在 L3 不可连时 append "system"），所以：

- 用户在 HUD 输入框打一句话、按回车
- L3 正常收到并回复
- **用户只看到助手的回答，完全看不到自己说了什么**
- 若 `voiceSessionActive` 碰巧为 `true`，则 user 气泡正常显示——随机行为

这与截图显示「我不跟你说了」×3 的场景**相反**（那是语音模拟路径），但同样属于「输入接受很离谱」的表现。两个极端同时存在：语音路径重复 3 条，打字路径一条都不显示。

---

### 补充根因 H：`registerUserInputHandler` 的触发来源被误解——HUD WS 上的 user 输入只来自 Lark 镜像，不来自 HUD 自己

v1 将 `registerUserInputHandler` 列为「路径 C：`sendInput` 内部触发」，暗示 HUD 发消息后会自己收到 user 回调。但实际情况更复杂：

`useSensoryWebSocket` 里 `onUserInputRef` 被触发的时机只有**两处**：

1. **WS `mirror_input` 帧**：L3 推送的 Lark 镜像输入（`source: "mirror"`）
2. **`sendInput` 本地发送成功后**（`source: "local"`）

HUD 的 `useSensoryWebSocket` 是一个**独立 WS 实例**，挂着 `hud-panel-*` session。  
当用户通过 HUD 底部输入框 `sendInput` 时：

- HUD WS 向 L3 发出 intent，同时本地调 `onUserInputRef.current?.(text, { source: "local" })`
- HUD 的 `registerUserInputHandler` 回调收到，但被 `voiceSessionActive` 门锁挡住（见根因 G）

当语音模拟路径让 **chat.tsx** 的 WS 发 `sendInput` 时：

- chat WS 本地调 `onUserInputRef`，但这个 `onUserInputRef` 挂的是 **chat WS 实例的 ref**，与 HUD WS 实例完全无关
- HUD 的 `registerUserInputHandler` **不会被触发**

因此 v1 中「路径 C：sendInput 内部触发 → HUD user 气泡」的描述仅在 **HUD 自己发 sendInput 且 voiceSessionActive 为 true** 这一极小窗口内成立，其余情况要么被门锁挡，要么根本没有触发。

---

### 补充根因 I：`voiceSessionActive` 是流式输出的总闸，但它的置 `true` 路径极窄，导致 WS 流式输出对大多数用户完全沉默

v1 提到 `voiceSessionActive` 门槛不一致，但没有分析**这个值多数情况下就是 false**，因此流式输出（chunk/answer）在 HUD 中**默认沉默**。

`voiceSessionActive` 被设为 `true` 的唯一时机：

| 位置 | 触发条件 |
|---|---|
| `listen("hud-panel-message")` 回调 | 收到 Rust 模拟脚本的助手消息时 |
| `listen("hud-panel-user-message")` 回调 | 收到 Rust 模拟脚本的用户消息时 |
| `listen("hud-voice-session")` 回调 | Rust 发 `active: true` 时（目前只有 `--jachin-voice-sim` 触发） |

**正常用户打开 HUD → 底部输入框发消息**这条路径，**没有任何地方**把 `voiceSessionActive` 设为 `true`。所以：

- `registerChunkHandler` 里 `if (!voiceSessionActive) return` 直接 return → 流式气泡不出现
- `registerAnswerHandler` 里同样 return → 最终回复也不出现
- `registerUserInputHandler` 里同样 return → user 气泡不出现

结论：**对于非模拟脚本的普通用户输入，HUD 的 WS 流式回路在当前代码中是完全失效的**。用户发消息后看到的是空白，而不是重复——两个极端交替出现，取决于 `voiceSessionActive` 上次被谁改过。

---

### 补充根因 J：`voiceSessionStore`（`voice/voiceSessionStore.ts`）与 HUD React state `voiceSessionActive` 是两套互不知情的状态

`voiceOrchestrator.ts` 通过 `voiceSessionStore.setState()` 管理语音会话状态（idle / listening / thinking / speaking / error）。  
HUD 使用的是完全独立的 `const [voiceSessionActive, setVoiceSessionActive] = useState(false)`。

两者之间**没有任何订阅关系**：

- `voiceOrchestrator` 调 `voiceSessionStore.setState("listening")` 时，HUD 的 `voiceSessionActive` **不变**
- `voiceSessionStore` 里的 Orb 状态与 HUD 里的 `setOrbState(...)` 调用也是两条独立路径

结果：

- `voiceOrchestrator.startSession()` 被调用 → `voiceSessionStore` 变 listening → HUD 仍不响应（`voiceSessionActive` 未变）
- `voiceOrchestrator.onL3Chunk()` 流式推进 → 只有 `synthesizeByJvs` 被调、TTS 在播 → HUD 没有 chunk 显示
- HUD 的「流式气泡」与 `voiceOrchestrator` 的执行**完全解耦**，互相看不见对方

这是 JVS 编排层与 HUD 展示层**没有连接线**的直接证据。`voiceOrchestrator.ts` 目前**没有被任何 UI 组件引用**（chat.tsx 和 HUDMessagePanel.tsx 均不 import 它），它的存在是一个骨架，尚未接入数据流。

---

### 补充根因 K：`activeRunId` 是 state，answer handler 的 runId 过滤用的是闭包里的陈旧值

`registerAnswerHandler` 的 effect 依赖列表包含 `activeRunId`（state）：

```typescript
}, [activeRunId, appendMessage, registerAnswerHandler, revealPanel, setOrbIdleDelayed, voiceSessionActive]);
```

这与根因 B 中 `streamingAssistantId` 的问题性质相同：

1. 每次 `activeRunId` state 更新 → effect 重新注册 handler（旧 handler 先被 `registerAnswerHandler(null)` 清除）
2. 若 answer 帧在 handler 重建的间隙到达（极小但非零的时间窗口），**answer 会被丢弃**
3. 更普遍的问题：runId 过滤逻辑 `if (rid && activeRunId && rid !== activeRunId) return` 中的 `activeRunId` 是注册时捕获的快照，若流程中 runId 发生变化（如超时重试、WS 断连重连），过滤会误判为「陈旧 run」而丢弃实际有效的 answer

**正确模式**：`activeRunId` 应用 `useRef`，answer handler effect 不依赖它，在 handler 内读 `activeRunIdRef.current`。

---

### 补充根因 L：HUD 的 `useSensoryWebSocket` 与 chat 的实例共用同一个 `registerChunkHandler` 槽位——两者会互相覆盖

`useSensoryWebSocket` 内部用单一的 `onChunkRef`（ref，非数组）存放 chunk handler：

```typescript
const registerChunkHandler = useCallback(
  (fn: ...) => { onChunkRef.current = fn; },
  [],
);
```

chat.tsx 和 HUDMessagePanel.tsx 各自调 `useSensoryWebSocket`，各自拿到**独立实例**，各自有独立的 `onChunkRef`，因此两者**不会互相覆盖 handler**——这个判断是正确的。

但有一个更隐蔽的问题：**HUD 的 WebSocket 与 chat 的 WebSocket 连的是同一个 L3 端点（`ws://localhost:18981/sensory`），会同时收到 L3 广播的所有帧**。

L3 `ws_server.py` 按 `session_id` 分区回复——只有 `chat_id`（即 `session_id`）匹配的连接才会收到该 run 的 chunk/answer。HUD WS 挂着 `hud-panel-*` session，chat WS 挂着用户 UUID session。

当 chat 发起一次 run：

- L3 只向 chat 的 session 推 chunk/answer，**HUD WS 收不到这些帧**
- HUD 的 `registerChunkHandler` 永远不会被调用
- 但如果 HUD 自己 `sendInput`（用 `hud-panel-*` session）——L3 开了一个新的 session run——这次 chunk/answer 只有 HUD WS 能收到，**chat WS 收不到**

所以两个窗口的 L3 流式输出是**物理隔离**的，不会互相干扰，但也无法共享。这直接导致：

- 语音场景下 chat 发起的 run → HUD 拿不到 chunk → HUD 只能依赖 Rust 事件灌入（路径 B）
- HUD 自己 sendInput → chat 历史看不到这次回复 → chat 与 HUD 历史分叉

这个「物理隔离」的存在，意味着任何想让「HUD 展示 chat 发起的 run 流式输出」的方案，都必须在 **Rust/orchestrator 层做中转**，而不是靠 HUD 多挂一个 WS 实例。

---

## 10. 补充后的完整根因总览

| # | 根因 | 表现 | v1 是否覆盖 |
|---|---|---|---|
| A | 同一 utterance 多路写 user | 3 条 user 气泡 | ✅ |
| B | `streamingAssistantId` state 驱动 effect 重挂载 | 多条 assistant + 结巴 | ✅ |
| C | Tauri `listen` 异步竞态 + StrictMode | 同事件触发 N 次 | ✅ |
| D | `voiceSessionActive` 门槛不一致 | 行为随机 | ✅（部分）|
| E | 双 session / 双 WS | 该显示的没显示 | ✅ |
| F | 缺少 runId / utterance 去重 | 重复全文气泡 | ✅ |
| G | `sendQuick` 后 user 气泡永不显示 | 打字路径 user 完全消失 | ❌ 新补 |
| H | `registerUserInputHandler` 触发来源被误解 | 路径 C 实际极窄 | ❌ 新补 |
| I | `voiceSessionActive` 默认 false → WS 流式完全沉默 | 普通用户看不到任何流式回复 | ❌ 新补 |
| J | `voiceSessionStore` 与 HUD `voiceSessionActive` 双头维护互不知情 | orchestrator 执行但 HUD 无感知 | ❌ 新补 |
| K | `activeRunId` state 驱动 answer handler 重建 → answer 可能丢弃 | 偶发回复消失 | ❌ 新补 |
| L | HUD WS 与 chat WS 物理隔离，chat run 的 chunk 无法路由到 HUD | 语音场景 HUD 只能靠事件灌入 | ❌ 新补（v1 有提但未说透机制） |

---

## 8. 一句话结论

HUD 输入/输出「离谱」的根因不是某个按钮或某行 CSS，而是 **缺少统一的 utterance 编排层**：  
**5 条独立写入路径、2 套 L3 session、1 个有缺陷的流式 state 机、以及异步事件订阅竞态** 叠加，导致修局部永远漏全局。  

**正确方向**是按 `VOICE_COMPANION_MODULE_PLAN.md` 让 `voiceOrchestrator` 成为唯一 writer，HUD 只做展示；在此之前，Phase 1 的 listener 去重 + 流式 ref 改造即可显著缓解截图中的三重 user 与多条 assistant 问题。

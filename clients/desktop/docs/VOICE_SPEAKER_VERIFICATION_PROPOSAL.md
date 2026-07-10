# 声纹门禁方案 — 「两道门：唤醒验身 + 录指令识主」

> **目的**：Jachin 只响应**本机登记主人**的语音；旁人喊唤醒词、误触发、录指令时插话，**不要进 STT、不要进 L3、不要进聊天记录**。  
> **状态**：方案稿 v3（修正 v2 标题/阈值不一致、补全登记、性能、时机、文件格式）  
> **关联**：`VOICE_WAKE_ARCHITECTURE.md`、`VOICE_COMPANION_PIPELINE_READABLE.md`、`VOICE_BARGE_IN_AND_WAKE_ACK.md`、`data/models/voice/sv/README.md`

---

## 1. 先讲人话：两道门各管什么？

### 1.1 不要做的事

**不能**对麦克风录到的**每一帧音频**都跑一遍完整声纹 extract。

那样做有三个坏处：

- **慢**：CAM++ 虽比 SenseVoice 轻，高频跑仍会叠延迟，体验发粘。
- **费电**：KWS 常驻静默期一天要听很多帧，不必每帧都过声纹。
- **没必要**：应当**在有意义的时间点**才触发 SV，而非盲目全量。

**但**：S2 在 LISTENING 期间做**限频率滑窗**（如每隔 0.5s 算一次，而非每帧），不违反上述原则——关键是**不在 KWS 静默期全跑**，以及**不在 TTS 播放期跑**。

### 1.2 应该做的事：两个时机，两道门

**第一道门 — 唤醒时验身**（S1）

声纹检验的第一个最佳时机：**唤醒词被判定命中的那一瞬间**。

```text
门卫听到你配置的唤醒句（不是写死的 "Hey Jachin"）
    → 从麦克风环形缓存里抽出「带唤醒词的那 ~1～1.5 秒」
    → 送 JVS 声纹服务，与本地 centroid 比较
        ├─ 是主人：放行 → 滴声 / 「我在」→ 继续录指令 → STT → L3
        └─ 不是主人：拦截 → 静默或 Orb 闪红，不录指令，不 STT，写拒识日志
```

意义：陌生人可以碰一下门铃（KWS 误触发），但**进不了屋**。

**第二道门 — 录指令时识主**（S2）

声纹检验的第二个最佳时机：**VAD 录指令全程，按滑窗限频 label，只把「主人窗」拼进 STT**。

```text
LISTENING（VAD 录指令）
    → 每 ~0.5s 算一个窗的 score（限频，不每帧）
    → owner 窗保留；other 窗丢弃，不写入 STT buffer
    → concat(owner 窗) → STT → L3
```

意义：即使旁人在主人说话时插嘴，旁人那段**也不会进 L3**——不靠整段作废，靠时间轴上精确剔除。

### 1.3 和现网架构的对齐（别放错文件）

| 外部建议里容易写错的 | Jachin 里实际应该是 |
|----------------------|---------------------|
| Porcupine 听到 "Hey Jachin" | **用户自定义唤醒句**（`UserSettings.wake_word`）；现网 KWS 多为 **STT 辅助回退**，Porcupine 是目标态，声纹逻辑与具体 KWS 实现**解耦** |
| 在 `voiceOrchestrator.ts` 里拦截 | **不对**。Orchestrator 只管 L3 回复后的 **TTS 断句**；唤醒拦截应在 **Rust `wake_pipeline.rs`**（门卫 → 触发中枢之间） |
| 先播「我在」再验声纹 | **顺序反了**。应 **先验身，再** Earcon / 口头「我在」（见 `VOICE_BARGE_IN_AND_WAKE_ACK.md`） |
| verify 传两段音频 | 应是：**一段当前音频 WAV** + **本地已存的基准向量**（或 enroll 时就算好 centroid，verify 只传 WAV） |

---

## 2. 产品目标与边界

### 2.1 要解决的痛点

| 场景 | 现在 | 有声纹门之后 |
|------|------|--------------|
| 同事对你电脑喊你的唤醒句 | 可能进入对话 | **唤醒瞬间被拒**，无 STT、无 HUD |
| 电视里的人声误触发 | 可能 STT | 相似度低，**直接丢弃** |
| 主人正常唤醒 | 正常 | 多 **~100～200ms** 验声纹，可接受 |
| 主人用大窗 PTT（没唤醒） | 见 §5 副门策略 | 默认 S1 **不验** 或 **会话信任** |
| **主人录指令时旁人插话** | 混录进 STT | **S2 主人轨提取**：按时间窗标 owner / 非 owner，**只 STT 主人窗**；旁人句不进 L3（§3.4） |

### 2.2 不承诺什么

- 不是公安级身份认证，不能替代登录密码。
- 不能防「播放主人录音」的完整回放攻击（后期再加挑战句）。
- 不是「旁人说话内容完全进不了麦克风」——只是**不会变成文字送给大模型**。
- **唤醒门 alone 解决不了「录指令时旁人插话」**——见 **§3.4**；S2 用 **主人轨提取** 实现「不听旁人」，**不是**靠整段作废来凑合。
- **两人完全同时叠音**（同一毫秒两嘴齐开）单麦仍无法完美分离——S3 Diarization 或提示重说；这与「先后插话」要区分开（§3.4.1）。

### 2.3 分层不变

- **L3 仍然只听文本**，不碰声纹。
- **JVS** 负责：STT、TTS、**声纹 extract/verify**（同一进程，不阻塞主链路）。
- **Rust** 负责：环形缓冲、唤醒判定、**唤醒门**、VAD 截句。

---

## 3. 端到端流程（陪伴 / 唤醒主路径）

### 3.1 登记阶段：「认主」（一次性）

Jachin 出厂不认识任何人，需要**认主流程**（设置页或首次引导）：

1. 提示用户朗读 **3 段**短句（每段 2～4 秒）。样本覆盖建议：
   - 1 段：用平常语气说出**唤醒句**（对应第一道门）
   - 2 段：随意说 1～2 句普通指令（对应第二道门滑窗，覆盖非唤醒时的音色）
2. 每段 WAV → JVS `POST /v1/sv/extract` → 得到 192 维 **embedding**
3. 三段向量 **L2 归一化后求平均** → **基准向量（centroid）**
4. 写入本地 `%USERPROFILE%\.jachin\voice\owner_voiceprint.json`（仅本机，不上云）

**一套 centroid 两道门都用**：CAM++ 是说话人无关场景的通用 speaker embedding，不需要为「唤醒」和「指令」分开登记；**同一个 centroid** 既用于唤醒瞬间验身，也用于滑窗 label。之所以要录 1 段普通指令，是为了让 centroid 不只覆盖唤醒时的语调，避免滑窗时因语气变化误判。

未登记 + 声纹门开启：**不允许**语音进 L3，只引导去登记（键盘聊天仍可用）。

### 3.2 运行时：唤醒瞬间验身

```text
[KWS_IDLE] 门卫常驻，音频写入环形缓冲（例如最近 2～3 秒），不送 JVS
      │
      │ 命中用户配置的唤醒句
      ▼
[切片] 从 ring buffer 取 wake_hit 前后共 ~1.0～1.5s（16k mono）
      │
      ▼
[Speaker Gate] POST /v1/sv/verify（当前切片 + 本地 centroid）
      │
      ├─ score < T_low ──→ [REJECT] 静默或 Orb 短红，回 KWS_IDLE，记 speaker.reject
      │
      └─ score ≥ T_high ──→ [ACCEPT]
              │
              ▼
         滴声 / 可选「我在」（此时才播）
              │
              ▼
         VAD 录「唤醒句后面的指令」
              │
              ▼
         STT → inject → L3 → TTS
```

**关键**：KWS 阶段仍然**不**跑 SenseVoice 全量 STT 找词以外的声纹；声纹只在**已经判定唤醒**后跑**一次**短切片。  
**连续对话窗**（唤醒后 60s 内免重复喊唤醒词）：同一轮会话**不再每句都验声纹**——信任已在门口验过；若产品要求极高安全，可在 60s 窗过期后下次唤醒再验。

### 3.3 陌生人被拒时 UX

- **不要**进 STT、不要 HUD 用户气泡、不要 `doActualSend`
- Orb：**可选**极短红灯或保持沉寂（产品可配置「静默拒识」vs「闪一下提示」）
- 日志：`voice_companion.log` / `voice_chat.log` 写 `speaker.reject` + score（不落盘拒识音频）

### 3.4 录指令时旁人插话：产品目标怎么落地？（与「尾验整段作废」的矛盾）

#### 3.4.0 为什么主人轨提取是主路径，而不是整段尾验？

**产品目标**：旁人插话的内容 **不得作为用户指令** 进入 STT / L3 / 聊天记录。

要实现这个目标，需要理解「整段尾验」为什么行不通：

| 尾验判决 | 实际效果 | 问题 |
|----------|----------|------|
| 整段 score 够高 → 放行 | 旁人插 30% 时旁人字仍进 L3 | **没有剔旁人** |
| 整段 score 不够 → 丢弃 | 主人已说的部分也被扔掉 | **惩罚了主人** |

两条路都行不通——尾验天生是「二选一」，不能「保留主人、去掉旁人」。

**正确思路**：问题不是「这段录音是不是主人的」，而是「这段录音里，哪些毫秒是主人在说、哪些是旁人在说」——这是**时间轴上的精确标注**，不是整段判决。

因此分工如下：

- **主路径（S2）— 主人轨提取**：按时间切窗，label owner/other，**只把 owner 窗拼成 WAV 送 STT** → 实现「保留主人那句、删掉旁人那句」。
- **兜底（S2）— 提取后尾验**：对**已提取的主人轨 `owner_wav`** 做最终 verify；提不出任何合格 owner 轨（全段都是旁人）时才整轮作废 + 提示重说。
- **硬场景（S3）— Diarization**：两人同一时刻叠音、SV 切窗长期灰区时，才上分离模型。

#### 3.4.1 两种插话，能力边界不同

现网 **单麦 mono**（cpal）。要先分清场景，再选机制——不能混为一谈。

| 插话类型 | 例子 | S2 主人轨提取 | S3 Diarization |
|----------|------|---------------|----------------|
| **A. 先后插话** | 主人说完半句 → 旁人插一句 →（可能）主人再说 | ✅ **主路径可解**：低分窗跳过，高分窗保留 | 可选增强 |
| **B. 完全叠音** | 两人同时开口，波形叠在一起 | ⚠️ SV 切窗不够，可能两轨都标错 | ✅ **需要** |
| **C. TTS 播报时旁人喊** | Jachin 在念，旁人插嘴 | ✅ barge-in + SV（§3.4.3 层 4） | — |

**结论**：产品目标「不听旁人」在 **场景 A（最常见）** 下，**S2 即可达成**，不必等 Diarization；Diarization 是为 **B + 复杂恢复** 准备的，不是 S2 偷懒的借口。

#### 3.4.2 典型时间线（先后插话 — S2 主路径）

```text
主人：  ……「今天天气怎么样」
旁人：              「哎等等我帮你问」
        │←─ owner 窗 ─→│← non-owner ─→│

旧行为：整段 STT → 「今天天气怎么样哎等等我帮你问」  ❌

S2 主人轨提取：
  窗 1–2：score ≥ T_win → owner
  窗 3–4：score < T_win → 丢弃（不进 STT）
  送 STT 的 WAV = 仅窗 1–2 拼接
  STT → 「今天天气怎么样」  ✅ 旁人句未进 L3
```

**TTS 播报时被旁人打断**（另一类）：

```text
主人已唤醒，Jachin 正在朗读
旁人插话 → 现网 barge-in 会停播 ── 默认不验插话者
改法：barge-in 须主人 SV 通过才 hijack（§3.4.3 层 4）
```

#### 3.4.3 分层应对（按优先级重排）

**层 0 — S1：唤醒门**

- 陌生人 **喊不醒** Jachin；与录指令阶段的「剔旁人」无关，但是第一道门。

**层 1 — S2 主路径：主人轨提取（Owner Track Extraction）**

**推荐时机：VAD 截句完成后离线处理**（截句后把整段 WAV 送 `/label_windows` 批量算窗），而不是边录边逐帧实时滑窗。原因：

- **稳定**：截句后完整 WAV 已知，窗长和步长可按实际时长最优划分，不受实时延迟抖动影响。
- **低开销**：仅在截句事件触发后跑一次批量 extract，不占 KWS_IDLE 或 TTS 播放期的 CPU。
- **代价**：主人说完 → VAD 截句 → label → concat → STT，全链路比纯唤醒门多 **~50-100ms**（见 §4.4），可接受。

实时截断（可选）：若产品要求在旁人一开口立刻停录、不等 VAD 尾静音，则在 LISTENING 期间并行维护**轻量滑窗**（每 2 个 audio chunk 约 0.4s 一次），score 掉分 + 能量突变即截断；后续仍走截句后离线 label。两者不互斥，各自开关。

LISTENING 按 **~0.4～0.6s 步长、~0.5～1.0s 窗长** 滑动：

```text
对每个窗 w_i：
  score_i = cosine( embed(w_i), centroid )
  label_i = owner   if score_i ≥ T_win
          = other   if score_i < T_win_low   （迟滞，防抖动）

连续 owner 窗合并为片段 S1, S2, …
仅 concat(S*) → owner_wav → STT → L3
other 窗：不写 STT、不 inject、可打日志 speaker.skip_segment
```

- **这就是「只删旁人那句」**：删的是 **时间轴上的 non-owner 窗**，不是「整段作废」。
- **最短 owner 片段**：例如 ≥ 0.3s，避免噪声窗误触发 STT。
- **多段 owner**（插话后主人又说）：S2 默认 **只送第一段 owner**（保守）；设置可开 **「多段 owner 合并」**——中间 other 窗仍丢弃，只 concat 各 owner 段，**仍不需要 Diarization**，但需标定防误并。

**实时截断（与层 1 同构，边录边做）**

score 曲线掉分且 **能量突变** 时，不必等 800ms 尾静音：

```text
  score:  ████░░░░     ← 掉分点 = 疑似旁人开口
          截断送 STT ──┘ 只提交掉分前已确认的 owner buffer
```

与 `endpointing`：**「标为非 owner」优先于「继续攒句」**。

**层 2 — S2 兜底：提取后尾验（不是主路径）**

```text
owner_wav = concat(所有 owner 片段)
若 owner_wav 为空或总长 < T_min ──→ 整轮作废，提示「请单独说」
否则 verify(owner_wav) ──→ 通过才 STT
```

- **作用**：防止「全是旁人窗但误标」或「提取结果仍不可信」时 silent 送 L3。
- **不再**对「含混录的原始整段」做唯一判决——那才会导致「要么全过带旁人、要么全丢带主人」的矛盾。

**层 3 — S3：Diarization / 分离（叠音与多段恢复）**

仅在以下情况启用或自动降级触发：

- 窗级 score **长期灰区**（分不出 owner/other）
- 检测到 **高能量叠音**（双人同时说，SV 全窗都中等分）
- 用户开启 **「强抗干扰」** 且层 1 多次提取失败

流程：`diarize(wav) → 轨 0/1/… → 每轨 verify → 只 STT score 最高的 owner 轨`。  
CAM++ 仍负责 **哪轨是主人**；Diarization 负责 **时间上重叠的拆开**。

**层 4 — S3：TTS 播放时 barge-in 须主人**

见 `VOICE_BARGE_IN_AND_WAKE_ACK.md`：barge-in 触发 → ring 切片 SV → 非主人 **不打断** Jachin。

#### 3.4.4 产品默认建议（写进设置）

| 设置项 | 默认 | 说明 |
|--------|------|------|
| 唤醒门 | 开 | S1 |
| **主人轨提取（LISTENING 滑窗）** | **开** | S2 **主路径**；实现「不听旁人」 |
| 提取后尾验 | 开 | S2 **兜底**；非整段尾验 |
| 插话后再说：多段 owner 合并 | 关 | 开则 concat 多段 owner；标定成本高 |
| barge-in 须主人声纹 | 关 | S3 |
| 强抗干扰（自动 Diarization） | 关 | S3；叠音环境再开 |
| 提不出 owner 轨时 | 提示重说 | 不 silently 送 L3 |

#### 3.4.5 和「保安只站唤醒门」怎么同时成立？

1. **算力**：KWS 静默期不跑 SV；LISTENING 内 **限频率滑窗**（如每 2 个 audio chunk 算一次，或截句后离线算一遍），不在每一帧 extract。
2. **语义**：唤醒门 = **谁可以开始一场对话**；主人轨提取 = **这场对话里只听谁说的字**。两道门，各管一段。

```text
  ┌──────── 唤醒门（S1）────────┐
  │ 非主人喊唤醒 → 拒            │
  └────────────┬───────────────┘
               │ 主人
               ▼
  ┌──────── VAD 录指令（可含混录）──┐
  └────────────┬───────────────────┘
               ▼
  ┌──────── 主人轨提取（S2 主路径）──────────────┐
  │ 滑窗 label → 只 concat owner 窗 → owner_wav   │
  │ other 窗：丢弃，不进 STT                        │
  └────────────┬──────────────────────────────────┘
               ▼
  ┌──────── 提取后尾验（S2 兜底）────────────────┐
  │ owner_wav 空/不可信 → 提示重说                  │
  │ 通过 → STT → L3                                 │
  └────────────┬──────────────────────────────────┘
               │ 叠音 / 灰区
               ▼
  ┌──────── Diarization + 逐轨 SV（S3）──────────┐
  └──────────────────────────────────────────────┘
```

---

## 4. 后端：JVS 声纹模块（`sv_service.py`）

在现有 **voice_server（:18982）** 里加一块，与 STT/TTS **同进程、懒加载、可 warm**，不要另起一个重进程。

### 4.1 模型

- 路径：`data/models/voice/sv/speech_campplus_sv_zh-cn_16k-common/`
- **CAM++**，16 kHz，中文说话人验证，embedding **192 维**
- 推理：ONNX Runtime（与 JVS 其它模型一致）；ModelScope 仅作加载方式参考，**不**在运行时依赖网络

### 4.2 API（建议）

| 方法 | 路径 | 干什么 |
|------|------|--------|
| GET | `/v1/sv/status` | 模型是否就绪、版本 |
| POST | `/v1/sv/extract` | 上传一段 WAV → `{ embedding: float[192] }` |
| POST | `/v1/sv/enroll` | 上传 1～3 段 WAV → 服务端 extract 并 **返回 centroid**（也可只返回各条 embedding 让桌面自己平均） |
| POST | `/v1/sv/verify` | 上传 **一段 WAV** + **centroid**（JSON 数组）→ `{ score, is_match, reason }` |
| POST | `/v1/sv/label_windows` | 上传 **整段 WAV** + **centroid** + 窗参（步长/窗长）→ `{ windows: [{ start_ms, end_ms, score, label: owner\|other }] }`（S2 主人轨提取；可桌面算窗、JVS 批量 extract） |
| POST | `/v1/sv/filter_owner_track` | 上传整段 WAV + centroid → `{ owner_wav_b64, skipped_segments, owner_duration_ms }`（可选便捷接口，等价 label + concat） |
| POST | `/v1/models/audio/warm` | 现有 warm 增加 `sv: true` |

**注意**：`verify` 的比较在 **JVS 内**完成（WAV → embedding → cosine 与 centroid），桌面**不必**先 extract 再传两个向量，减少往返和泄露面。

### 4.3 相似度与阈值

分数：**余弦相似度**，理论范围约 0～1（向量已归一化时即点积）。

**两道门的阈值要分开定义**，不能共用同一对 high/low：

| 场景 | 音频长度 | score 稳定性 | 建议初始阈值 |
|------|----------|--------------|--------------|
| **唤醒门**（ring buffer 切片） | ~1.0～1.5s | 较高（含完整唤醒词） | `high ≥ 0.78`，`low < 0.72` |
| **滑窗 label**（LISTENING 内每窗） | ~0.5～1.0s | 较低（短窗 score 抖动大） | `win_high ≥ 0.70`，`win_low < 0.62` |

说明：
- 唤醒切片较长、含完整说话，CAM++ 结果稳定，可以用较严的 0.78/0.72。
- 滑窗短窗 score 天然抖动，若用唤醒门阈值会把主人的正常话也误剔。`win_high/win_low` 应**通过真人多次录音标定**，默认偏宽松，让「主人窗」尽量不漏。
- 两组阈值均写入 `owner_voiceprint.json`（见 §7），可在设置里独立调节。
- 外部说的「80%」≈ 0.80，对短窗偏严；**从 0.70 起调，误剔主人声音就降，误放旁人就升**。

**迟滞防抖**（两门均适用）：避免 score 曲线在阈值附近频繁跳变导致 label 锯齿，连续 2 次超阈才算切换。

### 4.4 性能预期

| 操作 | 输入长度 | 预估耗时（CPU warm） | 在哪条链路上 |
|------|----------|----------------------|--------------|
| 唤醒门 extract + cosine | ~1.5s WAV | **50～150ms** | 滴声播放前；用户可感知 |
| 滑窗批量 label（截句后） | 每窗 ~0.5-1s，共 N 窗 | **~20～60ms / 窗**，4 窗约 80～240ms | VAD 截句后，STT 前 |
| `filter_owner_track` 全段 | 整段（通常 2～8s） | **~150～400ms** | 同上，含 concat |

**用户感知**：
- 第一道门（唤醒）：滴声前增加 < 200ms，可接受。
- 第二道门（滑窗）：整段指令从说完到 STT 启动多 ~200ms，叠在 VAD 尾静音（约 800ms）后面，**用户通常感觉不到**。
- **JVS 必须 warm**（`POST /v1/models/audio/warm` 含 `sv: true`），避免第一次唤醒冷启动延迟超过 1s。

---

## 5. 副门：大窗 PTT / VAD 怎么办？

主门解决「**陪伴唤醒**」；大窗手动麦克风是**另一条入口**，策略分开讲。

**「S2 主人轨提取」和 PTT 副门是两个正交的概念**，必须先区分清楚：

- **主人轨提取**（§3.4）：针对的是**进入 LISTENING 后**，旁人插话时的混录问题——适用于唤醒后的指令录制，也适用于 PTT 录制时段。S2 开启后，**无论是唤醒模式还是 PTT 模式，只要处于 LISTENING 状态，就做滑窗 label**。
- **副门策略**：指的是**是否在 PTT 入口处做一次额外的「是不是主人」验证**（等同于对 PTT 也加唤醒门），与滑窗 label 无关。

| 副门策略 | 说明 | 建议阶段 |
|----------|------|----------|
| **A. 无额外验证** | PTT 入口不做 SV；S2 开启后 LISTENING 内仍走滑窗 | **S1 默认** |
| **B. 会话信任** | 若过去 N 分钟内唤醒门已通过，PTT 入口也免验 | S2 |
| **C. PTT 入口也验** | 每次 PTT 按下时先 verify（费算力，仅高安全场景） | 可选设置 |
| **D. PTT 首次验** | 应用启动后第一次 PTT 验一次，后面信任 | 折中 |

推荐产品默认：**S1 PTT 入口不加验证**；大窗语音和陪伴共用 **同一 centroid 文件**。S2 起，**PTT 录音时段内自动启用滑窗 label**（不需要额外开关，它是主人轨提取的自然延伸）；若需对 PTT 入口加门，开策略 C/D。

---

## 6. 代码该改哪（给 implementer 的地图，仍不写代码）

| 层级 | 改什么 | 不改什么 |
|------|--------|----------|
| **Rust `wake_pipeline.rs`** | KWS 命中 → 切 ring 切片 → 调 JVS verify → 通过才 `enter_wake_capture` / 播 Earcon；**S2** LISTENING 滑窗或截句后 **主人轨提取** → 仅 owner_wav 送 STT | 不在 React 里验 |
| **Rust `endpointing.rs`** | **S2** 与滑窗 score 联动：掉分截断、non-owner 不写入 utterance buffer | — |
| **Rust `wake_kws.rs` / 未来 Porcupine** | 只负责「何时算唤醒」；声纹不认具体 KWS 引擎 | — |
| **`voice_wake_bridge.rs` / inject** | 仅 **ACCEPT** 后才 inject 用户文本 | — |
| **`voiceOrchestrator.ts`** | **不动唤醒门**；仍在 L3 回复后管 TTS | 不是 KWS 回调入口 |
| **`chat.tsx` / 大窗 PTT** | S1 可不动；S2 做会话信任标记 | — |
| **设置 UI** | 认主向导、开关、重新登记、高级阈值 | — |
| **JVS `voice_server`** | 新增 `sv_service.py` + 路由 | 不连 L3 WebSocket |

---

## 7. 本地文件格式（SSOT）

路径：`%USERPROFILE%\.jachin\voice\owner_voiceprint.json`

```json
{
  "version": 2,
  "model_id": "metis-sv-speech-campplus-zh-cn-16k-common",
  "wake_word_hint": "用户登记时的唤醒句快照，仅展示用",
  "sample_count": 3,
  "centroid": [ /* 192 floats，唤醒门和滑窗共用同一 centroid */ ],

  "wake_gate": {
    "threshold_high": 0.78,
    "threshold_low": 0.72
  },

  "window_label": {
    "win_threshold_high": 0.70,
    "win_threshold_low": 0.62,
    "win_step_ms": 500,
    "win_len_ms": 800,
    "min_owner_duration_ms": 300,
    "debounce_count": 2
  },

  "created_at": "ISO8601",
  "updated_at": "ISO8601"
}
```

说明：
- `wake_gate`：第一道门（唤醒切片 ~1.5s）的判决阈值，较严。
- `window_label`：第二道门（LISTENING 滑窗）的判决阈值，较宽松，因短窗 score 抖动大。
- `debounce_count`：连续 N 次超阈才切换 label，防 score 曲线锯齿。
- `min_owner_duration_ms`：合格 owner 片段的最小时长，避免噪声窗触发 STT。
- 两组阈值均可在设置页独立调节，调后更新 `updated_at`。

Phase 2 可用 Windows DPAPI 加密整个文件。

---

## 8. 安全、隐私、失败

- **拒识音频不落盘**；日志只记 score 与 reason。
- **centroid 不出本机**（除非用户将来显式开云备份）。
- **JVS / SV 挂了**：fail closed —— 唤醒 **不** 进入 STT，提示「声纹服务不可用」；不要默默放行陌生人。
- **主人感冒沙哑**：允许设置里「重新认主」；灰区提示重说唤醒句。

---

## 9. 分期落地（调整优先级）

### Phase S1 — 唤醒门 + 后端（MVP）

- [ ] JVS：`sv_service.py` + `/extract` + `/verify` + `/enroll` + warm（`sv: true`）
- [ ] 设置页：**认主** 3 段（1 唤醒句 + 2 普通指令句）→ 写 `owner_voiceprint.json`（v2 格式，含 `wake_gate` 阈值）
- [ ] Rust：**唤醒命中 → 环形缓冲切片 → verify → 分支**
- [ ] **通过后才** Earcon / 「我在」→ 原有 VAD → STT → L3
- [ ] 拒识日志 + 可选 Orb 红闪
- **验收**：主人唤醒全流程正常；旁人喊同一唤醒句 **无 STT、无 L3、无气泡**

### Phase S2 — 主人轨提取 + 副门

- [ ] JVS：新增 `/label_windows` + `/filter_owner_track` 接口
- [ ] `owner_voiceprint.json` 升级 v2 格式，写入 `window_label` 阈值组
- [ ] **主人轨提取（主路径）**：VAD 截句后 `/label_windows` → 仅 concat owner 窗 → STT
- [ ] **提取后尾验（兜底）**：owner_wav 空或 verify 失败 → 提示重说，不送 L3
- [ ] **实时截断（可选）**：LISTENING 期间并行轻量滑窗，score 掉分 + 能量突变 → 提前 endpointing
- [ ] 会话信任：唤醒通过后 N 分钟内 PTT 入口免验（可选）
- [ ] 登记质量检测（太短/太噪不计入 centroid）
- [ ] profile 加密存储
- [ ] 设置页：暴露 `window_label` 阈值调节
- [ ] Porcupine 真 KWS 接入后，声纹门 **无需改逻辑**（仍用 ring slice）
- **验收**：先后插话场景 → STT **只有主人句**，旁人句 **不进 L3**（§3.4.2）

### Phase S3 — 叠音、加固

- [ ] **Diarization + 逐轨 SV**：完全叠音 / 窗级灰区自动降级
- [ ] 反回放（随机数字挑战句）
- [ ] 高安全模式：PTT 也验
- [ ] **barge-in 须主人 SV**：旁人不能 hijack TTS
- [ ] **多段 owner 合并**（插话后主人再说）：可选高级设置

---

## 10. 验收清单

1. 未认主 + 门开 → 无法语音对话，引导登记。
2. 认主后 → 用**自己的唤醒句**唤醒，延迟可接受，对话正常。
3. 旁人用**相同唤醒句** → 不答应、不 STT、log 有 `speaker.reject`。
4. 关声纹门 → 与现网一致。
5. JVS 未启动 → 明确失败，不 infinite「正在识别」。
6. 大窗 PTT（S1）→ 行为与现网一致，**不**因声纹回归失败。
7. **（S2）先后插话**：主人说半句 + 旁人插一句 → STT/L3 **仅含主人内容**，旁人句 **不出现** 在气泡或 L3 输入；log 有 `speaker.skip_segment`。
8. **（S2）整段全是旁人**：提不出 owner 轨 → 提示重说，不 STT（兜底尾验）。
9. **（S3）TTS 播放时旁人 barge-in** → 不 hijack（若开启 barge-in 须主人 SV）。
10. **（S3）两人叠音** → Diarization 或明确失败提示，不 silent 混送 L3。

---

## 11. 和旧版方案（v1）的差异说明

| v1/v2 写法 | v3 修正 |
|------------|---------|
| 每条 utterance 都跑 SV | **唤醒瞬间 + LISTENING 限频滑窗**；KWS 静默期/TTS 期不跑 |
| 三种 Profile 统一全量 Gate | **主门 = 唤醒**；PTT/VAD 内走滑窗 label，不是独立一道门 |
| SV 在 VAD 截句后（整段尾验） | **唤醒门：KWS 命中后 ring 切片**；插话：截句后离线 label，尾验只做兜底 |
| 拦截写在前端 Orchestrator | **Rust wake_pipeline** 为 SSOT |
| 插话 = 整段尾验或作废 | **主人轨提取** 为主路径；尾验只对 **提取后 owner_wav** 兜底 |
| Diarization 才能删旁人句 | **先后插话** S2 截句后 label 即可；Diarization 仅 **叠音** 硬场景 |
| 唤醒门和滑窗共用同一阈值 | **两套阈值分开**：`wake_gate`（1.5s 切片，较严） vs `window_label`（~0.8s 短窗，较宽） |
| 只录唤醒句 3 段作 centroid | 加录 1～2 段普通指令，让 centroid 覆盖非唤醒语调，减少滑窗误剔 |

---

## 12. 一句话总结

**两道门：第一道（唤醒）验「谁能开始对话」；第二道（LISTENING 滑窗）在时间轴上标出「谁在说话」，只把主人那段 WAV 送 STT——旁人插话的字不进 L3，主人说的字也不会被整段丢掉。两套阈值分开标定（唤醒 1.5s 切片较严，短窗较宽），同一个 centroid 两门共用，登记时多录 1 段普通指令覆盖非唤醒语调。叠音硬场景才上 S3 Diarization。**

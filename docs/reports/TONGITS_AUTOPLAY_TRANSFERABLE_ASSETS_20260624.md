# Tongits 自动打牌模块：可迁移资产总结

**日期**：2026-06-24  
**范围**：`scripts/main_bot_loop.py`、`tongits_*`、视觉识别、结算监控、自动化冒烟链路  
**目的**：总结 Tongits 自动打牌里真正有价值、值得迁移到其它 Agent / 桌面自动化 / 游戏 QA 场景的东西。

---

## 1. 总体判断

Tongits 模块最有价值的地方，不是“它会打 Tongits”本身，而是它已经做出了一套比较完整的 **现实世界自动化闭环**：

```text
看见画面
  -> 判断状态
  -> 用确定性规则决策
  -> 在时间预算内执行点击
  -> 执行前后持续守卫
  -> 用协议/日志验证结果
  -> 把失败样本沉淀下来继续训练
```

这套东西可以迁移到很多地方：

- 网页游戏自动化
- 桌面 UI 自动操作
- 游戏 QA / 冒烟测试
- Agent 视觉执行器
- 实时自动化工作流
- 多模态识别 + 规则决策系统

它的核心思想是：**不要让大模型直接控制一切，而是让模型做感知增强，让规则和状态机负责执行安全。**

---

## 2. 最值得保留的设计

### 2.1 感知、决策、执行三层解耦

当前链路大致是：

```text
视觉感知：看见牌、按钮、弹窗、结算画面
规则决策：判断摸牌、吃牌、亮牌、贴牌、弃牌
坐标执行：把决策变成真实点击
```

这点非常重要。

因为视觉模型经常会抖，云端 VLM 也会超时。如果直接让模型输出“点哪里”，系统会很脆。现在的做法是：

- 视觉只回答“我看到了什么”
- 规则只回答“现在应该怎么打”
- 执行器只负责“怎样安全地点”

这让系统更可测、更可解释，也更容易替换其中一层。

**可迁移价值**：

任何 UI Agent 都可以采用这个结构：

```text
Perception -> Decision -> Actuation
```

比如自动填写网页表单、自动玩小游戏、自动做桌面 QA，都不要让感知层直接变成动作层。

---

### 2.2 用本地规则做主决策，而不是用 LLM 打牌

`tongits_rules.py` 把牌型、散牌点、吃牌、弃牌、亮牌等逻辑做成了纯函数。

这是一件很好的事。

实时游戏里，LLM 直接决策有几个问题：

- 慢
- 不稳定
- 很难复现
- 解释成本高
- 有时会违反游戏规则

而本地规则函数有明显优势：

- 快
- 稳定
- 可单测
- 可回放
- 可持续调参

当前模块里比较好的规则抽象包括：

- `HandCard` 统一牌表示
- `find_set_melds`
- `find_straight_melds`
- `loose_scatter_points`
- `can_chow_with_discard`
- `pick_dump_card`
- `pick_next_meld_plan`

**可迁移价值**：

以后做其它游戏或业务自动化时，可以遵循这个原则：

> LLM/VLM 负责补充不确定信息，业务决策尽量落成本地可测规则。

---

### 2.3 回合守卫机制很值得抽出来

`tongits_turn_guard.py` 里的设计很干净：

```text
get_play_session()
abort_active_play_session()
is_play_aborted(session)
TurnPlayContext
```

它解决的是实时自动化里最危险的问题：

> 系统开始点击时还是我的回合，但点到一半回合结束了。

Tongits 的处理方式是：

- 每个回合创建一个 session generation
- 回合结束时 generation +1
- 正在执行的点击链路每一步都检查 session 是否还有效
- 真正点击前再截屏确认绿圈仍在
- 不满足就抛 `TurnAbortedError`，立即停止后续动作

这套机制非常可迁移。

**可迁移场景**：

- 游戏自动化：回合结束、战斗结束、弹窗切换时停止点击
- 网页自动化：页面跳转后取消旧点击链
- 桌面自动化：窗口焦点变化后中断危险操作
- Agent 工具执行：用户取消任务后，中断后续工具调用

可以把它抽象成通用的：

```text
ExecutionSession
  - generation
  - aborted()
  - ensure_active()
  - budget
  - deadline
```

---

### 2.4 时间预算驱动执行，而不是“能做多少做多少”

Tongits 自动出牌不是盲目追求最优，而是在有限回合时间里优先完成最重要的动作。

它明确设计了：

- 总预算：`TONGITS_TURN_BUDGET_SEC`
- Dump 预留：`TONGITS_TURN_DUMP_RESERVE_SEC`
- 每步亮牌估时：`TONGITS_TURN_MELD_STEP_EST_SEC`
- 手牌刷新估时：`TONGITS_TURN_HAND_REFRESH_EST_SEC`

这带来一个很成熟的策略：

```text
时间充足 -> 可以亮牌、贴牌、重识别
时间紧张 -> 跳过可选动作，优先 Dump 收尾
时间超出 -> 放弃本轮，避免误点
```

这个设计很有意义。

很多自动化失败不是因为“不会做”，而是因为“做得太晚”。Tongits 这里把实时性作为一等公民，值得迁移。

**可迁移价值**：

可以迁移成通用 Agent 执行策略：

```text
必须动作 > 可选优化动作
收尾动作 > 追求最优动作
安全退出 > 半途冒险
```

---

### 2.5 多模态融合做得务实

当前系统没有押宝单一路径，而是组合多种感知：

- YOLO：快，适合定位牌和框
- VLM：语义强，适合读标签、识别复杂 UI
- 本地 OpenCV 探针：便宜、快、稳定，适合按钮颜色和箭头
- 本地 OCR / 模板：适合固定区域数字识别
- CDP / 协议：适合结算、金币变化这类结果事实

这套组合很务实。

例如 qwen_full 模式里：

```text
YOLO 提供坐标
VLM 提供牌面标签
宿主按行和 x 坐标融合
```

这比“全靠 VLM 读完整画面”更可靠，也比“全靠 YOLO 识别全部语义”更灵活。

**可迁移价值**：

任何多模态自动化都可以采用这个原则：

> 不要问“哪个模型万能”，要问“每种信号最擅长证明什么”。

---

### 2.6 UI 本地探针是低成本高价值资产

`tongits_ui_probe.py` 里有很多小而实用的探针：

- 黄箭头比例判断是否摸牌/吃牌
- 牌面像素比例判断弃牌顶是否存在
- Challenge / Fold 按钮色块比例判断决斗弹窗
- POINT 区域本地 OCR
- Continue / Details / 结算按钮探针

这些不是高大上的模型，但很有工程价值。

因为本地探针有几个好处：

- 快
- 便宜
- 可解释
- 不依赖网络
- 适合做第一层门控

VLM 更适合做覆核，而不是每帧都让它当主判。

**可迁移价值**：

桌面自动化里应该沉淀一批类似的本地 UI Probe：

```text
button_color_probe
loading_spinner_probe
modal_overlay_probe
toast_probe
table_cell_probe
numeric_badge_probe
```

这会显著降低对大模型的依赖。

---

### 2.7 视觉打牌和协议结算分开，是很好的双通道验证

自动打牌主链路靠视觉：

```text
看牌 -> 出牌 -> 点按钮
```

但胜负和金币变化不靠视觉猜，而是通过 CDP / 协议帧监听：

```text
监听 3016 / 3017
解析 sumWinBonus / coinChanged
零和校验
去抖
落 settlement.log / csv
```

这是非常好的架构判断。

因为视觉适合操作过程，协议适合结果事实。

如果拿视觉去读结算金币，会遇到：

- 动画遮挡
- 结算面板闪烁
- OCR 错读正负号
- 多帧重复记账

而协议层可以做更强的事实校验：

- msgType 白名单
- 玩家列表解析
- 自己 uid 学习
- 三人零和校验
- 3016 / 3017 去重
- warmup 跳过历史回放

**可迁移价值**：

这适合迁移为通用自动化原则：

> 操作过程用视觉，最终结果尽量用结构化信号验证。

比如：

- 自动下单：视觉点击，订单结果用接口/日志确认
- 游戏 QA：视觉操作，结算用协议确认
- 网页自动化：浏览器操作，Network / Console / DOM 事件确认
- 桌面 Agent：UI 操作，文件/数据库/系统事件确认

---

### 2.8 自学习识牌库很有产品化潜力

`self_learning_card_recognizer.py` 的思路很好：

```text
OpenCV 模板命中 -> 秒级返回
模板 miss -> 调 VLM 识别
识别成功 -> 保存模板
下次再见 -> 本地命中
```

这相当于一个小型“视觉经验缓存”。

它的价值不只在牌：

- 图标识别
- 游戏道具识别
- 按钮皮肤识别
- UI 小组件识别
- 常见错误弹窗识别

都可以用同样方法。

**可迁移价值**：

可以抽象成通用能力：

```text
VisualMemory
  - match_local_template()
  - fallback_to_vlm()
  - learn_template()
  - reload_without_restart()
```

这比每次都调用 VLM 更便宜，也越用越快。

---

### 2.9 难例自动沉淀，是模型迭代闭环

系统会把“心虚样本”保存到 `hard_examples`：

```text
置信度落在可疑区间
  -> 保存原图
  -> 冷却防刷盘
  -> 后续用于标注 / 再训练
```

这个设计非常有意义。

它让线上运行不只是消耗模型，而是在持续生产训练数据。

**可迁移价值**：

所有视觉 Agent 都应该有类似机制：

```text
低置信度样本
误判后样本
用户纠正样本
执行失败样本
协议结果不一致样本
```

这些应该自动进入数据集，而不是靠人手动截图。

---

### 2.10 Dry-run 与真实执行分离

Tongits 执行器保留了 dry-run：

```text
TONGITS_AUTO_PLAY_DRY_RUN=1
```

这让系统可以在不真实点击的情况下验证：

- 当前会做什么动作
- 会点哪个坐标
- 为什么这么判断
- 预算是否足够
- 是否会触发守卫

这是所有危险自动化都应该有的能力。

**可迁移价值**：

Agent 执行系统应默认支持：

```text
plan only
dry-run
confirm before live
live mode
```

不要一上来就真点、真删、真提交。

---

### 2.11 冒烟测试做成端到端结果验证

`test_k11_tongits_autoplay_smoke.py` 不是简单启动脚本，而是验证一整局：

```text
打开页面
进入 Tongits
启动协议监控
启动自动打牌
等待一局结算
生成 PASS / FAIL
可发送 Lark 卡片
```

这个冒烟测试很有产品意义。

因为它验证的不是某个函数，而是：

> 这套自动打牌系统在真实环境里有没有产生一局可确认的结果。

**可迁移价值**：

其它 Agent 能力也应该有类似“端到端验收冒烟”：

- 不只测 API 是否通
- 不只测页面能打开
- 要测完整任务是否完成
- 最终结果要有结构化证据

---

## 3. 最有迁移价值的模块清单

| 模块 | 当前作用 | 可迁移成 |
|------|----------|----------|
| `tongits_turn_guard.py` | 回合 session、abort、预算 | 通用实时执行守卫 |
| `tongits_ui_probe.py` | 本地 UI 色块/OCR 探针 | 通用桌面 UI Probe 库 |
| `tongits_rules.py` | 纯函数规则决策 | 业务规则引擎模板 |
| `tongits_coord_executor.py` | 分阶段点击执行 | 通用坐标执行编排器 |
| `tongits_result_monitor.py` | 协议结算解析、去抖、校验 | 通用结构化结果监控器 |
| `tongits_cdp_capture.py` | CDP 注入、console / WS 捕获 | 浏览器自动化观测层 |
| `self_learning_card_recognizer.py` | VLM fallback 自学习模板 | 通用视觉记忆缓存 |
| `hard_examples` 采集逻辑 | 难例沉淀 | 视觉模型数据闭环 |
| `test_k11_tongits_autoplay_smoke.py` | 一局端到端验收 | 自动化能力冒烟模板 |

---

## 4. 可以抽象出来的通用能力

### 4.1 RealtimeExecutionGuard

从 `tongits_turn_guard.py` 抽象：

```text
RealtimeExecutionGuard
  - create_session()
  - abort_session()
  - ensure_active()
  - remaining()
  - must_do_final_action()
```

适合任何有“窗口期”的自动化任务。

### 4.2 VisualProbeKit

从 `tongits_ui_probe.py` 抽象：

```text
VisualProbeKit
  - color_ratio_probe
  - roi_probe
  - local_digit_ocr
  - button_state_probe
  - modal_presence_probe
```

适合桌面端、浏览器端、游戏端 UI 判断。

### 4.3 PerceptionDecisionAction Pipeline

从主链路抽象：

```text
PerceptionResult
DecisionPlan
ActionExecutor
ExecutionResult
```

这是 Agent 视觉执行器的基础形态。

### 4.4 ProtocolResultMonitor

从 CDP / result monitor 抽象：

```text
ProtocolResultMonitor
  - attach_browser()
  - inject_bridge()
  - observe_event()
  - parse_result()
  - validate_result()
  - debounce_record()
```

适合浏览器游戏、Web App QA、交易流程验证。

### 4.5 VisualMemoryCache

从自学习识牌抽象：

```text
VisualMemoryCache
  - local_match()
  - vlm_learn_on_miss()
  - atomic_save_template()
  - hot_reload_template()
```

适合任何固定视觉元素的长期识别。

---

## 5. 对 Jachin Agent 架构的启发

Tongits 模块对 Jachin 主 Agent 很有启发。

### 5.1 Agent 不应该只会“想”，还要会“守”

真实自动化最怕的是错时机、错窗口、错状态。

Tongits 的守卫机制说明：执行系统需要一层独立于模型的安全上下文。

迁移到 Jachin 后，可以形成：

```text
AgentExecutionContext
  - cancel / abort
  - active window check
  - time budget
  - required finalization
  - dry-run / live gate
```

### 5.2 视觉 Agent 要有证据分层

Tongits 不是“看一眼就点”，而是组合证据：

```text
UI 色块
OCR
YOLO 坐标
VLM 标签
协议事件
历史缓存
```

Jachin 的视觉 UI Agent 也应该这么做。

### 5.3 工具调用后应该有结果验证

Tongits 的结算监听说明：

> 动作执行成功，不等于任务完成成功。

点击 Dump 成功只是动作成功；收到 3016 结算才是结果证据。

这可以迁移到所有 Agent 工具：

```text
执行动作
  -> 观测反馈
  -> 结构化验证
  -> 记录结果
```

### 5.4 规则和模型应该合作，不应该互相替代

Tongits 里模型负责看，规则负责打。

这比“模型全包”更稳。

Jachin 其它业务域也可以采用类似分工：

```text
模型：理解、归纳、识别、补全
规则：约束、排序、选择、校验
执行器：落地动作、重试、收尾
```

---

## 6. 当前仍需注意的问题

这些不是否定，而是后续产品化时要继续收敛的地方。

1. **主链路文件较大**
   `main_bot_loop.py` 过于集中，后续应继续拆成状态机、感知、等待态、结算、日志等独立模块。

2. **环境变量很多**
   灵活是优点，但需要配置档分层：默认、开发、生产、低配机器、云识别优先、本地优先。

3. **部分逻辑仍带实验痕迹**
   例如 rule_bot、main_bot_loop、smoke、CDP monitor 多条路径并存，需要明确“生产主路径”和“实验路径”。

4. **视觉识别和规则引擎之间还可加强置信度协议**
   现在很多地方已有保护，但未来可以把每张牌、每个动作都带上统一 confidence / source / timestamp。

5. **结算协议很强，但依赖目标网页协议形态**
   迁移到其它游戏时，要把 result monitor 做成可配置 parser，而不是写死 3016/3017。

---

## 7. 最推荐优先迁移的三件事

如果只挑三件最值得迁移到 Jachin 主系统，我建议是：

### 第一：TurnPlayContext 式执行守卫

这是实时自动化安全底座。

它能直接提升 Jachin 在桌面自动化、浏览器自动化、长任务工具执行中的稳定性。

### 第二：视觉感知 + 规则决策 + 坐标执行三段式

这是多模态 Agent 真正可控的基础架构。

它比“VLM 看图后直接点”成熟得多。

### 第三：协议/结构化结果监控

这是端到端验收的关键。

Agent 做完事之后，必须有独立证据证明“真的完成了”。

---

## 8. 结论

Tongits 自动打牌模块最有意义的地方，是它把一个混乱、实时、视觉不稳定、网络不稳定的网页游戏，拆成了可控的工程系统：

```text
状态守卫
视觉感知
规则决策
预算执行
失败降级
协议验收
数据沉淀
端到端冒烟
```

这套经验完全值得迁移。

它说明 Jachin 后续做视觉 Agent、桌面 Agent、游戏 QA、自动化执行器时，不应该只追求“模型更聪明”，而应该建设一套更稳的自动化底座：

> 模型负责看懂世界，规则负责约束行为，执行器负责安全落地，监控器负责证明结果。


# Tongits 自动化打牌系统 — 实现汇报（全链路）

**汇报日期**：2026-06-19  
**汇报对象**：项目内部 / 技术复盘  
**系统定位**：菲律宾 Tongits 网页版挂机 — 绿圈探回合 → 全屏识牌 → 本地规则决策 → 坐标自动点击  
**主入口**：`scripts/main_bot_loop.py`  
**主执行器**：`scripts/tongits_coord_executor.py`  
**分辨率基准**：1920×1080（坐标均可环境变量覆盖）

---

## 一、系统概述

1. 本系统是一套 **「视觉侦察 + 规则引擎 + 坐标点击」** 的 Tongits 自动打牌机器人，不依赖 LLM 做出牌决策（主路径）。
2. 生产挂机默认链路：`main_bot_loop` 轮询绿圈 → 到我方回合后后台线程截屏识牌 → 若开启 `TONGITS_AUTO_PLAY` 则调用 `execute_scout_coord_turn()` 自动摸/吃/亮/贴/弃。
3. 非我方回合时，主循环 `on_waiting` 回调仅处理 **决斗弹窗自动应答**（Challenge / Fold）；结算弹窗自动点击代码保留但未挂载。
4. 存在 **第二条旁路**：`scripts/tongits_rule_bot.py`（OmniParser + VLM + `TongitsDecisionEngine`），用于实验/全链路 Bot，与主挂机 **不是同一入口**。
5. 四大模块分工：
   - **感知**：YOLO / Qwen-VL / Florence 认牌（`main_bot_loop` + `vision_proxy_qwen.py`）
   - **规则**：纯函数牌型与部署逻辑（`tongits_rules.py`）
   - **执行**：坐标点击与阶段编排（`tongits_coord_executor.py`）
   - **守卫**：绿圈 abort + 时间预算（`tongits_turn_guard.py`）

---

## 二、入口与启动方式

6. **Windows 一键启动**：`scripts/run_main_bot_loop.bat` → 优先使用 `.venv-omniparser\Scripts\python.exe` 执行 `scripts/main_bot_loop.py`，避免 Anaconda `c10.dll` 问题。
7. **Python 直接启动**：`python scripts/main_bot_loop.py`（需在项目根或 `scripts` 下，并配置 `.env` 中的 `DASHSCOPE_API_KEY` 等）。
8. **CLI 模式**（`main_bot_loop.main()`）：

| 参数 | 行为 |
|------|------|
| 无参 | 默认挂机：绿圈 → 侦察 → 可选自动出牌 |
| `--debug` | 绿圈/手牌 ROI 校准窗口 |
| `--once` | 单次探测绿圈与发牌状态 |
| `--save-only` | 仅截图，不做 YOLO |
| `--continuous` | 每秒连续 YOLO（调试） |
| `--yolo-once` | 单次全屏 YOLO，不依赖绿圈 |
| `--hybrid` / `--qwen-full` / `--full-yolo` / `--florence-local` | 切换侦察模式 |
| `--auto-play` | 开启自动出牌（默认 dry-run 只打日志） |
| `--auto-play-live` | 真实鼠标点击 |
| `--conf` / `--iou` / `--model` | YOLO 推理参数 |

9. 启动时自动 `load_dotenv`：依次尝试 `ROOT/.env`、`core/.env`、`~/.jachin/.env`。
10. 启动后 **预热 8 秒**（`TONGITS_STARTUP_GRACE_SEC`）：期间不截屏、不处理弹窗，给用户切回游戏窗口的时间。

---

## 三、主循环与回合检测

11. **主循环函数**：`_turn_poll_loop()`，每 `POLL_INTERVAL_SEC`（默认 0.2s）调用一次 `is_my_turn()`。
12. **绿圈判定原理**：
    - 截取左下角头像 ROI（默认 `AVATAR_ROI = (8,720,118,118)`，可 `TONGITS_AVATAR_ROI` 覆盖）。
    - BGR→HSV，统计绿色像素（两段 HSV 范围），可选仅统计边框环（`TONGITS_GREEN_RING_BORDER_ONLY`）。
    - 绿像素数 > `GREEN_PIXEL_THRESHOLD`（默认 80）→ 判定为「我的回合」。
13. **去抖**：`TurnCycleTracker` — 进入需连续 `TURN_ENTER_FRAMES`（默认 2）帧；退出需 `TURN_EXIT_FRAMES`（默认 4）帧；脚本启动首帧只同步状态、不触发侦察。
14. **回调链**：
    - 绿圈上升沿 → `on_yolo_turn_started()` → 后台线程 `_on_yolo_turn_worker()`。
    - 绿圈下降沿 → `on_turn_ended()` → `tongits_turn_guard.abort_active_play_session()` 使出牌 session 失效。
    - 非我方回合 → `on_waiting()` → `_try_handle_fight_offer_overlay()`。
15. **全帧绿圈复判**：出牌链路内每次点击前通过 `is_my_turn_on_frame(bgr)` 再验，避免截屏与轮询状态不一致。
16. **WIN 结算屏检测**：`_is_round_end_win_screen()` — 须 **绿圈已消失** + 头像区黄/红 WIN 徽标超阈值；命中则跳过 YOLO 并可点中央 WIN 按钮（`TONGITS_AUTO_CLICK_WIN`）。
17. **截屏互斥**：`_capture_busy` 锁，上一回合侦察未完成时跳过新回合，并登记 `_pending_turn_scout` 补跑。

---

## 四、截屏与画面校验

18. **截屏后端优先级**（Windows）：`ImageGrab`（与用户所见前台一致）→ `mss` → `pyautogui`；纯被动读屏，**不**自动切窗口/聚焦（避免破坏 F11 全屏）。
19. **色彩清洗**：`_prepare_frame_bgr()` — BGRA/RGB 统一转 BGR，供 OpenCV/YOLO 使用。
20. **牌桌校验**：`_looks_like_game_table()` — 中央区域 HSV 蓝/青比例 ≥ `GAME_TABLE_BLUE_RATIO_MIN`（默认 0.06），过滤桌面/PowerShell 误帧。
21. **发牌校验**：`_is_dealt_frame()` — 手牌 ROI 内白牌/边缘像素比达标（`HAND_CARD_RATIO_MIN` / `HAND_EDGE_RATIO_MIN`），未发牌则重试。
22. **重试机制**：`TONGITS_CAPTURE_RETRY_COUNT`（默认 2）次，间隔 `TONGITS_CAPTURE_RETRY_DELAY_SEC`（默认 1.5s）。
23. **回合延迟**：绿圈触发后等待 `TONGITS_TURN_CAPTURE_DELAY_SEC`（默认 1.0s）再侦察，等动画稳定。

---

## 五、视觉侦察流水线（四种模式）

24. **模式选择**：`TONGITS_SCOUT_MODE`，工厂函数 `_create_turn_scout()` 实例化对应 Scout。

| 模式 | Scout 类 | 手牌 | 明牌/对手/弃牌 |
|------|----------|------|----------------|
| `hybrid`（默认） | `YOLOScreenScout` | YOLO 裁区推理 | 对手区 Qwen VLM 并行 |
| `yolo_full` | `YOLOScreenScout` | 全屏 YOLO | 全屏 YOLO 按坐标分桶 |
| `qwen_full` | `QwenFullScreenScout` | YOLO 坐标 + VLM 标签融合 | 仅 VLM 标签（无坐标） |
| `florence_local` | `FlorenceLocalScout` | 本地 Florence OCR+HSV | 五战区本地识别 |

25. **YOLO 推理参数**（默认）：`conf=0.40`、`iou=0.40`、`imgsz=512`；权重 `scripts/model/weights.pt`（`TONGITS_YOLO_MODEL`）。
26. **五战区 ROI 分桶**（`_load_board_zone_rois()`）：
    - `player_hand` — 屏幕下方手牌
    - `my_melds` — Drop/Fight/Group/Dump 按钮正上方我方明牌槽
    - `opponent_left` / `opponent_right` — 左右对手明牌
    - `center_discard` — 中央弃牌堆顶牌（0 或 1 张）
27. 检测框按中心点落入 ROI 归属；归属顺序：中央弃牌 → 左对手 → 右对手 → 我方明牌 → 手牌（小区域优先，防重复）。
28. **一副牌约束**：`_validate_table_card_uniqueness()` — 全桌同标签不可重复出现；失败时 `deck_valid=False`，跳过自动出牌。

### 5.1 qwen_full 融合（当前推荐主模式）

29. **并行结构**：YOLO 手牌裁区推理（拿坐标）+ 五路 VLM 并行（拿标签），墙钟时间取最慢一路。
30. **手牌融合**（`TONGITS_QWEN_YOLO_VLM_FUSE=1`）：
    - VLM 输出标签序列，YOLO 输出框坐标。
    - 按行聚类（`TONGITS_FUSE_ROW_Y_TOL`）、组内 x 排序，VLM 标签与 YOLO 框配对。
    - 缺框时按牌间距插值补坐标（`TONGITS_FUSE_CARD_GAP_PX`）。
    - 明牌区 YOLO 框从手牌中剔除（明牌仅 VLM 标签）。
31. **明牌/对手/弃牌**：仅有标签无坐标；Sapaw 贴牌时用槽位估算点击 x（`_zone_click_xy()`）。
32. **VLM 代理**（`vision_proxy_qwen.py`）：
    - 职责边界：**只认牌/UI，不出牌决策**。
    - Provider：`TONGITS_VLM_PROVIDER=gemini|qwen`；默认 Qwen `qwen3.5-flash`。
    - 关键 API：`analyze_zone_labels_compact_with_qwen()`、`analyze_duel_point_with_qwen()`、`analyze_waiting_overlay_type_with_qwen()`。
    - 标签 SSOT：`parse_card_label()` / `canonical_card_label()`（A 不用 1，J/Q/K 不用 11/12/13）。

### 5.2 hybrid 模式

33. 手牌 + 我方明牌 + 弃牌顶：YOLO batch 裁区推理。
34. 左右对手明牌：裁切 ROI → 单次 `analyze_zone_melds_with_qwen()` 列标签。

### 5.3 florence_local 模式

35. 全本地 `vision_florence_local.py`，无云依赖；五战区 Florence OCR + HSV 辅助。

---

## 六、回合侦察 Worker 流程

36. **入口**：`_on_yolo_turn_worker(scout)`，在后台线程执行，不阻塞绿圈轮询。
37. **步骤**：
    1. 获取 `_capture_busy` 锁；检查启动预热。
    2. 延迟 `TURN_CAPTURE_DELAY_SEC`。
    3. 重试截屏 + 牌桌/发牌校验；WIN 屏则跳过。
    4. `scout.infer_turn_frame(bgr)` → `TurnScoutResult`（`by_zone`、`deck_valid`、`raw_detection_count` 等）。
    5. 更新 `_last_known_hand_scatter`（YOLO 手牌散牌点，供决斗兜底）。
    6. `_print_scout_report()` 打印五战区战报。
    7. `_save_turn_board_screenshot()` → `scripts/omnioutput/`。
    8. 影子特训 `_shadow_training_try_capture()` → `scripts/hard_examples/`（心虚置信度框抓拍）。
    9. 若 `TONGITS_AUTO_PLAY=1` 且 `deck_valid` → `_run_coord_auto_play()`。
38. **战报格式示例**：
    - `我的手牌 12 张: [SK@(1420,874), ...]`
    - `我方已亮明牌 8 张: [HA, H2, ...]`
    - `左侧对手明牌 6 张: [...]`
    - `中央弃牌顶牌 1 张: [CJ]`

---

## 七、数据输出目录

| 目录 | 触发 | 命名 | 开关 |
|------|------|------|------|
| `scripts/omnioutput/` | 每回合原图 | `{ts}_board_raw.jpg` | `TONGITS_TURN_SAVE_SCREENSHOT` |
| `scripts/yolo_marked/` | YOLO 检出成功 | `{ts}_raw{N}_n{M}_marked.jpg` | `TONGITS_YOLO_SAVE_MARKED` |
| `scripts/hard_examples/` | 心虚置信度 0.50~0.80 | `hard_example_{ts}.jpg` | `TONGITS_HARD_EXAMPLES` |
| `scripts/my_melds_crops/` | qwen_full 调试 | `{ts}_my_melds.jpg` | `TONGITS_SAVE_MY_MELDS_CROP` |

39. 影子特训冷却 `TONGITS_HARD_EXAMPLE_COOLDOWN_SEC`（默认 3s），防止静止画面刷盘。

---

## 八、自动出牌总流程（`execute_scout_coord_turn`）

40. **总入口**：`tongits_coord_executor.execute_scout_coord_turn()` → 创建 `TurnPlayContext` → `_execute_scout_coord_turn_body()`。
41. **阶段顺序固定**：**Draw（摸/吃/Fight）→ Meld（Drop/Sapaw）→ Dump（弃一张结束回合）**。
42. **输入**：`TurnScoutResult.by_zone` 中的手牌检测（含 `center_x/y`）、弃牌顶标签、桌面明牌标签。
43. **决策来源**：`tongits_rules.py` 纯函数（`decide_draw_action`、`pick_next_meld_plan`、`pick_dump_card`），**不经 LLM**。
44. **输出**：`{ok, actions[], hand[], dump, elapsed_ms}`；成功后刷新决斗用散牌点缓存。

---

## 九、Draw 阶段（摸牌 / 吃牌 / Fight）

45. **是否进入摸牌**：`needs_draw_phase()` — 手牌 < `TONGITS_HAND_READY_COUNT`（默认 13）且 UI 黄箭头 `is_draw_phase_hint()` 为真，或张数 ≤ `TONGITS_HAND_MIN_BEFORE_DRAW`（默认 12）回退。
46. **摸/吃决策**：`tongits_rules.decide_draw_action()`：
    - 弃牌顶 VLM 标签 + 手牌能否立刻成组（`can_chow_with_discard()`）→ 优先吃顶牌。
    - UI 辅助：`tongits_ui_probe.is_chow_available()` 检测弃牌 ROI 黄箭头+牌面。
    - 否则摸中央暗牌堆 `deck`。
47. **Fight 前置**（可选）：散牌点 ≤ `TONGITS_FIGHT_SCATTER_MAX`（默认 7）时，摸牌前先点 Fight 按钮（`TONGITS_AUTO_FIGHT=1`）。
48. **点击实现**：
    - 摸牌：`tongits_ui_probe.deck_click_xy()` → 默认 `(859,412)`。
    - 吃牌：`discard_click_xy()` → 默认 `(1010,415)`，可双击。
    - 经 `tongits_rule_bot.physical_click_xy()` 映射到屏幕坐标。
49. **摸牌后快刷**：等待 `TONGITS_POST_DRAW_WAIT_SEC`（默认 1.2s）→ 仅 YOLO+VLM 刷新手牌（`TONGITS_HAND_ONLY_RESCOUT=1`），不重跑五路 VLM。
50. **摸牌失败重试**：仍显示须摸牌且手牌未增加 → 重试点击暗牌堆，最多 `TONGITS_DRAW_RETRY_MAX`（默认 3）次；时间不足则停止重试。
51. **吃牌特殊路径**：吃顶牌后客户端自动亮组 → 跳过 Drop → 直接进入 Dump 路径。

---

## 十、Meld 阶段（亮牌 Drop / 贴牌 Sapaw）

52. **入口**：`_execute_meld_phase()`，循环调用 `pick_next_meld_plan()` 选最优一步。
53. **Drop 亮牌逻辑**：
    - 游戏 Autosort 已分组；**点组内最左一张手牌**即抬起整组（无需 Group 按钮）。
    - 本地二次校验：须为合法刻子/顺子（`_drop_group_validation()`）且在左侧（`_is_drop_group_left_side()`，防误点右侧散牌）。
    - 默认再点 Drop 按钮确认（`action_button_xy("drop")` → 默认 `(518,726)`）。
    - 已在 `my_melds` 的组跳过。
54. **Sapaw 贴牌逻辑**：
    - 选手牌一张 → 点桌面目标牌组槽位（`_sapaw_target_xy()` 按 ROI 内标签序列估算 x）。
    - 须至少留 1 张手牌用于后续 Dump。
55. **择优规则**：`pick_next_meld_plan()` 在 Drop 与 Sapaw 间选 **散牌分降低最多** 的一步；最多 `TONGITS_MAX_MELD_STEPS`（默认 8）步。
56. **每步后再侦察**：`TONGITS_MELD_FAST_RESCOUT=1` 时仅刷新手牌 + my_melds（不重跑对手/弃牌 VLM）。
57. **时间紧张跳过**：剩余 ≤ Dump 预留（默认 5s）→ 跳过可选亮牌/贴牌；或仅尝试一次本地秒算 Drop 后直 Dump。

---

## 十一、Dump 阶段（弃牌结束回合）

58. **选牌**：`pick_dump_card()` — 在散牌中选 **散牌点数最高** 的一张（`loose_cards()` 排除已在刻子/顺子中的牌）。
59. **执行**：点选手牌坐标 → 点 Dump 按钮（`action_button_xy("dump")` → 默认 `(1399,724)`）。
60. **Dump 前快刷**：预算允许时 `_refresh_hand_only()` 刷新手牌坐标（VLM 超时则回退 YOLO 框）。
61. **Dump 后**：绿圈消失 → `abort_active_play_session()` 终止 session；日志 `回合完成（已执行，Xms）`。

---

## 十二、回合守卫与时间预算（`tongits_turn_guard.py`）

62. **Session 机制**：`get_play_session()` / `abort_active_play_session()` — 绿圈消失时 generation+1，正在执行的出牌线程通过 `is_play_aborted()` 感知。
63. **TurnPlayContext** 字段：`session`、`started_at`、`budget_sec`（默认 18s）、`grab_frame`、`dry_run`。
64. **每次点击前**：`ensure_active()` — 检查 session 未 abort + 绿圈仍在。
65. **时间预算规则**：
    - `must_dump_only()`：剩余 ≤ `TONGITS_TURN_DUMP_RESERVE_SEC`（默认 5s）→ 进入 Dump 预留段。
    - `can_do_optional_meld()`：剩余 > 预留 + `TONGITS_TURN_MELD_STEP_EST_SEC`（默认 4s）才做亮牌/贴牌。
    - 手牌快刷 VLM 超时自动压缩（`cap = remain - 1.3s`）。
66. **中止异常**：`TurnAbortedError` — 捕获后返回 `{ok: False, aborted: True}`，不继续点击。

---

## 十三、非我方回合 — 决斗弹窗自动应答

67. **挂载位置**：`yolo_turn_main_loop._waiting()` → 仅 `_try_handle_fight_offer_overlay(scout)`；**结算自动点击未挂载**。
68. **触发条件**：非我方回合 + 非启动预热 + 非 `_capture_busy` + 节流/冷却通过。
69. **识别链（由严到松）**：
    1. `is_fight_offer_overlay()` — Challenge(橙) + Fold(蓝) 色块比 ≥ 0.06。
    2. **严格门槛** — challenge/fold 均 ≥ 0.12（`TONGITS_FIGHT_DETECT_*_RATIO_MIN_STRICT`）。
    3. VLM 覆核 `expected=duel`；连续超时 + UI 强证据 → **Fail-Open 降级**（`[StrategyShift]` 日志，默认 3 次后降级 12s）。
    4. **POINT 证据门控** — 本地 OCR 可读或墨迹比达标，防误判结算页底部按钮。
70. **决策链**：
    - **优先**：弹窗 POINT（本地 OCR + 可选云 VLM）vs `TONGITS_FIGHT_OVERLAY_POINT_MAX`（默认 7）→ Challenge / Fold。
    - **兜底**：POINT 不可读 → 用 `_last_known_hand_scatter`（上轮出完牌散牌点）vs `TONGITS_FIGHT_DEFENSE_SCATTER_MAX`（默认 7）。
    - 均无 → 跳过本轮不应答。
71. **散牌点缓存刷新**：
    - 侦察后：YOLO 手牌 `_hand_scatter_from_detections()`。
    - 出牌成功后：本轮手牌标签减去 Dump 牌 → `loose_scatter_points()` 估算。
72. **点击坐标**：Challenge `(835,816)` / Fold `(1126,816)`（`tongits_ui_probe`，可 `TONGITS_BUTTON_CHALLENGE_XY` 覆盖）。
73. **决斗 POINT 修复**：ROI `xywh`→`xyxy` 转换后再裁图；本地 OCR 优先；云端 4s 冷却。

---

## 十四、UI 探针（`tongits_ui_probe.py`）

74. **摸/吃阶段探针**：
    - `probe_draw_phase_stats()` — 牌堆/弃牌 ROI 内黄箭头比、牌面比。
    - `is_draw_phase_hint()` — 中央暗牌堆黄箭头 → 摸牌阶段。
    - `is_chow_available()` — 弃牌顶牌面 + 黄箭头在合理区间 → 可吃。
    - `deck_click_xy()` / `discard_click_xy()` — 点击坐标（略偏 ROI 中心下方）。
75. **决斗弹窗探针**：
    - `probe_fight_offer_stats()` / `is_fight_offer_overlay()`。
    - `duel_point_local_ocr()` — POINT 区模板匹配 OCR（0-9）。
    - `challenge_offer_click_xy()` / `fold_offer_click_xy()`。
76. **结算弹窗探针**（代码存在，主路径未用）：
    - `probe_round_settlement_stats()` / `is_round_settlement_overlay()`。
    - `continue_button_click_xy()` / `continue_button_has_highlight_border()`。
77. 所有探针 ROI 基于 1920×1080 默认中心点，可通过 `TONGITS_BUTTON_*_XY`、`TONGITS_DUEL_BTN_PROBE_*` 等覆盖。

---

## 十五、规则引擎（`tongits_rules.py`）

78. **核心数据结构**：`HandCard`（label/suit/rank/center_x/y）、`TableMeld`、`SapawMove`、`MeldPlan`。
79. **牌组识别**：
    - `find_set_melds()` — 刻子（同点数、花色互异、≥3）。
    - `find_straight_melds()` — 同花顺（≥3）。
    - `find_hand_melds_for_drop()` — 可亮出的非重叠牌组。
80. **散牌计算**：
    - `loose_cards()` — 不在任何 meld 中的牌。
    - `loose_scatter_points()` — 仅散牌点数之和（全成组时返回 0）。
81. **吃牌判定**：`can_chow_with_discard()` — 顶牌加入手牌后能否立刻成组；收益阈值 `TONGITS_CHOW_GAIN_THRESHOLD`。
82. **部署择优**：`pick_next_meld_plan()` — Drop 亮牌 vs Sapaw 贴牌，按散牌分降低量选最优；保证 Dump 前至少留 1 张。
83. **桌面解析**：`parse_zone_labels_to_melds()` / `all_table_melds_from_zones()` — 从 VLM 标签序列还原对手/我方牌组，供 Sapaw 目标定位。
84. **主挂机链不调用** `tongits_rule_bot.TongitsDecisionEngine`；该引擎仅供 `tongits_rule_bot.py` 旁路使用。

---

## 十六、物理点击（`tongits_rule_bot.physical_click_xy`）

85. **坐标映射**：`_screen_click_xy()` — 截图像素坐标 → PyAutoGUI 屏幕坐标（`TONGITS_SCALE_COORDS` 按分辨率缩放）。
86. **执行**：`pyautogui.moveTo` + `click()`；`FAILSAFE=True`（鼠标移到左上角中止）。
87. **Dry-run**：`TONGITS_AUTO_PLAY_DRY_RUN=1`（默认）时 `skip_real=True`，仅打日志不点击。
88. **固定按钮坐标表**（1920×1080，均可 `TONGITS_BUTTON_*_XY` 覆盖）：

| 动作 | 默认 (x,y) |
|------|-----------|
| deck（暗牌堆） | (859, 412) |
| discard（弃牌顶） | (1010, 415) |
| drop（亮牌） | (518, 726) |
| group | (1104, 724) |
| dump（弃牌） | (1399, 724) |
| fight | (813, 722) |
| challenge（决斗应战） | (835, 816) |
| fold（决斗认输） | (1126, 816) |
| continue（结算继续） | (650, 965) |

89. 屏幕尺寸：`TONGITS_SCREEN_WIDTH` / `TONGITS_SCREEN_HEIGHT`（默认 1920×1080）。

---

## 十七、旁路：全链路 Rule Bot（`tongits_rule_bot.py`）

90. **入口**：`python scripts/tongits_rule_bot.py --live|--vlm|--board|--opencv`。
91. **流程**：OmniParser 截屏 → 元素字典 → VLM 认牌（`vlm_board_analyzer`）→ `TongitsDecisionEngine.decide_action()` → `tongits_turn_executor` 执行。
92. **决策引擎**：`TongitsDecisionEngine` 按 `TurnPhase`（DRAW/MELD/DUMP/FIGHT_OFFER/IDLE）输出语义动作。
93. **Fast Mode**（`tongits_turn_executor.execute_fast_turn`）：OpenCV 盲摸 → 单次手牌 VLM → 本地规则 Dump。
94. **Full Mode**：多步 VLM 识阶段 + Group/Drop + Dump。
95. 与主挂机关系：**并行存在、非同一入口**；生产推荐 `main_bot_loop` + `tongits_coord_executor`。

---

## 十八、文件依赖与调用关系

```
run_main_bot_loop.bat
  └─ main_bot_loop.py :: main() → yolo_turn_main_loop()
       ├─ _turn_poll_loop()                    # 绿圈轮询
       │    ├─ on_started → _on_yolo_turn_worker() [Thread]
       │    ├─ on_ended  → tongits_turn_guard.abort_active_play_session()
       │    └─ on_waiting → _try_handle_fight_offer_overlay()
       │
       ├─ _create_turn_scout()                 # 侦察模式工厂
       │    ├─ YOLOScreenScout (hybrid/yolo_full)
       │    ├─ QwenFullScreenScout (qwen_full)
       │    └─ FlorenceLocalScout
       │
       ├─ _on_yolo_turn_worker()
       │    ├─ scout.infer_turn_frame()
       │    │    ├─ vision_proxy_qwen.py       # VLM 认牌
       │    │    └─ fast_card_recognizer.py   # ROI 解析
       │    └─ _run_coord_auto_play()
       │         └─ tongits_coord_executor.execute_scout_coord_turn()
       │              ├─ tongits_turn_guard.TurnPlayContext
       │              ├─ tongits_rules.py     # 纯函数决策
       │              ├─ tongits_ui_probe.py  # 摸/吃/决斗探针
       │              ├─ tongits_turn_executor.action_button_xy
       │              └─ tongits_rule_bot.physical_click_xy
       │
       └─ [_try_handle_round_settlement_overlay]  # 未挂载主循环

tongits_rule_bot.py（旁路，独立 CLI）
  ├─ vlm_board_analyzer.py
  ├─ TongitsDecisionEngine
  └─ tongits_turn_executor.py
```

96. **核心文件清单**：

| 文件 | 职责 |
|------|------|
| `scripts/main_bot_loop.py` | 主循环、侦察、战报、决斗等待态 |
| `scripts/tongits_coord_executor.py` | 自动出牌阶段编排与点击 |
| `scripts/tongits_rules.py` | 牌型/部署纯函数 SSOT |
| `scripts/tongits_ui_probe.py` | OpenCV UI 探针（黄箭头/按钮色块/OCR） |
| `scripts/tongits_turn_guard.py` | Session abort + 时间预算 |
| `scripts/tongits_turn_executor.py` | 按钮坐标 + Fast/Full 执行器 |
| `scripts/tongits_rule_bot.py` | 旁路 Bot + physical_click_xy |
| `scripts/vision_proxy_qwen.py` | 云端 VLM HTTP 代理 |
| `scripts/vlm_board_analyzer.py` | 富结构牌局分析（旁路用） |
| `scripts/fast_card_recognizer.py` | 多战区 ROI 推算 |
| `scripts/roi_config.json` | 可选 ROI 校准配置 |

---

## 十九、关键环境变量速查

### 启动 / 回合
- `TONGITS_AUTO_PLAY` / `TONGITS_AUTO_PLAY_DRY_RUN`
- `TONGITS_SCOUT_MODE`（hybrid / qwen_full / yolo_full / florence_local）
- `TONGITS_TURN_CAPTURE_DELAY_SEC`、`TONGITS_STARTUP_GRACE_SEC`
- `TONGITS_AVATAR_ROI`、`TONGITS_POLL_INTERVAL_SEC`

### YOLO / VLM
- `TONGITS_YOLO_MODEL`、`TONGITS_YOLO_CONF`、`TONGITS_YOLO_IOU`
- `TONGITS_VLM_PROVIDER`、`TONGITS_VLM_MODEL`、`DASHSCOPE_API_KEY`
- `TONGITS_VLM_HAND_TIMEOUT`、`TONGITS_VLM_LABEL_ZONE_TIMEOUT`
- `TONGITS_QWEN_YOLO_VLM_FUSE`、`TONGITS_FUSE_*`

### 自动出牌
- `TONGITS_AUTO_CHOW` / `TONGITS_AUTO_DROP` / `TONGITS_AUTO_SAPAW` / `TONGITS_AUTO_FIGHT`
- `TONGITS_TURN_BUDGET_SEC`（18）、`TONGITS_TURN_DUMP_RESERVE_SEC`（5）
- `TONGITS_HAND_READY_COUNT`（13）、`TONGITS_DRAW_RETRY_MAX`（3）
- `TONGITS_POST_DRAW_WAIT_SEC`、`TONGITS_POST_MELD_WAIT_SEC`

### 决斗等待态
- `TONGITS_AUTO_FIGHT_DEFENSE`、`TONGITS_FIGHT_OVERLAY_POINT_MAX`（7）
- `TONGITS_FIGHT_FALLBACK_LAST_SCATTER`、`TONGITS_FIGHT_DEFENSE_SCATTER_MAX`（7）
- `TONGITS_FIGHT_DETECT_*_RATIO_MIN_STRICT`（0.12）
- `TONGITS_OVERLAY_VLM_FAILOPEN_AFTER` / `_SEC`

### 坐标
- `TONGITS_BUTTON_*_XY`、`TONGITS_SCREEN_WIDTH/HEIGHT`
- `TONGITS_HAND_ROI_*_RATIO`、`TONGITS_ROI_MY_MELDS`

---

## 二十、端到端时序（主挂机路径）

```
[轮询 0.2s] is_my_turn() 绿圈?
    │
    ├─ 上升沿 → Thread: _on_yolo_turn_worker
    │              delay 1s → 截屏校验 → scout.infer_turn_frame()
    │              → 战报 + omnioutput
    │              → execute_scout_coord_turn()
    │                   ├─ Draw: 摸/吃/Fight + 手牌快刷
    │                   ├─ Meld: Drop/Sapaw 循环（预算允许）
    │                   └─ Dump: 点散牌 + Dump 按钮
    │
    ├─ 下降沿 → abort_active_play_session()
    │
    └─ 等待态 → _try_handle_fight_offer_overlay()
                   ├─ UI 探针 + VLM 覆核 + POINT OCR
                   └─ Challenge / Fold 点击
```

---

## 二十一、已知限制与待优化

97. **VLM 不稳定**：手牌快刷/等待态覆核频繁 2s 超时，回退 YOLO 框或跳过；Fail-Open 仅覆盖决斗等待态。
98. **时间预算紧**：18s 总预算下，侦察+VLM 常占 8~12s，导致「跳过可选亮牌」直接 Dump；快速 Group→Drop 按钮兜底尚未合入 `tongits_coord_executor.py`。
99. **坐标漂移**：Drop 后手牌自动居中，依赖 Dump 前 hand-only 快刷或 YOLO 重锚；预算不足时可能点到旧坐标。
100. **结算弹窗**：自动点击 CONTINUE 逻辑已实现但未挂载主循环（按当前需求仅处理决斗）。
101. **分辨率/窗口**：强依赖 1920×1080 与游戏全屏在最前；坐标需按实际客户端微调环境变量。
102. **一副牌约束**：VLM 误识会导致 `deck_valid=False` 整轮跳过自动出牌。

---

## 二十二、结论

103. Tongits 自动化打牌系统采用 **「绿圈触发 → 多模式视觉侦察 → 本地规则纯函数决策 → 固定坐标物理点击」** 架构，主路径 **不使用 LLM 出牌**。
104. 当前生产推荐配置：`TONGITS_SCOUT_MODE=qwen_full` + `TONGITS_AUTO_PLAY=1` + `TONGITS_AUTO_PLAY_DRY_RUN=0`（确认坐标后开 live）。
105. 非我方回合仅自动应答决斗弹窗；决策优先弹窗 POINT，不可读时回退上轮散牌点缓存。

# K11 游戏 L3 Agent 执行技能（DOM 文本驱动）

本文件供 **Executor**（`test_k11_l3_agent_games_smoke.py`）在每次调用 L3 `POST /api/v3/agent/run` 时，将全文或摘要注入 `user_input`，作为「大脑」的领域知识与操作边界。**不要在 Python 里写死具体 UI 路径；由本技能 + 页面文本树驱动决策。**

## 测试目标（对应《K11_平台冒烟测试用例》）

| 优先级 | 项 | 说明 |
|--------|----|------|
| P0 | 各游戏正常运行 | 能从大厅进入游戏 → 开局/入局 → 等到局内流程走完 → 结算并退回可继续操作的状态 |
| P0 | 游戏金币同步 | 进场前与退场后各采一次金币快照；差额应在合理范围（极端异常则判可疑） |

## 游戏领域知识

### Tongits King

- **类型**：卡牌类；通常需要先在大厅找到 **「Tongits King」**（或缩写/图标文案），进入 **房间列表或 Quick Play**，再 **Join / Play / Start** 入局。
- **自动化侧重**：大厅点击正确入口 → 局内若出现 **Start / Join / OK / Play**，按需点击 → 等待系统自动打牌直至 **结算** → **Confirm / OK / Exit / Back** 返回大厅。

### Bato-Bato Pick

- **类型**：猜拳类小游戏；入局后多为 **自动播放**，自动化侧重 **等待回合结束**，较少需要频繁点击。
- **自动化侧重**：大厅进入游戏 → 若有 **Play / Start**，点一次开局 → 进入自动演示阶段时使用 **`wait`** 观察 DOM 变化 → 结算后出现 **Result / Win / Loss / Confirm / OK** 时点选并退出。

## 阶段策略

### 大厅阶段

- 在文本树中查找当前 Executor 给出的 **`target_game`** 字符串（允许大小写/空格轻微差异）。
- 优先在 **`__clickable__`** 列表里找 **`inner_text`** 含目标游戏名的条目，并用其 **`selector_hint` / `class`** 构造标准 CSS（可多试 **`js_click`**）。
- 若列表里仍没有可靠命中（仅有 tabs 等），输出 **`wait`**（几秒后 DOM 可能刷新）或 **`fail`**，**不要**臆造属性选择器。

### 局内阶段

- 识别 **Start、Join、Play、OK、Continue** 等开局控件。
- 若已进入自动演示（文案频繁变化但无明确按钮），优先 **`wait`** 再观察。

### 等待阶段

- 若页面仍在加载或动画中（如百分比、loading、或与上一轮快照高度相似），输出 **`wait`**，`seconds` 建议 **3～8**（猜拳类可先 **5**）。
- **禁止**在无等待指令要求的情况下假定游戏已结束。

### 结束阶段

- 识别 **Result、Win、Loss、Draw、Settlement、Confirm、OK、Exit、Back、Leave** 等。
- 在结算界面点击 **Confirm / OK**（收起结算面板）并返回上一层；若返回大厅失败，再给出 **`wait`** 或再次点击 **Exit / Back**。（退回大厅仅用 Confirm／Exit／Back 描述；扩展正文时请避免 **关闭** 与 **招聘** 两字紧邻成词——详见文末「说明」。）

## 输出规范（强制）

你必须 **只输出一段可被解析的 JSON**（建议单行），不要 Markdown、不要 Thought/ReAct。**禁止调用任何 MCP/工具**（当前交互由 Executor 执行）；不要用代码块包裹 JSON；**不要在 JSON 前后写任何说明文字**（例如「我已经分析了页面」），否则 Executor 可能解析失败。

PAGE_CONTEXT 里 **`inner_text`** 是 Executor 摘要用的字段名，**不是** DOM 属性；禁止编造 `div[txt='…']`、`div[inner_text='…']` 之类选择器——浏览器不存在这些属性。请使用 **`selector_hint` / `class` / `id`** 能推出的合法 CSS，或无法命中时用 **`wait`** / **`fail`**。

字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `action` | string | `click` \| `js_click` \| `wait` \| `done` \| `fail` |
| `selector` | string | CSS 选择器；在 **任一 frame** 中命中即可（Executor 会遍历 iframe）。`wait`/`done`/`fail` 可为空字符串。 |
| `seconds` | number | 仅 `action=wait` 时 **必填**，正数秒数。 |
| `reason` | string | 简短人类可读理由（中文可）。 |
| `terminal_ok` | boolean | 仅 `done` 时使用：本局游戏与退出是否认为成功。 |
| `coin_sync_ok` | boolean \| null | 仅 `done` 时可选：若已知 Executor 金币对比结果，可辅助标注；未知填 `null`。 |

## 故障排查指南（Executor 反馈驱动）

当 Executor 在 ``<<< FEEDBACK_FROM_EXECUTOR >>>`` 或 ``last_execution_result`` 中报告 **解析失败、点击未命中、或点击后页面无变化** 时：

1. **第 1～2 次失败**：检查 selector 是否来自当前 ``__clickable__`` 的 ``selector_hint`` / ``class`` / ``id``；可改用 ``js_click`` 代替 ``click``（或反之）尝试同一候选节点。
2. **同一策略连续 3 次仍无效**：**必须更换路径**——禁止无限重复同一 selector。应优先输出 ``wait``（seconds 5～8）等待列表渲染，再重新观察 ``PAGE_CONTEXT``；或换一个 **inner_text** 含目标游戏名的条目。
3. **「inner_text」不是 DOM 属性**：禁止 ``txt=``、``inner_text=`` 等虚构属性；若不确定合法 CSS，宁可 ``wait`` 或 ``fail``，也不要编造选择器。
4. **最终目标不变**：始终围绕 ``target_game`` 完成「大厅 → 入局 →（自动）→ 结算 → 回大厅」；失败反馈是为了纠偏，不是切换话题。

## CSS 选择器约束

Executor 在各 iframe 内使用 ``document.querySelector`` 与 Playwright ``locator``：请选择 **标准 CSS**（如 ``#id``、``.class``、``button``、``a[href*="x"]``）。**勿用** Playwright 专有语法（例如 ``:has-text()``），否则无法命中节点。

示例：

```json
{"action":"js_click","selector":"button[class*='play']","seconds":0,"reason":"大厅尝试点击开局按钮","terminal_ok":false,"coin_sync_ok":null}
```

```json
{"action":"wait","selector":"","seconds":5,"reason":"猜拳演示中，稍后复查 DOM","terminal_ok":false,"coin_sync_ok":null}
```

```json
{"action":"done","selector":"","seconds":0,"reason":"已看到结算并退回大厅路径","terminal_ok":true,"coin_sync_ok":null}
```

```json
{"action":"fail","selector":"","seconds":0,"reason":"连续多轮未见目标游戏名且无法导航","terminal_ok":false,"coin_sync_ok":null}
```

## Executor 传入字段约定（由脚本拼接，模型须阅读）

- `target_game`：本回合要完成的游戏名称。
- `iteration`：当前外层步序号（感知-反馈闭环中的「第几轮尝试」）。
- `coin_hint`：进场/退场及差额摘要（若有）。
- `last_action`：上一轮 Executor 解析出的 JSON 指令摘要（若无则为 none）。
- `last_execution_result`：上一轮执行结果或 ERROR（解析失败 / 点击无效 / OK）。
- `failure_streak`：连续未达成有效进展的次数（供对照「故障排查指南」）。
- `page_context`：多 frame 拼接的简化 DOM 文本与交互元素摘要。

---

**版本**：与 `scripts/test_k11_l3_agent_games_smoke.py` 同步演进；Skill 文档位于 **`docs/tests/game_test_skill.md`**。新增游戏时只需扩展本节「领域知识」与阶段策略，无需改 Executor 框架代码。

**说明**：Executor 会把本文件与 **页面 DOM 全文**拼进 L3 的 `user_input`。站内文案可能含「取消」或单独的「招」「聘」字样（分区导航等）；扩展本节时请避免写入 **`关闭` 紧邻 `招聘`** 等易被 Intent Registry 误判为停招指令的连续词。**服务端**另已按 channel `http_k11_l3_agent_games_smoke` 跳过部分 HR 关键词预检；Skill 侧仍需避免上述四字紧邻以防 Registry 抢先短路。


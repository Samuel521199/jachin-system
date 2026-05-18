---
name: test-lark-file-card
description: |
  模拟 Skill：仅用于 Lark / IM 联调。发「/test」时在 JACHIN_HOME/workspace 下创建带时间戳的 txt，
  并向指定飞书会话推送一张结果卡片。不参与商城发布；逻辑在 l3_node/lark_test_file_skill.py。
native_tools: []
mcp_tools: []
---

# test-lark-file-card（模拟 Skill）

## 触发

在已连接 L3 Lark 长连接的会话中发送单独一行：

```text
/test
```

（仅匹配该行：去掉首尾空白后 **恰好** 为 ASCII 的 `/test`，共 5 个字符。）

**不会触发**：单独发 `test`、`／test`（全角斜杠）、`/testing`、`请执行 /test`、带 @ 或其它前缀整句。

## 行为

1. 在 `{{JACHIN_HOME}}/workspace` 下新建文件，文件名为：`现在是YYYY-MM-DD_HHMMSS.txt`（本地时间，文件名无冒号）。
2. 文件正文固定为：`我是一个测试文件`
3. 向会话 `oc_367e7998b7dfe39c67d1598101defdfe` 发送一张飞书交互卡片（标题 + Markdown 区 + 脚注）。  
   可通过环境变量覆盖目标会话：`TEST_SKILL_LARK_CHAT_ID`。

## 实现位置

可执行逻辑在仓库内 **`l3_node/lark_test_file_skill.py`**；IM 路由在 **`l3_node/im_channels/dispatcher.py`**（与 PMO 触发器同级，在 PMO 之前处理）。

## 定时 /test（L3 进程内，非仅桌面弹窗）

| 说法 | 行为 |
|------|------|
| `/test schedule 17:14` 或 `/test at 17:14` | 注册 **L3 APScheduler** 一次性任务，到点执行写文件 + 发 Lark 卡片 |
| 自然语言含 **`/test`（带斜杠）** + 时刻，如「**下午17:14执行/test**」 | 同上（`l3_node/lark_test_schedule.py`） |
| 模型调用 `util:schedule_desktop_reminder` 且 **body 恰为 `/test`** | 桌面弹窗 **+** 自动同步注册上述 L3 任务 |

**不会绑定本 Skill**：「下午17:14执行**test**」（无斜杠）、`/testing`、整句仅为闲聊里的英文 test。

**注意**：仅「桌面右下角提醒」不会在到点时跑 Skill；须走上表任一路径。

实现：`l3_node/lark_test_schedule.py`；持久化 `~/.jachin/test_skill_scheduled_jobs.json`（L3 重启可恢复未过期任务）。

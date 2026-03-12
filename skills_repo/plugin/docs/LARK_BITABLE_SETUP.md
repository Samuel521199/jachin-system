# Lark 多维表格 - 多 Agent 评审结果同步配置

## 一、Lark 应用配置

1. 在 [Lark 开放平台](https://open.larksuite.com) 创建应用，获取 **App ID**、**App Secret**
2. 为应用开通权限：`bitable:app`、`base:record:create`、`base:record:read`
3. 将应用添加到你使用的多维表文档，并赋予「可编辑」权限

## 二、多维表列结构

`atom_lark_bitable_sync` 默认写入以下列。**无需提前建列**，工具会在一键执行时自动检查，缺失则创建：

| 列名 | 说明 | 示例 |
|------|------|------|
| 候选人 | 候选人姓名（脱敏） | 张某某（脱敏） |
| 职位 | 招聘职位 | 资深 Golang 语言开发 _ 杭州 25-40K |
| 裁决 | 建议面试 / 淘汰 | 建议面试 |
| 技术评分 | 0-100 | 78 |
| 稳定性评分 | 0-100 | 72 |
| 推荐理由 | 一句话摘要 | 技术 78 分 / 稳定性 72 分 → 建议面试 |
| 技术理由 | 技术总监理由 | 候选人有 5 年 Go 开发经验… |
| 稳定性理由 | HR BP 理由 | 近 3 年 2 段经历… |
| 评审时间 | 评审时间 | 2025-03-09 14:32:00 |
| RunID | 运行 ID | run_abc123xyz |
| PDF链接 | 简历 PDF 链接（可选） | 空或 URL |

若你的多维表列名不同，可通过 `field_mapping` 参数覆盖，例如：

```json
{"候选人": "姓名", "职位": "岗位", "推荐理由": "摘要"}
```

## 三、环境变量

在项目根目录 `.env` 中配置：

```
LARK_APP_ID=你的AppID
LARK_APP_SECRET=你的AppSecret
# 通知群/单聊 ID（机器人发言、多维表同步完成通知）
LARK_CHAT_ID=oc_xxx
# 可选，不填则用默认多维表
# LARK_APP_TOKEN=RJgcbE9LtaBPILsnttmlS8iHgbf
# LARK_TABLE_ID=tblzQatxI7op9oBp
```

## 四、机器人发言

写入完成后会自动向 LARK_CHAT_ID 群发送通知。也可单独测试机器人发言：

```bash
# 发送固定文案
python scripts\test_lark_send_message.py --text "新一批的候选人信息已更新完成，请查收"

# 用阿里百炼生成回复后发送
python scripts\test_lark_send_message.py --prompt "用户问：现在有几个候选人？" --llm

# 无参数默认发送问候
python scripts\test_lark_send_message.py
```

## 五、测试

```bash
# 干跑（仅解析 MD，不写入）
python scripts\test_lark_bitable_sync.py --dry-run

# 正式写入
python scripts\test_lark_bitable_sync.py

# 指定 MD 文件
python scripts\test_lark_bitable_sync.py --md "path/to/result.md"
```

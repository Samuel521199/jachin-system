# Lark「我要招聘」无回复 — 排查指南

## 一、现象

- L2 技能清单为空：`L2 skills count=0`
- L3 已就绪：`L3 节点已就绪 node_id=l3-xxx，WebSocket 端口 18981`
- Lark 输入「我要招聘，java开发工程师，社招全职，20-35k，本科，1-3年」后长时间无回复

---

## 二、可能原因与排查

### 1. Lark Webhook 未转发到 L3（最常见）

**检查**：`skills_repo/plugin/.env` 是否配置 `L3_WS_URL`：

```env
# 必须配置，否则走百炼直连，招聘任务只记录不执行
L3_WS_URL=ws://127.0.0.1:18981
```

- 未配置：使用本地百炼，招聘类任务只会写入 `data/lark_tasks.json`，不会真正执行
- 端口错误：L3 若在 18982、18983 等端口，需与 `L3_WS_URL` 一致，或使用逗号多端口：`ws://127.0.0.1:18981,ws://127.0.0.1:18982`

**操作**：在 `skills_repo/plugin/.env` 中增加或修正 `L3_WS_URL`，然后重启 Webhook。

---

### 2. Lark Webhook 未启动

**检查**：是否在运行：

```powershell
cd skills_repo\plugin
python scripts\lark_bot_conversation.py --webhook --port 5000
```

启动成功时应看到：

```
[L3 壳模式] Lark 机器人仅做转发，所有对话由 Jachin L3 执行: ws://127.0.0.1:18981
Webhook 服务: http://0.0.0.0:5000/lark-webhook
```

若为 `[独立模式] 使用本地百炼`，说明 `L3_WS_URL` 未配置。

---

### 3. L3 招聘流程耗时过长

招聘流程包含：LLM 推理 → 调用 `atom_post_job_boss`（需 Chrome + Boss 登录）→ `add_automated_recruitment_task`，整体可能 1–3 分钟。

Lark Webhook 会先返回 200，在后台线程中处理并异步发回复。若 L3 执行过慢，用户会感觉「很久没回复」。

**建议**：在 Webhook 收到消息后，先发一条「正在处理招聘需求，请稍候…」到 Lark，再异步调用 L3。

---

### 4. L2 技能为空（影响有限）

`L2 skills count=0` 表示 L2 武库未同步，但招聘相关工具（`atom_post_job_boss`、`add_automated_recruitment_task`）在 L3 本地 MCP 中，不依赖 L2 技能清单。

若希望 HR 透析镜等 Wasm 技能可用，需在 L2 管理端同步技能：

1. 打开 http://192.168.110.14:18888/admin/ 点击 Sync
2. 或执行 `.\scripts\diagnose-skill-sync.ps1`

---

### 5. atom_post_job_boss 前置条件

发布职位需要：

- Chrome 已启动并连接 CDP（如 `http://127.0.0.1:9222`）
- Boss 直聘已登录招聘端
- 环境变量 `CDP_URL` 或 `PLAYWRIGHT_CDP_URL` 指向上述 CDP 地址

若 CDP 未连接或 Boss 未登录，工具会失败，L3 可能返回错误或超时。

---

## 三、推荐启动顺序

1. **L2**：`.\scripts\start-layer2.ps1`（或 run-gateway）
2. **L3**：`.\scripts\start-layer3.ps1` 或 `python -m l3_node`
3. **Lark Webhook**：`cd skills_repo\plugin; python scripts\lark_bot_conversation.py --webhook --port 5000`
4. **ngrok**（公网）：`ngrok http 5000`，并在 Lark 后台配置回调地址

---

## 四、快速自检命令

```powershell
# 1. 检查 L3 WebSocket 是否监听
netstat -ano | findstr 18981

# 2. 检查 plugin .env
Get-Content skills_repo\plugin\.env | Select-String "L3_WS_URL"

# 3. 测试 L3 直连（需先安装 websockets）
python -c "
import asyncio, json, websockets
async def t():
    async with websockets.connect('ws://127.0.0.1:18981') as ws:
        await ws.send(json.dumps({'intent':'你好','origin':'test'}))
        async for m in ws:
            print(m)
            if '"step_type":"answer"' in m or '"step_type":"error"' in m: break
asyncio.run(t())
"
```

---

## 五、相关文档

- [LARK_TO_JACHIN_L3_INTEGRATION.md](../skills_repo/plugin/docs/LARK_TO_JACHIN_L3_INTEGRATION.md) — Lark 接入 L3 集成说明
- [LARK_BOT_CONVERSATION.md](../skills_repo/plugin/docs/LARK_BOT_CONVERSATION.md) — Webhook 配置与事件订阅

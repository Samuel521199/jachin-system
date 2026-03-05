# L3 单体执行引擎

Jachin Nexus V2 - Layer 3 对标 OpenClaw 单机架构。

## 职责

- **本地解密**：从 L2 拉取密文 Key，私钥解密后仅存内存
- **直连 LLM**：持明文 Key 直接请求 api.openai.com 等，不经过 L2
- **ReAct 循环**：Thought -> Action -> Observation -> Final Answer
- **记忆同步**：定期将本地记忆同步至 L2，拉取梦境优化结果

## 目录结构

```
l3_node/
├── __init__.py
├── crypto.py          # RSA 解密（与 L2 对称）
├── llm_client.py      # SecurityContext + LiteLLMEngine
├── agent_core.py      # ReAct Agent + MemorySyncDaemon
├── bootstrap.py       # 引导：注册、拉 Key、创建引擎
├── engine/
│   ├── hooks_pipeline.py  # 洋葱中间件
│   └── ...
└── __main__.py        # 独立运行入口
```

## 依赖

与 `core/requirements.txt` 相同，需：`httpx`, `litellm`, `cryptography`。

## 运行

```bash
# 1. 确保 L2 已启动，且已创建子账号、添加 API Key
# 2. 设置环境变量
export SUB_ACCOUNT_ID=sub-xxx
export L2_BASE_URL=http://localhost:18888

# 3. 运行 L3 节点
python -m l3_node
```

## 集成到 Tauri 桌面端

Tauri 可启动 L3 为子进程，或通过 IPC 调用 `run_l3_agent`。L3 与 L2 解耦，推理请求不经过 L2。

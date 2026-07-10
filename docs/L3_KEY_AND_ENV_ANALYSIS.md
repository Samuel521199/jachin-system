# L3 拿 Key 逻辑与 env 访问全面分析

> **DashScope 国内 / 东南亚分 Key、默认 endpoint、与 L2 下发优先级**：见 [DASHSCOPE_REGIONAL_KEYS.md](./DASHSCOPE_REGIONAL_KEYS.md)（SSOT）。

## 一、L3 拿 Key 的三种路径

| 路径 | 触发时机 | 数据来源 | 注入方式 |
|------|----------|----------|----------|
| **A. L2 auth/poll** | gateway 模式，配对审批通过 | L2 返回 `encrypted_api_keys` | 用 L3 私钥解密 → `ctx.set_key("dashscope", ...)` |
| **B. L2 兜底拉取** | 首次聊天时 ctx 为空 | L2 `GET /api/v2/keys` | 同上 |
| **C. 环境变量兜底** | A/B 无可用 Key 或解密失败 | `os.environ` | `_inject_env_keys_into_ctx()`：DashScope 经 `get_dashscope_regional_credentials()`（`DASHSCOPE_API_KEY_SEA` / `_CN`、回退 `DASHSCOPE_API_KEY` / `QWEN_*`）；其他 provider 读对应 env |

**调用顺序**（以 `generate_response` 为例）：

1. `_inject_env_keys_into_ctx(ctx)` — 优先从 env（含区域化 DashScope）注入
2. 若仍无 Key → `try_fetch_keys_from_l2(ctx)` — 从 L2 拉取
3. 若仍无 Key → 抛出 `RuntimeError`

**与 L2 Key 的关系**：若已为当前区域配置 `DASHSCOPE_API_KEY_SEA` 或 `DASHSCOPE_API_KEY_CN`，实际 LiteLLM 调用时 **不会** 用 L2 下发的国内 Key 覆盖区域 Key（避免国际 endpoint + 国服 sk → 401）。见区域文档第三节。

---

## 二、L3 能否访问 env？按启动方式分析

### 2.1 命令行直接运行（`python -m l3_node --gateway`）

| 环节 | 是否生效 | 说明 |
|------|----------|------|
| **load_dotenv** | ✅ | `__main__.py` 开头执行，`_root` 由 `__file__` 推导为项目根 |
| **路径** | ✅ | `Path(_root)/".env"` 或 `Path.cwd()/".env"` |
| **os.environ** | ✅ | load_dotenv 会写入，`_inject_env_keys_into_ctx` 可读到 |

**结论**：能访问 env。

---

### 2.2 桌面 Tauri 用 Python 回退启动（`spawn_l3_via_python`）

| 环节 | 是否生效 | 说明 |
|------|----------|------|
| **load_dotenv** | ✅ | Python 进程执行 `__main__.py` |
| **cmd.env()** | ✅ | `load_l3_env_vars()` 从项目根 `.env` 与 `~/.jachin/.env` 读取白名单键，通过 `cmd.env(k,v)` 传入；可与 `env_overlay` 合并（网关配对） |
| **cwd** | ✅ | 通常设为项目根 |

**结论**：能访问 env；spawn 显式注入 `L3_ENV_KEYS` 中的变量（含 `JACHIN_ACTIVE_REGION`、`DASHSCOPE_API_KEY_SEA` / `_CN` 等）。

---

### 2.3 桌面 Tauri 用 Sidecar 启动（`bin/l3_node-xxx.exe`）

Sidecar 为 PyInstaller 打包的 l3_node：

| 环节 | 是否生效 | 说明 |
|------|----------|------|
| **load_dotenv** | ⚠️ 可能失败 | `__file__` 指向解压目录，项目根 `.env` 路径可能不对 |
| **sidecar.env()** | ✅ | 与 Python 回退一致：`load_l3_env_vars` 将白名单变量逐项 `sidecar.env(k, v)` 传入（见 `l3_spawn.rs`） |
| **合并** | ✅ | 子进程 `os.environ` 含 DashScope 等键时，`_inject_env_keys_into_ctx` 可读到 |

**结论**：即使 Sidecar 下 `load_dotenv` 未命中项目根，只要 `.env`（或 `~/.jachin/.env`）经 `load_l3_env_vars` 注入，L3 仍可从 env 兜底。**旧版「Sidecar 不传 DASHSCOPE」已不适用当前代码。**

---

## 三、env 加载的代码路径

### 3.1 `__main__.py` 中的 load_dotenv

```python
_root = __file__.rsplit("l3_node", 1)[0].rstrip("/\\")
for _p in [Path(_root) / ".env", Path.cwd() / ".env"]:
    if _p.exists():
        load_dotenv(_p, encoding="utf-8")
        break
```

- **命令行**：`__file__` 为项目内真实路径，`_root` 正确。
- **Sidecar**：`__file__` 为解压路径，第一个路径常无效；是否成功还取决于 cwd 下是否有 `.env`。桌面路径已通过 2.3 的 **显式 env 注入** 补偿。

### 3.2 `_inject_env_keys_into_ctx`（DashScope）

DashScope 使用 `get_dashscope_regional_credentials()`，变量含义与优先级见 [DASHSCOPE_REGIONAL_KEYS.md](./DASHSCOPE_REGIONAL_KEYS.md)。
其他 provider 仍直接读 `OPENAI_API_KEY` 等（以 `llm_client.py` 为准）。

### 3.3 桌面的 `load_l3_env_vars`

- 白名单：`L3_ENV_KEYS`（`clients/desktop/src-tauri/src/l3_spawn.rs`）。
- **Python 回退、Sidecar、便携直接 exe** 均使用该列表向子进程注入。
- 统帅目录 `.env` 与项目根合并时 **不覆盖** 项目已有同名键。

---

## 四、风险与建议

| 场景 | 说明 |
|------|------|
| 仅依赖 Sidecar 内 load_dotenv | 可能找不到项目根 `.env`；依赖 `load_l3_env_vars` 注入即可缓解 |
| dotenv 未安装 | `load_dotenv` 不执行；依赖 spawn 白名单注入或 L2 |
| 区域与 Key 不匹配 | 国际 endpoint + 国内 sk → 401；按区域文档配置 `_SEA` / `_CN` |

可选增强：在 `__main__.py` 中基于可执行文件目录向上查找 `.env`，进一步减少 cwd 依赖（非必须）。

---

## 五、流程总览

```
L3 启动
  ├─ 命令行 / Python 回退 / Sidecar / 便携直接 exe
  │    ├─ load_dotenv（路径正确则 ✅）
  │    └─ [桌面] load_l3_env_vars → cmd.env / sidecar.env（白名单）✅
  │
首次需要 Key 时
  ├─ _inject_env_keys_into_ctx()（DashScope 区域化 + 其他 provider）
  ├─ try_fetch_keys_from_l2()
  └─ LiteLLM：litellm_apply_dashscope_credentials（区域专用 Key 优先于 L2 explicit，见区域文档）
```

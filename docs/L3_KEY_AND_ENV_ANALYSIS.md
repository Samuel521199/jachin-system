# L3 拿 Key 逻辑与 env 访问全面分析

## 一、L3 拿 Key 的三种路径

| 路径 | 触发时机 | 数据来源 | 注入方式 |
|-----|---------|---------|---------|
| **A. L2 auth/poll** | gateway 模式，配对审批通过 | L2 返回 `encrypted_api_keys` | 用 L3 私钥解密 → `ctx.set_key()` |
| **B. L2 兜底拉取** | 首次聊天时 ctx 为空 | L2 `GET /api/v2/keys` | 同上 |
| **C. 环境变量兜底** | A/B 无 Key 或解密失败 | `os.environ` | `_inject_env_keys_into_ctx()` 读 `DASHSCOPE_API_KEY` 等 |

**调用顺序**（以 `generate_response` 为例）：
1. `_inject_env_keys_into_ctx(ctx)` — 优先从 env 注入
2. 若仍无 Key → `try_fetch_keys_from_l2(ctx)` — 从 L2 拉取
3. 若仍无 Key → 抛出 `RuntimeError`

---

## 二、L3 能否访问 env？按启动方式分析

### 2.1 命令行直接运行（`python -m l3_node --gateway`）

| 环节 | 是否生效 | 说明 |
|-----|---------|------|
| **load_dotenv** | ✅ | `__main__.py` 开头执行，`_root` 由 `__file__` 推导为项目根 |
| **路径** | ✅ | `Path(_root)/".env"` 或 `Path.cwd()/".env"`，cwd 通常为项目根 |
| **os.environ** | ✅ | load_dotenv 会写入，`_inject_env_keys_into_ctx` 可读到 |

**结论**：能访问 env，Key 可来自 .env。

---

### 2.2 桌面 Tauri 用 Python 回退启动（`spawn_l3_via_python`）

当 Sidecar 不可用时，桌面会执行 `python -m l3_node`：

| 环节 | 是否生效 | 说明 |
|-----|---------|------|
| **load_dotenv** | ✅ | 同上，Python 进程会执行 `__main__.py` |
| **cmd.env()** | ✅ | `load_l3_env_vars()` 从项目根 `.env` 读取，通过 `cmd.env(k,v)` 传给子进程 |
| **cmd.current_dir()** | ✅ | cwd 设为项目根，`Path.cwd()/".env"` 也能找到 |
| **双重保障** | ✅ | 即使 load_dotenv 失败，spawn 时已显式传入 DASHSCOPE_API_KEY 等 |

**结论**：能访问 env，且有 spawn 显式传参兜底。

---

### 2.3 桌面 Tauri 用 Sidecar 启动（`bin/l3_node-xxx.exe`）

Sidecar 是 PyInstaller 打包的 l3_node：

| 环节 | 是否生效 | 说明 |
|-----|---------|------|
| **load_dotenv** | ⚠️ 可能失败 | `__file__` 指向解压目录（如 `_MEIxxxxx/l3_node/__main__.py`），`_root` 不是项目根 |
| **Path(_root)/".env"** | ❌ | 解压目录下无 `.env` |
| **Path.cwd()/".env"** | ⚠️ 视 cwd 而定 | Sidecar 的 cwd 由 Tauri 决定，可能是 `target/debug` 等，未必有 `.env` |
| **cmd.env()** | ❌ | Sidecar 只传了 `L2_BASE_URL`，**未传** DASHSCOPE_API_KEY 等 |

**结论**：Sidecar 模式下，L3 很可能拿不到 env 中的 Key，只能依赖 L2 下发。

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
- **Sidecar**：`__file__` 为解压路径，`_root` 错误，第一个路径通常找不到；是否成功取决于 cwd 下是否有 `.env`。

### 3.2 `_inject_env_keys_into_ctx` 读取的变量

```python
dash = os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("QWEN_API_KEY") or os.environ.get("QWEN_AI_API_KEY")
openai_key = os.environ.get("OPENAI_API_KEY")
```

依赖 `os.environ` 已有值，来源为：
1. 进程继承的父进程 env
2. `load_dotenv()` 写入
3. Tauri spawn 时 `cmd.env()` 传入（仅 Python 回退路径）

### 3.3 桌面 spawn 的 `load_l3_env_vars`

```rust
// 从 root/.env 读取 DASHSCOPE_API_KEY, OPENAI_API_KEY, LITELLM_FALLBACK_MODELS, LLM_MODEL
for (k, v) in load_l3_env_vars(&root) {
    cmd = cmd.env(&k, &v);
}
```

- 仅用于 **Python 回退**（`spawn_l3_via_python`）
- **Sidecar 路径** 不经过此处，因此不会注入这些变量

---

## 四、风险与修复建议

### 4.1 当前风险

| 场景 | 风险 |
|-----|------|
| Sidecar 模式 | 无 env 注入，若 L2 未下发 Key，L3 无法从 env 兜底 |
| dotenv 未安装 | load_dotenv 不执行，仅靠 spawn 传参（Python 回退）或 L2 |
| .env 路径错误 | `_root` 或 cwd 不对时，load_dotenv 找不到文件 |

### 4.2 建议修复

1. **Sidecar 也注入 env**：在 `sidecar.spawn()` 前，对 Sidecar 使用与 Python 相同的 `load_l3_env_vars` 逻辑，通过 `sidecar.env(k, v)` 传入。
2. **load_dotenv 路径增强**：在 `__main__.py` 中增加基于可执行文件路径的查找（如 `sys.executable` 所在目录向上查找 `.env`），以适配 PyInstaller 等打包场景。
3. **显式兜底路径**：在 `_inject_env_keys_into_ctx` 中，若 `os.environ` 无 Key，可尝试从 `~/.jachin/.env` 或项目根 `.env` 再次 load_dotenv 并重试。

---

## 五、流程总览

```
L3 启动
  ├─ 命令行 / Python 回退
  │    ├─ load_dotenv(项目根/.env) ✅
  │    └─ [Python 回退] cmd.env(DASHSCOPE_API_KEY 等) ✅
  │
  └─ Sidecar
       ├─ load_dotenv ⚠️（路径可能错误）
       └─ 无 cmd.env 注入 ❌

首次需要 Key 时
  ├─ _inject_env_keys_into_ctx() → 读 os.environ
  ├─ try_fetch_keys_from_l2() → L2 GET /keys，解密
  └─ 仍无 Key → RuntimeError
```

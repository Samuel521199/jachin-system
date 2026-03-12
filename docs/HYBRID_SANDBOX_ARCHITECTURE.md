# 混合动力沙箱架构 (Hybrid Sandbox)

**版本**: 1.0  
**状态**: 设计规范  
**定位**: 划时代的微内核沙箱隔离方案 — WASM 为主 + UDS 为辅

---

## 一、为什么传统方案不够「划时代」

| 方案 | 问题 | 结论 |
|------|------|------|
| **Docker / 容器化** | 太重。边缘设备（树莓派）上每个小功能起一个容器，内存开销和冷启动延迟（秒级）会把系统拖垮 | 不适合轻量插件 |
| **Python venv / 子进程** | 太弱。缺乏严格内存和权限隔离。`os.system("rm -rf /")` 即可让整个前线边缘智能体报废 | 安全边界不足 |

为匹配 Jachin Nexus 的赛博朋克与去中心化愿景，真正划时代的方案是 **WebAssembly (WASM) 为主 + UDS (Unix Domain Sockets) 为辅** 的「混合动力沙箱」。

---

## 二、核心逻辑层：WASM + WASI（极速与绝对安全）

适用于绝大多数轻量级业务插件：API 连接器、RAG 文本解析器、上下文记忆路由、多级权限校验逻辑。

### 2.1 Default Deny 安全模型

WASM 最迷人的地方：运行在沙箱中的代码**默认没有任何权限**。

- 不能读写本地文件
- 不能发起网络请求
- 甚至不知道宿主机操作系统是什么

只有当 Layer 2 的 PluginManager 通过 **WASI (WebAssembly System Interface)** 显式注入权限时，才能执行操作：

- 例如：「只允许访问 `/tmp/plugin_a/` 目录」
- 例如：「只允许向 `api.github.com` 发起请求」

### 2.2 微秒级冷启动

WASM 模块的加载和执行是**微秒级**。Layer 2 可实现真正的「即插即用」，高并发时按需瞬间唤醒插件。

### 2.3 跨语言

开发者可用 **Rust、Go、C++** 甚至 **Python (py2wasm)** 编写插件，编译成统一的 `.wasm` 字节码。

### 2.4 WASI 模式（已实现）

`core/wasm_runner.py` 支持 **WASI stdin/stdout** 协议，用于 Python (py2wasm) 插件：

```python
sandbox = JachinWasmSandbox()
result = sandbox.run_plugin(wasm_path, stdin_json={"ticker": "BTC"})  # WASI 模式
# 或
result = sandbox.run_plugin_wasi(wasm_path, stdin_str='{"ticker":"BTC"}')
```

---

## 三、重型算力层：gRPC / UDS 独立进程（隔离崩溃）

适用于重型 AI 和图形插件：VITS 语音合成、NeRFs、高斯溅射等需直接调用 GPU 显存的高级 3D 渲染。

### 3.1 进程隔离与通信

- 在 manifest 中标记为 `heavy_compute`
- PluginManager 将其作为**独立操作系统子进程**启动
- 通过 **UDS (Unix Domain Sockets)** 或轻量级 **gRPC** 与微内核主进程通信

### 3.2 防爆破机制

若 3D 渲染插件因显存溢出 (OOM) 崩溃：

- 独立进程，**只自己默默死掉**
- 主微内核**稳如泰山**
- 可捕获连接断开，输出统一错误日志，并尝试重启该插件

---

## 四、PluginManager 沙箱装载流程（P0 核心逻辑）

当 UpdaterAgent 下载好 `.jmp` 包后，PluginManager 的操作流程。**第一步签名验证**详见 [P0_TRUST_AND_HEARTBEAT_SPEC.md](./P0_TRUST_AND_HEARTBEAT_SPEC.md)。

```
# 1. 解包与验证 (防篡改) — 无此步，沙箱形同虚设
jmp_package = extract_and_verify_signature(downloaded_file, public_key)

# 2. 读取 Manifest
manifest = jmp_package.get_manifest()
plugin_type = manifest.get("execution_model")  # 'wasm' 或 'heavy_process'

if plugin_type == 'wasm':
    # 3a. WASM 严格沙箱加载
    memory_limit = manifest.resource_footprint.ram_estimate_mb
    allowed_domains = manifest.permissions.network
    allowed_dirs = manifest.permissions.filesystem

    sandbox = WasmEngine.create_instance(
        jmp_package.get_bytecode(),
        memory_limit=memory_limit,
        wasi_config=WasiConfig(allowed_dirs, allowed_domains)
    )
    Microkernel.register_route(manifest.plugin_id, sandbox)

elif plugin_type == 'heavy_process':
    # 3b. 独立进程加载
    socket_path = f"/tmp/jachin_uds_{manifest.plugin_id}.sock"

    process = launch_subprocess(jmp_package.get_entrypoint(), socket_path)
    client = GrpcClient(socket_path)
    Microkernel.register_route(manifest.plugin_id, client)
```

---

## 五、JMP Manifest 扩展（execution_model）

| 字段 | 类型 | 说明 |
|------|------|------|
| `execution_model` | string | `wasm`（默认）或 `heavy_process` |
| `permissions.network` | string[] | WASI 允许的域名列表，如 `["api.github.com"]` |
| `permissions.filesystem` | string[] | WASI 允许的目录列表，如 `["/tmp/plugin_a"]` |

**示例**：

```json
{
  "plugin_id": "com.jachin.weather",
  "execution_model": "wasm",
  "resource_footprint": { "ram_estimate_mb": 64, "gpu_required": false },
  "permissions": {
    "network": ["api.openweathermap.org"],
    "filesystem": []
  }
}
```

```json
{
  "plugin_id": "com.jachin.vits-tts",
  "execution_model": "heavy_process",
  "resource_footprint": { "ram_estimate_mb": 2048, "gpu_required": true },
  "entrypoint": "vits_server.py"
}
```

---

## 六、与现有实现的演进关系

| 当前实现 | 演进路径 |
|----------|----------|
| `core/plugin/sandbox.py`（Python AST + __builtins__） | 短期保留，作为 Python 插件的过渡方案；中长期迁移至 WASM 编译 |
| `core/plugin/validator.py` | 继续承担签名校验、manifest 解析；增加 `execution_model` 分支 |
| `core/updater/agent.py` | 不变，仍负责下载与校验；PluginManager 内部根据 manifest 选择装载路径 |

---

## 七、技术选型建议

| 组件 | 推荐 | 说明 |
|------|------|------|
| **WASM 运行时** | Wasmtime / Extism | 成熟、支持 WASI、多语言 SDK |
| **UDS 通信** | gRPC over UDS | 与 Jachin Link 协议一致，便于复用 |
| **进程隔离** | cgroups (Linux) | 配合 `heavy_process` 限制 CPU/内存 |

---

**相关文档**:
- [P0_TRUST_AND_HEARTBEAT_SPEC.md](./P0_TRUST_AND_HEARTBEAT_SPEC.md) - 信任链（Ed25519 签名、.jmp 结构、防降级）
- [JMP_SPEC.md](./JMP_SPEC.md) - 协议规范（含 execution_model、content_hashes）
- [PLUGIN_SECURITY_SANDBOX.md](./PLUGIN_SECURITY_SANDBOX.md) - 当前 Python 沙箱实现
- [MICROKERNEL_ECOSYSTEM_BATTLE_PLAN.md](./MICROKERNEL_ECOSYSTEM_BATTLE_PLAN.md) - P0 沙箱热插拔战役

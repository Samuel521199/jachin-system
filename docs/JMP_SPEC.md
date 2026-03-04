# JMP 规范 (Jachin Module Protocol)

**版本**: 2.0  
**状态**: 规范草案  
**定位**: Layer 1 与 Layer 2 之间的智慧分发标准  
**升级说明**: 详见 [MICROKERNEL_ECOSYSTEM_UPGRADE.md](./MICROKERNEL_ECOSYSTEM_UPGRADE.md)

---

## 1. 概述

JMP 是 Jachin 生态的「宪法」。Layer 2 从 Layer 1 下载的必须是一个**标准化的压缩包**，而非散乱代码。

**包格式**: `.jmp` 或 `.jsp`（ZIP 归档）

---

## 2. 包结构

一个符合 JMP 规范的 Skill/Persona 包必须包含以下文件：

```
{plugin_id}.jmp  (ZIP 归档)
├── manifest.json      # 必需：元数据、权限、content_hashes
├── signature.sig      # 必需：Layer 1 私钥对 manifest 的 Ed25519 签名 (Base64)
└── payload/            # 必需：实际代码与资源
    ├── main.wasm       # 或 main.py（过渡期）
    ├── prompt.txt      # 可选
    ├── requirements.txt # 可选
    └── assets/         # 可选
```

---

## 3. manifest.json

### 3.1 基础字段（兼容 1.0）

**必需字段**:

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` / `plugin_id` | string | 插件唯一标识（反向域名），如 `com.jachin.weather` |
| `version` | string | 语义化版本，如 `1.0.0` |
| `name` | string | 显示名称 |
| `entry` / `entrypoint` | string | 入口文件，默认 `main.py` |
| `permissions` | array | 所需权限，如 `["internet.access", "file.read"]` |

**可选字段**:

| 字段 | 类型 | 说明 |
|------|------|------|
| `description` | string | 描述 |
| `author` | string | 作者 |
| `capabilities` | array | 能力列表（供 Commander 路由） |
| `runtime` | object | 运行时配置（python_version, resources） |

### 3.2 JMP 2.0 扩展（环境感知与依赖树）

| 字段 | 类型 | 说明 |
|------|------|------|
| `jmp_version` | string | 协议版本，如 `"2.0"` |
| `type` | string | `skill` / `persona` / `memory` |
| `category` | string | 如 `IO_Enhancement`、`RAG` |
| `compatibility` | object | 见下表 |
| `resource_footprint` | object | 见下表 |

**compatibility 子字段**:

| 子字段 | 类型 | 说明 |
|--------|------|------|
| `os` | string[] | 支持环境：`linux` / `k8s` / `docker` / `windows` / `bare_metal` |
| `min_core_version` | string | 微内核最低版本要求（语义化版本） |
| `conflicts_with` | string[] | 互斥插件 ID 列表 |

**resource_footprint 子字段**:

| 子字段 | 类型 | 说明 |
|--------|------|------|
| `ram_estimate_mb` | number | 预估内存占用 (MB) |
| `gpu_required` | boolean | 是否必须 GPU |

### 3.3 混合沙箱扩展（execution_model）

> 详见 [HYBRID_SANDBOX_ARCHITECTURE.md](./HYBRID_SANDBOX_ARCHITECTURE.md)

| 字段 | 类型 | 说明 |
|------|------|------|
| `execution_model` | string | `wasm`（默认）、`heavy_process`（重型进程）或 `resource_mount`（静态资源挂载） |
| `permissions.network` | string[] | WASI 允许的域名，如 `["api.github.com"]` |
| `permissions.filesystem` | string[] | WASI 允许的目录，如 `["/tmp/plugin_a"]` |

**resource_mount 说明**：Persona 语音包、Memory 向量库等重型静态资产，无需作为进程运行。Layer 2 解压到只读目录，施加只读保护，通过环境变量 `JACHIN_VOL_{PLUGIN_ID}`（如 `JACHIN_VOL_COM_JACHIN_MEMORY_LEGAL`）挂载给 VAD/RAG 等 Skill 读取。生产环境可配合 `mount --bind -o ro` 实现内核级锁死。

**wasm 插件 ABI**：
- **简易版**：导出 `run() -> i32`，供 Jachin 沙箱直接调用。见 [jachin-plugin-sdk](../jachin-plugin-sdk/README.md)。
- **WASI 版**：stdin/stdout JSON 协议，Python (py2wasm) 插件。见 [jachin-plugin-sdk-python](../jachin-plugin-sdk-python/README.md)，`core/wasm_runner.run_plugin_wasi()`。
- **完整版**：导出 `memory` 和 `execute(ptr, len) -> out_len`，宿主将 JSON 写入 memory，调用后读取结果。

### 3.4 防篡改与防降级（信任链）

> 详见 [P0_TRUST_AND_HEARTBEAT_SPEC.md](./P0_TRUST_AND_HEARTBEAT_SPEC.md)

| 字段 | 类型 | 说明 |
|------|------|------|
| `content_hashes` | object | 各文件路径 → SHA-256 哈希，如 `{"payload/main.wasm": "sha256:abc123..."}` |
| `issued_at` | string | ISO 8601 时间戳，用于拒绝过期包 |

**manifest.json 2.0 示例**:

```json
{
  "jmp_version": "2.0",
  "plugin_id": "core-vad-audio",
  "type": "skill",
  "category": "IO_Enhancement",
  "compatibility": {
    "os": ["linux", "k8s"],
    "min_core_version": "1.2.0",
    "conflicts_with": ["legacy-audio-input"]
  },
  "resource_footprint": {
    "ram_estimate_mb": 256,
    "gpu_required": false
  },
  "entrypoint": "main.py",
  "permissions": ["internet.access"]
}
```

### 3.5 智能版税扩展

> 详见 [REVENUE_AND_ROYALTY_SPEC.md](./REVENUE_AND_ROYALTY_SPEC.md)、[ECOSYSTEM_AND_COMMERCIALIZATION_WHITEPAPER.md](./ECOSYSTEM_AND_COMMERCIALIZATION_WHITEPAPER.md)

**单插件版税**（JPP 脚手架 `plugin.json` 已支持）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `royalty.percentage` | number | 每次调用分润比例（0–100），如 30 表示 30% 给作者 |

**复合蓝图** (Workflow) 需声明依赖与版税：

| 字段 | 类型 | 说明 |
|------|------|------|
| `price_usd` | number | 蓝图售价（美元） |
| `royalty_dependencies` | array | 依赖的原子插件及各自版税 |

**权限示例** (需在 manifest 中声明):

- `internet.access` - 访问网络
- `file.read` / `file.write` - 文件读写
- `system.power` - 关机/重启
- `network.local` - 访问本地网卡

---

## 4. main.py

- 必须导出可被 WASM 沙箱加载的插件类（v5.0 已废弃 Ray）
- 继承 `BaseSkillActor`（或兼容基类）
- 实现 `execute(capability, params)` 或等价方法

---

## 5. prompt.txt

- 供 Commander 路由使用的意图识别提示词
- 格式：纯文本，描述该技能可处理的意图关键词
- 示例：`天气 查询 温度 预报`

---

## 6. 与现有架构的映射

| JMP 规范 | 现有实现 |
|----------|----------|
| manifest.json | common/schemas/manifest.py (PluginManifest) |
| .jmp 包 | .jsp 包（兼容） |
| main.py | skills_repo/_bundled/*/main.py |

---

**相关文档**:
- [jachin-plugin-sdk](../jachin-plugin-sdk/README.md) - JPP Rust 脚手架
- [jachin-plugin-sdk-python](../jachin-plugin-sdk-python/README.md) - JPP Python SDK（WASI stdin/stdout）
- [LAYER1_ARCHITECTURE_AND_DESIGN.md](./LAYER1_ARCHITECTURE_AND_DESIGN.md) | [HYBRID_SANDBOX_ARCHITECTURE.md](./HYBRID_SANDBOX_ARCHITECTURE.md) | [P0_TRUST_AND_HEARTBEAT_SPEC.md](./P0_TRUST_AND_HEARTBEAT_SPEC.md)

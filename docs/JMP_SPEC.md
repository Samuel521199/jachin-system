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
| `execution_model` | string | `wasm`（默认，轻量沙箱）或 `heavy_process`（重型独立进程） |
| `permissions.network` | string[] | WASI 允许的域名，如 `["api.github.com"]` |
| `permissions.filesystem` | string[] | WASI 允许的目录，如 `["/tmp/plugin_a"]` |

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

**权限示例** (需在 manifest 中声明):

- `internet.access` - 访问网络
- `file.read` / `file.write` - 文件读写
- `system.power` - 关机/重启
- `network.local` - 访问本地网卡

---

## 4. main.py

- 必须导出可被 Ray Actor 加载的类
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

**相关文档**: [LAYER1_ARCHITECTURE_AND_DESIGN.md](./LAYER1_ARCHITECTURE_AND_DESIGN.md) | [HYBRID_SANDBOX_ARCHITECTURE.md](./HYBRID_SANDBOX_ARCHITECTURE.md) | [P0_TRUST_AND_HEARTBEAT_SPEC.md](./P0_TRUST_AND_HEARTBEAT_SPEC.md) | [common/schemas/manifest.py](../common/schemas/manifest.py)

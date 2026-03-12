# 🔥 构建 Jachin Wasm 神经元，赚取你的第一笔加密/法币版税！

> **只要 5 分钟，写一段代码，编译成 Wasm 上传至 Jachin Nexus 商城。任何边缘智能体调用你的技能，你都将获得高额的分润！自带物理沙箱隔离，安全无忧。**

---

## 为什么是 JPP？

- **零门槛**：会写 Rust（或 C）就能上架，无需学习复杂框架
- **真金白银**：每次调用自动分润，`plugin.json` 里设置 `royalty.percentage`
- **物理沙箱**：Wasm 燃料熔断，死循环也伤不了宿主，用户放心
- **全球分发**：上传一次，所有 Jachin 边缘智能体都能拉取

---

## 3 步入门教程

### 步骤 1：编写逻辑

编辑 `src/lib.rs`，实现你的技能：

```rust
#[no_mangle]
pub extern "C" fn run() -> i32 {
    // 示例：智能灯泡控制，返回 1=开 0=关
    1
}
```

**更多示例**：见 `examples/data_wash.rs`（数据清洗）；天气查询、文本摘要……只要导出 `run` 函数即可。

### 步骤 2：一键编译

```bash
make build
```

输出：`dist/main.wasm`，可直接上传。

### 步骤 3：在控制台上传发布

1. 打开 [Jachin Nexus Console](http://localhost:3000/console)
2. 进入 The Forge 或 神经元商城
3. 上传 `dist/main.wasm` + `plugin.json`
4. 设置价格与分润比例
5. 发布！全球边缘智能体即刻可用

---

## 标准 ABI 接口

Jachin 边缘智能体通过以下方式调用你的插件：

```python
# Layer 2 core/wasm_runner.py
sandbox = JachinWasmSandbox()
result = sandbox.run_plugin("main.wasm", function_name="run", fuel_limit=100_000)
# result = 1 (你的 run 返回值)
```

**约定**：

| 导出函数 | 签名 | 说明 |
|----------|------|------|
| `run` | `() -> i32` | 主入口，无参，返回状态码 |
| `execute` | `(ptr, len) -> out_len` | JPP 2.0 扩展，JSON 输入输出（可选） |

---

## 插件清单 (plugin.json)

```json
{
  "id": "com.example.smart-bulb",
  "version": "1.0.0",
  "name": "智能灯泡控制器",
  "royalty": { "percentage": 30 }
}
```

- `id`：唯一标识，建议反向域名
- `royalty.percentage`：每次调用分润比例（0–100）

---

## 项目结构

```
jachin-plugin-sdk/
├── src/lib.rs      # 插件逻辑
├── plugin.json     # 清单与分润设置
├── Makefile        # make build
├── Cargo.toml      # Rust 配置
└── README.md
```

---

## 环境要求

- [Rust](https://rustup.rs/)（含 `wasm32-unknown-unknown` target）
- `make`（或手动执行 `cargo build --target wasm32-unknown-unknown --release`）

---

## 相关链接

- [Jachin Nexus](https://github.com/Samuel521199/jachin-system) - 主仓库
- [JMP 规范](../docs/JMP_SPEC.md) - 完整协议
- [plugins/](../plugins/) - 测试用 dummy.wasm

---

**现在就开始，用代码赚版税！** 🚀

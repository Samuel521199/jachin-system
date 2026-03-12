# WASM 技能插件目录

Processor 节点在此目录放置 `.wasm` 插件，作为**边缘智能体的 Wasm 技能武器**。  
蓝图下发后，Agent 通过 ReAct 循环**自主决定**何时调用这些技能，而非机械按序执行。详见 [docs/LAYER2_AGENT_LOOP_DESIGN.md](../docs/LAYER2_AGENT_LOOP_DESIGN.md)。

## 蓝图语义：Persona & Skillset

蓝图 = **岗位说明书**：人设 + 技能清单。Processor 节点即技能，由 Agent 按需调用。

## 测试用 dummy.wasm

```bash
python scripts/gen_dummy_wasm.py
```

会生成 `plugins/dummy.wasm`，导出 `run` 函数，返回 42。

## 自编译 WASM（Rust 示例）

```rust
// src/lib.rs
#[no_mangle]
pub extern "C" fn run() -> i32 {
    42
}
```

```bash
rustup target add wasm32-unknown-unknown
cargo build --target wasm32-unknown-unknown --release
# 输出: target/wasm32-unknown-unknown/release/xxx.wasm
```

## 自编译 WASM（C 示例，需 Emscripten）

```c
// hello.c
int run(void) { return 42; }
```

```bash
emcc -o hello.wasm hello.c -s EXPORTED_FUNCTIONS='["_run"]' -s STANDALONE_WASM
```

## 节点配置（Forge Processor）

在 Forge 画布的 Processor 节点 `data` 中可设置：

- `label`: 技能名称，供 Agent 在系统 Prompt 中识别
- `wasm_path`: 插件路径（相对或绝对）
- `fuel_limit`: 燃料上限，默认 100000

Agent 通过 `Action: run <label 或序号>` 调用对应技能。

## JPP 开发者脚手架

快速开发 Wasm 插件并上架商城：
- **Rust**：[jachin-plugin-sdk/](../jachin-plugin-sdk/README.md)
- **Python**：[jachin-plugin-sdk-python/](../jachin-plugin-sdk-python/README.md)（py2wasm + @jachin_plugin）

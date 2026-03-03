# WASM 插件目录

Processor 节点可在此目录放置 `.wasm` 插件，由守护进程在受限沙箱中执行。

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

## 节点配置

在 Forge 画布的 Processor 节点 `data` 中可设置：

- `wasm_path`: 插件路径（相对或绝对）
- `fuel_limit`: 燃料上限，默认 100000

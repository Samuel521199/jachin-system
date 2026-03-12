# jachin-system-pilot

全链路测试 Skill，用于验证 L1→L2→L3 分发与 Wasm 沙箱执行。

## 功能

- 返回 `{"status":"ok","message":"系统状态：正常","timestamp":"..."}`
- 声明权限 `system:read_basic`

## 编译

```bash
# 需要 Rust 工具链
rustup target add wasm32-unknown-unknown
make build
# 或
cargo build --target wasm32-unknown-unknown --release
cp target/wasm32-unknown-unknown/release/jachin_system_pilot.wasm main.wasm
```

## 发布

```bash
# 1. 打包
jachin pack

# 2. 发布（PRIVATE 影子上传 或 PUBLIC 完整包）
jachin publish
```

**完整流程说明**：见 [docs/PACK_AND_PUBLISH.md](docs/PACK_AND_PUBLISH.md)

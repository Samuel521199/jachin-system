//! Jachin Plugin Protocol (JPP) - 标准 ABI 示例
//!
//! 边缘智能体调用方式：
//!   sandbox.run_plugin("main.wasm", function_name="run", fuel_limit=100_000)
//!
//! 本示例：智能灯泡控制器
//! - 输入：通过 Agent 上下文传递（当前简化版为无参）
//! - 输出：i32 状态码 (0=关, 1=开) 或 JSON 字符串长度（JPP 2.0 扩展）
//!
//! 编译：make build 或 cargo build --target wasm32-unknown-unknown --release

/// JPP 标准入口：导出 `run` 供 Jachin 沙箱调用
#[no_mangle]
pub extern "C" fn run() -> i32 {
    // 示例逻辑：模拟控制智能灯泡
    // 实际场景中，Agent 可通过 execute(ptr, len) 传入 JSON，此处简化
    let state = 1i32; // 1 = 灯已开启
    state
}

/// 可选：导出 execute 用于 JSON 输入输出（JPP 2.0 完整 ABI）
/// 宿主将 JSON 写入 memory[ptr..ptr+len]，调用后从 memory 读取 out_len 字节
#[no_mangle]
pub extern "C" fn execute(ptr: i32, len: i32) -> i32 {
    // 简化实现：忽略输入，返回固定结果长度 0（表示无输出）
    // 完整实现需 wasm_bindgen 或手动 memory 操作
    let _ = (ptr, len);
    0
}

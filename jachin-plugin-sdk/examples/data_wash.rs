//! 示例：数据清洗插件
//!
//! 复制到 src/lib.rs 替换默认实现。
//! 当前简化版：模拟清洗，返回处理条数。
//! JPP 2.0 完整版可通过 execute(ptr, len) 接收 JSON 输入。

#[no_mangle]
pub extern "C" fn run() -> i32 {
    // 模拟：清洗了 42 条数据
    42
}

//! Jachin System Pilot - 全链路测试 Skill
//!
//! 返回「系统状态：正常」与时间戳，用于验证 L1→L2→L3 分发与 Wasm 沙箱执行。
//! ABI: execute(ptr, len) -> 输出长度，输入/输出均为 JSON，通过线性内存传递。
//!
//! 编译: cargo build --target wasm32-unknown-unknown --release
//! 输出: target/wasm32-unknown-unknown/release/jachin_system_pilot.wasm -> 复制为 main.wasm

use core::slice;
use core::str;

/// 输出缓冲区偏移（与宿主约定：0x8000 避免覆盖 wasm .data 段）
const OUTPUT_OFFSET: i32 = 0x8000;

/// JPP 标准入口：execute(ptr, len) -> 输出字节数
/// 宿主将 JSON 写入 memory[ptr..ptr+len]，调用后从 memory[0..return_value] 读取输出
#[no_mangle]
pub extern "C" fn execute(ptr: i32, len: i32) -> i32 {
    if ptr == 0 && len == 0 {
        return write_output(r#"{"status":"ok","message":"系统状态：正常","timestamp":"N/A (沙箱无时钟)"}"#);
    }

    let input = unsafe {
        let ptr_u8 = ptr as *const u8;
        let s = slice::from_raw_parts(ptr_u8, len as usize);
        str::from_utf8_unchecked(s)
    };

    // 解析输入（可选），生成输出
    let _ = input; // 可扩展：根据 input 调整行为
    let output = r#"{"status":"ok","message":"系统状态：正常","timestamp":"N/A (沙箱无时钟)"}"#;
    write_output(output)
}

/// 将 JSON 写入 OUTPUT_OFFSET，返回写入字节数（宿主从该偏移读取）
fn write_output(json: &str) -> i32 {
    let bytes = json.as_bytes();
    let len = bytes.len();
    if len == 0 {
        return 0;
    }

    unsafe {
        let dest = OUTPUT_OFFSET as *mut u8;
        let dest_slice = slice::from_raw_parts_mut(dest, len);
        dest_slice.copy_from_slice(bytes);
    }
    len as i32
}

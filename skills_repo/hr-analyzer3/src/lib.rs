//! HR 透析镜 3 - Jachin OS L3 Wasm Skill
//!
//! 流程：解析输入 → MCP 拉取简历 → 组装 Prompt → LLM 分析 → 附录倒置渲染
//! 回收站功能测试用。
//!
//! 编译: cargo build --target wasm32-unknown-unknown --release
//! 输出: target/wasm32-unknown-unknown/release/hr_analyzer3.wasm -> main.wasm

#![no_std]

extern crate alloc;

use alloc::format;
use core::alloc::{GlobalAlloc, Layout};
use core::slice;
use core::str;

extern "C" {
    fn __rust_alloc(size: usize, align: usize) -> *mut u8;
    fn __rust_dealloc(ptr: *mut u8, size: usize, align: usize);
}

struct HostAlloc;

unsafe impl GlobalAlloc for HostAlloc {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        __rust_alloc(layout.size(), layout.align())
    }
    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        __rust_dealloc(ptr, layout.size(), layout.align())
    }
}

#[global_allocator]
static A: HostAlloc = HostAlloc;

/// 输出缓冲区偏移（与宿主约定）
const OUTPUT_OFFSET: i32 = 0x8000;

/// 默认岗位（MVP 写死兜底）
const DEFAULT_ROLE: &str = "云边协同后端架构师";

/// HR 专家 System Prompt（终极 Prompt 车间）
const SYSTEM_PROMPT: &str = r#"你是一位拥有 20 年经验的硅谷顶尖 HR 专家与技术面试官。
你的任务是根据给定的【岗位要求】，对候选人的【原始简历】进行极其严苛、客观的评估。
【核心评价体系与权重（满分 5.0 分）】
1. T0 级别 (硬性实力，占比 50%)：工作经验深度、技术栈匹配度、过往项目带来的实际商业价值。
2. T1 级别 (软性素质，占比 30%)：团队协作、自驱力、反思迭代能力、抗压能力（需从其项目攻坚经历中推断）。
3. 通用条件 (占比 20%)：学历背景、工作年限匹配、跳槽频繁度带来的稳定风险、薪酬合理性。
【绝对禁令：反幻觉机制】
必须像法官一样只看证据！简历中未写明或无法合理推断的部分，绝不允许主观臆断或脑补，必须明确标注为「信息缺失，无法评估」，绝不能凭空赋分。
【输出格式要求】
必须输出格式规范的 Markdown 报告：
1. 💡 核心结论与录用建议
2. 📊 综合评分（满分 5.0，及各维度得分拆解）
3. ⚔️ 优势分析
4. ⚠️ 劣势与风险提示（含缺失信息预警）
"#;

extern "C" {
    fn mcp_read_file(path_ptr: i32, path_len: i32) -> i32;
    fn llm_complete(prompt_ptr: i32, prompt_len: i32) -> i32;
}

#[panic_handler]
fn panic(_: &core::panic::PanicInfo) -> ! {
    loop {}
}

/// 从 JSON 中提取 key 的 value 并反转义（\n \t \" \\），供 jd 等多行文本使用
/// 兼容 "key":"val" 与 "key": "val"（Python json.dumps 会加空格）
fn extract_json_str_unescaped(json: &str, key: &str) -> Option<alloc::string::String> {
    use alloc::string::String;
    use alloc::vec::Vec;
    let pat_no_space = format!(r#""{}":"#, key);
    let pat_with_space = format!(r#""{}": "#, key);
    let start = json.find(&pat_no_space)
        .or_else(|| json.find(&pat_with_space))?;
    let val_start = if json[start..].starts_with(&pat_no_space) {
        start + pat_no_space.len()
    } else {
        start + pat_with_space.len()
    };
    let tail = &json[val_start..];
    let bytes = tail.as_bytes();
    let mut end = 0usize;
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'\\' && i + 1 < bytes.len() {
            i += 2;
            continue;
        }
        if bytes[i] == b'"' {
            end = i;
            break;
        }
        i += 1;
    }
    let raw = &tail[..end];
    let mut out = Vec::new();
    let mut j = 0;
    let raw_bytes = raw.as_bytes();
    while j < raw_bytes.len() {
        if raw_bytes[j] == b'\\' && j + 1 < raw_bytes.len() {
            j += 1;
            match raw_bytes[j] {
                b'n' => out.push(b'\n'),
                b't' => out.push(b'\t'),
                b'r' => out.push(b'\r'),
                b'"' => out.push(b'"'),
                b'\\' => out.push(b'\\'),
                _ => { out.push(b'\\'); out.push(raw_bytes[j]); }
            }
            j += 1;
        } else {
            out.push(raw_bytes[j]);
            j += 1;
        }
    }
    String::from_utf8(out).ok()
}

/// 简单提取（不反转义），用于短字段；兼容 "key":"val" 与 "key": "val"
fn extract_json_str<'a>(json: &'a str, key: &str) -> Option<&'a str> {
    let pat_no_space = format!(r#""{}":"#, key);
    let pat_with_space = format!(r#""{}": "#, key);
    let start = json.find(&pat_no_space).or_else(|| json.find(&pat_with_space))?;
    let val_start = if json[start..].starts_with(&pat_no_space) {
        start + pat_no_space.len()
    } else {
        start + pat_with_space.len()
    };
    let mut i = val_start;
    let bytes = json.as_bytes();
    while i < bytes.len() {
        if bytes[i] == b'\\' && i + 1 < bytes.len() {
            i += 2;
            continue;
        }
        if bytes[i] == b'"' {
            return Some(&json[val_start..i]);
        }
        i += 1;
    }
    None
}

/// JPP 标准入口：execute(ptr, len) -> 输出字节数
#[no_mangle]
pub extern "C" fn execute(ptr: i32, len: i32) -> i32 {
    let input = if ptr != 0 && len > 0 {
        unsafe {
            let ptr_u8 = ptr as *const u8;
            let s = slice::from_raw_parts(ptr_u8, len as usize);
            str::from_utf8_unchecked(s)
        }
    } else {
        "{}"
    };

    // job_desc：优先 jd_template（L2 动态配置），其次 jd_path（MCP 读取），再次 jd/target_role
    let job_desc = if let Some(jd_template) = extract_json_str_unescaped(input, "jd_template") {
        jd_template
    } else if let Some(jd_path) = extract_json_str_unescaped(input, "jd_path") {
        let pb = jd_path.as_bytes();
        let len = unsafe { mcp_read_file(pb.as_ptr() as i32, pb.len() as i32) };
        if len > 0 {
            alloc::string::String::from_utf8_lossy(unsafe {
                slice::from_raw_parts(OUTPUT_OFFSET as *const u8, len as usize)
            }).into_owned()
        } else {
            alloc::string::String::from(DEFAULT_ROLE)
        }
    } else if let Some(jd) = extract_json_str_unescaped(input, "jd") {
        jd
    } else {
        extract_json_str(input, "target_role").map(|s| alloc::string::String::from(s))
            .unwrap_or_else(|| alloc::string::String::from(DEFAULT_ROLE))
    };

    // resume_path（含 Windows 反斜杠，需反转义）或 resume_filename
    let path = extract_json_str_unescaped(input, "resume_path")
        .or_else(|| extract_json_str(input, "resume_filename").map(|s| alloc::string::String::from(s)))
        .unwrap_or_else(|| alloc::string::String::from("zhangsan_resume.md"));

    // 任务 2：跨端呼叫 L2 MCP 拉取简历（必须复制到 owned String，否则 llm_complete 会覆盖 OUTPUT_OFFSET）
    let path_bytes = path.as_bytes();
    let read_len = unsafe { mcp_read_file(path_bytes.as_ptr() as i32, path_bytes.len() as i32) };
    if read_len <= 0 {
        return write_output("⚠️ 无法连接 L2 机房或读取文件失败");
    }
    let resume_text = alloc::string::String::from_utf8_lossy(unsafe {
        slice::from_raw_parts(OUTPUT_OFFSET as *const u8, read_len as usize)
    }).into_owned();

    // 任务 3+4：组装 Prompt 并呼叫 LLM
    let user_prompt = format!(
        "【岗位要求】\n{}\n\n【原始简历】\n{}\n\n请按 System Prompt 要求输出 Markdown 报告。",
        job_desc, resume_text
    );
    let full_prompt = format!("{}\n\n---\n\n{}", SYSTEM_PROMPT, user_prompt);

    let prompt_bytes = full_prompt.as_bytes();
    let prompt_ptr = prompt_bytes.as_ptr() as i32;
    let prompt_len = prompt_bytes.len() as i32;

    let report_len = unsafe { llm_complete(prompt_ptr, prompt_len) };
    if report_len <= 0 {
        return write_output("⚠️ LLM 调用失败");
    }

    let report = unsafe {
        let src = OUTPUT_OFFSET as *const u8;
        let s = slice::from_raw_parts(src, report_len as usize);
        str::from_utf8_unchecked(s)
    };

    // 任务 4：附录倒置
    let final_output = format!(
        "{}\n\n---\n### 🗄️ 附录：原始简历档案\n\n{}",
        report, resume_text
    );

    write_output(&final_output)
}

/// 将内容写入 OUTPUT_OFFSET，返回写入字节数
fn write_output(s: &str) -> i32 {
    let bytes = s.as_bytes();
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

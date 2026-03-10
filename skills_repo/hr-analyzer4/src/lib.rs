//! HR 透析镜 4 - Jachin OS L3 Wasm Skill
//!
//! 支持单文件与目录级批量分析：
//! - resume_filename/resume_path：单份简历
//! - target_dir：目录下所有 .md/.txt/.pdf 简历批量分析
//!
//! 批量模式输出 JSON 数组，Loader 解析后独立持久化。
//! 编译: cargo build --target wasm32-unknown-unknown --release

#![no_std]

extern crate alloc;

use alloc::format;
use alloc::string::String;
use alloc::vec::Vec;
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

const OUTPUT_OFFSET: i32 = 0x8000;
const DEFAULT_ROLE: &str = "云边协同后端架构师";

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

【关键指令】：请在整个 Markdown 报告的最末尾，根据该候选人的最终得分（满分 5 分，及格线为 3 分），严格选择以下两种格式之一输出总结（用于系统提取）：

如果得分 >= 3 分（推荐录用），请严格输出：
---SUMMARY_PASS---
姓名：[候选人姓名]
得分：[数字]
核心优势：[一句话概括卖点]
---SUMMARY_PASS---

如果得分 < 3 分（淘汰出局），请严格输出：
---SUMMARY_REJECT---
姓名：[候选人姓名]
得分：[数字]
淘汰原因：[一句话概括致命缺陷]
---SUMMARY_REJECT---
"#;

extern "C" {
    fn mcp_read_file(path_ptr: i32, path_len: i32) -> i32;
    fn mcp_list_directory(path_ptr: i32, path_len: i32) -> i32;
    fn llm_complete(prompt_ptr: i32, prompt_len: i32) -> i32;
    /// NDJSON 流式输出：宿主立即转发，避免大数组内存溢出
    fn host_stream_ndjson(ptr: i32, len: i32);
}

#[panic_handler]
fn panic(_: &core::panic::PanicInfo) -> ! {
    loop {}
}

fn extract_json_str_unescaped(json: &str, key: &str) -> Option<String> {
    let pat_no_space = format!(r#""{}":"#, key);
    let pat_with_space = format!(r#""{}": "#, key);
    let start = json.find(&pat_no_space).or_else(|| json.find(&pat_with_space))?;
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
                _ => {
                    out.push(b'\\');
                    out.push(raw_bytes[j]);
                }
            }
            j += 1;
        } else {
            out.push(raw_bytes[j]);
            j += 1;
        }
    }
    String::from_utf8(out).ok()
}

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

/// JSON 转义字符串
fn json_escape(s: &str) -> String {
    let mut out = String::new();
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            _ => out.push(c),
        }
    }
    out
}

/// 解析 list_directory 返回，提取 .md/.txt/.pdf 文件名
fn parse_list_directory_result(raw: &str, base_path: &str) -> Vec<String> {
    let mut files = Vec::new();
    let base = base_path.trim_end_matches('/').trim_end_matches('\\');
    let raw_trim = raw.trim();

    fn add_file(files: &mut Vec<String>, base: &str, s: &str) {
        if s.contains('\n') || s.len() > 200 {
            return;
        }
        let name = s.trim();
        if !name.ends_with(".md") && !name.ends_with(".txt") && !name.ends_with(".pdf") {
            return;
        }
        let full = if base.is_empty() || name.contains('/') || name.contains('\\') {
            String::from(name)
        } else {
            format!("{}/{}", base, name)
        };
        files.push(full);
    }

    if raw_trim.starts_with("[\"") || (raw_trim.starts_with('[') && raw_trim.contains(",\"")) {
        for part in raw_trim
            .trim_start_matches('[')
            .trim_end_matches(']')
            .split(',')
        {
            let s = part.trim().trim_matches('"');
            add_file(&mut files, base, s);
        }
        return files;
    }
    for line in raw.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let name = line
            .strip_prefix("[FILE]")
            .map(|s| s.trim())
            .unwrap_or(line);
        add_file(&mut files, base, name);
    }
    if files.is_empty() && (raw.contains(".md") || raw.contains(".txt") || raw.contains(".pdf")) {
        for part in raw.split_whitespace() {
            add_file(&mut files, base, part);
        }
    }
    files
}

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

#[no_mangle]
pub extern "C" fn execute(ptr: i32, len: i32) -> i32 {
    let raw_input = if ptr != 0 && len > 0 {
        unsafe {
            let ptr_u8 = ptr as *const u8;
            let s = slice::from_raw_parts(ptr_u8, len as usize);
            str::from_utf8_unchecked(s)
        }
    } else {
        "{}"
    };

    // Loader：首行为 path1|||path2 或 JD_START:::content:::JD_END，可能第二行为 JD 或 JSON
    let (files_first_line, input, jd_first_line) = if let Some(nl) = raw_input.find('\n') {
        let first = raw_input[..nl].trim();
        let after_first = raw_input[nl + 1..].trim();
        let (files, jd_line, rest) = if first.starts_with("JD_START:::") && first.contains(":::JD_END") {
            let jd = if let Some(end) = first.find(":::JD_END") {
                let raw = first[10..end].trim();
                Some(raw.replace('\r', "\n"))
            } else {
                None
            };
            (None, jd, after_first)
        } else if first.contains("|||") && !first.starts_with('{') {
            // 批量模式：首行 paths，第二行可能为 JD_START 或直接 JSON
            let (jd, rest) = if let Some(nl2) = after_first.find('\n') {
                let second = after_first[..nl2].trim();
                let after_second = after_first[nl2 + 1..].trim();
                if second.starts_with("JD_START:::") && second.contains(":::JD_END") {
                    let jd = if let Some(end) = second.find(":::JD_END") {
                        Some(second[10..end].trim().replace('\r', "\n"))
                    } else {
                        None
                    };
                    (jd, after_second)
                } else {
                    (None, after_first)
                }
            } else {
                (None, after_first)
            };
            (Some(String::from(first)), jd, rest)
        } else {
            (None, None, after_first)
        };
        (files, if rest.is_empty() { "{}" } else { rest }, jd_line)
    } else {
        (None, raw_input, None)
    };

    // 优先 jd_first_line（Loader 首行 JD_START:::），其次 jd_template（JSON），避免 JSON 转义导致提取失败
    let jd_template_value = jd_first_line
        .filter(|s| !s.trim().is_empty())
        .or_else(|| {
            extract_json_str_unescaped(input, "jd_template")
                .filter(|s| !s.trim().is_empty())
                .or_else(|| extract_json_str(input, "jd_template").map(|s| String::from(s)))
        });
    let job_desc = if let Some(jd) = jd_template_value {
        jd
    } else if let Some(jd_path) = extract_json_str_unescaped(input, "jd_path") {
        let pb = jd_path.as_bytes();
        let n = unsafe { mcp_read_file(pb.as_ptr() as i32, pb.len() as i32) };
        if n > 0 {
            let s = String::from(
                String::from_utf8_lossy(unsafe {
                    slice::from_raw_parts(OUTPUT_OFFSET as *const u8, n as usize)
                })
                .trim(),
            );
            if !s.is_empty() {
                s
            } else {
                String::from(DEFAULT_ROLE)
            }
        } else {
            String::from(DEFAULT_ROLE)
        }
    } else if let Some(jd) = extract_json_str_unescaped(input, "jd") {
        jd
    } else {
        extract_json_str(input, "target_role")
            .map(String::from)
            .unwrap_or_else(|| String::from(DEFAULT_ROLE))
    };

    // 空字符串视为未提供：使用 DEFAULT_ROLE，避免 LLM 提示「未提供【岗位要求】」
    let job_desc = if job_desc.trim().is_empty() {
        String::from(DEFAULT_ROLE)
    } else {
        job_desc
    };

    // 调试：流式输出 jd_len、extract 原始结果，便于排查岗位 JD 传入问题
    let jd_preview: String = job_desc.chars().take(60).collect();
    let extracted_raw = extract_json_str_unescaped(input, "jd_template");
    let extracted_preview: String = extracted_raw
        .as_ref()
        .map(|s| s.chars().take(80).collect())
        .unwrap_or_else(|| String::from("(extract returned None)"));
    let debug_ndjson = format!(
        r#"{{"status":"debug","jd_len":{},"jd_preview":"{}","extracted_preview":"{}"}}"#,
        job_desc.len(),
        json_escape(&jd_preview),
        json_escape(&extracted_preview),
    );
    unsafe { host_stream_ndjson(debug_ndjson.as_ptr() as i32, debug_ndjson.len() as i32) };

    let target_dir = extract_json_str_unescaped(input, "target_dir")
        .or_else(|| extract_json_str(input, "target_dir").map(String::from));
    let resume_path = extract_json_str_unescaped(input, "resume_path");
    let resume_filename = extract_json_str(input, "resume_filename").map(String::from);
    // 防重黑名单：L3 调度器传入，跳过已分析简历以节省 Token
    let skip_files_raw = extract_json_str_unescaped(input, "skip_files")
        .or_else(|| extract_json_str(input, "skip_files").map(String::from));
    let skip_files: alloc::vec::Vec<String> = skip_files_raw
        .as_ref()
        .map(|s| {
            s.split("|||")
                .map(|x| x.trim())
                .filter(|x| !x.is_empty())
                .map(String::from)
                .collect()
        })
        .unwrap_or_else(alloc::vec::Vec::new);
    // 参考日期：中国时区，供判断应届生、工作经历时间
    let reference_date = extract_json_str_unescaped(input, "reference_date")
        .or_else(|| extract_json_str(input, "reference_date").map(String::from));
    // 动态配置：L3 UI 注入
    let focus_keywords = extract_json_str_unescaped(input, "focus_keywords")
        .or_else(|| extract_json_str(input, "focus_keywords").map(String::from));
    let strictness_raw = extract_json_str(input, "strictness").unwrap_or("standard").trim();

    let files: Vec<String> = if let Some(ref s) = files_first_line {
        let list: Vec<String> = s
            .split("|||")
            .map(|x: &str| x.trim())
            .filter(|x: &&str| !x.is_empty())
            .map(String::from)
            .collect();
        if !list.is_empty() {
            list
        } else if let Some(ref dir) = target_dir {
            let dir_bytes = dir.as_bytes();
            let n = unsafe { mcp_list_directory(dir_bytes.as_ptr() as i32, dir_bytes.len() as i32) };
            if n <= 0 {
                return write_output("⚠️ 无法获取目录列表，请确认 target_dir 路径正确且 MCP 已挂载");
            }
            let raw = String::from_utf8_lossy(unsafe {
                slice::from_raw_parts(OUTPUT_OFFSET as *const u8, n as usize)
            });
            let list = parse_list_directory_result(&raw, dir);
            if list.is_empty() {
                return write_output("⚠️ 目录下未找到 .md、.txt 或 .pdf 简历文件");
            }
            list
        } else {
            alloc::vec![]
        }
    } else if let Some(ref dir) = target_dir {
        let dir_bytes = dir.as_bytes();
        let n = unsafe { mcp_list_directory(dir_bytes.as_ptr() as i32, dir_bytes.len() as i32) };
        if n <= 0 {
            return write_output("⚠️ 无法获取目录列表，请确认 target_dir 路径正确且 MCP 已挂载");
        }
        let raw = String::from_utf8_lossy(unsafe {
            slice::from_raw_parts(OUTPUT_OFFSET as *const u8, n as usize)
        });
        let list = parse_list_directory_result(&raw, dir);
        if list.is_empty() {
            return write_output("⚠️ 目录下未找到 .md、.txt 或 .pdf 简历文件");
        }
        list
    } else if let Some(ref path) = resume_path {
        alloc::vec![path.clone()]
    } else {
        let fn_ = resume_filename
            .unwrap_or_else(|| String::from("zhangsan_resume.md"));
        let base = extract_json_str_unescaped(input, "resume_input_dir")
            .or_else(|| extract_json_str(input, "resume_input_dir").map(String::from))
            .unwrap_or_else(|| String::from("data/hr_resumes"));
        alloc::vec![format!("{}/{}", base.trim_end_matches('/'), fn_)]
    };

    let total = files.len();
    let mut current: usize = 0;

    for file_path in &files {
        let path_norm = file_path.replace('\\', "/");
        let path_trimmed = path_norm.trim_end_matches('/');
        let filename: String = path_trimmed
            .rsplit('/')
            .next()
            .filter(|s| !s.is_empty())
            .map(String::from)
            .unwrap_or_else(|| format!("resume_{}", current));
        // 防重：若 stem 在 skip_files 黑名单中，跳过（不读取、不调用 LLM）
        let stem = if let Some(dot) = filename.rfind('.') {
            String::from(&filename[..dot])
        } else {
            filename.clone()
        };
        if skip_files.iter().any(|s| s == &stem) {
            continue;
        }
        current += 1;

        let path_bytes = file_path.as_bytes();
        let read_len = unsafe { mcp_read_file(path_bytes.as_ptr() as i32, path_bytes.len() as i32) };

        if read_len <= 0 {
            let ndjson = format!(
                r#"{{"status":"progress","filename":"{}","current":{},"total":{},"report_content":"⚠️ 读取简历失败","resume_text":""}}"#,
                json_escape(&filename),
                current,
                total,
            );
            let b = ndjson.as_bytes();
            unsafe { host_stream_ndjson(b.as_ptr() as i32, b.len() as i32) };
            continue;
        }
        let resume_text = String::from_utf8_lossy(unsafe {
            slice::from_raw_parts(OUTPUT_OFFSET as *const u8, read_len as usize)
        })
        .into_owned();

        let date_hint = reference_date
            .as_ref()
            .filter(|s| !s.trim().is_empty())
            .map(|d| format!("\n\n【参考日期】当前为中国北京时间 {}，请据此判断应届生毕业年份、工作经历起止时间等，避免将未来日期误判。", d.trim()))
            .unwrap_or_else(|| String::new());
        let user_prompt = format!(
            "【岗位要求】\n{}\n\n【原始简历】\n{}{}\n\n请按 System Prompt 要求输出 Markdown 报告。",
            job_desc, resume_text, date_hint
        );

        // 动态 Prompt 注入：focus_keywords + strictness（在每份简历循环内生效）
        let mut system_extra = String::new();
        if let Some(ref kw) = focus_keywords {
            let t = kw.trim();
            if !t.is_empty() {
                system_extra.push_str("\n\n[⚠️ 核心考察指令]：请务必重点考察以下维度的契合度：");
                system_extra.push_str(t);
                system_extra.push_str("。有则加分，无则在劣势中指出。");
            }
        }
        if strictness_raw.eq_ignore_ascii_case("strict") {
            system_extra.push_str("\n\n[评判标准]：请以极其挑剔、毒辣的眼光审视该简历。不要放过任何水分，宁缺毋滥。");
        } else if strictness_raw.eq_ignore_ascii_case("lenient") {
            system_extra.push_str("\n\n[评判标准]：请以伯乐眼光审视，重点挖掘潜力和亮点，给予积极评价。");
        }
        let full_prompt = format!("{}{}\n\n---\n\n{}", SYSTEM_PROMPT, system_extra, user_prompt);
        // 调试：流式输出 user_prompt 前 300 字符，验证【岗位要求】已正确传入模型
        let prompt_preview: String = full_prompt.chars().skip(full_prompt.find("【岗位要求】").unwrap_or(0)).take(300).collect();
        let debug_prompt = format!(
            r#"{{"status":"debug_prompt","job_desc_len":{},"prompt_preview":"{}"}}"#,
            job_desc.len(),
            json_escape(&prompt_preview),
        );
        unsafe { host_stream_ndjson(debug_prompt.as_ptr() as i32, debug_prompt.len() as i32) };
        let prompt_bytes = full_prompt.as_bytes();
        let prompt_ptr = prompt_bytes.as_ptr() as i32;
        let prompt_len = prompt_bytes.len() as i32;
        let report_len = unsafe { llm_complete(prompt_ptr, prompt_len) };

        let report = if report_len > 0 {
            String::from_utf8_lossy(unsafe {
                slice::from_raw_parts(OUTPUT_OFFSET as *const u8, report_len as usize)
            })
            .into_owned()
        } else {
            String::from("⚠️ LLM 调用失败")
        };

        let final_report = format!(
            "{}\n\n---\n### 🗄️ 附录：原始简历档案\n\n{}",
            report, resume_text
        );

        let ndjson = format!(
            r#"{{"status":"progress","filename":"{}","current":{},"total":{},"report_content":"{}","resume_text":"{}"}}"#,
            json_escape(&filename),
            current,
            total,
            json_escape(&final_report),
            json_escape(&resume_text),
        );
        let b = ndjson.as_bytes();
        unsafe { host_stream_ndjson(b.as_ptr() as i32, b.len() as i32) };
    }

    let done_json = r#"{"status":"done"}"#;
    let done_bytes = done_json.as_bytes();
    unsafe { host_stream_ndjson(done_bytes.as_ptr() as i32, done_bytes.len() as i32) };

    write_output(&format!(r#"{{"status":"done","total":{}}}"#, total))
}

//! 独立进程：下载新版本 → minisign 校验 → 检查用户数据目录 → 等待主进程退出 →
//! 结束仍占用主程序路径的残留进程 → 若为 NSIS/MSI 则从暂存路径启动安装包，否则覆盖便携 exe 并启动。
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

#[path = "../updater_common.rs"]
mod updater_common;

use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use reqwest::blocking::Client;
use sysinfo::{Pid, System};

use updater_common::{
    assert_signed_artifact_version_matches_release, embedded_updater_pubkey_b64,
    resolve_updater_pubkey_for_job,
    hot_update_debug_log_dir, hot_update_payload_sha256_hex, parse_minisign_trusted_file_field,
    signature_wire_debug_summary, sniff_file_looks_like_windows_nsis_installer_package,
    user_data_ready_for_hot_update, verify_minisign_payload_traced, HotUpdateJob,
    HotUpdatePrepareResult,
};

const LOG_FILE: &str = "hot_update_debug.log";
const HELPER_PKG_VERSION: &str = env!("CARGO_PKG_VERSION");

fn now_ts_ms() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0)
}

fn log_line(msg: &str) {
    let sanitized = msg.replace('\r', " ").replace('\n', " | ");
    let pid = std::process::id();
    let line = format!(
        "[{}ms pid={} proc=helper v={}] [updater_helper] {}\n",
        now_ts_ms(),
        pid,
        HELPER_PKG_VERSION,
        sanitized
    );
    eprintln!("[jachin-updater-helper] {}", sanitized);
    let dir = hot_update_debug_log_dir();
    if let Err(e) = fs::create_dir_all(&dir) {
        eprintln!("mkdir debug dir {}: {e}", dir.display());
        return;
    }
    let path = dir.join(LOG_FILE);
    match OpenOptions::new().create(true).append(true).open(&path) {
        Ok(mut f) => {
            if let Err(e) = f.write_all(line.as_bytes()) {
                eprintln!("write {}: {e}", path.display());
            }
        }
        Err(e) => eprintln!("open {}: {e}", path.display()),
    }
}

fn log_session_banner(job_path: &Path) {
    let exe = std::env::current_exe()
        .map(|p| p.display().to_string())
        .unwrap_or_else(|_| "(unknown)".into());
    let dir = hot_update_debug_log_dir();
    log_line(&format!(
        "======== session_start helper={HELPER_PKG_VERSION} pid={} exe={} job={} debug_dir={} ========",
        std::process::id(),
        exe,
        job_path.display(),
        dir.display()
    ));
}

/// 日志用：不打印 query（可能含预签名 token），只保留 scheme/host 与路径长度。
fn download_url_log_safe(url: &str) -> String {
    let total = url.len();
    let (scheme, rest) = if let Some(r) = url.strip_prefix("https://") {
        ("https", r)
    } else if let Some(r) = url.strip_prefix("http://") {
        ("http", r)
    } else {
        return format!("non_http total_len={total}");
    };
    let host = rest
        .split(|c| c == '/' || c == '?')
        .next()
        .unwrap_or("?");
    let path_and_more_len = rest.len().saturating_sub(host.len());
    format!(
        "scheme={scheme} host={host} path_query_tail_len={path_and_more_len} total_len={total}"
    )
}

fn log_job_loaded(job: &HotUpdateJob, job_path: &Path) {
    log_line(&format!(
        "job_loaded path={} prepare_only={} apply_only={} parent_pid={} new_version={} target_exe={}",
        job_path.display(),
        job.prepare_only,
        job.apply_only,
        job.parent_pid,
        job.new_version,
        job.target_exe.display()
    ));
    if !job.download_url.is_empty() {
        log_line(&format!("job download_url {}", download_url_log_safe(&job.download_url)));
    }
    if !job.signature.is_empty() {
        log_line(&format!(
            "job signature {}",
            signature_wire_debug_summary(&job.signature)
        ));
    }
    if let Some(ref p) = job.prepare_result_path {
        log_line(&format!("job prepare_result_path={}", p.display()));
    }
    if let Some(ref p) = job.staged_new_exe {
        log_line(&format!("job staged_new_exe={}", p.display()));
    }
    let wire = resolve_updater_pubkey_for_job(job);
    log_line(&format!(
        "verify_pubkey_source={} {}",
        if job.updater_pubkey_wire.as_ref().map(|s| !s.trim().is_empty()).unwrap_or(false) {
            "job_json"
        } else {
            "helper_embedded"
        },
        signature_wire_debug_summary(wire)
    ));
    log_line(&format!(
        "helper_embedded_pubkey_only {}",
        signature_wire_debug_summary(embedded_updater_pubkey_b64())
    ));
}

fn parse_args() -> Result<PathBuf, String> {
    let mut it = std::env::args().skip(1);
    match (it.next(), it.next()) {
        (Some(flag), Some(path)) if flag == "--job" => Ok(PathBuf::from(path)),
        _ => Err("用法: jachin-updater-helper --job <path-to-json>".into()),
    }
}

fn wait_parent_exit(parent_pid: u32) -> Result<(), String> {
    let pid = Pid::from_u32(parent_pid);
    for i in 0..900 {
        let mut s = System::new();
        s.refresh_processes();
        if s.process(pid).is_none() {
            log_line(&format!("主进程 {} 已退出 (轮询 {}×200ms)", parent_pid, i));
            return Ok(());
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    Err("等待主进程退出超时（约 3 分钟）".into())
}

/// 与任务管理器里「映像路径」比较用：小写、统一反斜杠、去掉 `\\?\` 前缀。
fn exe_path_compare_key(path: &Path) -> String {
    let raw = path.to_string_lossy();
    let s = raw.trim_start_matches(r"\\?\");
    s.replace('/', r"\").to_lowercase()
}

/// 仅根据扩展名与文件名判断（快速路径）。
fn looks_like_windows_installer_filename(path: &Path) -> bool {
    let ext = path
        .extension()
        .and_then(|e| e.to_str())
        .unwrap_or("")
        .to_lowercase();
    if ext == "msi" {
        return true;
    }
    if ext != "exe" {
        return false;
    }
    let name = path
        .file_name()
        .map(|n| n.to_string_lossy().to_lowercase())
        .unwrap_or_default();
    name.contains("-setup")
        || name.contains("_setup")
        || name.ends_with("setup.exe")
}

/// NSIS / MSI 安装包；含内容嗅探，避免「主程序名却是安装桩」时误判为便携 exe。
fn looks_like_windows_installer_artifact(path: &Path) -> bool {
    if looks_like_windows_installer_filename(path) {
        return true;
    }
    sniff_file_looks_like_windows_nsis_installer_package(path).unwrap_or(false)
}

#[cfg(windows)]
fn terminate_processes_running_exe(target_exe: &Path) {
    let want = exe_path_compare_key(target_exe);
    if want.is_empty() {
        return;
    }
    let self_pid = std::process::id();
    let mut sys = System::new();
    sys.refresh_processes();
    for (pid, proc_) in sys.processes() {
        let pid_u = pid.as_u32();
        if pid_u == self_pid {
            continue;
        }
        let Some(exe) = proc_.exe() else {
            continue;
        };
        if exe.as_os_str().is_empty() {
            continue;
        }
        let got = exe_path_compare_key(exe);
        if got == want {
            log_line(&format!(
                "terminate_processes_running_exe pid={pid_u} exe={}",
                exe.display()
            ));
            let _ = proc_.kill();
        }
    }
}

/// 主进程已退出后，仍可能有同名 exe 的残留进程（如 WebView2 未及时释放句柄），短轮询结束之。
#[cfg(windows)]
fn ensure_no_straggler_process_for_target_exe(target_exe: &Path) {
    for round in 0..5 {
        terminate_processes_running_exe(target_exe);
        std::thread::sleep(Duration::from_millis(400));
        let mut sys = System::new();
        sys.refresh_processes();
        let want = exe_path_compare_key(target_exe);
        let self_pid = std::process::id();
        let mut any = false;
        for (pid, proc_) in sys.processes() {
            if pid.as_u32() == self_pid {
                continue;
            }
            let Some(exe) = proc_.exe() else {
                continue;
            };
            if exe.as_os_str().is_empty() {
                continue;
            }
            if exe_path_compare_key(exe) == want {
                any = true;
                break;
            }
        }
        if !any {
            if round > 0 {
                log_line(&format!(
                    "ensure_no_straggler_process_for_target_exe 第 {} 轮后已无占用 {}",
                    round + 1,
                    target_exe.display()
                ));
            }
            return;
        }
    }
    log_line("ensure_no_straggler_process_for_target_exe 仍检测到同名映像进程，安装可能失败；已尽力结束。");
}

/// 安装 NSIS 前再清一轮：按「进程映像文件名」结束仍存活的子树（覆盖 WebView2 等仍占句柄的情况）。
#[cfg(windows)]
fn taskkill_process_tree_by_image_name(image: &str) {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    match std::process::Command::new("taskkill")
        .args(["/F", "/IM", image, "/T"])
        .creation_flags(CREATE_NO_WINDOW)
        .output()
    {
        Ok(o) => {
            let code = o.status.code();
            let err = String::from_utf8_lossy(&o.stderr);
            let err_short: String = err.trim().chars().take(240).collect();
            log_line(&format!(
                "taskkill /IM {image} /F /T code={code:?} stderr={err_short}"
            ));
        }
        Err(e) => log_line(&format!("taskkill spawn failed: {e}")),
    }
}

#[cfg(windows)]
fn launch_windows_installer_detached(installer: &Path) -> Result<(), String> {
    use std::os::windows::process::CommandExt;
    const DETACHED: u32 = 0x0000_0008;
    std::process::Command::new(installer)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .creation_flags(DETACHED)
        .spawn()
        .map_err(|e| format!("启动安装包失败: {e}"))?;
    log_line(&format!(
        "已启动安装包（独立进程，未覆盖主 exe）: {}",
        installer.display()
    ));
    Ok(())
}

#[cfg(windows)]
fn replace_exe_and_launch(target: &Path, new_bytes_path: &Path) -> Result<(), String> {
    use std::os::windows::process::CommandExt;
    const DETACHED: u32 = 0x0000_0008;

    if sniff_file_looks_like_windows_nsis_installer_package(new_bytes_path).unwrap_or(false) {
        return Err(
            "暂存文件内容判定为 NSIS 安装包，禁止覆盖主程序 exe（否则快捷方式会启动安装向导）。\
             请使用安装包热更新路径（助手应直接启动 setup.exe）。"
                .into(),
        );
    }

    let bak = target.with_extension("exe.bak-hot");
    let _ = fs::remove_file(&bak);
    if target.exists() {
        fs::rename(target, &bak).map_err(|e| format!("备份旧 exe 失败: {e}"))?;
    }
    fs::copy(new_bytes_path, target).map_err(|e| format!("写入新 exe 失败: {e}"))?;

    std::process::Command::new(target)
        .creation_flags(DETACHED)
        .spawn()
        .map_err(|e| format!("启动新版本失败: {e}"))?;

    log_line(&format!("已启动新版本: {}", target.display()));
    Ok(())
}

#[cfg(not(windows))]
fn replace_exe_and_launch(_target: &Path, _new_bytes_path: &Path) -> Result<(), String> {
    Err("当前仅实现 Windows 便携 exe 原地替换".into())
}

fn write_prepare_result(path: &Path, result: &HotUpdatePrepareResult) -> Result<(), String> {
    let dir = path.parent().ok_or("result 路径无父目录")?;
    fs::create_dir_all(dir).map_err(|e| format!("创建结果目录: {e}"))?;
    let partial = path.with_extension("json.partial");
    let json = serde_json::to_string_pretty(result).map_err(|e| format!("序列化结果: {e}"))?;
    fs::write(&partial, json.as_bytes()).map_err(|e| format!("写入结果临时文件: {e}"))?;
    fs::rename(&partial, path).map_err(|e| format!("提交结果文件: {e}"))?;
    Ok(())
}

fn fail_prepare(path: &Path, new_version: &str, msg: String) -> Result<(), String> {
    let r = HotUpdatePrepareResult {
        ok: false,
        staged_new_exe: None,
        new_version: new_version.to_string(),
        error: Some(msg.clone()),
    };
    if let Err(e) = write_prepare_result(path, &r) {
        log_line(&format!(
            "write_prepare_result FAILED (fail_prepare): {e} path={}",
            path.display()
        ));
    }
    Err(msg)
}

fn download_verify_to_tmp(job: &HotUpdateJob, tmp: &Path) -> Result<(), String> {
    if tmp.exists() {
        let _ = fs::remove_file(tmp);
    }
    log_line("downloading...");
    let client = Client::builder()
        .user_agent("jachin-updater-helper/1")
        .timeout(Duration::from_secs(900))
        .danger_accept_invalid_certs(false)
        .build()
        .map_err(|e| format!("http client: {e}"))?;

    let resp = client
        .get(&job.download_url)
        .send()
        .map_err(|e| format!("下载请求失败: {e}"))?
        .error_for_status()
        .map_err(|e| format!("下载 HTTP 错误: {e}"))?;

    let bytes = resp
        .bytes()
        .map_err(|e| format!("读取响应体: {e}"))?
        .to_vec();
    fs::write(tmp, &bytes).map_err(|e| format!("写入临时文件: {e}"))?;
    log_line(&format!(
        "downloaded bytes={} sha256={} verifying minisign (traced)...",
        bytes.len(),
        hot_update_payload_sha256_hex(&bytes)
    ));

    verify_minisign_payload_traced(
        &bytes,
        &job.signature,
        resolve_updater_pubkey_for_job(job),
        |step| log_line(&format!("minisign {step}")),
    )?;

    assert_signed_artifact_version_matches_release(&job.signature, &job.new_version)?;
    if let Ok(Some(ref fname)) = parse_minisign_trusted_file_field(&job.signature) {
        log_line(&format!("minisign trusted file 字段与 new_version={} 一致: {fname}", job.new_version));
    }

    log_line("minisign verify_minisign_payload_traced finished OK");
    Ok(())
}

fn run_prepare(job_path: &Path, job: &HotUpdateJob) -> Result<(), String> {
    let result_path = job
        .prepare_result_path
        .as_ref()
        .ok_or_else(|| "prepare_only 缺少 prepare_result_path".to_string())?;

    log_line(&format!(
        "prepare_only parent_pid={} target={} version={}",
        job.parent_pid,
        job.target_exe.display(),
        job.new_version
    ));

    let tmp_initial = std::env::temp_dir().join(format!(
        "jachin-desktop-{}-new.exe",
        job.new_version.replace(['/', '\\'], "_")
    ));

    let tmp = match (|| -> Result<PathBuf, String> {
        download_verify_to_tmp(job, &tmp_initial)?;
        let mut staged = tmp_initial.clone();
        if sniff_file_looks_like_windows_nsis_installer_package(&tmp_initial).unwrap_or(false) {
            let low = tmp_initial
                .file_name()
                .map(|n| n.to_string_lossy().to_lowercase())
                .unwrap_or_default();
            if !low.contains("setup") {
                let renamed = std::env::temp_dir().join(format!(
                    "jachin-desktop-{}-setup-staged.exe",
                    job.new_version.replace(['/', '\\'], "_")
                ));
                if renamed.exists() {
                    let _ = fs::remove_file(&renamed);
                }
                fs::rename(&tmp_initial, &renamed).map_err(|e| format!("NSIS 暂存重命名失败: {e}"))?;
                log_line(&format!(
                    "prepare: 判定为 NSIS，已将暂存重命名为 {}",
                    renamed.display()
                ));
                staged = renamed;
            }
        }
        log_line("minisign OK, checking user data...");
        user_data_ready_for_hot_update()?;
        Ok(staged)
    })() {
        Ok(p) => p,
        Err(e) => return fail_prepare(result_path, &job.new_version, e),
    };

    let ok_result = HotUpdatePrepareResult {
        ok: true,
        staged_new_exe: Some(tmp.to_string_lossy().to_string()),
        new_version: job.new_version.clone(),
        error: None,
    };
    if let Err(e) = write_prepare_result(result_path, &ok_result) {
        log_line(&format!(
            "write_prepare_result FAILED ok=true err={e} path={}",
            result_path.display()
        ));
        return fail_prepare(
            result_path,
            &job.new_version,
            format!("准备成功但写入结果文件失败（前端会一直等待）: {e}"),
        );
    }

    let staged_sha = fs::read(&tmp)
        .map(|b| hot_update_payload_sha256_hex(&b))
        .unwrap_or_else(|e| format!("(staged_read_err:{e})"));
    log_line(&format!(
        "prepare OK staged={} staged_sha256={}",
        tmp.display(),
        staged_sha
    ));
    let _ = fs::remove_file(job_path);
    Ok(())
}

fn run_apply(job_path: &Path, job: &HotUpdateJob) -> Result<(), String> {
    let staged = job
        .staged_new_exe
        .as_ref()
        .ok_or_else(|| "apply_only 缺少 staged_new_exe".to_string())?;

    log_line(&format!(
        "apply_only parent_pid={} target={} staged={} version={}",
        job.parent_pid,
        job.target_exe.display(),
        staged.display(),
        job.new_version
    ));

    if !staged.is_file() {
        return Err(format!("暂存安装包不存在: {}", staged.display()));
    }

    user_data_ready_for_hot_update()?;

    log_line("waiting for parent process to exit（主程序应先自行退出；随后清理仍占用安装目录的残留进程）...");
    wait_parent_exit(job.parent_pid)?;

    #[cfg(windows)]
    {
        std::thread::sleep(Duration::from_millis(500));
        ensure_no_straggler_process_for_target_exe(&job.target_exe);
        std::thread::sleep(Duration::from_millis(1200));

        let main_image = job
            .target_exe
            .file_name()
            .and_then(|s| s.to_str())
            .unwrap_or("jachin-desktop.exe");
        taskkill_process_tree_by_image_name(main_image);
        std::thread::sleep(Duration::from_millis(2000));

        if looks_like_windows_installer_artifact(staged) {
            launch_windows_installer_detached(staged)?;
            log_line(&format!(
                "安装包模式：暂存文件在安装完成前保留: {}",
                staged.display()
            ));
        } else {
            replace_exe_and_launch(&job.target_exe, staged)?;
            let _ = fs::remove_file(staged);
        }
    }
    #[cfg(not(windows))]
    {
        return Err("非 Windows 未实现替换".into());
    }

    let _ = fs::remove_file(job_path);
    log_line("apply done");
    Ok(())
}

fn run_legacy(job_path: &Path, job: &HotUpdateJob) -> Result<(), String> {
    log_line(&format!(
        "legacy full flow parent_pid={} target={} version={}",
        job.parent_pid,
        job.target_exe.display(),
        job.new_version
    ));

    let tmp = std::env::temp_dir().join(format!(
        "jachin-desktop-{}-new.exe",
        job.new_version.replace(['/', '\\'], "_")
    ));

    download_verify_to_tmp(job, &tmp)?;

    log_line("minisign OK, checking user data...");
    user_data_ready_for_hot_update()?;

    log_line("waiting for parent process to exit（主程序应先自行退出；随后清理仍占用安装目录的残留进程）...");
    wait_parent_exit(job.parent_pid)?;

    #[cfg(windows)]
    {
        std::thread::sleep(Duration::from_millis(500));
        ensure_no_straggler_process_for_target_exe(&job.target_exe);
        std::thread::sleep(Duration::from_millis(1200));

        let main_image = job
            .target_exe
            .file_name()
            .and_then(|s| s.to_str())
            .unwrap_or("jachin-desktop.exe");
        taskkill_process_tree_by_image_name(main_image);
        std::thread::sleep(Duration::from_millis(2000));

        if looks_like_windows_installer_artifact(&tmp) {
            launch_windows_installer_detached(&tmp)?;
            log_line(&format!(
                "安装包模式：暂存文件在安装完成前保留: {}",
                tmp.display()
            ));
        } else {
            replace_exe_and_launch(&job.target_exe, &tmp)?;
            let _ = fs::remove_file(&tmp);
        }
    }
    #[cfg(not(windows))]
    {
        return Err("非 Windows 未实现替换".into());
    }

    let _ = fs::remove_file(job_path);
    log_line("done");
    Ok(())
}

fn run(job_path: &Path) -> Result<(), String> {
    log_session_banner(job_path);
    let raw = fs::read_to_string(job_path).map_err(|e| format!("读取任务: {e}"))?;
    let job: HotUpdateJob = serde_json::from_str(&raw).map_err(|e| format!("解析任务 JSON: {e}"))?;
    log_job_loaded(&job, job_path);

    if job.apply_only {
        return run_apply(job_path, &job);
    }
    if job.prepare_only {
        return run_prepare(job_path, &job);
    }
    run_legacy(job_path, &job)
}

fn main() {
    let job_path = match parse_args() {
        Ok(p) => p,
        Err(e) => {
            eprintln!("{e}");
            std::process::exit(2);
        }
    };

    match run(&job_path) {
        Ok(()) => {
            log_line("======== session_end status=OK ========");
        }
        Err(e) => {
            log_line(&format!("======== session_end status=ERR err={e} ========"));
            std::process::exit(1);
        }
    }
}

#[cfg(test)]
mod tests {
    //! MIME Base64（76 列换行）与生产 `signature` 外层解码回归；`cargo test --bin jachin-updater-helper`。

    use base64::Engine;
    use crate::updater_common::{
        assert_signed_artifact_version_matches_release, parse_minisign_trusted_file_field,
    };

    /// 生产环境抓取的 MIME 折行外层 Base64（内含字面 `\n`）。
    const RAW_MIME_SIGNATURE: &str = "dW50cnVzdGVkIGNvbW1lbnQ6IHNpZ25hdHVyZSBmcm9tIHRhdXJpIHNlY3JldCBrZXkKUlVUK0h0\nNGhTRHFzQ0pLZlFJbFVlK1RNNWhRZTRVOWdRZ3gyaWdvMUR6M1dTQThHbFc1cVRaeVlQcGUwMEJ0\nbnJ2YWFWMkJpUjV5b3dEN2RDWlVQMjZWTk53bm0rY1IrM0FBPQp0cnVzdGVkIGNvbW1lbnQ6IHRp\nbWVzdGFtcDoxNzc1NjM1MDA1CWZpbGU6amFjaGluLWRlc2t0b3AuZXhlCjBNSWMwcDdRcitFNkVR\nWTVydDJSbUowUWFBT01pNlAxdzJxRCtvUmJNWTRCdXNVZ1JYYWZuRUJqSnhVcnBHT1d3QjdQRUpE\nSVZVZnhVREVCZnBpZURBPT0K";

    #[test]
    fn test_base64_decode_signature_production_helper_path() {
        let decoded = crate::updater_common::decode_mime_wrapped_signature_outer_base64(RAW_MIME_SIGNATURE)
            .unwrap_or_else(|e| panic!("热更新共用解码路径失败（应已处理 MIME 换行）: {e}"));

        assert!(
            decoded.trim_start().starts_with("untrusted comment:"),
            "解码后应为 minisign 明文 .sig，实际前缀: {:?}",
            decoded.chars().take(48).collect::<String>()
        );
        assert!(
            decoded.contains("trusted comment:"),
            "解码后应包含 trusted comment 行"
        );
    }

    /// 与用户手写「先 `replace` 再 decode」等价；crate 使用 `Engine` API（无已弃用的 `base64::decode`）。
    /// 生产日志中「0.8.75 登记 + 0.8.74 安装包」时的 signature（trusted file 含 0.8.74）。
    const SIG_074_FILE_IN_WIRE: &str = "dW50cnVzdGVkIGNvbW1lbnQ6IHNpZ25hdHVyZSBmcm9tIHRhdXJpIHNlY3JldCBrZXkKUlVUK0h0NGhTRHFzQ0xFOEE3aWVXNnV2Q0p3TDBrRWFEaTgzcGZpQ2wrcjliRnNqa2w4alZLTWcrajgvSHJnTCtxTzgyR3Zidm5qWDdQZmtCdS9DSmdab3NCeG9wS2VvQndJPQp0cnVzdGVkIGNvbW1lbnQ6IHRpbWVzdGFtcDoxNzc1NzAyNDY2CWZpbGU6SmFjaGluIERlc2t0b3AgU3ByaXRlXzAuOC43NF94NjQtc2V0dXAuZXhlCi9rdTNJL0kxeWpZL2hFSFdKSWtHN0lqMGpONEFYcmJTNDFJbzZlODVDV3BZYWwveEY2VjhtU0g5N3BzbDdoUTk0NHR5VTdVeHFyNDdRSFZORzFLbUFnPT0K";

    #[test]
    fn test_parse_trusted_file_field_contains_074() {
        let name = parse_minisign_trusted_file_field(SIG_074_FILE_IN_WIRE)
            .expect("parse")
            .expect("some");
        assert!(
            name.contains("0.8.74") || name.to_lowercase().contains("0.8.74"),
            "name={name:?}"
        );
    }

    #[test]
    fn test_assert_signed_mismatch_075_vs_074_sig() {
        let e = assert_signed_artifact_version_matches_release(SIG_074_FILE_IN_WIRE, "0.8.75")
            .expect_err("应拒绝");
        assert!(
            e.contains("0.8.74") && e.contains("0.8.75"),
            "unexpected msg: {e}"
        );
    }

    #[test]
    fn test_assert_signed_ok_when_expected_matches_file() {
        assert_signed_artifact_version_matches_release(SIG_074_FILE_IN_WIRE, "0.8.74").unwrap();
    }

    #[test]
    fn test_base64_decode_signature_manual_strip_then_engine_decode() {
        let clean_sig = RAW_MIME_SIGNATURE.replace('\n', "").replace('\r', "");
        let result = base64::engine::general_purpose::STANDARD.decode(clean_sig.as_bytes());
        assert!(
            result.is_ok(),
            "Base64 解码失败: {:?}",
            result.as_ref().err()
        );
        let utf8 = String::from_utf8(result.unwrap()).expect("decoded sig file is utf8");
        assert!(utf8.trim_start().starts_with("untrusted comment:"));
    }
}

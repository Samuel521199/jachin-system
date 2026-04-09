//! 热更新调试：追加写入固定目录日志（Windows 调试路径，勿用于生产收集用户隐私）。
//! 不落盘完整 Bearer / token，仅记录长度与是否配置。
//!
//! 目录：`crate::updater_common::hot_update_debug_log_dir()`（环境变量 `JACHIN_HOT_UPDATE_DEBUG_DIR`）。

use crate::updater_common::hot_update_debug_log_dir;
use serde_json::Value;
use std::fs::OpenOptions;
use std::io::Write;
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

const LOG_FILE: &str = "hot_update_debug.log";

fn now_ts_ms() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0)
}

/// 追加一行 UTF-8 日志（自动建目录）。
pub fn append_line(source: &str, message: &str) {
    let base = hot_update_debug_log_dir();
    let dir = Path::new(&base);
    if let Err(e) = std::fs::create_dir_all(dir) {
        eprintln!("[updater_debug] mkdir {}: {}", base.display(), e);
        return;
    }
    let path = dir.join(LOG_FILE);
    let sanitized = message.replace('\r', " ").replace('\n', " | ");
    let pid = std::process::id();
    let line = format!(
        "[{}ms pid={} proc=desktop] [{}] {}\n",
        now_ts_ms(),
        pid,
        source,
        sanitized
    );
    match OpenOptions::new().create(true).append(true).open(&path) {
        Ok(mut f) => {
            if let Err(e) = f.write_all(line.as_bytes()) {
                eprintln!("[updater_debug] write {}: {}", path.display(), e);
            }
        }
        Err(e) => eprintln!("[updater_debug] open {}: {}", path.display(), e),
    }
}

fn read_tauri_updater_endpoints_debug() -> String {
    let p = Path::new(env!("CARGO_MANIFEST_DIR")).join("tauri.conf.json");
    let raw = match std::fs::read_to_string(&p) {
        Ok(s) => s,
        Err(e) => return format!("(read tauri.conf.json failed: {} path={})", e, p.display()),
    };
    let v: Value = match serde_json::from_str(&raw) {
        Ok(x) => x,
        Err(e) => return format!("(parse tauri.conf.json: {})", e),
    };
    v.pointer("/plugins/updater/endpoints")
        .map(|x| x.to_string())
        .unwrap_or_else(|| "(no plugins.updater.endpoints)".into())
}

fn read_tauri_pubkey_fingerprint_debug() -> String {
    let p = Path::new(env!("CARGO_MANIFEST_DIR")).join("tauri.conf.json");
    let Ok(raw) = std::fs::read_to_string(&p) else {
        return "(no pubkey)".into();
    };
    let Ok(v): Result<Value, _> = serde_json::from_str(&raw) else {
        return "(parse err)".into();
    };
    let pk = v
        .pointer("/plugins/updater/pubkey")
        .and_then(|x| x.as_str())
        .unwrap_or("");
    let len = pk.len();
    let head: String = pk.chars().take(24).collect();
    format!("pubkey_len={} pubkey_prefix={}...", len, head)
}

/// 进程启动时写一条环境快照（含 nexus_config 摘要，不含密钥明文）。
pub fn log_startup_rust(app_version: &str) {
    let (nc_path, nc_exists, nc_summary) = crate::nexus_config::updater_debug_summary();
    let bearer_ok = crate::nexus_config::updater_bearer_token().is_some();
    let endpoints = read_tauri_updater_endpoints_debug();
    let pk_info = read_tauri_pubkey_fingerprint_debug();
    let arch = std::env::consts::ARCH;
    let os = std::env::consts::OS;
    let exe = std::env::current_exe()
        .map(|p| p.display().to_string())
        .unwrap_or_else(|_| "(unknown exe)".into());

    append_line(
        "rust_startup",
        &format!(
            "app_version={} os={} arch={} exe={} log_dir={} log_file={} \
             nexus_config_path={:?} nexus_config_exists={} \
             nexus_config_fields={} \
             updater_plugin_bearer_header_installed={} \
             tauri_endpoints_json={} \
             {}",
            app_version, os, arch, exe, hot_update_debug_log_dir().display(), LOG_FILE,
            nc_path,
            nc_exists,
            nc_summary,
            bearer_ok,
            endpoints,
            pk_info
        ),
    );
}

/// 前端 `invoke`：写入一行（检查更新 / 安装前后等）。
#[tauri::command]
pub fn updater_debug_append(line: String) {
    append_line("webview", &line);
}

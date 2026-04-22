//! Mirror-Trace：两台机器对比用的统一热键诊断格式（落盘 + stdout）。
//! 格式：`[HOTKEY_TRACE] | Event: {name} | PID: {pid} | Data: {json}`
//!
//! `trace_raw_hotkey` 等导出函数为按需接线的诊断钩子；未从热键路径调用时保留 API，避免 rustc 误报。
#![allow(dead_code)]

use serde_json::{json, Value};
use tauri::Manager;
use std::fs::OpenOptions;
use std::io::Write;
use std::path::PathBuf;
use std::sync::{Mutex, OnceLock};
use std::time::{SystemTime, UNIX_EPOCH};

static WRITE_LOCK: OnceLock<Mutex<()>> = OnceLock::new();

const LOG_FILE: &str = "omni_hotkey_interaction.log";

pub fn resolved_log_dir() -> PathBuf {
    if let Ok(p) = std::env::var("JACHIN_OMNI_HOTKEY_LOG_DIR") {
        return PathBuf::from(p);
    }
    let home = std::env::var("USERPROFILE")
        .or_else(|_| std::env::var("HOME"))
        .unwrap_or_else(|_| ".".into());
    PathBuf::from(home)
        .join(".jachin")
        .join("jachin_debug")
        .join("打包")
}

pub fn resolved_log_file() -> PathBuf {
    resolved_log_dir().join(LOG_FILE)
}

fn ts_ms() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0)
}

fn thread_id_str() -> String {
    format!("{:?}", std::thread::current().id())
}

/// 统一行：控制台 + `omni_hotkey_interaction.log`（UTF-8 追加）
pub fn trace(event: &str, mut data: Value) {
    if let Some(obj) = data.as_object_mut() {
        obj.entry("ts_ms").or_insert_with(|| json!(ts_ms()));
    }
    let pid = std::process::id();
    let data_str = serde_json::to_string(&data).unwrap_or_else(|_| "{}".to_string());
    let line = format!(
        "[HOTKEY_TRACE] | Event: {} | PID: {} | Data: {}\n",
        event, pid, data_str
    );
    print!("{}", line);
    let lock = WRITE_LOCK.get_or_init(|| Mutex::new(()));
    let Ok(_g) = lock.lock() else {
        return;
    };
    let dir = resolved_log_dir();
    if std::fs::create_dir_all(&dir).is_err() {
        return;
    }
    let path = dir.join(LOG_FILE);
    if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(&path) {
        let _ = f.write_all(line.as_bytes());
    }
}

/// 任意全局快捷键回调一进来的原始事件（含 Released，便于 A/B diff）
pub fn trace_raw_hotkey(shortcut_hint: &str, state: &str) {
    trace(
        "HOTKEY_RAW_PLUGIN_CALLBACK",
        json!({
            "ts_ms": ts_ms(),
            "thread_id": thread_id_str(),
            "shortcut": shortcut_hint,
            "state": state,
        }),
    );
}

/// 在调用 `toggle_chat_omni` 之前：若 Machine B 没有此行，说明 OS/插件层未把键交给 Rust
pub fn trace_signal_received_by_rust<R: tauri::Runtime>(app: &tauri::AppHandle<R>, shortcut_hint: &str) {
    let window_exists = app.get_webview_window("chat").is_some();
    let window_visible = app
        .get_webview_window("chat")
        .map(|w| w.is_visible().unwrap_or(false))
        .unwrap_or(false);

    let ping = match app.run_on_main_thread(|| {}) {
        Ok(()) => "PONG".to_string(),
        Err(e) => format!("ERR:{e}"),
    };

    trace(
        "HOTKEY_SIGNAL_RECEIVED_BY_RUST",
        json!({
            "ts_ms": ts_ms(),
            "thread_id": thread_id_str(),
            "shortcut": shortcut_hint,
            "window_exists": window_exists,
            "window_visible": window_visible,
            "app_main_thread_ping": ping,
        }),
    );
}

/// `set_focus` 之后记录（Windows 可对照 `last_os_error`，仅作辅助）
pub fn trace_set_focus_result(op: &str, result: &Result<(), tauri::Error>) {
    #[cfg(windows)]
    let last_os = Some(std::io::Error::last_os_error().to_string());
    #[cfg(not(windows))]
    let last_os: Option<String> = None;

    trace(
        "WINDOW_SET_FOCUS_TRACE",
        json!({
            "ts_ms": ts_ms(),
            "thread_id": thread_id_str(),
            "op": op,
            "set_focus_ok": result.is_ok(),
            "set_focus_err": result.as_ref().err().map(|e| e.to_string()),
            "std_io_last_os_error_after_call": last_os,
        }),
    );
}

/// 注册成功时写一条，便于两份日志对齐「注册了哪条组合」
pub fn trace_registration(combo: &str, ok: bool, err: Option<&str>) {
    trace(
        "HOTKEY_REGISTER_CANDIDATE",
        json!({
            "combo": combo,
            "registered": ok,
            "error": err,
        }),
    );
}

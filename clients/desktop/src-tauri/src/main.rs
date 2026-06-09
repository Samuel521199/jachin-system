// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod commands;
mod config;
mod nexus_config;
#[allow(dead_code)]
mod updater_common;
mod updater_debug_log;
mod updater_spawn;
mod device;
mod device_registry;
mod kernel;
mod l3_spawn;
mod pubsub;
mod reminder_scheduler;
mod stt;
mod tts;
mod window;
mod omni_hotkey_mirror_trace;
mod inbox_store;

#[cfg(windows)]
#[link(name = "user32")]
extern "system" {
    fn MessageBoxW(
        h_wnd: *mut std::ffi::c_void,
        lp_text: *const u16,
        lp_caption: *const u16,
        u_type: u32,
    ) -> i32;
}

/// 未启用 ambient 时的占位命令，返回明确错误或 false。
#[cfg(not(feature = "ambient"))]
mod stt_voice_stub {
    #[tauri::command]
    pub fn start_voice_capture() -> Result<(), String> {
        Err("请使用 --features ambient 构建以启用语音采集".to_string())
    }
    #[tauri::command]
    pub fn stop_voice_capture() -> Result<(), String> {
        Err("请使用 --features ambient 构建以启用语音采集".to_string())
    }
    #[tauri::command]
    pub fn is_voice_capture_running() -> bool {
        false
    }
}

use sysinfo::System;

use device::DeviceController;
use device_registry::{DeviceRegistry, DeviceCommand, DeviceResponse};
use pubsub::start_pubsub_server;
use tauri::{
    menu::{MenuBuilder, MenuItem},
    tray::{TrayIconBuilder, TrayIconEvent},
    Emitter, EventTarget, Listener, Manager, PhysicalPosition, PhysicalSize, Size,
};
use tauri_plugin_global_shortcut::{Builder as GlobalShortcutBuilder, ShortcutState};
use tauri_plugin_notification::NotificationExt;
use serde_json::json;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::sync::Mutex as StdMutex;
use tokio::sync::Mutex;

/// [v5.0 已废弃] 原 Dapr 调用，现由 Layer 1 HTTP 心跳与云端 API 取代
#[tauri::command]
async fn invoke_backend(
    _method: String,
    _data: Option<serde_json::Value>,
    _http_verb: Option<String>,
) -> Result<serde_json::Value, String> {
    Err("Dapr 已废弃。v5.0 请使用 Layer 1 云端 API 或扫码配对后的 HTTP 心跳链路。".to_string())
}

/// 控制设备
#[tauri::command]
async fn control_device(
    device_id: String,
    action: String,
    params: Option<serde_json::Value>,
) -> Result<serde_json::Value, String> {
    let controller = DeviceController::new();
    
    controller
        .execute(&device_id, &action, params)
        .await
        .map_err(|e| e.to_string())
}

/// 获取系统信息
#[tauri::command]
fn get_system_info() -> Result<serde_json::Value, String> {
    Ok(serde_json::json!({
        "platform": std::env::consts::OS,
        "arch": std::env::consts::ARCH,
    }))
}

/// TTS 自检（Capability Check）- 检测 Arch 和 RAM
#[tauri::command]
fn tts_self_check() -> Result<serde_json::Value, String> {
    let engine = tts::SpeechEngine::new(
        "http://localhost:18888",
        None,
        None::<tts::AliyunTtsConfig>,
    );
    let result = engine.self_check_result();
    Ok(serde_json::json!({
        "arch_ok": result.arch_ok,
        "memory_ok": result.memory_ok,
        "compute_ok": result.compute_ok,
        "local_enabled": result.local_enabled,
        "reason": result.reason,
    }))
}

/// TTS 模型是否已存在
#[tauri::command]
fn tts_has_model() -> Result<bool, String> {
    let mgr = tts::SpeechEngine::model_manager(Some("http://localhost:18888"), None);
    Ok(mgr.has_model())
}

/// TTS 语音合成（Local/Edge/Cloud 按 Fallback 顺序）
/// 返回 WAV 音频的 base64 字符串，供前端解码播放
#[tauri::command]
async fn tts_speak(text: String) -> Result<String, String> {
    let mgr = tts::SpeechEngine::model_manager(Some("http://localhost:18888"), None);
    if !mgr.has_model() {
        return Err("请先调用 tts_ensure_model 下载模型".to_string());
    }
    let model_dir = mgr.data_dir().clone();
    let engine = tts::SpeechEngine::new(
        "http://localhost:18888",
        Some(model_dir),
        None::<tts::AliyunTtsConfig>,
    );
    let wav_bytes = engine.speak(&text).await?;
    Ok(base64::Engine::encode(
        &base64::engine::general_purpose::STANDARD,
        &wav_bytes,
    ))
}

/// TTS 确保模型已下载（若不存在则从 Tier 2 下载，通过 tts-download-progress 事件推送进度）
#[tauri::command]
async fn tts_ensure_model(app: tauri::AppHandle) -> Result<serde_json::Value, String> {
    let mgr = tts::SpeechEngine::model_manager(Some("http://localhost:18888"), None);

    let app_handle = app.clone();
    let on_progress: Option<tts::ProgressCallback> = Some(Box::new(move |downloaded, total| {
        let _ = app_handle.emit("tts-download-progress", serde_json::json!({
            "downloaded": downloaded,
            "total": total,
        }));
    }));

    let (model_path, voices_path) = mgr.ensure_model(on_progress).await?;
    Ok(serde_json::json!({
        "model_path": model_path.to_string_lossy(),
        "voices_path": voices_path.to_string_lossy(),
    }))
}

/// 获取系统资源（CPU / 内存）供控制台 Vital Signs 使用
#[tauri::command]
fn get_system_stats() -> Result<serde_json::Value, String> {
    let mut sys = System::new_all();
    sys.refresh_all();
    std::thread::sleep(std::time::Duration::from_millis(250));
    sys.refresh_cpu_usage();
    let raw_cpu = sys.global_cpu_info().cpu_usage();
    let cpu_percent = if raw_cpu <= 1.0 { raw_cpu * 100.0 } else { raw_cpu };
    sys.refresh_memory();
    let total = sys.total_memory();
    let used = sys.used_memory();
    Ok(serde_json::json!({
        "cpu_usage_percent": cpu_percent.min(100.0).max(0.0),
        "memory_total_bytes": total,
        "memory_used_bytes": used,
    }))
}

/// 隐私模式状态（全局可读写的开关）
static PRIVACY_MODE: AtomicBool = AtomicBool::new(false);

/// 获取当前隐私模式是否开启
#[tauri::command]
fn get_privacy_mode() -> Result<bool, String> {
    Ok(PRIVACY_MODE.load(Ordering::Relaxed))
}

/// 快捷指令：隐私模式（切换开关，并返回当前是否开启）
#[tauri::command]
async fn quick_action_privacy_mode(app: tauri::AppHandle) -> Result<bool, String> {
    let prev = PRIVACY_MODE.load(Ordering::Relaxed);
    let next = !prev;
    PRIVACY_MODE.store(next, Ordering::Relaxed);
    let (title, body) = if next {
        ("隐私模式", "已开启隐私模式，本地数据将不再上报")
    } else {
        ("隐私模式", "已关闭隐私模式，恢复正常上报")
    };
    let _ = app.notification().builder().title(title).body(body).show();
    Ok(next)
}

/// 快捷指令：立即清理内存（通知 + 可扩展为调用后端清理缓存）
#[tauri::command]
async fn quick_action_clear_memory(app: tauri::AppHandle) -> Result<(), String> {
    let _ = app.notification().builder().title("清理内存").body("已触发内存清理，缓存将在后台释放").show();
    Ok(())
}

/// 鹰眼监控状态：true = 控制台显示并聚焦，false = 控制台隐藏（可切换）
static EAGLE_EYE_ON: AtomicBool = AtomicBool::new(false);

#[tauri::command]
fn get_eagle_eye_mode() -> Result<bool, String> {
    Ok(EAGLE_EYE_ON.load(Ordering::Relaxed))
}

/// 快捷指令：鹰眼监控（切换：开启时显示并聚焦 main，关闭时隐藏 main）
#[tauri::command]
async fn quick_action_eagle_eye(app: tauri::AppHandle) -> Result<bool, String> {
    let prev = EAGLE_EYE_ON.load(Ordering::Relaxed);
    let next = !prev;
    EAGLE_EYE_ON.store(next, Ordering::Relaxed);
    let app_handle = app.clone();
    if next {
        // 在主线程执行 show，否则 Windows 上可能无效
        app.run_on_main_thread(move || {
            if let Some(w) = app_handle.get_webview_window("main") {
                let _ = w.show();
                let _ = w.unminimize();
                let _ = w.set_focus();
            }
        })
        .map_err(|e| e.to_string())?;
    } else {
        let app_deferred = app.clone();
        tauri::async_runtime::spawn(async move {
            tokio::time::sleep(tokio::time::Duration::from_millis(120)).await;
            let inner = app_deferred.clone();
            let _ = app_deferred.run_on_main_thread(move || {
                if let Some(w) = inner.get_webview_window("main") {
                    let _ = w.hide();
                }
            });
        });
    }
    let (title, body) = if next {
        ("鹰眼监控", "已开启：控制台已显示并聚焦")
    } else {
        ("鹰眼监控", "已关闭：控制台已隐藏")
    };
    let _ = app.notification().builder().title(title).body(body).show();
    Ok(next)
}

/// 休眠状态：true = Omni 条已隐藏（桌面精灵默认不显示）
static HIBERNATE_ON: AtomicBool = AtomicBool::new(false);

/// Omni 条是否处于右下角「陪伴圆」模式（窗口缩小而非 hide，便于存在感）
static CHAT_COMPANION_MODE: AtomicBool = AtomicBool::new(false);
/// 进入陪伴模式前记录的外尺寸，用于恢复用户拖拽后的大小
static CHAT_RESTORE_SIZE: std::sync::Mutex<Option<(u32, u32)>> = std::sync::Mutex::new(None);

/// 无法取得显示器时的兜底外尺寸（物理像素，与 tauri.conf 首帧接近）
const CHAT_DEFAULT_WIDTH: u32 = 820;
const CHAT_DEFAULT_HEIGHT: u32 = 640;
const CHAT_MIN_WIDTH: u32 = 360;
const CHAT_MIN_HEIGHT: u32 = 280;
/// 陪伴条略宽于球体，贴边缩进后仍有一條可悬停的透明区（类似网盘角标）
const CHAT_COMPANION_W: u32 = 72;
const CHAT_COMPANION_H: u32 = 56;
const CHAT_COMPANION_MIN_W: u32 = 56;
const CHAT_COMPANION_MIN_H: u32 = 48;
/// 贴边时留在屏幕内的可见厚度（物理像素）
const COMPANION_PEEK_VISIBLE_PX: i32 = 10;
/// 「dock」= 小球完全露出时的左上角；peek 只改窗口位置不改此值
static COMPANION_DOCK_POSITION: StdMutex<Option<(i32, i32)>> = StdMutex::new(None);

/// Omni 双栏所需最小 **逻辑宽度**（左列约 420 + 右画布 + 边距）；`outer_size` 为物理像素，须乘 `scale_factor`
const CHAT_SKILL_CANVAS_MIN_TOTAL_LOGICAL: f64 = 980.0;

/// 未记忆用户外尺寸时：主显示器 **逻辑** 宽 55%、高约 82%；宽 clamp 760–1280（逻辑）；再乘 `scale_factor` 得物理像素。
fn chat_omni_ideal_physical_size_from_monitor_dimensions(
    monitor_width_px: u32,
    monitor_height_px: u32,
    scale_factor: f64,
) -> (u32, u32) {
    let f = scale_factor.max(0.01);
    let mw = monitor_width_px as f64;
    let mh = monitor_height_px as f64;
    let lw = mw / f;
    let lh = mh / f;
    let mut w_log = (lw * 0.55).round();
    w_log = w_log.max(760.0).min(1280.0);
    let mut h_log = (lh * 0.82).round();
    h_log = h_log.min(lh * 0.88).max(340.0);
    let w_px = (w_log * f).round().max(CHAT_MIN_WIDTH as f64) as u32;
    let h_px = (h_log * f).round().max(CHAT_MIN_HEIGHT as f64) as u32;
    (w_px, h_px)
}

/// 有「恢复尺寸」则用记忆值；否则按当前屏黄金比例；再失败用 CHAT_DEFAULT_*。
fn resolve_chat_omni_outer_size(chat: &tauri::WebviewWindow) -> (u32, u32) {
    if let Ok(guard) = CHAT_RESTORE_SIZE.lock() {
        if let Some((w, h)) = *guard {
            return (w.max(CHAT_MIN_WIDTH), h.max(CHAT_MIN_HEIGHT));
        }
    }
    let sf = chat.scale_factor().unwrap_or(1.0);
    if let Ok(Some(m)) = chat.current_monitor() {
        let s = m.size();
        return chat_omni_ideal_physical_size_from_monitor_dimensions(s.width, s.height, sf);
    }
    (CHAT_DEFAULT_WIDTH, CHAT_DEFAULT_HEIGHT)
}

/// Skill 画布打开前记录的外宽度，关闭时还原（避免用户误以为需手动拖窗）
static CHAT_WIDTH_BEFORE_SKILL_CANVAS: StdMutex<Option<u32>> = StdMutex::new(None);

/// 供前端在 Esc 后立即切换陪伴 UI（不依赖 event listen 权限）
#[derive(Debug, Clone, serde::Serialize)]
#[serde(rename_all = "camelCase")]
pub struct HideChatWindowResult {
    pub companion: bool,
    pub fully_hidden: bool,
}

/// 桌面 Omni / 哨兵：单行快照，写入 `~/.jachin/l3_debug.log` 便于与 L3 日志对照。
fn omni_surface_debug_snapshot(app: &tauri::AppHandle) -> String {
    let rust_companion = CHAT_COMPANION_MODE.load(Ordering::Relaxed);
    let hib = HIBERNATE_ON.load(Ordering::Relaxed);
    let mut parts = vec![format!(
        "rust_companion={} hibernate={}",
        rust_companion, hib
    )];
    if let Some(chat) = app.get_webview_window("chat") {
        let mini = chat.is_minimized().unwrap_or(false);
        let vis = chat.is_visible().unwrap_or(false);
        let sz = chat
            .outer_size()
            .map(|z| format!("{}x{}", z.width, z.height))
            .unwrap_or_else(|_| "?x?".into());
        let mon = chat
            .current_monitor()
            .ok()
            .flatten()
            .map(|m| {
                let p = m.position();
                let s = m.size();
                format!("mon@{}:{} sz={}x{}", p.x, p.y, s.width, s.height)
            })
            .unwrap_or_else(|| "mon=?".into());
        parts.push(format!(
            "chat mini={} vis={} outer={} {}",
            mini, vis, sz, mon
        ));
    } else {
        parts.push("chat_absent=1".into());
    }
    if let Some(n) = app.get_webview_window("notification") {
        let nv = n.is_visible().unwrap_or(false);
        let ns = n
            .outer_size()
            .map(|z| format!("{}x{}", z.width, z.height))
            .unwrap_or_else(|_| "?".into());
        parts.push(format!("notif vis={} outer={}", nv, ns));
    } else {
        parts.push("notif_absent=1".into());
    }
    parts.join(" ")
}

/// 前端 `listen('omni-companion-mode')`：优先投递到 chat Webview（Tauri 2 事件目标更明确）
fn emit_omni_companion_ui(app: &tauri::AppHandle, companion: bool) {
    l3_spawn::write_jachin_shared_l3_debug(
        "omni_companion_emit",
        &format!(
            "emit_payload_companion={} {} file={}",
            companion,
            omni_surface_debug_snapshot(app),
            l3_spawn::jachin_shared_l3_debug_path().display()
        ),
    );
    let payload = json!({ "companion": companion });
    if let Err(e) = app.emit_to(
        EventTarget::webview_window("chat"),
        "omni-companion-mode",
        payload.clone(),
    ) {
        eprintln!("[Omni] emit_to(chat) omni-companion-mode failed: {}, fallback broadcast", e);
        l3_spawn::write_jachin_shared_l3_debug(
            "omni_companion_emit_err",
            &format!("emit_to_failed={e} fallback_broadcast=1"),
        );
        let _ = app.emit("omni-companion-mode", payload);
    }
}

#[tauri::command]
fn get_hibernate_mode() -> Result<bool, String> {
    Ok(HIBERNATE_ON.load(Ordering::Relaxed))
}

/// 快捷指令：休眠系统（切换 Omni 条；桌面精灵保持默认隐藏）
#[tauri::command]
async fn quick_action_hibernate(app: tauri::AppHandle) -> Result<bool, String> {
    let prev = HIBERNATE_ON.load(Ordering::Relaxed);
    let next = !prev;
    HIBERNATE_ON.store(next, Ordering::Relaxed);
    if let Some(sprite) = app.get_webview_window("sprite") {
        if next {
            let _ = sprite.hide();
        }
    }
    if let Some(chat) = app.get_webview_window("chat") {
        if next {
            CHAT_COMPANION_MODE.store(false, Ordering::Relaxed);
            emit_omni_companion_ui(&app, false);
            let _ = chat.hide();
        } else {
            let _ = chat.set_min_size(Some(Size::Physical(PhysicalSize::new(
                CHAT_MIN_WIDTH,
                CHAT_MIN_HEIGHT,
            ))));
            let (w, h) = resolve_chat_omni_outer_size(&chat);
            let _ = chat.set_size(Size::Physical(PhysicalSize::new(w, h)));
            let _ = position_chat_omni_bar(&app);
            CHAT_COMPANION_MODE.store(false, Ordering::Relaxed);
            emit_omni_companion_ui(&app, false);
            let _ = chat.show();
            let _ = chat.set_focus();
        }
    }
    let (title, body) = if next {
        ("休眠系统", "已开启：Omni 条与桌面精灵已隐藏，托盘或全局快捷键可恢复")
    } else {
        ("休眠系统", "已关闭：Omni 条已恢复显示（精灵默认保持关闭）")
    };
    let _ = app.notification().builder().title(title).body(body).show();
    Ok(next)
}

/// 将 Omni 输入条置于主显示器水平、垂直居中（尺寸已由 `resolve_chat_omni_outer_size` 定好后再调用）
fn position_chat_omni_bar(app: &tauri::AppHandle) -> Result<(), String> {
    let Some(chat) = app.get_webview_window("chat") else {
        return Err("chat window missing".into());
    };
    let monitor = chat
        .current_monitor()
        .map_err(|e| e.to_string())?
        .ok_or_else(|| "no monitor".to_string())?;
    let mon_pos = monitor.position();
    let mon_size = monitor.size();
    let win_size = chat.outer_size().map_err(|e| e.to_string())?;
    let x = mon_pos.x + (mon_size.width as i32 - win_size.width as i32) / 2;
    let y = mon_pos.y + (mon_size.height as i32 - win_size.height as i32) / 2;
    chat.set_position(PhysicalPosition::new(x, y))
        .map_err(|e| e.to_string())?;
    Ok(())
}

/// 右下角「陪伴圆」锚点（主显示器工作区右下留白）
fn position_chat_companion(app: &tauri::AppHandle) -> Result<(), String> {
    let Some(chat) = app.get_webview_window("chat") else {
        return Err("chat window missing".into());
    };
    let monitor = chat
        .current_monitor()
        .map_err(|e| e.to_string())?
        .ok_or_else(|| "no monitor".to_string())?;
    let mon_pos = monitor.position();
    let mon_size = monitor.size();
    let margin: i32 = 20;
    let w = CHAT_COMPANION_W as i32;
    let h = CHAT_COMPANION_H as i32;
    let x = mon_pos.x + mon_size.width as i32 - w - margin;
    let y = mon_pos.y + mon_size.height as i32 - h - margin;
    chat.set_position(PhysicalPosition::new(x, y))
        .map_err(|e| e.to_string())?;
    Ok(())
}

fn companion_sync_dock_from_window(app: &tauri::AppHandle) -> Result<(), String> {
    let Some(chat) = app.get_webview_window("chat") else {
        return Err("chat window missing".into());
    };
    let p = chat.outer_position().map_err(|e| e.to_string())?;
    if let Ok(mut g) = COMPANION_DOCK_POSITION.lock() {
        *g = Some((p.x, p.y));
    }
    Ok(())
}

fn companion_ensure_dock(app: &tauri::AppHandle) -> Result<(i32, i32), String> {
    if let Ok(g) = COMPANION_DOCK_POSITION.lock() {
        if let Some(p) = *g {
            return Ok(p);
        }
    }
    companion_sync_dock_from_window(app)?;
    if let Ok(g) = COMPANION_DOCK_POSITION.lock() {
        if let Some(p) = *g {
            return Ok(p);
        }
    }
    Err("companion dock unset".into())
}

/// 贴最近屏边，仅留一条细边在桌面内（网盘式半隐藏）
fn companion_apply_peek(app: &tauri::AppHandle) -> Result<(), String> {
    let Some(chat) = app.get_webview_window("chat") else {
        return Err("chat window missing".into());
    };
    let (dx, dy) = companion_ensure_dock(app)?;
    let sz = chat.outer_size().map_err(|e| e.to_string())?;
    let w = sz.width as i32;
    let h = sz.height as i32;
    let monitor = chat
        .current_monitor()
        .map_err(|e| e.to_string())?
        .ok_or_else(|| "no monitor".to_string())?;
    let mon_pos = monitor.position();
    let mon_size = monitor.size();
    let mx = mon_pos.x;
    let my = mon_pos.y;
    let mw = mon_size.width as i32;
    let mh = mon_size.height as i32;
    let cx = dx + w / 2;
    let cy = dy + h / 2;
    let d_left = cx - mx;
    let d_right = mx + mw - cx;
    let d_top = cy - my;
    let d_bottom = my + mh - cy;
    let peek = COMPANION_PEEK_VISIBLE_PX;
    let (nx, ny) = if d_right <= d_left && d_right <= d_top && d_right <= d_bottom {
        let x = mx + mw - peek;
        let y = dy.clamp(my, (my + mh - h).max(my));
        (x, y)
    } else if d_left <= d_right && d_left <= d_top && d_left <= d_bottom {
        let x = mx - w + peek;
        let y = dy.clamp(my, (my + mh - h).max(my));
        (x, y)
    } else if d_top <= d_bottom {
        let y = my - h + peek;
        let x = dx.clamp(mx, (mx + mw - w).max(mx));
        (x, y)
    } else {
        let y = my + mh - peek;
        let x = dx.clamp(mx, (mx + mw - w).max(mx));
        (x, y)
    };
    chat.set_position(PhysicalPosition::new(nx, ny))
        .map_err(|e| e.to_string())?;
    Ok(())
}

fn companion_apply_reveal(app: &tauri::AppHandle) -> Result<(), String> {
    let Some(chat) = app.get_webview_window("chat") else {
        return Err("chat window missing".into());
    };
    let (dx, dy) = companion_ensure_dock(app)?;
    chat.set_position(PhysicalPosition::new(dx, dy))
        .map_err(|e| e.to_string())?;
    Ok(())
}

#[tauri::command]
fn companion_peek(app: tauri::AppHandle) -> Result<(), String> {
    if !CHAT_COMPANION_MODE.load(Ordering::Relaxed) {
        return Ok(());
    }
    let app_clone = app.clone();
    app.run_on_main_thread(move || {
        let _ = companion_apply_peek(&app_clone);
    })
    .map_err(|e| e.to_string())?;
    Ok(())
}

#[tauri::command]
fn companion_reveal(app: tauri::AppHandle) -> Result<(), String> {
    if !CHAT_COMPANION_MODE.load(Ordering::Relaxed) {
        return Ok(());
    }
    let app_clone = app.clone();
    app.run_on_main_thread(move || {
        let _ = companion_apply_reveal(&app_clone);
    })
    .map_err(|e| e.to_string())?;
    Ok(())
}

#[tauri::command]
fn companion_set_dock_position(app: tauri::AppHandle, x: i32, y: i32) -> Result<(), String> {
    if !CHAT_COMPANION_MODE.load(Ordering::Relaxed) {
        return Ok(());
    }
    let app_clone = app.clone();
    app.run_on_main_thread(move || {
        if let Some(chat) = app_clone.get_webview_window("chat") {
            let Ok(sz) = chat.outer_size() else {
                return;
            };
            let Ok(m_opt) = chat.current_monitor() else {
                return;
            };
            let Some(monitor) = m_opt else {
                return;
            };
            let mx = monitor.position().x;
            let my = monitor.position().y;
            let mw = monitor.size().width as i32;
            let mh = monitor.size().height as i32;
            let w = sz.width as i32;
            let h = sz.height as i32;
            let cx = x.clamp(mx, (mx + mw - w).max(mx));
            let cy = y.clamp(my, (my + mh - h).max(my));
            let _ = chat.set_position(PhysicalPosition::new(cx, cy));
            if let Ok(mut g) = COMPANION_DOCK_POSITION.lock() {
                *g = Some((cx, cy));
            }
        }
    })
    .map_err(|e| e.to_string())?;
    Ok(())
}

/// 系统标题栏 / Win+↓ 等将窗口最小化到任务栏时，转为右下角陪伴圆（与 Esc、`hide_chat_window` 一致）。
fn convert_os_minimize_to_companion_if_needed(app: &tauri::AppHandle) {
    l3_spawn::write_jachin_shared_l3_debug(
        "os_minimize_to_companion_enter",
        &omni_surface_debug_snapshot(app),
    );
    let Some(chat) = app.get_webview_window("chat") else {
        l3_spawn::write_jachin_shared_l3_debug(
            "os_minimize_to_companion_skip",
            "reason=chat_window_missing",
        );
        return;
    };
    if HIBERNATE_ON.load(Ordering::Relaxed) {
        l3_spawn::write_jachin_shared_l3_debug(
            "os_minimize_to_companion_skip",
            "reason=hibernate_on",
        );
        return;
    }
    if !chat.is_minimized().unwrap_or(false) {
        l3_spawn::write_jachin_shared_l3_debug(
            "os_minimize_to_companion_skip",
            "reason=not_os_minimized",
        );
        return;
    }
    let already_companion = CHAT_COMPANION_MODE.load(Ordering::Relaxed);
    let _ = chat.unminimize();
    if already_companion {
        let _ = chat.show();
        let _ = chat.set_focus();
        emit_omni_companion_ui(app, true);
        l3_spawn::write_jachin_shared_l3_debug(
            "os_minimize_to_companion_branch",
            "action=restore_already_companion_unminimize_show",
        );
        return;
    }
    let _ = minimize_chat_to_companion(app);
    l3_spawn::write_jachin_shared_l3_debug(
        "os_minimize_to_companion_branch",
        "action=minimize_chat_to_companion_after_unminimize",
    );
}

/// Esc / hide：缩为右下角小圆（需先放宽 min_inner_size，否则达不到 tauri.conf 的 360×280 下限）
fn minimize_chat_to_companion(app: &tauri::AppHandle) -> Result<(), String> {
    let Some(chat) = app.get_webview_window("chat") else {
        return Err("chat window missing".into());
    };
    if let Ok(sz) = chat.outer_size() {
        if sz.width >= 120 && sz.height >= 120 {
            if let Ok(mut g) = CHAT_RESTORE_SIZE.lock() {
                *g = Some((sz.width, sz.height));
            }
        }
    }
    chat.set_min_size(Some(Size::Physical(PhysicalSize::new(
        CHAT_COMPANION_MIN_W,
        CHAT_COMPANION_MIN_H,
    ))))
    .map_err(|e| format!("set_min_size(companion): {e}"))?;
    chat.set_size(Size::Physical(PhysicalSize::new(
        CHAT_COMPANION_W,
        CHAT_COMPANION_H,
    )))
    .map_err(|e| format!("set_size(companion): {e}"))?;
    position_chat_companion(app)?;
    chat
        .show()
        .map_err(|e| format!("show(companion): {e}"))?;
    let _ = companion_sync_dock_from_window(app);
    CHAT_COMPANION_MODE.store(true, Ordering::SeqCst);
    emit_omni_companion_ui(app, true);
    Ok(())
}

fn restore_chat_full_omni(app: &tauri::AppHandle) -> Result<(), String> {
    let Some(chat) = app.get_webview_window("chat") else {
        return Err("chat window missing".into());
    };
    let (w, h) = resolve_chat_omni_outer_size(&chat);
    chat.set_min_size(Some(Size::Physical(PhysicalSize::new(
        CHAT_MIN_WIDTH,
        CHAT_MIN_HEIGHT,
    ))))
    .map_err(|e| format!("set_min_size(restore): {e}"))?;
    chat.set_size(Size::Physical(PhysicalSize::new(w, h)))
        .map_err(|e| format!("set_size(restore): {e}"))?;
    position_chat_omni_bar(app)?;
    CHAT_COMPANION_MODE.store(false, Ordering::SeqCst);
    emit_omni_companion_ui(app, false);
    if let Ok(mut g) = COMPANION_DOCK_POSITION.lock() {
        *g = None;
    }
    Ok(())
}

/// 陪伴模式下再按 Esc：彻底 hide，并恢复最小尺寸约束供下次打开
fn hide_chat_fully_reset(app: &tauri::AppHandle) -> Result<(), String> {
    let Some(chat) = app.get_webview_window("chat") else {
        return Err("chat window missing".into());
    };
    chat.set_min_size(Some(Size::Physical(PhysicalSize::new(
        CHAT_MIN_WIDTH,
        CHAT_MIN_HEIGHT,
    ))))
    .map_err(|e| format!("set_min_size(reset): {e}"))?;
    CHAT_COMPANION_MODE.store(false, Ordering::SeqCst);
    emit_omni_companion_ui(app, false);
    if let Ok(mut g) = COMPANION_DOCK_POSITION.lock() {
        *g = None;
    }
    chat.hide().map_err(|e| e.to_string())?;
    Ok(())
}

/// 切换 Omni 条显示；由托盘、全局快捷键等调用
pub(crate) fn toggle_chat_omni(app: &tauri::AppHandle) {
    let app_clone = app.clone();
    let _ = app.run_on_main_thread(move || {
        if let Some(chat) = app_clone.get_webview_window("chat") {
            let visible = chat.is_visible().unwrap_or(false);
            let companion = CHAT_COMPANION_MODE.load(Ordering::Relaxed);
            if !visible {
                let _ = chat.set_min_size(Some(Size::Physical(PhysicalSize::new(
                    CHAT_MIN_WIDTH,
                    CHAT_MIN_HEIGHT,
                ))));
                let (w, h) = resolve_chat_omni_outer_size(&chat);
                let _ = chat.set_size(Size::Physical(PhysicalSize::new(w, h)));
                let _ = position_chat_omni_bar(&app_clone);
                CHAT_COMPANION_MODE.store(false, Ordering::SeqCst);
                emit_omni_companion_ui(&app_clone, false);
                let _ = chat.show();
                let fr = chat.set_focus();
                omni_hotkey_mirror_trace::trace_set_focus_result("toggle_show_from_hidden", &fr);
                return;
            }
            if companion {
                let _ = restore_chat_full_omni(&app_clone);
                let fr = chat.set_focus();
                omni_hotkey_mirror_trace::trace_set_focus_result("toggle_restore_from_companion", &fr);
                return;
            }
            let _ = minimize_chat_to_companion(&app_clone);
        }
    });
}

/// 注册 Omni 全局快捷键：按顺序尝试，被占用则换下一个（失败不崩溃）
fn register_omni_hotkeys(app: &tauri::AppHandle) {
    use tauri_plugin_global_shortcut::GlobalShortcutExt;
    omni_hotkey_mirror_trace::trace(
        "MIRROR_TRACE_SESSION",
        serde_json::json!({
            "log_file": omni_hotkey_mirror_trace::resolved_log_file().to_string_lossy(),
            "note": "grep HOTKEY_TRACE or Event: for A/B machine diff",
        }),
    );
    let _ = app.global_shortcut().unregister_all();

    const CANDIDATES: &[&str] = &[
        "ctrl+alt+x",
        "alt+q",
        "ctrl+shift+space",
        "alt+space",
    ];
    let mut registered = false;
    for combo in CANDIDATES {
        let r = app.global_shortcut().on_shortcut(*combo, |app, shortcut, event| {
            let state_s = match event.state {
                ShortcutState::Pressed => "Pressed",
                ShortcutState::Released => "Released",
            };
            let shortcut_hint = format!("{:?}", shortcut);
            omni_hotkey_mirror_trace::trace_raw_hotkey(&shortcut_hint, state_s);
            if event.state != ShortcutState::Pressed {
                return;
            }
            omni_hotkey_mirror_trace::trace_signal_received_by_rust(app, &shortcut_hint);
            toggle_chat_omni(app);
        });
        match &r {
            Ok(()) => {
                omni_hotkey_mirror_trace::trace_registration(combo, true, None);
                eprintln!(
                    "[Omni] 全局快捷键已注册: {}（前者被占用时会自动尝试列表中的下一个）",
                    combo
                );
                registered = true;
                break;
            }
            Err(e) => {
                omni_hotkey_mirror_trace::trace_registration(combo, false, Some(&e.to_string()));
                eprintln!("[Omni] 跳过 {}: {}", combo, e);
            }
        }
    }
    if !registered {
        omni_hotkey_mirror_trace::trace(
            "HOTKEY_REGISTER_ALL_FAILED",
            serde_json::json!({ "note": "no_candidate_registered" }),
        );
        eprintln!(
            "[Omni] 未能注册全局快捷键，请使用托盘图标左键打开 Omni 条，或释放 Alt+Space 后再试。"
        );
    }
}

/// 释放 L3 Sidecar 并退出进程（托盘「退出」与 `app_exit` 命令共用）。
fn shutdown_application(app: &tauri::AppHandle) {
    if let Some(l3) = app.try_state::<std::sync::Arc<l3_spawn::L3Handle>>() {
        l3.kill();
    }
    app.exit(0);
}

/// 旧版热更新曾把 NSIS 安装包复制为 `jachin-desktop.exe`，导致桌面快捷方式打开安装向导而非应用。
#[cfg(windows)]
fn guard_main_exe_not_nsis_installer_stub() {
    let Ok(exe) = std::env::current_exe() else {
        return;
    };
    let Ok(true) = updater_common::sniff_file_looks_like_windows_nsis_installer_package(&exe) else {
        return;
    };
    const MB_OK: u32 = 0x0000_0000;
    const MB_ICONERROR: u32 = 0x0000_0010;
    fn to_wide(s: &str) -> Vec<u16> {
        s.encode_utf16().chain(std::iter::once(0)).collect()
    }
    let title = "Jachin Desktop — 主程序文件异常";
    let body = "检测到当前程序文件实际是 NSIS 安装向导，通常由旧版热更新误把「…-setup.exe」覆盖成 jachin-desktop.exe 导致。\n\n\
请：使用官网下载的安装包重新运行安装（覆盖安装即可），或先卸载再从安装包安装。\n\n\
说明：安装包与安装后的主程序不是同一个文件；快捷方式应指向安装目录里的 jachin-desktop.exe。\n\n\
新版热更新已修复，不会再把安装包覆盖为主程序。";
    let t = to_wide(title);
    let b = to_wide(body);
    unsafe {
        MessageBoxW(std::ptr::null_mut(), b.as_ptr(), t.as_ptr(), MB_OK | MB_ICONERROR);
    }
    std::process::exit(86);
}

fn main() {
    #[cfg(windows)]
    guard_main_exe_not_nsis_installer_stub();

    // 启动时生成策略（用户覆盖 > 自动检测），并打印决策来源
    let profile = kernel::HardwareProfile::detect();
    let settings = config::UserSettings::load();
    let _config = kernel::generate_policy(profile, &settings);

    let mut builder = tauri::Builder::default();

    // 必须尽量靠前注册：阻止多开 exe；再次启动时唤起已有实例的主窗口
    #[cfg(desktop)]
    {
        builder = builder.plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            let ah = app.clone();
            let ah_focus = ah.clone();
            let _ = ah.run_on_main_thread(move || {
                if let Some(w) = ah_focus.get_webview_window("main") {
                    let _ = w.show();
                    let _ = w.unminimize();
                    let _ = w.set_focus();
                }
            });
        }));
    }

    builder
        .plugin(tauri_plugin_shell::init())
        .plugin(
            tauri_plugin_window_state::Builder::default()
                // sprite：勿自动恢复显示。chat：勿用持久化尺寸覆盖 Esc 陪伴圆（否则会立刻弹回大窗）
                .with_denylist(&["sprite", "chat", "notification"])
                .build(),
        )
        .plugin(tauri_plugin_notification::init())
        .plugin({
            let mut b = tauri_plugin_updater::Builder::new();
            if let Some(tok) = nexus_config::updater_bearer_token() {
                b = b
                    .header("Authorization", format!("Bearer {}", tok))
                    .expect("Authorization header value");
            }
            b.build()
        })
        .plugin(GlobalShortcutBuilder::new().build())
        .invoke_handler(tauri::generate_handler![
            commands::settings::get_current_config,
            commands::settings::get_user_settings,
            commands::settings::update_user_settings,
            commands::settings::get_desktop_ui_lang,
            commands::settings::set_desktop_ui_lang,
            commands::native_fs_policy::native_fs_policy_get,
            commands::native_fs_policy::native_fs_policy_set,
            commands::pairing::is_gateway_paired,
            commands::pairing::read_l2_gateway_config,
            commands::pairing::read_l2_gateway_url,
            commands::pairing::write_l2_gateway_config,
            commands::pairing::gateway_connect,
            commands::pairing::is_l3_engine_ready,
            commands::pairing::set_use_local_mode,
            commands::skill_sync::perform_startup_sync,
            commands::skill_sync::is_skill_sync_in_progress,
            commands::skill_sync::uninstall_skill,
            stt_start_wake_listener,
            stt_stop_wake_listener,
            stt_wake_listener_running,
            stt_emit_wake_up,
            invoke_backend,
            control_device,
            get_system_info,
            app_exit,
            spawn_hot_update_and_exit,
            spawn_hot_update_prepare,
            apply_staged_hot_update_and_exit,
            get_system_stats,
            tts_self_check,
            tts_has_model,
            tts_ensure_model,
            tts_speak,
            get_privacy_mode,
            get_eagle_eye_mode,
            get_hibernate_mode,
            show_chat_window,
            hide_chat_window,
            desktop_diag_log,
            is_chat_companion_mode,
            companion_peek,
            companion_reveal,
            companion_set_dock_position,
            expand_chat_window_for_skill_canvas,
            restore_chat_window_after_skill_canvas_rust,
            jachin_sentry_notify,
            jachin_sentry_notify_dismiss,
            schedule_jachin_reminder,
            cancel_jachin_reminder,
            list_jachin_reminders,
            jachin_inbox_list,
            jachin_inbox_mark_read,
            jachin_inbox_mark_all_read,
            jachin_expand_main_from_notification,
            show_console_window,
            quick_action_privacy_mode,
            quick_action_clear_memory,
            quick_action_eagle_eye,
            quick_action_hibernate,
            handle_device_command,
            launch_pmo_copilot_script,
            commands::pmo_run::get_pmo_copilot_run_status,
            commands::pmo_run::stop_pmo_copilot_run,
            commands::pmo_config::read_pmo_skill_config,
            commands::pmo_config::write_pmo_skill_config,
            commands::pmo_config::open_pmo_skill_config_dir,
            commands::im_channels_config::read_im_channels_config,
            commands::im_channels_config::write_im_channels_config,
            commands::im_channels_config::open_im_channels_config_dir,
            #[cfg(feature = "ambient")]
            stt::commands::start_voice_capture,
            #[cfg(feature = "ambient")]
            stt::commands::stop_voice_capture,
            #[cfg(feature = "ambient")]
            stt::commands::is_voice_capture_running,
            #[cfg(not(feature = "ambient"))]
            stt_voice_stub::start_voice_capture,
            #[cfg(not(feature = "ambient"))]
            stt_voice_stub::stop_voice_capture,
            #[cfg(not(feature = "ambient"))]
            stt_voice_stub::is_voice_capture_running,
            updater_debug_log::updater_debug_append,
        ])
        .setup(|app| {
            updater_debug_log::log_startup_rust(&app.package_info().version.to_string());
            nexus_config::ensure_default_nexus_config_from_example(app.handle());
            app.manage(std::sync::Arc::new(commands::pmo_run::PmoRunTracker::new()));

            // L3 引擎生命周期：静默启动 l3_node Sidecar（--ws-only），Ctrl+C 时 kill 释放端口
            match l3_spawn::spawn_l3_node(&*app) {
                Ok(child) => {
                    let l3 = std::sync::Arc::new(l3_spawn::L3Handle::new(child));
                    l3_spawn::register_ctrlc_kill(&l3);
                    app.manage(l3);
                    println!("[L3] 引擎已启动 ws://127.0.0.1:18981");
                }
                Err(e) if l3_spawn::is_skip_l3_auto_spawn(&e) => {
                    println!(
                        "[L3] 已跳过 Sidecar 自动启动（JACHIN_SKIP_L3_SPAWN=1）。若已用 start-layer3.ps1 等同控制台运行 python -m l3_node，此为预期，无需再执行 run_l3.ps1。"
                    );
                }
                Err(e) => {
                    let msg = format!("[L3] 侧车启动失败: {}。请检查安装目录 bin\\l3_node-*.exe、.env 与 logs\\l3_debug.log；调试可双击 run_l3_console.bat", e);
                    eprintln!("{}", msg);
                    l3_spawn::write_l3_debug(&msg);
                    // 不阻塞启动，前端 useSensoryWebSocket 会显示未连接
                }
            }

            // 初始化设备注册
            let device_id = format!("desktop-{}", whoami::fallible::hostname().unwrap_or_else(|_| "unknown".to_string()));
            let registry = Arc::new(Mutex::new(DeviceRegistry::new(device_id.clone())));
            
            // 存储 registry 到应用状态（在 setup 中）
            app.manage(registry.clone());

            let reminders = Arc::new(reminder_scheduler::ReminderService::new(app.app_handle().clone()));
            reminder_scheduler::ReminderService::spawn_tick_loop(reminders.clone());
            app.manage(reminders.clone());

            #[cfg(feature = "ambient")]
            app.manage(stt::SttState::new());
            
            // 启动 Pub/Sub HTTP 服务器（用于接收 Dapr 推送的命令）
            let app_handle_clone = app.app_handle().clone();
            let device_id_clone = device_id.clone();
            let pubsub_port = 8002; // 桌面客户端的应用端口
            let reminders_pubsub = reminders.clone();
            
            // 在后台任务中启动 Pub/Sub 服务器
            tauri::async_runtime::spawn(async move {
                tokio::time::sleep(tokio::time::Duration::from_secs(1)).await;
                
                // 启动 Pub/Sub 服务器
                if let Err(e) = start_pubsub_server(
                    app_handle_clone.clone(),
                    device_id_clone.clone(),
                    pubsub_port,
                    reminders_pubsub.clone(),
                )
                .await
                {
                    eprintln!("[PubSub] Failed to start Pub/Sub server: {}", e);
                } else {
                    println!("[PubSub] Pub/Sub server started on port {}", pubsub_port);
                }
            });
            
            // 在后台任务中注册设备（带重试，后端可能尚未就绪）
            let registry_clone = registry.clone();
            let device_id_clone_for_heartbeat = device_id.clone();
            tauri::async_runtime::spawn(async move {
                tokio::time::sleep(tokio::time::Duration::from_secs(2)).await;
                let reg = registry_clone.lock().await;
                let mut announced = false;
                for attempt in 1..=3 {
                    if reg.announce().await.is_ok() {
                        println!("[DeviceRegistry] Device registered successfully: {}", device_id_clone_for_heartbeat);
                        announced = true;
                        break;
                    }
                    if attempt < 3 {
                        tokio::time::sleep(tokio::time::Duration::from_secs(5)).await;
                    }
                }
                if !announced {
                    eprintln!("[DeviceRegistry] Backend unavailable - device registry disabled (start backend to enable)");
                }
                reg.start_heartbeat_loop();
            });
            
            // 监听设备命令事件（从 Pub/Sub 服务器接收）
            let app_handle_for_events = app.app_handle().clone();
            let registry_for_events = registry.clone();
            let device_id_for_events = device_id.clone();
            
            let app_handle_for_listen = app_handle_for_events.clone();
            let _ = app_handle_for_listen.listen("device-command", move |event| {
                // Tauri 事件 payload 是 JSON 字符串
                let command_json = event.payload();
                // 解析 JSON 字符串为 DeviceCommand
                if let Ok(command) = serde_json::from_str::<DeviceCommand>(command_json) {
                    let app_handle = app_handle_for_events.clone();
                    let registry = registry_for_events.clone();
                    let device_id = device_id_for_events.clone();
                    
                        // 在异步任务中处理命令
                        tauri::async_runtime::spawn(async move {
                        if command.target_device_id != device_id {
                            return;
                        }
                        
                        let mut response_status = "success";
                        let mut response_result = Some(json!({"success": true}));
                        let mut response_error = None;
                        
                        // 根据能力名称执行相应操作
                        match command.capability_name.as_str() {
                            "notification.show" => {
                                let title = command.params["title"].as_str().unwrap_or("通知");
                                let message = command.params["message"].as_str().unwrap_or("");
                                
                                // 使用 Tauri 通知插件显示通知
                                if let Err(e) = app_handle.notification()
                                    .builder()
                                    .title(title)
                                    .body(message)
                                    .show()
                                {
                                    eprintln!("[Notification] Failed to show notification: {}", e);
                                }
                            }
                            "window.show" => {
                                let window_name = command.params["window_name"].as_str().unwrap_or("sprite");
                                if let Some(window) = app_handle.get_webview_window(window_name) {
                                    if let Err(e) = window.show() {
                                        response_status = "error";
                                        response_error = Some(format!("Failed to show window: {}", e));
                                        response_result = None;
                                    }
                                } else {
                                    response_status = "error";
                                    response_error = Some(format!("Window not found: {}", window_name));
                                    response_result = None;
                                }
                            }
                            "window.hide" => {
                                let window_name = command.params["window_name"].as_str().unwrap_or("sprite");
                                if let Some(window) = app_handle.get_webview_window(window_name) {
                                    if let Err(e) = window.hide() {
                                        response_status = "error";
                                        response_error = Some(format!("Failed to hide window: {}", e));
                                        response_result = None;
                                    }
                                } else {
                                    response_status = "error";
                                    response_error = Some(format!("Window not found: {}", window_name));
                                    response_result = None;
                                }
                            }
                            "sprite.set_state" => {
                                let state = command.params["state"].as_str().unwrap_or("idle");
                                // 发送状态更新事件到前端
                                let _ = app_handle.emit("sprite-state-change", json!({
                                    "state": state
                                }));
                            }
                            _ => {
                                response_status = "error";
                                response_error = Some(format!("Unknown capability: {}", command.capability_name));
                                response_result = None;
                            }
                        }
                        
                        // 发送响应
                        let reg = registry.lock().await;
                        let response = DeviceResponse {
                            command_id: command.command_id.clone(),
                            device_id: device_id.clone(),
                            status: response_status.to_string(),
                            result: response_result,
                            error: response_error,
                            timestamp: device_registry::current_timestamp(),
                        };
                        
                        if let Err(e) = reg.send_response(response).await {
                            eprintln!("[DeviceRegistry] Failed to send response: {}", e);
                        }
                    });
                }
            });
            
            // 系统托盘：左键切换 Omni；右键菜单可打开交互界面 / 控制台 / 退出
            if let Some(icon) = app.default_window_icon() {
                let tray_build = (|| -> Result<(), tauri::Error> {
                    let item_chat = MenuItem::with_id(
                        app,
                        "tray_chat",
                        "打开交互界面",
                        true,
                        None::<&str>,
                    )?;
                    let item_console = MenuItem::with_id(
                        app,
                        "tray_console",
                        "打开控制台",
                        true,
                        None::<&str>,
                    )?;
                    let item_quit =
                        MenuItem::with_id(app, "tray_quit", "退出 Jachin", true, None::<&str>)?;
                    let tray_menu = MenuBuilder::new(app)
                        .item(&item_chat)
                        .item(&item_console)
                        .item(&item_quit)
                        .build()?;

                    let _tray = TrayIconBuilder::new()
                        .icon(icon.clone())
                        .tooltip(
                            "Jachin · 左键切换 Omni · 右键可打开交互界面或控制台 · 快捷键 Ctrl+Alt+X 等",
                        )
                        .menu(&tray_menu)
                        .on_menu_event(|app, event| {
                            match event.id.as_ref() {
                                "tray_chat" => {
                                    toggle_chat_omni(app.app_handle());
                                }
                                "tray_console" => {
                                    let ah = app.app_handle().clone();
                                    let ah_focus = ah.clone();
                                    let _ = ah.run_on_main_thread(move || {
                                        if let Some(w) = ah_focus.get_webview_window("main") {
                                            let _ = w.show();
                                            let _ = w.unminimize();
                                            let _ = w.set_focus();
                                        }
                                    });
                                }
                                "tray_quit" => shutdown_application(app.app_handle()),
                                _ => {}
                            }
                        })
                        .on_tray_icon_event(|tray, event| {
                            if let TrayIconEvent::Click {
                                button: tauri::tray::MouseButton::Left,
                                ..
                            } = event
                            {
                                toggle_chat_omni(tray.app_handle());
                            }
                        })
                        .build(app)?;
                    Ok(())
                })();

                if let Err(e) = tray_build {
                    eprintln!("[Tray] 创建失败（无托盘菜单）: {}", e);
                }
            }

            // 桌面精灵：固定初始位置；若用户从控制台再次打开，仍从该锚点旁弹出 chat
            if let Some(sprite_window) = app.get_webview_window("sprite") {
                let _ = sprite_window.set_position(tauri::LogicalPosition::new(100.0, 100.0));
                let _ = sprite_window.hide();
            }

            register_omni_hotkeys(app.handle());

            // 启动时默认同时显示：控制台（main）与 Omni（chat）。关闭/最小化由用户在窗口内操作；托盘与 Alt+Shift+Space 仍可唤回。
            // 自动化/无头场景可设 JACHIN_SKIP_STARTUP_WINDOWS=1 恢复旧行为（不自动 show）。
            let skip_startup_ui = std::env::var("JACHIN_SKIP_STARTUP_WINDOWS")
                .map(|v| {
                    let v = v.trim();
                    v == "1" || v.eq_ignore_ascii_case("true") || v.eq_ignore_ascii_case("yes")
                })
                .unwrap_or(false);
            if !skip_startup_ui {
                let ah = app.handle().clone();
                if let Some(w) = ah.get_webview_window("main") {
                    let _ = w.show();
                    let _ = w.unminimize();
                }
                show_omni_chat_window(&ah);
                eprintln!(
                    "[Desktop] 启动时已显示控制台与 Omni（JACHIN_SKIP_STARTUP_WINDOWS=1 可跳过）"
                );
            }

            Ok(())
        })
        .on_window_event(|window, event| {
            // main 窗口点击关闭时：不销毁窗口，改为隐藏，便于从 Chat/托盘再次打开
            if window.label() == "main" {
                if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                    api.prevent_close();
                    EAGLE_EYE_ON.store(false, Ordering::Relaxed);
                    let _ = window.hide();
                }
                return;
            }
            // chat：原生最小化进任务栏时不会走 hide_chat_window；失焦后短暂延迟再检测 is_minimized，转为陪伴圆
            if window.label() == "chat" {
                if let tauri::WindowEvent::Focused(false) = event {
                    let app = window.app_handle().clone();
                    tauri::async_runtime::spawn(async move {
                        tokio::time::sleep(std::time::Duration::from_millis(64)).await;
                        let app2 = app.clone();
                        let _ = app.run_on_main_thread(move || {
                            convert_os_minimize_to_companion_if_needed(&app2);
                        });
                    });
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

/// 启动唤醒词监听（模式 B：Wake-Up）。检测到配置的唤醒词/名字时发出 WAKE_UP 事件。
/// wake_word: 可选，为空时从 UserSettings 读取，再空则用 "Jachin"
#[tauri::command]
async fn stt_start_wake_listener(
    app: tauri::AppHandle,
    wake_word: Option<String>,
) -> Result<(), String> {
    let word = wake_word.or_else(|| {
        crate::config::UserSettings::load()
            .wake_word
            .filter(|s| !s.trim().is_empty())
    });
    stt::WakeWordDetector::start(app, word);
    Ok(())
}

/// 停止唤醒词监听
#[tauri::command]
fn stt_stop_wake_listener() -> Result<(), String> {
    stt::WakeWordDetector::stop();
    Ok(())
}

/// 是否正在监听唤醒词
#[tauri::command]
fn stt_wake_listener_running() -> Result<bool, String> {
    Ok(stt::WakeWordDetector::is_running())
}

/// 模拟唤醒（测试用）：向前端发送 WAKE_UP 事件
#[tauri::command]
async fn stt_emit_wake_up(app: tauri::AppHandle) -> Result<(), String> {
    stt::WakeWordDetector::emit_wake_up(&app);
    Ok(())
}

/// 右侧 Skill 画布激活时：保证 Omni 窗口总宽度 ≥ 双栏最小值，并前置显示/聚焦（无需用户手动拉宽）
#[tauri::command]
async fn expand_chat_window_for_skill_canvas(app: tauri::AppHandle) -> Result<(), String> {
    let app_handle = app.clone();
    app
        .run_on_main_thread(move || {
            let Some(chat) = app_handle.get_webview_window("chat") else {
                return;
            };
            let Ok(sz) = chat.outer_size() else {
                return;
            };
            if let Ok(mut g) = CHAT_WIDTH_BEFORE_SKILL_CANVAS.lock() {
                if g.is_none() {
                    *g = Some(sz.width);
                }
            }
            let factor = chat.scale_factor().unwrap_or(1.0);
            let min_w_phys =
                (CHAT_SKILL_CANVAS_MIN_TOTAL_LOGICAL * factor).ceil().max(1.0) as u32;
            let new_w = sz.width.max(min_w_phys);
            // 始终 set_size：避免「已达标」时系统未重排导致右栏仍要手拖
            let _ = chat.set_size(Size::Physical(PhysicalSize::new(new_w, sz.height)));
            let _ = position_chat_omni_bar(&app_handle);
            let _ = chat.show();
            let _ = chat.set_focus();
        })
        .map_err(|e| e.to_string())?;
    Ok(())
}

/// Skill 画布关闭后恢复扩窗前宽度
#[tauri::command]
async fn restore_chat_window_after_skill_canvas_rust(app: tauri::AppHandle) -> Result<(), String> {
    let app_handle = app.clone();
    app
        .run_on_main_thread(move || {
            let prev = CHAT_WIDTH_BEFORE_SKILL_CANVAS
                .lock()
                .ok()
                .and_then(|mut g| g.take());
            let Some(w) = prev else {
                return;
            };
            let Some(chat) = app_handle.get_webview_window("chat") else {
                return;
            };
            if let Ok(sz) = chat.outer_size() {
                let _ = chat.set_size(Size::Physical(PhysicalSize::new(w, sz.height)));
                let _ = position_chat_omni_bar(&app_handle);
            }
        })
        .map_err(|e| e.to_string())?;
    Ok(())
}

/// 主线程：将 Omni 聊天窗置于条形态并显示、聚焦（与 `show_chat_window` 命令一致）。
fn show_omni_chat_window(app: &tauri::AppHandle) {
    if let Some(chat_window) = app.get_webview_window("chat") {
        if CHAT_COMPANION_MODE.load(Ordering::Relaxed) {
            let _ = restore_chat_full_omni(app);
        } else if !chat_window.is_visible().unwrap_or(false) {
            let _ = chat_window.set_min_size(Some(Size::Physical(PhysicalSize::new(
                CHAT_MIN_WIDTH,
                CHAT_MIN_HEIGHT,
            ))));
            let (w, h) = resolve_chat_omni_outer_size(&chat_window);
            let _ = chat_window.set_size(Size::Physical(PhysicalSize::new(w, h)));
            let _ = position_chat_omni_bar(app);
        } else {
            let _ = position_chat_omni_bar(app);
        }
        let _ = chat_window.show();
        let _ = chat_window.set_focus();
    }
}

/// 右下角哨兵通知：贴在当前显示器 **工作区** 右下（不压 Windows 任务栏；Tauri 2.5 Monitor 无 work_area，用底边预留）。
fn position_notification_bottom_right(app: &tauri::AppHandle) -> Result<(), String> {
    let Some(win) = app.get_webview_window("notification") else {
        return Err("notification window missing".into());
    };
    let monitor = app
        .get_webview_window("chat")
        .as_ref()
        .and_then(|c| c.current_monitor().ok().flatten())
        .or_else(|| win.current_monitor().ok().flatten())
        .ok_or_else(|| "no monitor".to_string())?;
    let mon_pos = monitor.position();
    let mon_size = monitor.size();
    let factor = win.scale_factor().unwrap_or(1.0);
    let nw = (380.0_f64 * factor).round().clamp(280.0, 800.0) as u32;
    let nh = (88.0_f64 * factor).round().clamp(64.0, 200.0) as u32;
    let margin = (10.0_f64 * factor).round() as i32;
    // 横置任务栏典型高度（逻辑像素量级）→ 物理像素，从屏幕底边上移，避免盖住任务栏/系统托盘
    #[cfg(windows)]
    let taskbar_reserve = (48.0_f64 * factor).round().max(32.0) as i32;
    #[cfg(not(windows))]
    let taskbar_reserve = (40.0_f64 * factor).round().max(24.0) as i32;
    let screen_bottom = mon_pos.y + mon_size.height as i32;
    let x = mon_pos.x + mon_size.width as i32 - nw as i32 - margin;
    let y = screen_bottom - taskbar_reserve - margin - nh as i32;
    win.set_size(Size::Physical(PhysicalSize::new(nw, nh)))
        .map_err(|e| e.to_string())?;
    win.set_position(PhysicalPosition::new(x, y))
        .map_err(|e| e.to_string())?;
    Ok(())
}

/// 主线程：右下角哨兵通知展示逻辑（`jachin_sentry_notify` 与定时提醒共用）。
pub(crate) fn show_sentry_toast_inner(
    app_handle: &tauri::AppHandle,
    title: String,
    body: String,
    log_prefix: &str,
) {
    let payload = serde_json::json!({ "title": title, "body": body });
    let snap = omni_surface_debug_snapshot(app_handle);
    let Some(win) = app_handle.get_webview_window("notification") else {
        eprintln!("[Sentry] notification webview missing");
        l3_spawn::write_jachin_shared_l3_debug(
            &format!("{log_prefix}_main_thread"),
            "FAIL notification_webview_missing",
        );
        return;
    };
    let pos_res = position_notification_bottom_right(app_handle);
    if let Err(ref e) = pos_res {
        eprintln!("[Sentry] position failed: {e}");
    }
    let emit_res = app_handle.emit_to(
        EventTarget::webview_window("notification"),
        "jachin-notification-show",
        payload,
    );
    if let Err(ref e) = emit_res {
        eprintln!("[Sentry] emit_to notification failed: {e}");
    }
    let show_res = win.show();
    if let Err(ref e) = show_res {
        eprintln!("[Sentry] notification show failed: {e}");
    }

    // 控制台消息中心：持久化 + 广播，列表与 Omni 哨兵同源
    match inbox_store::append_sentry_inbox(title.clone(), body.clone()) {
        Ok(_) => {
            let _ = app_handle.emit("jachin-inbox-updated", serde_json::json!({}));
        }
        Err(e) => eprintln!("[Sentry] inbox append failed: {e}"),
    }

    let notif_vis_after = win.is_visible().unwrap_or(false);
    l3_spawn::write_jachin_shared_l3_debug(
        &format!("{log_prefix}_main_thread_done"),
        &format!(
            "{} position_ok={} emit_ok={} show_ok={} notif_visible_after={} pos_err={:?} emit_err={:?} show_err={:?}",
            snap,
            pos_res.is_ok(),
            emit_res.is_ok(),
            show_res.is_ok(),
            notif_vis_after,
            pos_res.err(),
            emit_res.err(),
            show_res.err(),
        ),
    );
}

/// Omni 最小化 / 陪伴圆 / 完全隐藏时：透明子窗口右下角通知（非系统 Notification）
#[tauri::command]
async fn jachin_sentry_notify(app: tauri::AppHandle, title: String, body: String) -> Result<(), String> {
    let title_preview: String = title.chars().take(100).collect();
    let body_preview: String = body.chars().take(160).collect();
    l3_spawn::write_jachin_shared_l3_debug(
        "sentry_notify_invoke",
        &format!(
            "title_len={} body_len={} title_preview={:?} body_preview={:?} pre_sleep {}",
            title.len(),
            body.len(),
            title_preview,
            body_preview,
            omni_surface_debug_snapshot(&app)
        ),
    );
    tokio::time::sleep(std::time::Duration::from_millis(60)).await;
    let app_handle = app.clone();
    let title_m = title;
    let body_m = body;
    app
        .run_on_main_thread(move || {
            show_sentry_toast_inner(&app_handle, title_m, body_m, "sentry_notify");
        })
        .map_err(|e| e.to_string())?;
    Ok(())
}

/// 注册定时提醒（持久化，到点右下角哨兵）。
#[tauri::command]
fn schedule_jachin_reminder(
    reminders: tauri::State<'_, Arc<reminder_scheduler::ReminderService>>,
    fire_at_unix_ms: u64,
    title: String,
    body: String,
) -> Result<String, String> {
    reminders.add(fire_at_unix_ms, title, body)
}

#[tauri::command]
fn cancel_jachin_reminder(
    reminders: tauri::State<'_, Arc<reminder_scheduler::ReminderService>>,
    id: String,
) -> Result<(), String> {
    reminders.cancel(&id)
}

#[tauri::command]
fn list_jachin_reminders(
    reminders: tauri::State<'_, Arc<reminder_scheduler::ReminderService>>,
) -> Result<Vec<reminder_scheduler::Reminder>, String> {
    reminders.list()
}

/// L3 控制台消息中心：与右下角哨兵 toast 同源的持久化列表
#[tauri::command]
fn jachin_inbox_list() -> Result<Vec<inbox_store::InboxItem>, String> {
    inbox_store::list_inbox()
}

#[tauri::command]
fn jachin_inbox_mark_read(app: tauri::AppHandle, id: String) -> Result<(), String> {
    inbox_store::mark_inbox_read(id)?;
    let _ = app.emit("jachin-inbox-updated", serde_json::json!({}));
    Ok(())
}

#[tauri::command]
fn jachin_inbox_mark_all_read(app: tauri::AppHandle) -> Result<(), String> {
    inbox_store::mark_all_inbox_read()?;
    let _ = app.emit("jachin-inbox-updated", serde_json::json!({}));
    Ok(())
}

#[tauri::command]
async fn jachin_sentry_notify_dismiss(app: tauri::AppHandle) -> Result<(), String> {
    l3_spawn::write_jachin_shared_l3_debug(
        "sentry_notify_dismiss",
        &omni_surface_debug_snapshot(&app),
    );
    let app_handle = app.clone();
    app
        .run_on_main_thread(move || {
            if let Some(win) = app_handle.get_webview_window("notification") {
                let _ = win.hide();
            }
        })
        .map_err(|e| e.to_string())?;
    Ok(())
}

/// 点击哨兵条：展开 Omni 并收起通知
#[tauri::command]
async fn jachin_expand_main_from_notification(app: tauri::AppHandle) -> Result<(), String> {
    show_chat_window(app.clone()).await?;
    jachin_sentry_notify_dismiss(app).await?;
    Ok(())
}

/// 显示对话窗口（Omni 条）：屏幕居中偏下，不依赖桌面精灵位置
#[tauri::command]
async fn show_chat_window(app: tauri::AppHandle) -> Result<(), String> {
    l3_spawn::write_jachin_shared_l3_debug(
        "show_chat_window_cmd",
        &omni_surface_debug_snapshot(&app),
    );
    let app_handle = app.clone();
    app.run_on_main_thread(move || {
        show_omni_chat_window(&app_handle);
        l3_spawn::write_jachin_shared_l3_debug(
            "show_chat_window_after",
            &omni_surface_debug_snapshot(&app_handle),
        );
    })
    .map_err(|e| e.to_string())?;
    Ok(())
}

/// Esc：先缩为右下角陪伴圆；再按一次则彻底隐藏。必须等主线程执行完再返回，前端才能立刻切 UI。
#[tauri::command]
async fn hide_chat_window(app: tauri::AppHandle) -> Result<HideChatWindowResult, String> {
    l3_spawn::write_jachin_shared_l3_debug(
        "hide_chat_window_cmd",
        &format!(
            "before {} already_companion={}",
            omni_surface_debug_snapshot(&app),
            CHAT_COMPANION_MODE.load(Ordering::Relaxed)
        ),
    );
    let (tx, rx) = tokio::sync::oneshot::channel::<Result<HideChatWindowResult, String>>();
    let app_handle = app.clone();
    app.run_on_main_thread(move || {
        let out = if CHAT_COMPANION_MODE.load(Ordering::Relaxed) {
            hide_chat_fully_reset(&app_handle).map(|_| HideChatWindowResult {
                companion: false,
                fully_hidden: true,
            })
        } else {
            minimize_chat_to_companion(&app_handle).map(|_| HideChatWindowResult {
                companion: true,
                fully_hidden: false,
            })
        };
        match &out {
            Ok(ok) => {
                l3_spawn::write_jachin_shared_l3_debug(
                    "hide_chat_window_result",
                    &format!(
                        "companion={} fully_hidden={} {}",
                        ok.companion,
                        ok.fully_hidden,
                        omni_surface_debug_snapshot(&app_handle)
                    ),
                );
            }
            Err(e) => {
                l3_spawn::write_jachin_shared_l3_debug(
                    "hide_chat_window_err",
                    &format!("err={e} {}", omni_surface_debug_snapshot(&app_handle)),
                );
            }
        }
        let _ = tx.send(out);
    })
    .map_err(|e| e.to_string())?;
    rx.await
        .map_err(|_| "hide_chat_window: 主线程未响应".to_string())?
}

/// 前端写入 `~/.jachin/l3_debug.log`（与 L3 共用）：哨兵预判、Web 层陪伴态等。
#[tauri::command]
fn desktop_diag_log(category: String, message: String) {
    let sanitized = message.chars().take(4000).collect::<String>();
    l3_spawn::write_jachin_shared_l3_debug(&category, &sanitized);
}

#[tauri::command]
fn is_chat_companion_mode() -> bool {
    CHAT_COMPANION_MODE.load(Ordering::Relaxed)
}

/// 退出应用（先 kill L3 释放端口）
#[tauri::command]
fn app_exit(app: tauri::AppHandle) -> Result<(), String> {
    shutdown_application(&app);
    Ok(())
}

fn resolve_spawn_hot_update_payload(
    download_url: Option<String>,
    signature: Option<String>,
    new_version: Option<String>,
    payload: Option<updater_spawn::SpawnHotUpdatePayload>,
) -> Result<updater_spawn::SpawnHotUpdatePayload, String> {
    if let Some(p) = payload {
        Ok(p)
    } else if let (Some(d), Some(s), Some(n)) = (download_url, signature, new_version) {
        Ok(updater_spawn::SpawnHotUpdatePayload {
            download_url: d,
            signature: s,
            new_version: n,
        })
    } else {
        Err(
            "缺少参数。请传 payload: { downloadUrl, signature, newVersion }，或顶层 downloadUrl、signature、newVersion。"
                .into(),
        )
    }
}

/// 启动独立热更新助手进程并退出本应用（助手下载、校验、检查用户数据后替换 exe 并启动新版本）。
///
/// 兼容两种 IPC 形态（避免已发布的旧 exe 仅识别 `payload`、而新前端只发扁平字段导致对不齐）：
/// - 推荐：`payload: { downloadUrl, signature, newVersion }`（与 `gateway_connect` 的 `input` 风格一致）
/// - 亦可：顶层 `downloadUrl`、`signature`、`newVersion`
#[tauri::command]
fn spawn_hot_update_and_exit(
    app: tauri::AppHandle,
    download_url: Option<String>,
    signature: Option<String>,
    new_version: Option<String>,
    payload: Option<updater_spawn::SpawnHotUpdatePayload>,
) -> Result<(), String> {
    let job = resolve_spawn_hot_update_payload(download_url, signature, new_version, payload)
        .map_err(|e| format!("spawn_hot_update_and_exit: {e}"))?;
    updater_spawn::spawn_hot_update_job(&app, job)?;
    shutdown_application(&app);
    Ok(())
}

/// 仅下载并校验新版本到临时目录，**不退出主程序**；完成后向前端派发 `hot-update-prepare-result`。
#[tauri::command]
fn spawn_hot_update_prepare(
    app: tauri::AppHandle,
    download_url: Option<String>,
    signature: Option<String>,
    new_version: Option<String>,
    payload: Option<updater_spawn::SpawnHotUpdatePayload>,
) -> Result<(), String> {
    let job = resolve_spawn_hot_update_payload(download_url, signature, new_version, payload)
        .map_err(|e| format!("spawn_hot_update_prepare: {e}"))?;
    updater_spawn::spawn_hot_update_prepare_job(&app, job)?;
    Ok(())
}

/// 用户确认后：启动助手执行「等主进程退出 → 覆盖 exe → 启动新版本」，并**立即退出本应用**。
#[tauri::command]
fn apply_staged_hot_update_and_exit(
    app: tauri::AppHandle,
    staged_new_exe: String,
    new_version: String,
) -> Result<(), String> {
    let staged = std::path::PathBuf::from(staged_new_exe.trim());
    if !staged.is_file() {
        return Err("暂存安装包不存在或已清理，请重新点击「立即更新」下载。".into());
    }
    updater_spawn::spawn_hot_update_apply_job(&app, staged, new_version)?;
    shutdown_application(&app);
    Ok(())
}

/// 打开控制台窗口（设备/技能/监控等完整面板）
#[tauri::command]
async fn show_console_window(app: tauri::AppHandle) -> Result<(), String> {
    let app_handle = app.clone();
    app.run_on_main_thread(move || {
        if let Some(w) = app_handle.get_webview_window("main") {
            let _ = w.show();
            let _ = w.unminimize();
            let _ = w.set_focus();
        }
    })
    .map_err(|e| e.to_string())
}

/// PMO Copilot：后台启动全流程或 INIT（无弹窗）；日志写入 ``logs/pmo_copilot_<ts>.log``。
/// ``init_only=true`` 时等价 ``run_pmo_copilot_skill.py --init``（拉表 + mirror_import）。
#[tauri::command]
async fn launch_pmo_copilot_script(
    init_only: Option<bool>,
    tracker: tauri::State<'_, std::sync::Arc<commands::pmo_run::PmoRunTracker>>,
) -> Result<String, String> {
    let init_only = init_only.unwrap_or(false);
    use std::path::PathBuf;
    use std::process::Command as StdCommand;

    let app_root: PathBuf = l3_spawn::project_root()
        .or_else(l3_spawn::exe_dir)
        .ok_or_else(|| "无法定位应用根目录".to_string())?;

    let sidecar = l3_spawn::portable_l3_sidecar_exe_path().filter(|p| p.exists());
    let script_path = {
        let from_root = app_root.join("scripts").join("run_pmo_copilot_skill.py");
        if from_root.exists() {
            Some(from_root)
        } else {
            l3_spawn::exe_dir()
                .map(|d| d.join("scripts").join("run_pmo_copilot_skill.py"))
                .filter(|p| p.exists())
        }
    };

    enum LaunchMode {
        Sidecar(PathBuf),
        PythonScript(PathBuf),
    }

    let has_l3_source = app_root.join("l3_node").join("__main__.py").exists();

    // 开发机（含 l3_node 源码）：优先 Python 脚本，与 start-layer3.ps1 同源 L3 并存，避免侧车 exe 抢 l3.lock
    let mode = if script_path.is_some() && has_l3_source {
        LaunchMode::PythonScript(script_path.unwrap())
    } else if let Some(sc) = sidecar {
        LaunchMode::Sidecar(sc)
    } else if script_path.is_some() {
        return Err(
            "安装目录缺少 bin/l3_node 侧车，无法用 python 运行 PMO（安装包不含 l3_node 源码）。\
             请重新安装完整包或确认 bin/l3_node-x86_64-pc-windows-msvc.exe 存在。"
                .to_string(),
        );
    } else {
        return Err(
            "找不到 PMO Copilot 入口：请确认安装包含 bin/l3_node 侧车或 scripts/run_pmo_copilot_skill.py"
                .to_string(),
        );
    };

    let launch_label = match (&mode, init_only) {
        (LaunchMode::Sidecar(p), true) => format!("INIT 数据更新 · L3 侧车: {}", p.display()),
        (LaunchMode::Sidecar(p), false) => format!("PMO 全流程 · L3 侧车: {}", p.display()),
        (LaunchMode::PythonScript(p), true) => format!("INIT 数据更新 · Python: {}", p.display()),
        (LaunchMode::PythonScript(p), false) => format!("PMO 全流程 · Python: {}", p.display()),
    };

    #[cfg(windows)]
    {
        use std::fs::OpenOptions;
        use std::io::Write;
        use std::os::windows::process::CommandExt;
        use std::time::{SystemTime, UNIX_EPOCH};

        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        let log_dir = app_root.join("logs");
        std::fs::create_dir_all(&log_dir).map_err(|e| format!("创建 logs 目录失败: {e}"))?;
        let ts = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        let log_path = log_dir.join(format!("pmo_copilot_{ts}.log"));
        let mut log_file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&log_path)
            .map_err(|e| format!("打开 PMO 日志文件失败: {e}"))?;
        let _ = writeln!(
            log_file,
            "[desktop] PMO Copilot 后台启动 pid={} ts={ts} cwd={}",
            std::process::id(),
            cwd = app_root.display()
        );
        let _ = log_file.flush();
        let err_file = log_file
            .try_clone()
            .map_err(|e| format!("打开 PMO 日志文件失败: {e}"))?;

        let root_str = app_root.to_string_lossy();
        let mut cmd = match &mode {
            LaunchMode::Sidecar(sc) => {
                let mut c = StdCommand::new(sc);
                c.arg("--run-pmo-copilot");
                if init_only {
                    c.arg("--init");
                }
                c
            }
            LaunchMode::PythonScript(_) => {
                let mut c = StdCommand::new("python");
                c.arg("-u").arg("scripts/run_pmo_copilot_skill.py");
                if init_only {
                    c.arg("--init");
                }
                c
            }
        };
        let pmo_log_dir = log_dir.join("pmo");
        let _ = std::fs::create_dir_all(&pmo_log_dir);
        cmd.current_dir(&app_root)
            .env("JACHIN_APP_ROOT", root_str.as_ref())
            .env("JACHIN_LOG_DIR", pmo_log_dir.to_string_lossy().as_ref())
            .env("JACHIN_PMO_COPILOT_RUN", "1")
            .env("JACHIN_L3_CONSOLE", "0")
            .env("PYTHONUNBUFFERED", "1")
            .env("PYTHONUTF8", "1")
            .env("JACHIN_L3_DEEP_LOG", "0")
            .env("JACHIN_EXEC_TRACE_STDERR", "0")
            .env("JACHIN_L3_LOG_LITELLM_DETAIL", "0")
            .env("LOG_LEVEL", "WARNING")
            .env("JACHIN_LOG_LEVEL", "WARNING")
            .env("JACHIN_L3_FILE_LOG_COMPACT", "1")
            .stdout(log_file)
            .stderr(err_file)
            .creation_flags(CREATE_NO_WINDOW);
        let child = cmd
            .spawn()
            .map_err(|e| format!("启动 PMO Copilot 失败: {e}"))?;
        let pid = tracker.register_child(child, launch_label.clone())?;

        return Ok(format!(
            "PMO 已在后台运行（PID {pid}）。{}状态见本页指示灯；日志: {} / {}",
            if init_only { "INIT 拉表入库 · " } else { "" },
            log_path.display(),
            pmo_log_dir.join("pmo_l3_debug.log").display()
        ));
    }

    #[cfg(not(windows))]
    {
        #[cfg(target_os = "macos")]
        {
            let root_esc = app_root.to_string_lossy().replace('\'', "\\'");
            let inner = match &mode {
                LaunchMode::Sidecar(sc) => {
                    let sc_esc = sc.to_string_lossy().replace('\'', "\\'");
                    if init_only {
                        format!(
                            "cd '{}' && '{}' --run-pmo-copilot --init",
                            root_esc, sc_esc
                        )
                    } else {
                        format!("cd '{}' && '{}' --run-pmo-copilot", root_esc, sc_esc)
                    }
                }
                LaunchMode::PythonScript(_) => {
                    if init_only {
                        format!(
                            "cd '{}' && python scripts/run_pmo_copilot_skill.py --init",
                            root_esc
                        )
                    } else {
                        format!("cd '{}' && python scripts/run_pmo_copilot_skill.py", root_esc)
                    }
                }
            };
            let apple_script = format!("tell application \"Terminal\" to do script \"{}\"", inner);
            StdCommand::new("osascript")
                .args(["-e", &apple_script])
                .spawn()
                .map_err(|e| format!("启动 PMO Copilot 失败: {e}"))?;
        }
        #[cfg(not(target_os = "macos"))]
        {
            let root_esc = app_root.to_string_lossy().replace('\'', "'\\''");
            let cmd_str = match &mode {
                LaunchMode::Sidecar(sc) => {
                    let sc_esc = sc.to_string_lossy().replace('\'', "'\\''");
                    if init_only {
                        format!(
                            "cd '{}' && '{}' --run-pmo-copilot --init; read -p 'Press Enter to close...'",
                            root_esc, sc_esc
                        )
                    } else {
                        format!(
                            "cd '{}' && '{}' --run-pmo-copilot; read -p 'Press Enter to close...'",
                            root_esc, sc_esc
                        )
                    }
                }
                LaunchMode::PythonScript(_) => {
                    if init_only {
                        format!(
                            "cd '{}' && python scripts/run_pmo_copilot_skill.py --init; read -p 'Press Enter to close...'",
                            root_esc
                        )
                    } else {
                        format!(
                            "cd '{}' && python scripts/run_pmo_copilot_skill.py; read -p 'Press Enter to close...'",
                            root_esc
                        )
                    }
                }
            };
            let launched = StdCommand::new("x-terminal-emulator")
                .args(["-e", "bash", "-c", &cmd_str])
                .spawn()
                .is_ok()
                || StdCommand::new("xterm")
                    .args(["-e", "bash", "-c", &cmd_str])
                    .spawn()
                    .is_ok();
            if !launched {
                return Err(
                    "找不到可用终端模拟器（尝试了 x-terminal-emulator / xterm）".to_string(),
                );
            }
        }

        Ok(format!(
            "已启动（cwd: {}）: {}",
            app_root.to_string_lossy(),
            launch_label
        ))
    }
}

/// 处理设备指令（从 Dapr Pub/Sub 接收）
#[tauri::command]
async fn handle_device_command(
    app: tauri::AppHandle,
    command: DeviceCommand,
    registry: tauri::State<'_, Arc<Mutex<DeviceRegistry>>>,
) -> Result<DeviceResponse, String> {
    let device_id = format!("desktop-{}", whoami::fallible::hostname().unwrap_or_else(|_| "unknown".to_string()));
    
    // 验证目标设备ID
    if command.target_device_id != device_id {
        return Err(format!("Command target mismatch: expected {}, got {}", device_id, command.target_device_id));
    }

    // 根据能力名称执行相应操作
    let result = match command.capability_name.as_str() {
        "notification.show" => {
            // 显示通知
            let title = command.params["title"].as_str().unwrap_or("通知");
            let message = command.params["message"].as_str().unwrap_or("");
            
            // 使用 Tauri 通知插件显示通知
            if let Err(e) = app.notification()
                .builder()
                .title(title)
                .body(message)
                .show()
            {
                eprintln!("[Notification] Failed to show notification: {}", e);
            }
            
            Ok(serde_json::json!({"success": true}))
        }
        "window.show" => {
            let window_name = command.params["window_name"].as_str().unwrap_or("sprite");
            if let Some(window) = app.get_webview_window(window_name) {
                window.show().map_err(|e| e.to_string())?;
                Ok(serde_json::json!({"success": true}))
            } else {
                Err(format!("Window not found: {}", window_name))
            }
        }
        "window.hide" => {
            let window_name = command.params["window_name"].as_str().unwrap_or("sprite");
            if let Some(window) = app.get_webview_window(window_name) {
                window.hide().map_err(|e| e.to_string())?;
                Ok(serde_json::json!({"success": true}))
            } else {
                Err(format!("Window not found: {}", window_name))
            }
        }
        "sprite.set_state" => {
            let state = command.params["state"].as_str().unwrap_or("idle");
            // 通过事件发送到前端，更新 Rive 动画状态
            let _ = app.emit("sprite-state-change", json!({
                "state": state
            }));
            Ok(serde_json::json!({"success": true, "state": state}))
        }
        _ => Err(format!("Unknown capability: {}", command.capability_name)),
    };

    // 构建响应
    let response = match result {
        Ok(data) => DeviceResponse {
            command_id: command.command_id.clone(),
            device_id: device_id.clone(),
            status: "success".to_string(),
            result: Some(data),
            error: None,
            timestamp: device_registry::current_timestamp(),
        },
        Err(e) => DeviceResponse {
            command_id: command.command_id.clone(),
            device_id: device_id.clone(),
            status: "error".to_string(),
            result: None,
            error: Some(e),
            timestamp: device_registry::current_timestamp(),
        },
    };

    // 发送响应到 Dapr Pub/Sub
    let reg = registry.lock().await;
    if let Err(e) = reg.send_response(response.clone()).await {
        eprintln!("[DeviceRegistry] Failed to send response: {}", e);
    }

    Ok(response)
}

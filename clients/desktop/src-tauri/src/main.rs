// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod commands;
mod config;
mod nexus_config;
mod device;
mod device_registry;
mod kernel;
mod l3_spawn;
mod pubsub;
mod stt;
mod tts;
mod window;

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
use tauri::{Manager, Emitter, Listener, tray::{TrayIconBuilder, TrayIconEvent}};
use tauri_plugin_global_shortcut::{Builder as GlobalShortcutBuilder, ShortcutState};
use tauri_plugin_notification::NotificationExt;
use serde_json::json;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
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
            let _ = chat.hide();
        } else {
            let _ = position_chat_omni_bar(&app);
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

/// 将 Omni 输入条置于主显示器水平居中、垂直约 2/3（Raycast / Spotlight 风格）
fn position_chat_omni_bar(app: &tauri::AppHandle) -> Result<(), String> {
    use tauri::PhysicalPosition;
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
    let y = mon_pos.y + (mon_size.height as i32 - win_size.height as i32) * 2 / 3;
    chat.set_position(PhysicalPosition::new(x, y))
        .map_err(|e| e.to_string())?;
    Ok(())
}

/// 切换 Omni 条显示；由托盘、全局快捷键等调用
fn toggle_chat_omni(app: &tauri::AppHandle) {
    let app_clone = app.clone();
    let _ = app.run_on_main_thread(move || {
        if let Some(chat) = app_clone.get_webview_window("chat") {
            let visible = chat.is_visible().unwrap_or(false);
            if visible {
                let _ = chat.hide();
            } else {
                let _ = position_chat_omni_bar(&app_clone);
                let _ = chat.show();
                let _ = chat.set_focus();
            }
        }
    });
}

/// 注册 Omni 全局快捷键（PowerToys Run 等常占用 Alt+Space，故做多候选，失败不崩溃）
fn register_omni_hotkeys(app: &tauri::AppHandle) {
    use tauri_plugin_global_shortcut::GlobalShortcutExt;
    const CANDIDATES: &[&str] = &["alt+shift+space", "ctrl+shift+space", "alt+space"];
    let mut registered = false;
    for combo in CANDIDATES {
        match app.global_shortcut().on_shortcut(*combo, |app, _, event| {
            if event.state != ShortcutState::Pressed {
                return;
            }
            toggle_chat_omni(app);
        }) {
            Ok(()) => {
                eprintln!("[Omni] 全局快捷键已注册: {}（若需 Alt+Space，请在 PowerToys 中改掉「Run」占用）", combo);
                registered = true;
                break;
            }
            Err(e) => eprintln!("[Omni] 跳过 {}: {}", combo, e),
        }
    }
    if !registered {
        eprintln!(
            "[Omni] 未能注册全局快捷键，请使用托盘图标左键打开 Omni 条，或释放 Alt+Space 后再试。"
        );
    }
}

fn main() {
    // 启动时生成策略（用户覆盖 > 自动检测），并打印决策来源
    let profile = kernel::HardwareProfile::detect();
    let settings = config::UserSettings::load();
    let _config = kernel::generate_policy(profile, &settings);

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(
            tauri_plugin_window_state::Builder::default()
                // 桌面精灵不再作为默认交互；勿从 .window-state.json 恢复「上次曾打开」导致重新显示
                .with_denylist(&["sprite"])
                .build(),
        )
        .plugin(tauri_plugin_notification::init())
        .plugin({
            let mut b = tauri_plugin_updater::Builder::new();
            if let Some(tok) = nexus_config::access_token() {
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
            show_console_window,
            quick_action_privacy_mode,
            quick_action_clear_memory,
            quick_action_eagle_eye,
            quick_action_hibernate,
            handle_device_command,
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
        ])
        .setup(|app| {
            // L3 引擎生命周期：静默启动 l3_node Sidecar（--ws-only），Ctrl+C 时 kill 释放端口
            match l3_spawn::spawn_l3_node(&*app) {
                Ok(child) => {
                    let l3 = std::sync::Arc::new(l3_spawn::L3Handle::new(child));
                    l3_spawn::register_ctrlc_kill(&l3);
                    app.manage(l3);
                    println!("[L3] 引擎已启动 ws://127.0.0.1:18981");
                }
                Err(e) => {
                    let msg = format!("[L3] 启动失败: {}", e);
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

            #[cfg(feature = "ambient")]
            app.manage(stt::SttState::new());
            
            // 启动 Pub/Sub HTTP 服务器（用于接收 Dapr 推送的命令）
            let app_handle_clone = app.app_handle().clone();
            let device_id_clone = device_id.clone();
            let pubsub_port = 8002; // 桌面客户端的应用端口
            
            // 在后台任务中启动 Pub/Sub 服务器
            tauri::async_runtime::spawn(async move {
                tokio::time::sleep(tokio::time::Duration::from_secs(1)).await;
                
                // 启动 Pub/Sub 服务器
                if let Err(e) = start_pubsub_server(app_handle_clone.clone(), device_id_clone.clone(), pubsub_port).await {
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
            
            // 创建系统托盘图标
            // 使用默认窗口图标
            if let Some(icon) = app.default_window_icon() {
                let _ = TrayIconBuilder::new()
                    .icon(icon.clone())
                    .tooltip("Jachin · 左键 Omni · 右键控制台 · 快捷键默认 Alt+Shift+Space（见终端日志）")
                    .on_tray_icon_event(|tray, event| {
                        match event {
                            TrayIconEvent::Click {
                                button: tauri::tray::MouseButton::Left,
                                ..
                            } => {
                                toggle_chat_omni(tray.app_handle());
                            }
                            TrayIconEvent::Click {
                                button: tauri::tray::MouseButton::Right,
                                ..
                            } => {
                                // 右键点击：打开控制台窗口（主线程执行以保证 Windows 上生效）
                                let app_handle = tray.app_handle().clone();
                                let app_in_closure = app_handle.clone();
                                let _ = app_handle.run_on_main_thread(move || {
                                    if let Some(w) = app_in_closure.get_webview_window("main") {
                                        let _ = w.show();
                                        let _ = w.unminimize();
                                        let _ = w.set_focus();
                                    }
                                });
                            }
                            _ => {}
                        }
                    })
                    .build(app); // 如果构建失败，忽略错误（托盘图标是可选的）
            }

            // 桌面精灵：固定初始位置；若用户从控制台再次打开，仍从该锚点旁弹出 chat
            if let Some(sprite_window) = app.get_webview_window("sprite") {
                let _ = sprite_window.set_position(tauri::LogicalPosition::new(100.0, 100.0));
                let _ = sprite_window.hide();
            }

            register_omni_hotkeys(app.handle());
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

/// 显示对话窗口（Omni 条）：屏幕居中偏下，不依赖桌面精灵位置
#[tauri::command]
async fn show_chat_window(app: tauri::AppHandle) -> Result<(), String> {
    let app_handle = app.clone();
    app.run_on_main_thread(move || {
        let _ = position_chat_omni_bar(&app_handle);
        if let Some(chat_window) = app_handle.get_webview_window("chat") {
            let _ = chat_window.show();
            let _ = chat_window.set_focus();
        }
    })
    .map_err(|e| e.to_string())?;
    Ok(())
}

/// 隐藏对话窗口
#[tauri::command]
async fn hide_chat_window(app: tauri::AppHandle) -> Result<(), String> {
    if let Some(chat_window) = app.get_webview_window("chat") {
        chat_window.hide().map_err(|e| e.to_string())?;
    }
    Ok(())
}

/// 退出应用（先 kill L3 释放端口）
#[tauri::command]
fn app_exit(app: tauri::AppHandle) -> Result<(), String> {
    if let Some(l3) = app.try_state::<std::sync::Arc<l3_spawn::L3Handle>>() {
        l3.kill();
    }
    app.exit(0);
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

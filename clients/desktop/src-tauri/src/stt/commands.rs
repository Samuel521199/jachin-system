//! STT 语音采集的 Tauri 状态与 Commands
//!
//! 因 cpal::Stream 在 Windows 上非 Send，ListeningGuard 不能放入 Tauri State。
//! 改为由专用“持有线程”在本地持有 guard，State 只保存 Start 信道、Stop 信令与 running 标志。
//!
//! 在 main.rs 的 setup 中需：
//!   app.manage(SttState::new());  // 内部会 spawn 持有线程
//! 并在 invoke_handler 中注册：start_voice_capture, stop_voice_capture, is_voice_capture_running。

#![cfg(feature = "ambient")]

use crate::stt;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc;
use std::sync::{Arc, Mutex};
use std::thread;
use tauri::State;

const VAD_MODEL_FILENAME: &str = "silero_vad.onnx";
const PORTABLE_DATA_DIR: &str = "_portable_data";
const VAD_DEBUG_PATH_ENV: &str = "JACHIN_VAD_DEBUG_PATH";

/// 解析 VAD 模型路径：环境变量 > 便携目录 > 标准数据目录。
fn resolve_vad_model_path() -> PathBuf {
    if let Ok(debug_path) = std::env::var(VAD_DEBUG_PATH_ENV) {
        return PathBuf::from(debug_path.trim()).join("vad").join(VAD_MODEL_FILENAME);
    }
    if let Ok(exe_path) = std::env::current_exe() {
        if let Some(exe_dir) = exe_path.parent() {
            let portable = exe_dir.join(PORTABLE_DATA_DIR).join("vad").join(VAD_MODEL_FILENAME);
            if portable.exists() {
                return portable;
            }
        }
    }
    directories::ProjectDirs::from("com", "jachin", "desktop")
        .map(|d| d.data_local_dir().join("vad").join(VAD_MODEL_FILENAME))
        .unwrap_or_else(|| PathBuf::from("data/vad").join(VAD_MODEL_FILENAME))
}

/// 全局状态：Start 信道、Stop 信令、是否正在采集。ListeningGuard 由持有线程持有。
pub struct SttState {
    start_tx: Mutex<Option<mpsc::Sender<(tauri::AppHandle, PathBuf)>>>,
    stop_tx: Arc<Mutex<Option<mpsc::Sender<()>>>>,
    running: Arc<AtomicBool>,
}

impl SttState {
    /// 创建状态并 spawn 持有线程。应在 setup 中调用一次，然后 manage(state)。
    pub fn new() -> Self {
        let (start_tx, start_rx) = mpsc::channel::<(tauri::AppHandle, PathBuf)>();
        let stop_tx = Arc::new(Mutex::new(None::<mpsc::Sender<()>>));
        let stop_tx_clone = Arc::clone(&stop_tx);
        let running = Arc::new(AtomicBool::new(false));
        let running_clone = Arc::clone(&running);

        thread::spawn(move || {
            while let Ok((app_handle, model_path)) = start_rx.recv() {
                if !model_path.exists() {
                    continue;
                }
                running_clone.store(true, Ordering::Relaxed);
                let guard = match stt::start_listening(app_handle.clone(), model_path) {
                    Ok(g) => g,
                    Err(_) => {
                        running_clone.store(false, Ordering::Relaxed);
                        continue;
                    }
                };
                let (stop_send, stop_recv) = mpsc::channel();
                if let Ok(mut g) = stop_tx_clone.lock() {
                    *g = Some(stop_send);
                }
                let _ = stop_recv.recv();
                drop(guard);
                if let Ok(mut g) = stop_tx_clone.lock() {
                    *g = None;
                }
                running_clone.store(false, Ordering::Relaxed);
            }
        });

        Self {
            start_tx: Mutex::new(Some(start_tx)),
            stop_tx,
            running,
        }
    }
}

/// 启动语音采集：向持有线程发送 Start，由该线程创建并持有 ListeningGuard。
#[tauri::command]
pub fn start_voice_capture(
    app_handle: tauri::AppHandle,
    state: State<'_, SttState>,
) -> Result<(), String> {
    if state.running.load(Ordering::Relaxed) {
        return Err("语音采集已在运行，请先调用 stop_voice_capture".to_string());
    }
    let model_path = resolve_vad_model_path();
    if !model_path.exists() {
        return Err(format!(
            "VAD 模型不存在: {:?}。请将 silero_vad.onnx 放入该路径，或设置 {} 指向包含 vad 的目录",
            model_path,
            VAD_DEBUG_PATH_ENV
        ));
    }
    let guard = state.start_tx.lock().map_err(|e| e.to_string())?;
    if let Some(ref tx) = *guard {
        tx.send((app_handle, model_path)).map_err(|e| e.to_string())?;
    } else {
        return Err("持有线程已退出".to_string());
    }
    Ok(())
}

/// 停止语音采集：向持有线程发送 Stop，触发 ListeningGuard 的 drop。
#[tauri::command]
pub fn stop_voice_capture(state: State<'_, SttState>) -> Result<(), String> {
    let mut guard = state.stop_tx.lock().map_err(|e| e.to_string())?;
    if let Some(tx) = guard.take() {
        let _ = tx.send(());
    }
    Ok(())
}

/// 当前是否正在采集。
#[tauri::command]
pub fn is_voice_capture_running(state: State<'_, SttState>) -> bool {
    state.running.load(Ordering::Relaxed)
}
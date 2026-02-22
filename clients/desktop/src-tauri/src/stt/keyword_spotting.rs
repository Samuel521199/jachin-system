//! 唤醒词检测 (Keyword Spotting)
//!
//! 设计：后台 Loop 持续监听麦克风 -> 识别到 "Jachin" -> 发出 WAKE_UP 事件。
//! 当前为占位实现：后台循环仅轮询运行标志；真实 KWS 可后续接入 openWakeWord (ONNX) / oww-rs。

use serde_json::json;
use std::sync::atomic::{AtomicBool, Ordering};
use tauri::{AppHandle, Emitter};

/// 全局：是否正在运行唤醒监听
static WAKE_LISTENER_RUNNING: AtomicBool = AtomicBool::new(false);

/// 唤醒词检测器
pub struct WakeWordDetector;

impl WakeWordDetector {
    /// 事件名：检测到唤醒词时发送到前端
    pub const WAKE_UP_EVENT: &'static str = "WAKE_UP";

    /// 启动唤醒词监听（后台任务）。
    /// 占位：循环仅检查运行标志，不采集麦克风；后续接入 cpal + ONNX 后在此循环内采集并推理，得分超阈值时 emit WAKE_UP。
    pub fn start(app: AppHandle) {
        if WAKE_LISTENER_RUNNING.swap(true, Ordering::SeqCst) {
            return;
        }
        tauri::async_runtime::spawn(async move {
            #[allow(unused_variables)]
            let app = app;
            while WAKE_LISTENER_RUNNING.load(Ordering::SeqCst) {
                // 占位：真实实现在此处读麦克风 -> 跑 openWakeWord -> 若检测到则 app.emit(WAKE_UP_EVENT, json!({...}))
                tokio::time::sleep(tokio::time::Duration::from_millis(100)).await;
            }
        });
    }

    /// 停止唤醒词监听
    pub fn stop() {
        WAKE_LISTENER_RUNNING.store(false, Ordering::SeqCst);
    }

    /// 是否正在监听
    pub fn is_running() -> bool {
        WAKE_LISTENER_RUNNING.load(Ordering::SeqCst)
    }

    /// 供测试或手动触发：向所有窗口发送 WAKE_UP 事件（例如前端“模拟唤醒”按钮可调 Tauri 命令再调此方法，或 Rust 内 KWS 检测到时调用）
    pub fn emit_wake_up(app: &AppHandle) {
        let _ = app.emit(
            Self::WAKE_UP_EVENT,
            json!({ "source": "keyword_spotting" }),
        );
    }
}

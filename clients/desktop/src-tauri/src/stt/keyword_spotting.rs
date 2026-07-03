//! 唤醒词检测入口：ambient 下委托 wake_listener；否则仅占位。

use serde_json::json;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::RwLock;
use tauri::{AppHandle, Emitter, Manager};

/// 全局：是否正在运行唤醒监听（非 ambient 占位）
static WAKE_LISTENER_RUNNING: AtomicBool = AtomicBool::new(false);
static CURRENT_WAKE_WORD: RwLock<Option<String>> = RwLock::new(None);

pub struct WakeWordDetector;

impl WakeWordDetector {
    pub const WAKE_UP_EVENT: &'static str = "WAKE_UP";

    /// 桌面启动时是否自动拉起唤醒监听（`start-layer3.ps1` 默认设 `JACHIN_AUTO_WAKE_LISTENER=1`）。
    pub fn should_auto_start() -> bool {
        if std::env::var("JACHIN_SKIP_WAKE_LISTENER").ok().as_deref() == Some("1") {
            return false;
        }
        if std::env::var("JACHIN_AUTO_WAKE_LISTENER").ok().as_deref() == Some("1") {
            return true;
        }
        crate::config::UserSettings::load()
            .sprite_voice_mode
            .as_deref()
            == Some("wake_up")
    }

    pub fn auto_start_if_enabled(app: AppHandle) {
        if !Self::should_auto_start() {
            return;
        }
        let word = crate::config::UserSettings::load()
            .wake_word
            .filter(|s| !s.trim().is_empty());
        crate::l3_spawn::write_voice_companion_debug(
            "rust",
            "wake.auto_start",
            word.as_deref().unwrap_or("Jachin"),
            "",
        );
        Self::start(app, word);
    }

    pub fn start(app: AppHandle, wake_word: Option<String>) {
        let word = wake_word
            .filter(|s| !s.trim().is_empty())
            .or_else(|| {
                std::env::var("JACHIN_WAKE_WORD")
                    .ok()
                    .filter(|s| !s.trim().is_empty())
            })
            .unwrap_or_else(|| "Jachin".to_string());
        if let Ok(mut cur) = CURRENT_WAKE_WORD.write() {
            *cur = Some(word.clone());
        }

        #[cfg(feature = "ambient")]
        {
            if let Some(state) = app.try_state::<super::wake_listener::WakeListenerState>() {
                if let Err(e) = state.start(app.clone(), word) {
                    eprintln!("[Wake] start failed: {}", e);
                }
                return;
            }
        }

        if WAKE_LISTENER_RUNNING.swap(true, Ordering::SeqCst) {
            return;
        }
        tauri::async_runtime::spawn(async move {
            while WAKE_LISTENER_RUNNING.load(Ordering::SeqCst) {
                tokio::time::sleep(tokio::time::Duration::from_millis(500)).await;
            }
        });
    }

    pub fn stop() {
        #[cfg(feature = "ambient")]
        {
            // 需要 AppHandle 才能 stop state — 由 stt_stop_wake_listener 直接调 state
        }
        WAKE_LISTENER_RUNNING.store(false, Ordering::SeqCst);
    }

    pub fn is_running() -> bool {
        WAKE_LISTENER_RUNNING.load(Ordering::SeqCst)
    }

    pub fn emit_wake_up(app: &AppHandle) {
        let wake_word = CURRENT_WAKE_WORD
            .read()
            .ok()
            .and_then(|g| g.clone())
            .unwrap_or_else(|| "Jachin".to_string());
        let _ = app.emit(
            Self::WAKE_UP_EVENT,
            json!({ "source": "keyword_spotting", "wake_word": wake_word }),
        );
    }
}

//! 唤醒后陪伴链路注入（与 `--jachin-voice-sim` user 路径一致）。

use serde_json::json;
use tauri::{AppHandle, Emitter, EventTarget, Manager};

use crate::l3_spawn;

/// 激活陪伴会话并向 chat webview 注入用户文本。
pub fn inject_companion_user(app: &AppHandle, content: &str) {
    let text = content.trim();
    if text.is_empty() {
        return;
    }

    crate::HUD_VOICE_SESSION_ACTIVE.store(true, std::sync::atomic::Ordering::Relaxed);

    if crate::CHAT_COMPANION_MODE.load(std::sync::atomic::Ordering::Relaxed) {
        let _ = crate::companion_apply_reveal(app);
        if let Some(chat) = app.get_webview_window("chat") {
            let _ = chat.set_always_on_top(true);
            let _ = chat.show();
        }
        crate::emit_omni_companion_ui(app, true);
    } else if let Err(e) = crate::minimize_chat_to_companion(app) {
        eprintln!("[voice-wake] companion orb: {}", e);
    }

    let _ = app.emit("hud-voice-session", json!({ "active": true }));
    let _ = app.emit_to(
        EventTarget::webview_window("chat"),
        "voice-sim-user-input",
        json!({ "content": text }),
    );
    let _ = app.emit_to(
        EventTarget::webview_window("hud_panel"),
        "hud-panel-user-message",
        json!({ "content": text }),
    );

    l3_spawn::write_voice_companion_debug(
        "rust",
        "wake.inject",
        &text.chars().take(120).collect::<String>(),
        "",
    );
}

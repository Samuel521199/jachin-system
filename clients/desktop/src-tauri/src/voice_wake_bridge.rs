//! 唤醒后陪伴链路注入（与 `--jachin-voice-sim` user 路径一致）。

use serde_json::json;
use tauri::{AppHandle, Emitter, EventTarget, Manager};

use crate::l3_spawn;

/// 激活陪伴会话并向 chat webview 注入用户文本。
#[allow(dead_code)]
pub fn inject_companion_user(app: &AppHandle, content: &str) {
    inject_companion_user_with_source(app, content, "wake");
}

#[allow(dead_code)]
pub fn inject_companion_user_with_source(app: &AppHandle, content: &str, source: &str) {
    inject_companion_user_with_owner_evidence(app, content, source, None, None, None, None);
}

#[allow(dead_code)]
pub fn inject_companion_user_with_owner_evidence(
    app: &AppHandle,
    content: &str,
    source: &str,
    owner_duration_ms: Option<u32>,
    total_duration_ms: Option<u32>,
    skipped_segments_count: Option<usize>,
    reason: Option<&str>,
) {
    let text = content.trim();
    if text.is_empty() {
        return;
    }
    let source = source.trim();
    let source = if source.is_empty() { "wake" } else { source };

    crate::HUD_VOICE_SESSION_ACTIVE.store(true, std::sync::atomic::Ordering::Relaxed);

    crate::CHAT_COMPANION_MODE.store(false, std::sync::atomic::Ordering::SeqCst);
    crate::emit_omni_companion_ui(app, false);
    if let Some(chat) = app.get_webview_window("chat") {
        let _ = chat.set_always_on_top(false);
        let _ = chat.show();
        let _ = chat.unminimize();
        let _ = chat.set_focus();
    }

    let _ = app.emit("hud-voice-session", json!({ "active": true }));
    let _ = app.emit_to(
        EventTarget::webview_window("chat"),
        "voice-sim-user-input",
        json!({
            "content": text,
            "source": source,
            "voice_speaker_verified": true,
            "voice_owner_track_accepted": true,
            "voice_owner_track_reason": reason.unwrap_or("rust_owner_track_ok"),
            "voice_owner_duration_ms": owner_duration_ms,
            "voice_total_duration_ms": total_duration_ms,
            "voice_owner_skipped_segments_count": skipped_segments_count
        }),
    );
    l3_spawn::write_voice_companion_debug(
        "rust",
        "wake.inject",
        &text.chars().take(120).collect::<String>(),
        source,
    );
}

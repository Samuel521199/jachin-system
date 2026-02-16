//! Settings Commands - 用户设置与运行时配置

use crate::config::UserSettings;
use crate::kernel::{generate_policy, HardwareProfile, RuntimeConfig};
use serde::Serialize;
use tauri::Emitter;

/// 返回合并后的最终 RuntimeConfig（供 UI 显示当前生效状态）
#[tauri::command]
pub fn get_current_config() -> Result<RuntimeConfig, String> {
    let profile = HardwareProfile::detect();
    let settings = UserSettings::load();
    Ok(generate_policy(profile, &settings))
}

/// 返回 settings.json 的原始内容（供 UI 回显选项）
#[tauri::command]
pub fn get_user_settings() -> Result<UserSettings, String> {
    Ok(UserSettings::load())
}

/// 更新用户设置：前端传入完整 UserSettings，直接持久化并触发事件
#[tauri::command]
pub fn update_user_settings(app: tauri::AppHandle, patch: UserSettings) -> Result<(), String> {
    patch.save()?;
    app.emit("settings-updated", SettingsUpdatedPayload { restart_required: true })
        .map_err(|e| e.to_string())?;
    Ok(())
}

#[derive(Clone, Serialize)]
struct SettingsUpdatedPayload {
    restart_required: bool,
}

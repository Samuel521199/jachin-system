/// Window Management - 窗口管理模块
///
/// 处理多窗口协同、位置同步等
use tauri::{AppHandle, Manager};

#[allow(dead_code)]
pub struct WindowManager {
    app: AppHandle,
}

impl WindowManager {
    #[allow(dead_code)]
    pub fn new(app: AppHandle) -> Self {
        Self { app }
    }

    /// 同步对话窗口位置到精灵窗口旁边
    #[allow(dead_code)]
    pub async fn sync_chat_position(&self) -> Result<(), Box<dyn std::error::Error>> {
        if let Some(sprite_window) = self.app.get_webview_window("sprite") {
            if let Some(chat_window) = self.app.get_webview_window("chat") {
                if let Ok(sprite_pos) = sprite_window.inner_position() {
                    chat_window
                        .set_position(tauri::LogicalPosition::new(
                            sprite_pos.x as f64 + 220.0,
                            sprite_pos.y as f64,
                        ))
                        .map_err(|e| format!("Failed to set chat position: {}", e))?;
                }
            }
        }
        Ok(())
    }

    /// 获取精灵窗口位置
    #[allow(dead_code)]
    pub async fn get_sprite_position(&self) -> Result<(f64, f64), Box<dyn std::error::Error>> {
        if let Some(sprite_window) = self.app.get_webview_window("sprite") {
            let pos = sprite_window
                .inner_position()
                .map_err(|e| format!("Failed to get sprite position: {}", e))?;
            Ok((pos.x as f64, pos.y as f64))
        } else {
            Err("Sprite window not found".into())
        }
    }
}

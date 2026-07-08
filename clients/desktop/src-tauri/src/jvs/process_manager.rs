use std::path::PathBuf;
use std::sync::Mutex;

use serde::Serialize;
use tauri::Manager;
use tauri_plugin_shell::ShellExt;

use crate::l3_spawn;

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct JvsStatus {
    pub running: bool,
    pub auto_spawn_enabled: bool,
    pub base_url: String,
    pub host: String,
    pub port: u16,
    pub model_root: String,
    pub last_error: Option<String>,
}

#[derive(Debug, Clone)]
pub struct JvsConfig {
    pub host: String,
    pub port: u16,
    pub base_url: String,
    pub model_root: String,
    pub auto_spawn_enabled: bool,
}

pub struct JvsHandle {
    child: Mutex<Option<tauri_plugin_shell::process::CommandChild>>,
    last_error: Mutex<Option<String>>,
    config: JvsConfig,
}

impl JvsHandle {
    pub fn new(config: JvsConfig) -> Self {
        Self {
            child: Mutex::new(None),
            last_error: Mutex::new(None),
            config,
        }
    }

    pub fn status(&self) -> JvsStatus {
        let running = self
            .child
            .lock()
            .ok()
            .and_then(|g| g.as_ref().map(|_| true))
            .unwrap_or(false);
        let last_error = self.last_error.lock().ok().and_then(|g| g.clone());
        JvsStatus {
            running,
            auto_spawn_enabled: self.config.auto_spawn_enabled,
            base_url: self.config.base_url.clone(),
            host: self.config.host.clone(),
            port: self.config.port,
            model_root: self.config.model_root.clone(),
            last_error,
        }
    }

    pub fn set_error(&self, msg: String) {
        if let Ok(mut g) = self.last_error.lock() {
            *g = Some(msg);
        }
    }

    fn clear_error(&self) {
        if let Ok(mut g) = self.last_error.lock() {
            *g = None;
        }
    }

    pub fn stop(&self) {
        if let Ok(mut guard) = self.child.lock() {
            if let Some(c) = guard.take() {
                let _ = c.kill();
            }
        }
    }

    fn set_child(&self, child: tauri_plugin_shell::process::CommandChild) {
        if let Ok(mut guard) = self.child.lock() {
            if let Some(old) = guard.take() {
                let _ = old.kill();
            }
            *guard = Some(child);
        }
    }
}

impl Drop for JvsHandle {
    fn drop(&mut self) {
        self.stop();
    }
}

fn env_flag_enabled(name: &str) -> bool {
    match std::env::var(name) {
        Ok(v) => {
            let t = v.trim().to_lowercase();
            matches!(t.as_str(), "1" | "true" | "yes" | "on")
        }
        Err(_) => false,
    }
}

pub fn load_jvs_config() -> JvsConfig {
    let host =
        std::env::var("JACHIN_VOICE_SERVER_HOST").unwrap_or_else(|_| "127.0.0.1".to_string());
    let port = std::env::var("JACHIN_VOICE_SERVER_PORT")
        .ok()
        .and_then(|v| v.parse::<u16>().ok())
        .unwrap_or(18982);
    let base_url = std::env::var("JACHIN_VOICE_SERVER_URL")
        .unwrap_or_else(|_| format!("http://{}:{}", host, port));
    let model_root = std::env::var("JACHIN_VOICE_MODEL_ROOT")
        .unwrap_or_else(|_| r"D:\project\jachin-system-main\data\models\voice".to_string());
    // JACHIN_SKIP_VOICE_SPAWN=1 means disable autospawn
    let auto_spawn_enabled = !env_flag_enabled("JACHIN_SKIP_VOICE_SPAWN");

    JvsConfig {
        host,
        port,
        base_url,
        model_root,
        auto_spawn_enabled,
    }
}

async fn check_health(url: &str) -> Result<(), String> {
    let health_url = format!("{}/health", url.trim_end_matches('/'));
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(3))
        .build()
        .map_err(|e| e.to_string())?;
    let resp = client
        .get(&health_url)
        .send()
        .await
        .map_err(|e| e.to_string())?;
    if !resp.status().is_success() {
        return Err(format!("JVS health status {}", resp.status()));
    }
    Ok(())
}

async fn warm_audio_models(url: &str) -> Result<(), String> {
    let warm_url = format!("{}/v1/models/audio/warm", url.trim_end_matches('/'));
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(120))
        .build()
        .map_err(|e| e.to_string())?;
    let resp = client
        .post(&warm_url)
        .json(&serde_json::json!({ "stt": true, "tts": true, "sv": false }))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    if !resp.status().is_success() {
        return Err(format!("JVS warm status {}", resp.status()));
    }
    Ok(())
}

fn resolve_python_for_voice(root: &std::path::Path) -> String {
    let venv = root.join(".venv-voice").join("Scripts").join("python.exe");
    if venv.is_file() {
        #[cfg(target_os = "windows")]
        {
            let pythonw = venv.with_file_name("pythonw.exe");
            if pythonw.is_file() {
                return pythonw.to_string_lossy().to_string();
            }
        }
        return venv.to_string_lossy().to_string();
    }
    #[cfg(target_os = "windows")]
    {
        "pythonw".to_string()
    }
    #[cfg(not(target_os = "windows"))]
    "python".to_string()
}

fn resolve_voice_server_main() -> Option<PathBuf> {
    let root = l3_spawn::project_root()?;
    let p = root.join("voice_server").join("main.py");
    if p.is_file() {
        Some(p)
    } else {
        None
    }
}

pub async fn start_jvs_process(app: &tauri::AppHandle) -> Result<(), String> {
    let state = app
        .try_state::<std::sync::Arc<JvsHandle>>()
        .ok_or_else(|| "JVS state not initialized".to_string())?;
    let cfg = state.config.clone();

    if check_health(&cfg.base_url).await.is_ok() {
        match warm_audio_models(&cfg.base_url).await {
            Ok(()) => l3_spawn::write_voice_companion_debug(
                "rust",
                "jvs_warm_ok",
                "reused",
                &cfg.base_url,
            ),
            Err(e) => {
                l3_spawn::write_voice_companion_debug("rust", "jvs_warm_warn", &e, &cfg.base_url)
            }
        }
        state.clear_error();
        l3_spawn::write_jachin_shared_l3_debug(
            "jvs",
            "health check ok; reuse existing voice_server",
        );
        l3_spawn::write_voice_companion_debug("rust", "jvs_reuse", "health ok", &cfg.base_url);
        return Ok(());
    }

    let main_py = resolve_voice_server_main()
        .ok_or_else(|| "voice_server/main.py not found, cannot spawn JVS".to_string())?;
    let root = l3_spawn::project_root().ok_or_else(|| "project root not found".to_string())?;
    let python_exe = resolve_python_for_voice(&root);

    let mut cmd = app
        .shell()
        .command(&python_exe)
        .args([main_py.to_string_lossy().to_string()]);

    cmd = cmd
        .current_dir(&root)
        .env("JACHIN_VOICE_SERVER_HOST", cfg.host.clone())
        .env("JACHIN_VOICE_SERVER_PORT", cfg.port.to_string())
        .env("JACHIN_VOICE_SERVER_URL", cfg.base_url.clone())
        .env("JACHIN_VOICE_MODEL_ROOT", cfg.model_root.clone())
        .env("PYTHONUNBUFFERED", "1")
        .env("PYTHONUTF8", "1");

    let (_rx, child) = cmd
        .spawn()
        .map_err(|e| format!("spawn voice_server failed: {}", e))?;
    state.set_child(child);

    // 模型预热可能需数十秒；短超时会导致「spawn 失败」但进程仍在后台加载
    for _ in 0..120 {
        if check_health(&cfg.base_url).await.is_ok() {
            match warm_audio_models(&cfg.base_url).await {
                Ok(()) => l3_spawn::write_voice_companion_debug(
                    "rust",
                    "jvs_warm_ok",
                    "spawned",
                    &cfg.base_url,
                ),
                Err(e) => l3_spawn::write_voice_companion_debug(
                    "rust",
                    "jvs_warm_warn",
                    &e,
                    &cfg.base_url,
                ),
            }
            state.clear_error();
            l3_spawn::write_jachin_shared_l3_debug("jvs", "voice_server spawned and healthy");
            l3_spawn::write_voice_companion_debug("rust", "jvs_spawn_ok", "spawned", &cfg.base_url);
            return Ok(());
        }
        tokio::time::sleep(std::time::Duration::from_millis(500)).await;
    }

    let err = "voice_server spawned but health check timeout".to_string();
    state.set_error(err.clone());
    l3_spawn::write_jachin_shared_l3_debug("jvs", &err);
    l3_spawn::write_voice_companion_debug("rust", "jvs_spawn_timeout", &err, &cfg.base_url);
    Err(err)
}

#[allow(dead_code)]
pub fn start_jvs_process_sync(app: &tauri::AppHandle) -> Result<(), String> {
    let app_clone = app.clone();
    if let Ok(handle) = tokio::runtime::Handle::try_current() {
        tokio::task::block_in_place(|| handle.block_on(start_jvs_process(&app_clone)))
    } else {
        tauri::async_runtime::block_on(async move { start_jvs_process(&app_clone).await })
    }
}

#[tauri::command]
pub async fn jvs_start(app: tauri::AppHandle) -> Result<JvsStatus, String> {
    start_jvs_process(&app).await?;
    let state = app
        .try_state::<std::sync::Arc<JvsHandle>>()
        .ok_or_else(|| "JVS state not initialized".to_string())?;
    Ok(state.status())
}

#[tauri::command]
pub async fn jvs_stop(app: tauri::AppHandle) -> Result<JvsStatus, String> {
    let state = app
        .try_state::<std::sync::Arc<JvsHandle>>()
        .ok_or_else(|| "JVS state not initialized".to_string())?;
    state.stop();
    Ok(state.status())
}

#[tauri::command]
pub async fn jvs_status(app: tauri::AppHandle) -> Result<JvsStatus, String> {
    let state = app
        .try_state::<std::sync::Arc<JvsHandle>>()
        .ok_or_else(|| "JVS state not initialized".to_string())?;
    let mut s = state.status();
    if check_health(&s.base_url).await.is_err() {
        s.running = false;
    }
    Ok(s)
}

#[tauri::command]
pub async fn jvs_health(app: tauri::AppHandle) -> Result<serde_json::Value, String> {
    let state = app
        .try_state::<std::sync::Arc<JvsHandle>>()
        .ok_or_else(|| "JVS state not initialized".to_string())?;
    let status = check_health(&state.config.base_url).await.is_ok();
    Ok(serde_json::json!({
        "ok": status,
        "base_url": state.config.base_url,
    }))
}

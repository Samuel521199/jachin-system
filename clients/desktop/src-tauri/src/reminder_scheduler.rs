//! 桌面定时提醒：持久化到 ~/.jachin/desktop_reminders.json，tokio 秒级 tick，到点复用右下角哨兵通知。

use directories::BaseDirs;
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};
use tauri::AppHandle;
use uuid::Uuid;

const MIN_LEAD_MS: u64 = 1_000;
const MAX_REMINDERS: usize = 500;
const TITLE_MAX: usize = 200;
const BODY_MAX: usize = 4_000;

#[derive(Clone, Serialize, Deserialize)]
pub struct Reminder {
    pub id: String,
    pub fire_at_unix_ms: u64,
    pub title: String,
    pub body: String,
}

struct Inner {
    items: Vec<Reminder>,
    path: PathBuf,
}

pub struct ReminderService {
    inner: Mutex<Inner>,
    app: AppHandle,
}

fn unix_now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

fn reminders_path() -> PathBuf {
    BaseDirs::new()
        .map(|b| b.home_dir().join(".jachin").join("desktop_reminders.json"))
        .unwrap_or_else(|| PathBuf::from("desktop_reminders.json"))
}

fn load_items(path: &PathBuf) -> Vec<Reminder> {
    let raw = match fs::read_to_string(path) {
        Ok(s) => s,
        Err(_) => return Vec::new(),
    };
    serde_json::from_str::<Vec<Reminder>>(&raw).unwrap_or_default()
}

impl ReminderService {
    pub fn new(app: AppHandle) -> Self {
        let path = reminders_path();
        if let Some(parent) = path.parent() {
            let _ = fs::create_dir_all(parent);
        }
        let items = load_items(&path);
        Self {
            inner: Mutex::new(Inner { items, path }),
            app,
        }
    }

    fn persist(&self) -> Result<(), String> {
        let g = self.inner.lock().map_err(|e| e.to_string())?;
        let json =
            serde_json::to_string_pretty(&g.items).map_err(|e| e.to_string())?;
        fs::write(&g.path, json).map_err(|e| e.to_string())
    }

    /// 注册一条提醒；返回 reminder id。
    pub fn add(&self, fire_at_unix_ms: u64, title: String, body: String) -> Result<String, String> {
        let now = unix_now_ms();
        if fire_at_unix_ms < now.saturating_add(MIN_LEAD_MS) {
            return Err(format!(
                "fire_at_unix_ms 须至少比当前时间晚 {} 毫秒（当前 {} ms）",
                MIN_LEAD_MS, now
            ));
        }
        let title = title.chars().take(TITLE_MAX).collect::<String>();
        let body = body.chars().take(BODY_MAX).collect::<String>();
        if body.trim().is_empty() {
            return Err("body 不能为空".into());
        }
        let id = Uuid::new_v4().to_string();
        let r = Reminder {
            id: id.clone(),
            fire_at_unix_ms,
            title,
            body,
        };
        {
            let mut g = self.inner.lock().map_err(|e| e.to_string())?;
            if g.items.len() >= MAX_REMINDERS {
                return Err(format!("提醒数量已达上限 {}", MAX_REMINDERS));
            }
            g.items.push(r);
        }
        self.persist()?;
        Ok(id)
    }

    pub fn cancel(&self, id: &str) -> Result<(), String> {
        let id = id.trim();
        if id.is_empty() {
            return Err("id 不能为空".into());
        }
        let removed = {
            let mut g = self.inner.lock().map_err(|e| e.to_string())?;
            let before = g.items.len();
            g.items.retain(|r| r.id != id);
            before != g.items.len()
        };
        if !removed {
            return Err("未找到该 id".into());
        }
        self.persist()
    }

    pub fn list(&self) -> Result<Vec<Reminder>, String> {
        let g = self.inner.lock().map_err(|e| e.to_string())?;
        Ok(g.items.clone())
    }

    /// 须在 Tauri 已初始化异步运行时后调用（如 `setup` 内）；勿用 `tokio::spawn`，否则无 reactor 会 panic。
    pub fn spawn_tick_loop(self: Arc<Self>) {
        tauri::async_runtime::spawn(async move {
            let mut interval = tokio::time::interval(std::time::Duration::from_secs(1));
            loop {
                interval.tick().await;
                if let Err(e) = self.tick_due().await {
                    eprintln!("[Reminder] {}", e);
                }
            }
        });
    }

    async fn tick_due(&self) -> Result<(), String> {
        let now = unix_now_ms();
        let mut due: Vec<Reminder> = Vec::new();
        {
            let mut g = self.inner.lock().map_err(|e| e.to_string())?;
            g.items.retain(|r| {
                if r.fire_at_unix_ms <= now {
                    due.push(r.clone());
                    false
                } else {
                    true
                }
            });
        }
        self.persist()?;
        for r in due {
            self.fire_one(r);
        }
        Ok(())
    }

    fn fire_one(&self, r: Reminder) {
        let app = self.app.clone();
        let title = r.title;
        let body = r.body;
        tauri::async_runtime::spawn(async move {
            tokio::time::sleep(std::time::Duration::from_millis(60)).await;
            let app_h = app.clone();
            if let Err(e) = app.run_on_main_thread(move || {
                crate::show_sentry_toast_inner(&app_h, title, body, "reminder_toast");
            }) {
                eprintln!("[Reminder] run_on_main_thread failed: {:?}", e);
            }
        });
    }
}

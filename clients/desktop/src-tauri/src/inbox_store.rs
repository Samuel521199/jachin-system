//! 控制台消息中心：持久化 Jachin 右下角哨兵弹窗，便于未即时查看时回溯。
//! 文件：`~/.jachin/console_inbox.json`

use serde::{Deserialize, Serialize};
use std::fs;
use std::path::PathBuf;
use uuid::Uuid;

const MAX_ITEMS: usize = 200;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InboxItem {
    pub id: String,
    pub title: String,
    pub body: String,
    pub created_at_ms: u64,
    pub read: bool,
}

#[derive(Debug, Default, Serialize, Deserialize)]
struct InboxFile {
    items: Vec<InboxItem>,
}

fn inbox_json_path() -> PathBuf {
    let home = directories::BaseDirs::new()
        .map(|b| b.home_dir().to_path_buf())
        .unwrap_or_else(|| PathBuf::from("."));
    home.join(".jachin").join("console_inbox.json")
}

fn read_file() -> Result<InboxFile, String> {
    let path = inbox_json_path();
    if !path.is_file() {
        return Ok(InboxFile::default());
    }
    let s = fs::read_to_string(&path).map_err(|e| format!("read inbox: {e}"))?;
    serde_json::from_str(&s).map_err(|e| format!("parse inbox: {e}"))
}

fn write_file(f: &InboxFile) -> Result<(), String> {
    let path = inbox_json_path();
    if let Some(p) = path.parent() {
        fs::create_dir_all(p).map_err(|e| format!("mkdir inbox: {e}"))?;
    }
    let s = serde_json::to_string_pretty(f).map_err(|e| format!("serialize inbox: {e}"))?;
    fs::write(&path, s).map_err(|e| format!("write inbox: {e}"))
}

/// 在每次哨兵 toast 展示前调用，追加一条未读记录。
pub fn append_sentry_inbox(title: String, body: String) -> Result<InboxItem, String> {
    let mut f = read_file()?;
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0);
    let item = InboxItem {
        id: format!("inbox-{}-{}", now, Uuid::new_v4()),
        title,
        body,
        created_at_ms: now,
        read: false,
    };
    f.items.insert(0, item.clone());
    if f.items.len() > MAX_ITEMS {
        f.items.truncate(MAX_ITEMS);
    }
    write_file(&f)?;
    Ok(item)
}

pub fn list_inbox() -> Result<Vec<InboxItem>, String> {
    let f = read_file()?;
    Ok(f.items)
}

pub fn mark_inbox_read(id: String) -> Result<(), String> {
    let mut f = read_file()?;
    for it in &mut f.items {
        if it.id == id {
            it.read = true;
            break;
        }
    }
    write_file(&f)
}

pub fn mark_all_inbox_read() -> Result<(), String> {
    let mut f = read_file()?;
    for it in &mut f.items {
        it.read = true;
    }
    write_file(&f)
}

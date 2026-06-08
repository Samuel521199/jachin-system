//! PMO Copilot 后台子进程状态（供项目管理页轮询，不读日志文件）

use serde::Serialize;
use std::process::Child;
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

#[derive(Clone, Serialize)]
#[serde(rename_all = "snake_case")]
pub struct PmoCopilotRunStatus {
    /// ``idle`` | ``running`` | ``finished`` | ``failed``
    pub phase: String,
    pub pid: Option<u32>,
    pub started_at_ms: Option<u64>,
    pub finished_at_ms: Option<u64>,
    pub exit_code: Option<i32>,
    pub label: Option<String>,
}

struct PmoRunInner {
    child: Option<Child>,
    started_at_ms: u64,
    label: String,
    finished_at_ms: Option<u64>,
    exit_code: Option<i32>,
    stopped_by_user: bool,
}

/// 持有 PMO 子进程 ``Child``，``try_wait`` 判断运行/结束
pub struct PmoRunTracker(pub Mutex<PmoRunInner>);

impl PmoRunTracker {
    pub fn new() -> Self {
        Self(Mutex::new(PmoRunInner {
            child: None,
            started_at_ms: 0,
            label: String::new(),
            finished_at_ms: None,
            exit_code: None,
            stopped_by_user: false,
        }))
    }

    fn now_ms() -> u64 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_millis() as u64)
            .unwrap_or(0)
    }

    /// 注册新启动的子进程；若上一轮仍在跑则拒绝
    pub fn register_child(&self, child: Child, label: String) -> Result<u32, String> {
        let mut g = self
            .0
            .lock()
            .map_err(|e| format!("PMO 状态锁失败: {e}"))?;
        if let Some(ref mut existing) = g.child {
            match existing.try_wait() {
                Ok(None) => {
                    return Err("PMO Copilot 已在后台运行中，请等待当前任务结束后再启动".to_string());
                }
                Ok(Some(status)) => {
                    g.finished_at_ms = Some(Self::now_ms());
                    g.exit_code = status.code();
                    g.child = None;
                }
                Err(e) => return Err(format!("检查 PMO 进程状态失败: {e}")),
            }
        }
        let pid = child.id();
        g.child = Some(child);
        g.started_at_ms = Self::now_ms();
        g.label = label;
        g.finished_at_ms = None;
        g.exit_code = None;
        g.stopped_by_user = false;
        Ok(pid)
    }

    /// 终止当前 PMO 子进程（Windows 使用 taskkill /T 结束进程树）
    pub fn stop_child(&self) -> Result<u32, String> {
        let mut g = self
            .0
            .lock()
            .map_err(|e| format!("PMO 状态锁失败: {e}"))?;
        let Some(mut child) = g.child.take() else {
            return Err("当前没有运行中的 PMO 任务".to_string());
        };
        let pid = child.id();
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            use std::process::Command as StdCommand;
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            let _ = StdCommand::new("taskkill")
                .args(["/T", "/F", "/PID", &pid.to_string()])
                .creation_flags(CREATE_NO_WINDOW)
                .output();
        }
        #[cfg(not(windows))]
        {
            let _ = child.kill();
        }
        let _ = child.wait();
        g.finished_at_ms = Some(Self::now_ms());
        g.exit_code = None;
        g.stopped_by_user = true;
        Ok(pid)
    }

    pub fn snapshot(&self) -> Result<PmoCopilotRunStatus, String> {
        let mut g = self
            .0
            .lock()
            .map_err(|e| format!("PMO 状态锁失败: {e}"))?;

        if let Some(ref mut child) = g.child {
            match child.try_wait() {
                Ok(None) => {
                    return Ok(PmoCopilotRunStatus {
                        phase: "running".to_string(),
                        pid: Some(child.id()),
                        started_at_ms: Some(g.started_at_ms),
                        finished_at_ms: None,
                        exit_code: None,
                        label: Some(g.label.clone()),
                    });
                }
                Ok(Some(status)) => {
                    g.finished_at_ms = Some(Self::now_ms());
                    g.exit_code = status.code();
                    g.child = None;
                }
                Err(e) => return Err(format!("检查 PMO 进程状态失败: {e}")),
            }
        }

        if let Some(finished_at) = g.finished_at_ms {
            let phase = if g.stopped_by_user {
                "stopped"
            } else if g.exit_code == Some(0) {
                "finished"
            } else {
                "failed"
            };
            return Ok(PmoCopilotRunStatus {
                phase: phase.to_string(),
                pid: None,
                started_at_ms: if g.started_at_ms > 0 {
                    Some(g.started_at_ms)
                } else {
                    None
                },
                finished_at_ms: Some(finished_at),
                exit_code: g.exit_code,
                label: Some(g.label.clone()),
            });
        }

        Ok(PmoCopilotRunStatus {
            phase: "idle".to_string(),
            pid: None,
            started_at_ms: None,
            finished_at_ms: None,
            exit_code: None,
            label: None,
        })
    }
}

#[tauri::command]
pub fn get_pmo_copilot_run_status(
    tracker: tauri::State<'_, std::sync::Arc<PmoRunTracker>>,
) -> Result<PmoCopilotRunStatus, String> {
    tracker.snapshot()
}

#[tauri::command]
pub fn stop_pmo_copilot_run(
    tracker: tauri::State<'_, std::sync::Arc<PmoRunTracker>>,
) -> Result<String, String> {
    let pid = tracker.stop_child()?;
    Ok(format!("已停止 PMO 任务（PID {pid}）"))
}

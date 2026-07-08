//! 唤醒监听状态：持有 WakePipelineGuard，支持热重载 wake_word。

#![cfg(feature = "ambient")]

use super::wake_pipeline::{start_wake_pipeline, WakePipelineConfig};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};
use tauri::AppHandle;

pub struct WakeListenerState {
    start_tx: Mutex<Option<mpsc::Sender<WakeStartRequest>>>,
    stop_tx: Arc<Mutex<Option<mpsc::Sender<()>>>>,
    manual_wake_tx: Arc<Mutex<Option<mpsc::Sender<()>>>>,
    running: Arc<AtomicBool>,
}

struct WakeStartRequest {
    app: AppHandle,
    wake_word: String,
}

impl WakeListenerState {
    pub fn new() -> Self {
        let (start_tx, start_rx) = mpsc::channel::<WakeStartRequest>();
        let stop_tx = Arc::new(Mutex::new(None::<mpsc::Sender<()>>));
        let manual_wake_tx = Arc::new(Mutex::new(None::<mpsc::Sender<()>>));
        let running = Arc::new(AtomicBool::new(false));

        let running_holder = Arc::clone(&running);
        let stop_tx_holder = Arc::clone(&stop_tx);
        let manual_wake_tx_holder = Arc::clone(&manual_wake_tx);

        thread::spawn(move || {
            loop {
                // 等待 start 或 stop
                let req = match start_rx.recv() {
                    Ok(r) => r,
                    Err(_) => break,
                };

                if let Ok(mut g) = manual_wake_tx_holder.lock() {
                    *g = None;
                }
                running_holder.store(false, Ordering::Relaxed);

                let (stop_send, stop_recv) = mpsc::channel();
                if let Ok(mut g) = stop_tx_holder.lock() {
                    *g = Some(stop_send);
                }

                let (manual_tx, manual_rx) = mpsc::channel();
                if let Ok(mut g) = manual_wake_tx_holder.lock() {
                    *g = Some(manual_tx);
                }

                let cfg = WakePipelineConfig {
                    wake_word: req.wake_word,
                };
                let guard = match start_wake_pipeline(req.app.clone(), cfg, manual_rx) {
                    Ok(g) => {
                        crate::l3_spawn::write_voice_companion_debug(
                            "rust",
                            "wake.listener_started",
                            "ok",
                            "",
                        );
                        running_holder.store(true, Ordering::Relaxed);
                        g
                    }
                    Err(e) => {
                        crate::l3_spawn::write_voice_companion_debug(
                            "rust",
                            "wake.start_fail",
                            &e,
                            "",
                        );
                        eprintln!("[Wake] start failed: {}", e);
                        continue;
                    }
                };

                // 阻塞直到 stop
                let _ = stop_recv.recv();
                drop(guard);
                if let Ok(mut g) = manual_wake_tx_holder.lock() {
                    *g = None;
                }
                running_holder.store(false, Ordering::Relaxed);
            }
        });

        Self {
            start_tx: Mutex::new(Some(start_tx)),
            stop_tx,
            manual_wake_tx,
            running,
        }
    }

    pub fn start(&self, app: AppHandle, wake_word: String) -> Result<(), String> {
        self.stop_inner();
        let guard = self.start_tx.lock().map_err(|e| e.to_string())?;
        if let Some(ref tx) = *guard {
            tx.send(WakeStartRequest { app, wake_word })
                .map_err(|e| e.to_string())?;
            Ok(())
        } else {
            Err("唤醒持有线程已退出".to_string())
        }
    }

    fn stop_inner(&self) {
        if let Ok(mut g) = self.stop_tx.lock() {
            if let Some(tx) = g.take() {
                let _ = tx.send(());
            }
        }
        if let Ok(mut g) = self.manual_wake_tx.lock() {
            *g = None;
        }
        self.running.store(false, Ordering::Relaxed);
    }

    pub fn stop(&self) {
        self.stop_inner();
    }

    pub fn is_running(&self) -> bool {
        self.running.load(Ordering::Relaxed)
    }

    /// 等待唤醒管线释放麦克风（stop 后 Windows cpal 仍需短暂冷却）。
    pub fn wait_until_stopped(&self, timeout_ms: u64) -> bool {
        let deadline = Instant::now() + Duration::from_millis(timeout_ms);
        while self.running.load(Ordering::Relaxed) {
            if Instant::now() >= deadline {
                return false;
            }
            thread::sleep(Duration::from_millis(20));
        }
        // stop_inner 会先把 running 置 false，但 guard/stream 可能尚未 drop
        thread::sleep(Duration::from_millis(180));
        true
    }

    pub fn trigger_manual_wake(&self) -> Result<(), String> {
        let guard = self.manual_wake_tx.lock().map_err(|e| e.to_string())?;
        if let Some(ref tx) = *guard {
            tx.send(()).map_err(|e| e.to_string())?;
            Ok(())
        } else {
            Err("唤醒监听未运行".to_string())
        }
    }
}

//! STT 语音采集的 Tauri 状态与 Commands
//!
//! 因 cpal::Stream 在 Windows 上非 Send，ListeningGuard 不能放入 Tauri State。
//! 改为由专用“持有线程”在本地持有 guard，State 只保存 Start 信道、Stop 信令与 running 标志。
//!
//! 在 main.rs 的 setup 中需：
//!   app.manage(SttState::new());  // 内部会 spawn 持有线程
//! 并在 invoke_handler 中注册：start_voice_capture, stop_voice_capture, is_voice_capture_running。

#![cfg(feature = "ambient")]

use crate::config::UserSettings;
use crate::stt;
use crate::stt::manager::{PttCaptureOutcome, SttAudioPayload};
use crate::stt::speaker_verification::{
    jvs_filter_owner_track_blocking, load_owner_voiceprint_profile,
};
use crate::stt::wake_listener::WakeListenerState;
use base64::Engine;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};
use tauri::{Manager, State};

const VAD_MODEL_FILENAME: &str = "silero_vad.onnx";
const PORTABLE_DATA_DIR: &str = "_portable_data";
const VAD_DEBUG_PATH_ENV: &str = "JACHIN_VAD_DEBUG_PATH";

/// 解析 VAD 模型路径：环境变量 > 便携目录 > 标准数据目录。
fn resolve_vad_model_path() -> PathBuf {
    if let Ok(debug_path) = std::env::var(VAD_DEBUG_PATH_ENV) {
        return PathBuf::from(debug_path.trim())
            .join("vad")
            .join(VAD_MODEL_FILENAME);
    }
    if let Ok(exe_path) = std::env::current_exe() {
        if let Some(exe_dir) = exe_path.parent() {
            let portable = exe_dir
                .join(PORTABLE_DATA_DIR)
                .join("vad")
                .join(VAD_MODEL_FILENAME);
            if portable.exists() {
                return portable;
            }
        }
    }
    directories::ProjectDirs::from("com", "jachin", "desktop")
        .map(|d| d.data_local_dir().join("vad").join(VAD_MODEL_FILENAME))
        .unwrap_or_else(|| PathBuf::from("data/vad").join(VAD_MODEL_FILENAME))
}

/// 全局状态：VAD 连续采集 + PTT 按住说话（互斥占麦）。
pub struct SttState {
    start_tx: Mutex<Option<mpsc::Sender<(tauri::AppHandle, PathBuf)>>>,
    stop_tx: Arc<Mutex<Option<mpsc::Sender<()>>>>,
    running: Arc<AtomicBool>,
    ptt_start_tx: Mutex<Option<mpsc::Sender<(tauri::AppHandle, PathBuf)>>>,
    ptt_finalize_tx: Arc<Mutex<Option<mpsc::Sender<()>>>>,
    ptt_running: Arc<AtomicBool>,
    /// PTT 线程已注册 finalize 信道，可安全 stop
    ptt_finalize_ready: Arc<AtomicBool>,
    /// 最近一次 PTT 截句结果（`stop_ptt_capture` 同步读取，避免仅依赖前端事件）
    ptt_outcome: Arc<Mutex<Option<PttCaptureOutcome>>>,
}

const PTT_START_READY_MS: u64 = 3000;
const PTT_STOP_RETRY_MS: u64 = 3000;
const PTT_STOP_WAIT_MS: u64 = 10000;
const PTT_STOP_POLL_MS: u64 = 30;
const STRICT_PTT_OWNER_MIN_RATIO: f32 = 0.35;
const STRICT_PTT_MAX_SKIPPED_SEGMENTS: usize = 6;

#[derive(Debug, serde::Serialize)]
pub struct CompanionOwnerTrackFilterResult {
    pub accepted: bool,
    pub used_owner_track: bool,
    pub wav_base64: Option<String>,
    pub reason: String,
    pub owner_duration_ms: Option<u32>,
    pub skipped_segments_count: Option<usize>,
}

fn estimate_wav_duration_ms(wav: &[u8]) -> Option<u32> {
    if wav.len() < 44 || &wav.get(0..4)? != b"RIFF" || &wav.get(8..12)? != b"WAVE" {
        return None;
    }
    let mut offset = 12usize;
    let mut channels = 1u16;
    let mut sample_rate = 16_000u32;
    let mut bits_per_sample = 16u16;
    let mut data_bytes = 0u32;
    while offset + 8 <= wav.len() {
        let id = &wav[offset..offset + 4];
        let size = u32::from_le_bytes(wav[offset + 4..offset + 8].try_into().ok()?) as usize;
        let body = offset + 8;
        if body + size > wav.len() {
            break;
        }
        if id == b"fmt " && size >= 16 {
            channels = u16::from_le_bytes(wav[body + 2..body + 4].try_into().ok()?);
            sample_rate = u32::from_le_bytes(wav[body + 4..body + 8].try_into().ok()?);
            bits_per_sample = u16::from_le_bytes(wav[body + 14..body + 16].try_into().ok()?);
        } else if id == b"data" {
            data_bytes = size as u32;
            break;
        }
        offset = body + size + (size % 2);
    }
    let bytes_per_sample = (bits_per_sample as u32 / 8).max(1);
    let bytes_per_sec = sample_rate
        .saturating_mul(channels as u32)
        .saturating_mul(bytes_per_sample);
    if bytes_per_sec == 0 || data_bytes == 0 {
        return None;
    }
    Some(((data_bytes as u64 * 1000) / bytes_per_sec as u64) as u32)
}
fn ensure_jvs_blocking(app: &tauri::AppHandle) -> Result<String, String> {
    let base_url = app
        .try_state::<std::sync::Arc<crate::jvs::process_manager::JvsHandle>>()
        .map(|h| h.status().base_url.clone())
        .unwrap_or_else(|| "http://127.0.0.1:18982".to_string());
    let health_url = format!("{}/health", base_url.trim_end_matches('/'));
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(3))
        .build()
        .map_err(|e| e.to_string())?;
    if client
        .get(&health_url)
        .send()
        .and_then(|r| r.error_for_status())
        .is_ok()
    {
        return Ok(base_url);
    }
    crate::jvs::process_manager::start_jvs_process_sync(app)?;
    Ok(app
        .try_state::<std::sync::Arc<crate::jvs::process_manager::JvsHandle>>()
        .map(|h| h.status().base_url.clone())
        .unwrap_or_else(|| "http://127.0.0.1:18982".to_string()))
}

impl SttState {
    fn capture_busy(&self) -> bool {
        self.running.load(Ordering::Relaxed) || self.ptt_running.load(Ordering::Relaxed)
    }

    fn wait_ptt_finalize_ready(&self, timeout_ms: u64) -> bool {
        let deadline = Instant::now() + Duration::from_millis(timeout_ms);
        while Instant::now() < deadline {
            if self.ptt_finalize_ready.load(Ordering::Acquire) {
                return true;
            }
            if !self.ptt_running.load(Ordering::Relaxed) {
                thread::sleep(Duration::from_millis(PTT_STOP_POLL_MS));
                if self.ptt_finalize_ready.load(Ordering::Acquire) {
                    return true;
                }
                return false;
            }
            thread::sleep(Duration::from_millis(PTT_STOP_POLL_MS));
        }
        false
    }

    /// 创建状态并 spawn 持有线程。应在 setup 中调用一次，然后 manage(state)。
    pub fn new() -> Self {
        let (start_tx, start_rx) = mpsc::channel::<(tauri::AppHandle, PathBuf)>();
        let stop_tx = Arc::new(Mutex::new(None::<mpsc::Sender<()>>));
        let stop_tx_clone = Arc::clone(&stop_tx);
        let running = Arc::new(AtomicBool::new(false));
        let running_clone = Arc::clone(&running);

        thread::spawn(move || {
            while let Ok((app_handle, model_path)) = start_rx.recv() {
                if !model_path.exists() {
                    continue;
                }
                running_clone.store(true, Ordering::Relaxed);
                let guard = match stt::start_listening(app_handle.clone(), model_path) {
                    Ok(g) => g,
                    Err(_) => {
                        running_clone.store(false, Ordering::Relaxed);
                        continue;
                    }
                };
                let (stop_send, stop_recv) = mpsc::channel();
                if let Ok(mut g) = stop_tx_clone.lock() {
                    *g = Some(stop_send);
                }
                let _ = stop_recv.recv();
                drop(guard);
                if let Ok(mut g) = stop_tx_clone.lock() {
                    *g = None;
                }
                running_clone.store(false, Ordering::Relaxed);
            }
        });

        let (ptt_start_tx, ptt_start_rx) = mpsc::channel::<(tauri::AppHandle, PathBuf)>();
        let ptt_finalize_tx = Arc::new(Mutex::new(None::<mpsc::Sender<()>>));
        let ptt_finalize_tx_holder = Arc::clone(&ptt_finalize_tx);
        let ptt_running = Arc::new(AtomicBool::new(false));
        let ptt_running_holder = Arc::clone(&ptt_running);
        let ptt_finalize_ready = Arc::new(AtomicBool::new(false));
        let ptt_finalize_ready_holder = Arc::clone(&ptt_finalize_ready);
        let ptt_outcome = Arc::new(Mutex::new(None::<PttCaptureOutcome>));
        let ptt_outcome_holder = Arc::clone(&ptt_outcome);

        thread::spawn(move || {
            while let Ok((app_handle, model_path)) = ptt_start_rx.recv() {
                if !model_path.exists() {
                    continue;
                }
                ptt_running_holder.store(true, Ordering::Relaxed);
                ptt_finalize_ready_holder.store(false, Ordering::Release);
                if let Ok(mut g) = ptt_outcome_holder.lock() {
                    *g = None;
                }
                let (finalize_send, finalize_recv) = mpsc::channel();
                if let Ok(mut g) = ptt_finalize_tx_holder.lock() {
                    *g = Some(finalize_send);
                }
                ptt_finalize_ready_holder.store(true, Ordering::Release);
                let running_flag = Arc::clone(&ptt_running_holder);
                running_flag.store(true, Ordering::Relaxed);
                let outcome = super::manager::run_ptt_capture(
                    app_handle,
                    model_path,
                    finalize_recv,
                    running_flag,
                );
                if let Ok(mut g) = ptt_outcome_holder.lock() {
                    *g = Some(outcome);
                }
                ptt_finalize_ready_holder.store(false, Ordering::Release);
                if let Ok(mut g) = ptt_finalize_tx_holder.lock() {
                    *g = None;
                }
                ptt_running_holder.store(false, Ordering::Relaxed);
            }
        });

        Self {
            start_tx: Mutex::new(Some(start_tx)),
            stop_tx,
            running,
            ptt_start_tx: Mutex::new(Some(ptt_start_tx)),
            ptt_finalize_tx,
            ptt_running,
            ptt_finalize_ready,
            ptt_outcome,
        }
    }
}

/// 启动语音采集：向持有线程发送 Start，由该线程创建并持有 ListeningGuard。
#[tauri::command]
pub fn start_voice_capture(
    app_handle: tauri::AppHandle,
    state: State<'_, SttState>,
) -> Result<(), String> {
    if state.capture_busy() {
        return Err("麦克风已被占用，请先停止当前语音采集".to_string());
    }
    let model_path = resolve_vad_model_path();
    if !model_path.exists() {
        return Err(format!(
            "VAD 模型不存在: {:?}。请将 silero_vad.onnx 放入该路径，或设置 {} 指向包含 vad 的目录",
            model_path, VAD_DEBUG_PATH_ENV
        ));
    }
    let guard = state.start_tx.lock().map_err(|e| e.to_string())?;
    if let Some(ref tx) = *guard {
        tx.send((app_handle, model_path))
            .map_err(|e| e.to_string())?;
    } else {
        return Err("持有线程已退出".to_string());
    }
    Ok(())
}

/// 停止语音采集：向持有线程发送 Stop，触发 ListeningGuard 的 drop。
#[tauri::command]
pub fn stop_voice_capture(state: State<'_, SttState>) -> Result<(), String> {
    let mut guard = state.stop_tx.lock().map_err(|e| e.to_string())?;
    if let Some(tx) = guard.take() {
        let _ = tx.send(());
    }
    Ok(())
}

/// 当前是否正在采集。
#[tauri::command]
pub fn is_voice_capture_running(state: State<'_, SttState>) -> bool {
    state.running.load(Ordering::Relaxed)
}

/// PTT 按下：Rust cpal 采音，松开 `stop_ptt_capture` 后立即截句并发射 `STT_AUDIO_READY`。
#[tauri::command]
pub fn start_ptt_capture(
    app_handle: tauri::AppHandle,
    state: State<'_, SttState>,
    wake: State<'_, WakeListenerState>,
) -> Result<(), String> {
    if state.capture_busy() {
        return Err("麦克风已被占用，请先停止当前语音采集".to_string());
    }
    wake.stop();
    if !wake.wait_until_stopped(2500) {
        return Err("无法释放麦克风：唤醒监听仍在运行，请稍后重试".to_string());
    }
    let model_path = resolve_vad_model_path();
    if !model_path.exists() {
        return Err(format!(
            "VAD 模型不存在: {:?}。请将 silero_vad.onnx 放入该路径，或设置 {} 指向包含 vad 的目录",
            model_path, VAD_DEBUG_PATH_ENV
        ));
    }
    let guard = state.ptt_start_tx.lock().map_err(|e| e.to_string())?;
    if let Some(ref tx) = *guard {
        tx.send((app_handle, model_path))
            .map_err(|e| e.to_string())?;
    } else {
        return Err("PTT 持有线程已退出".to_string());
    }
    drop(guard);
    if !state.wait_ptt_finalize_ready(PTT_START_READY_MS) {
        return Err("PTT 采音线程启动超时，请重试".to_string());
    }
    Ok(())
}

/// PTT 松开：触发立即截句，等待采音线程完成后返回 WAV（同时仍发射 `STT_AUDIO_READY` 供 VAD 路径复用）。
#[tauri::command]
pub fn stop_ptt_capture(state: State<'_, SttState>) -> Result<SttAudioPayload, String> {
    let deadline = Instant::now() + Duration::from_millis(PTT_STOP_RETRY_MS);
    let mut finalize_sent = false;
    loop {
        {
            let guard = state.ptt_finalize_tx.lock().map_err(|e| e.to_string())?;
            if let Some(ref tx) = *guard {
                tx.send(())
                    .map_err(|e| format!("PTT 截句信号发送失败: {e}"))?;
                finalize_sent = true;
                break;
            }
        }
        if Instant::now() >= deadline {
            break;
        }
        if !state.ptt_running.load(Ordering::Relaxed)
            && !state.ptt_finalize_ready.load(Ordering::Relaxed)
        {
            return Err("PTT 未在运行".to_string());
        }
        thread::sleep(Duration::from_millis(PTT_STOP_POLL_MS));
    }
    if !finalize_sent {
        return Err("PTT 截句超时：采音线程未就绪，请稍候再点「结束」或重新开始录音".to_string());
    }

    let wait_deadline = Instant::now() + Duration::from_millis(PTT_STOP_WAIT_MS);
    while Instant::now() < wait_deadline {
        if let Ok(mut guard) = state.ptt_outcome.lock() {
            if let Some(outcome) = guard.take() {
                return match outcome {
                    PttCaptureOutcome::Ready(payload) => Ok(payload),
                    PttCaptureOutcome::Failed(f) => Err(f.detail),
                };
            }
        }
        if !state.ptt_running.load(Ordering::Relaxed) {
            thread::sleep(Duration::from_millis(PTT_STOP_POLL_MS));
            if let Ok(mut guard) = state.ptt_outcome.lock() {
                if let Some(outcome) = guard.take() {
                    return match outcome {
                        PttCaptureOutcome::Ready(payload) => Ok(payload),
                        PttCaptureOutcome::Failed(f) => Err(f.detail),
                    };
                }
            }
            // 线程已结束但 outcome 可能仍在落锁写入，继续等到 deadline，避免“先报超时后收到 STT_AUDIO_READY”。
            continue;
        }
        thread::sleep(Duration::from_millis(PTT_STOP_POLL_MS));
    }
    Err(
        "未收到录音数据（超时）。若唤醒门卫刚关闭，请再试一次；并确认麦克风权限与默认输入设备。"
            .to_string(),
    )
}

#[tauri::command]
pub fn is_ptt_capture_running(state: State<'_, SttState>) -> bool {
    state.ptt_running.load(Ordering::Relaxed)
}

/// 陪伴态语音按钮：按声纹设置对 PTT WAV 做 owner-track 过滤（仅过滤，不做 STT）。
/// - accepted=false: 严格拒绝本轮（前端不应再送 STT/L3）
/// - accepted=true 且 wav_base64 存在: 用过滤后的 owner 轨继续 STT
/// - accepted=true 且 wav_base64 为空: 回退使用原始 WAV
#[tauri::command]
pub fn companion_filter_owner_track_wav(
    app_handle: tauri::AppHandle,
    wav_base64: String,
) -> Result<CompanionOwnerTrackFilterResult, String> {
    let settings = UserSettings::load();
    let sv_enabled = settings.speaker_verification_enabled.unwrap_or(true);
    let owner_track_enabled = settings.speaker_owner_track_enabled.unwrap_or(true);
    let strict = settings.speaker_verification_strict.unwrap_or(false);
    if !sv_enabled || !owner_track_enabled {
        return Ok(CompanionOwnerTrackFilterResult {
            accepted: true,
            used_owner_track: false,
            wav_base64: None,
            owner_duration_ms: None,
            skipped_segments_count: None,
            reason: "sv_bypass_disabled".to_string(),
        });
    }
    let profile = match load_owner_voiceprint_profile() {
        Ok(Some(p)) => p,
        Ok(None) => {
            return Ok(CompanionOwnerTrackFilterResult {
                accepted: !strict,
                used_owner_track: false,
                wav_base64: None,
                owner_duration_ms: None,
                skipped_segments_count: None,
                reason: if strict {
                    "sv_reject_profile_missing".to_string()
                } else {
                    "sv_bypass_profile_missing".to_string()
                },
            })
        }
        Err(e) => {
            return Ok(CompanionOwnerTrackFilterResult {
                accepted: !strict,
                used_owner_track: false,
                wav_base64: None,
                owner_duration_ms: None,
                skipped_segments_count: None,
                reason: if strict {
                    format!("sv_reject_profile_error:{e}")
                } else {
                    format!("sv_bypass_profile_error:{e}")
                },
            })
        }
    };
    let wav = base64::engine::general_purpose::STANDARD
        .decode(wav_base64.trim().as_bytes())
        .map_err(|e| format!("base64 decode failed: {e}"))?;
    if wav.is_empty() {
        return Ok(CompanionOwnerTrackFilterResult {
            accepted: !strict,
            used_owner_track: false,
            wav_base64: None,
            owner_duration_ms: None,
            skipped_segments_count: None,
            reason: if strict {
                "sv_reject_empty_wav".to_string()
            } else {
                "sv_bypass_empty_wav".to_string()
            },
        });
    }
    let base_url = match ensure_jvs_blocking(&app_handle) {
        Ok(v) => v,
        Err(e) => {
            return Ok(CompanionOwnerTrackFilterResult {
                accepted: !strict,
                used_owner_track: false,
                wav_base64: None,
                owner_duration_ms: None,
                skipped_segments_count: None,
                reason: if strict {
                    format!("sv_reject_jvs_unavailable:{e}")
                } else {
                    format!("sv_bypass_jvs_unavailable:{e}")
                },
            })
        }
    };
    match jvs_filter_owner_track_blocking(&base_url, &wav, &profile) {
        Ok(result) => {
            let owner_duration_ms = result.owner_duration_ms;
            let skipped_segments_count = result.skipped_segments_count;
            let total_duration_ms = estimate_wav_duration_ms(&wav).unwrap_or(0);
            let owner_ratio = if total_duration_ms > 0 {
                owner_duration_ms as f32 / total_duration_ms as f32
            } else {
                1.0
            };
            if strict
                && (owner_duration_ms == 0
                    || owner_ratio < STRICT_PTT_OWNER_MIN_RATIO
                    || skipped_segments_count > STRICT_PTT_MAX_SKIPPED_SEGMENTS)
            {
                return Ok(CompanionOwnerTrackFilterResult {
                    accepted: false,
                    used_owner_track: true,
                    wav_base64: None,
                    owner_duration_ms: Some(owner_duration_ms),
                    skipped_segments_count: Some(skipped_segments_count),
                    reason: format!(
                        "sv_reject_ambiguous_owner_track:ratio={owner_ratio:.2},skipped={skipped_segments_count}"
                    ),
                });
            }
            match result.wav {
                Some(owner_wav) if !owner_wav.is_empty() => Ok(CompanionOwnerTrackFilterResult {
                    accepted: true,
                    used_owner_track: true,
                    wav_base64: Some(base64::engine::general_purpose::STANDARD.encode(owner_wav)),
                    owner_duration_ms: Some(owner_duration_ms),
                    skipped_segments_count: Some(skipped_segments_count),
                    reason: "sv_owner_track_ok".to_string(),
                }),
                _ => Ok(CompanionOwnerTrackFilterResult {
                    accepted: !strict,
                    used_owner_track: true,
                    wav_base64: None,
                    owner_duration_ms: Some(owner_duration_ms),
                    skipped_segments_count: Some(skipped_segments_count),
                    reason: if strict {
                        "sv_reject_no_owner_track".to_string()
                    } else {
                        "sv_bypass_no_owner_track".to_string()
                    },
                }),
            }
        }
        Err(e) => Ok(CompanionOwnerTrackFilterResult {
            accepted: !strict,
            used_owner_track: false,
            wav_base64: None,
            owner_duration_ms: None,
            skipped_segments_count: None,
            reason: if strict {
                format!("sv_reject_filter_error:{e}")
            } else {
                format!("sv_bypass_filter_error:{e}")
            },
        }),
    }
}

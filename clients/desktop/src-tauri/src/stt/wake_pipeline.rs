//! 语音唤起主循环：STT 辅助 KWS → Earcon → Verbal ACK → VAD → JVS STT → 陪伴 inject。
//! 对话窗口内连续提问；出声期间 VAD Barge-in + Audio Masking。

#![cfg(feature = "ambient")]

use super::audio_capture::start_capture;
use super::audio_processor::AudioProcessor;
use super::endpointing::{EndpointingMachine, RecordingState};
use super::speaker_verification::{
    jvs_filter_owner_track_blocking, jvs_verify_blocking, load_owner_voiceprint_profile,
};
use super::vad_engine::SileroVadEngine;
use super::wake_audio::{generate_tone_wav, pcm_f32_to_wav};
use super::wake_barge_in::BargeInDetector;
use super::wake_kws::{normalize_wake_text, transcript_matches_wake, SttAssistedKws};
use crate::config::UserSettings;
use crate::jvs::process_manager::JvsHandle;
use crate::l3_spawn;
use crate::voice_playback;
use crate::voice_session;
use crate::wake_ack;
use crossbeam_channel::unbounded;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::Receiver;
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant};
use tauri::{AppHandle, Emitter, Manager};

const CHUNK_LEN: usize = 512;
const SAMPLE_RATE: u32 = 16000;
const LISTENING_TIMEOUT_MS: u64 = 6000;
const COOLDOWN_MS: u64 = 1500;
const CONVERSATION_WINDOW_SEC: u64 = 60;
const RING_BUFFER_SEC: f64 = 2.0;
const WAKE_VERIFY_SLICE_SEC: f64 = 1.5;
const BARGE_REARM_SEC: u64 = 8;
const BARGE_COOLDOWN_MS: u64 = 450;
const VAD_MODEL_FILENAME: &str = "silero_vad.onnx";
const VAD_DEBUG_PATH_ENV: &str = "JACHIN_VAD_DEBUG_PATH";
const PORTABLE_DATA_DIR: &str = "_portable_data";

#[derive(Clone, Copy, PartialEq, Eq)]
enum WakePhase {
    KwsIdle,
    WakeCapture,
    Conversation,
    Cooldown,
}

pub struct WakePipelineConfig {
    pub wake_word: String,
}

fn resolve_vad_model_path() -> PathBuf {
    // 1) 显式调试目录（最高优先级）
    if let Ok(debug_path) = std::env::var(VAD_DEBUG_PATH_ENV) {
        return PathBuf::from(debug_path.trim())
            .join("vad")
            .join(VAD_MODEL_FILENAME);
    }
    // 2) JACHIN_APP_ROOT（start-layer3.ps1 会设置），优先使用仓库 data/vad
    if let Ok(app_root) = std::env::var("JACHIN_APP_ROOT") {
        let p = PathBuf::from(app_root)
            .join("data")
            .join("vad")
            .join(VAD_MODEL_FILENAME);
        if p.exists() {
            return p;
        }
    }
    // 3) 以当前工作目录推断仓库 data/vad（兼容直接 cargo run）
    if let Ok(cwd) = std::env::current_dir() {
        let p = cwd.join("data").join("vad").join(VAD_MODEL_FILENAME);
        if p.exists() {
            return p;
        }
    }
    // 4) 可移植安装目录
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
    // 5) 用户目录（默认）
    directories::ProjectDirs::from("com", "jachin", "desktop")
        .map(|d| d.data_local_dir().join("vad").join(VAD_MODEL_FILENAME))
        .unwrap_or_else(|| PathBuf::from("data/vad").join(VAD_MODEL_FILENAME))
}

fn resolve_jvs_base_url(app: &AppHandle) -> String {
    app.try_state::<Arc<JvsHandle>>()
        .map(|h| h.status().base_url.clone())
        .unwrap_or_else(|| "http://127.0.0.1:18982".to_string())
}

fn play_wav_bytes(bytes: &[u8]) {
    if let Err(e) = voice_playback::play_wav_bytes_sync(bytes) {
        l3_spawn::write_voice_companion_debug("rust", "wake.play_fail", &e, "");
    }
}

fn play_wake_earcon() {
    play_wav_bytes(&generate_tone_wav(880.0, 120, SAMPLE_RATE));
    l3_spawn::write_voice_companion_debug("rust", "wake.earcon", "ok", "");
}

fn play_timeout_earcon() {
    play_wav_bytes(&generate_tone_wav(440.0, 280, SAMPLE_RATE));
    l3_spawn::write_voice_companion_debug("rust", "wake.timeout_tone", "ok", "");
}

fn emit_wake_up(app: &AppHandle, wake_word: &str) {
    let _ = app.emit(
        super::keyword_spotting::WakeWordDetector::WAKE_UP_EVENT,
        serde_json::json!({
            "source": "wake_pipeline",
            "wake_word": wake_word,
        }),
    );
    l3_spawn::write_voice_companion_debug("rust", "wake.detected", wake_word, "");
}

fn emit_barge_in(app: &AppHandle, source: &str) {
    let _ = app.emit("voice-barge-in", serde_json::json!({ "source": source }));
    l3_spawn::write_voice_companion_debug("rust", "barge_in", source, "");
}

fn trigger_barge_in(
    app: &AppHandle,
    endpointing: &mut EndpointingMachine,
    barge: &mut BargeInDetector,
    ring_buffer: &[[f32; CHUNK_LEN]],
    barge_latched: &mut bool,
    barge_latched_until: &mut Option<Instant>,
) -> bool {
    if *barge_latched {
        return false;
    }
    if !voice_playback::is_playing() && !voice_session::companion_phase_is_thinking_or_speaking() {
        return false;
    }
    voice_playback::stop_playback_sync();
    emit_barge_in(app, "rust_vad");
    *barge_latched = true;
    *barge_latched_until = Some(Instant::now() + Duration::from_secs(BARGE_REARM_SEC));
    barge.reset();
    barge.set_cooldown(Duration::from_millis(BARGE_COOLDOWN_MS));
    if let Err(e) = endpointing.seed_from_ring(ring_buffer, true) {
        l3_spawn::write_voice_companion_debug("rust", "wake.rearm_seed_fail", &e, "");
        endpointing.reset();
    }
    true
}

fn append_ring(ring: &mut Vec<[f32; CHUNK_LEN]>, chunk: [f32; CHUNK_LEN], max_chunks: usize) {
    ring.push(chunk);
    while ring.len() > max_chunks {
        ring.remove(0);
    }
}

fn clear_barge_latch_if_expired(
    barge_latched: &mut bool,
    barge_latched_until: &mut Option<Instant>,
    endpointing: &mut EndpointingMachine,
) {
    if *barge_latched {
        if let Some(until) = *barge_latched_until {
            if Instant::now() > until {
                *barge_latched = false;
                *barge_latched_until = None;
                endpointing.reset();
                l3_spawn::write_voice_companion_debug("rust", "wake.rearm_timeout", "", "");
            }
        }
    }
}

fn session_active() -> bool {
    crate::HUD_VOICE_SESSION_ACTIVE.load(Ordering::Relaxed)
}

fn should_monitor_barge_in() -> bool {
    voice_session::should_monitor_barge_in(session_active())
}

fn blocking_jvs_stt(base_url: &str, wav: &[u8]) -> Result<String, String> {
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(45))
        .build()
        .map_err(|e| e.to_string())?;
    let part = reqwest::blocking::multipart::Part::bytes(wav.to_vec())
        .mime_str("audio/wav")
        .map_err(|e| e.to_string())?
        .file_name("speech.wav");
    let form = reqwest::blocking::multipart::Form::new().part("audio", part);
    let url = format!("{}/v1/stt/transcribe", base_url.trim_end_matches('/'));
    let resp = client
        .post(&url)
        .multipart(form)
        .send()
        .map_err(|e| e.to_string())?;
    if !resp.status().is_success() {
        return Err(format!("JVS STT status {}", resp.status()));
    }
    let json: serde_json::Value = resp.json().map_err(|e| e.to_string())?;
    Ok(json
        .get("text")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_string())
}

fn ensure_jvs_blocking(app: &AppHandle) -> Result<String, String> {
    let base_url = resolve_jvs_base_url(app);
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
    let app_clone = app.clone();
    tauri::async_runtime::block_on(async move {
        crate::jvs::process_manager::start_jvs_process(&app_clone).await
    })?;
    Ok(resolve_jvs_base_url(app))
}

fn sv_gate_enabled(settings: &UserSettings) -> bool {
    settings.speaker_verification_enabled.unwrap_or(true)
}

fn sv_strict_mode(settings: &UserSettings) -> bool {
    settings.speaker_verification_strict.unwrap_or(false)
}

fn sv_owner_track_enabled(settings: &UserSettings) -> bool {
    settings.speaker_owner_track_enabled.unwrap_or(true)
}

fn build_wake_slice_from_ring(ring: &[[f32; CHUNK_LEN]]) -> Vec<f32> {
    if ring.is_empty() {
        return Vec::new();
    }
    let target_samples = (SAMPLE_RATE as f64 * WAKE_VERIFY_SLICE_SEC)
        .round()
        .max(0.0) as usize;
    let target_chunks = (target_samples / CHUNK_LEN).max(1);
    let start = ring.len().saturating_sub(target_chunks);
    let mut out = Vec::with_capacity((ring.len() - start) * CHUNK_LEN);
    for chunk in &ring[start..] {
        out.extend_from_slice(chunk);
    }
    out
}

fn pass_wake_speaker_gate(app: &AppHandle, ring_buffer: &[[f32; CHUNK_LEN]], reason: &str) -> bool {
    let settings = UserSettings::load();
    if !sv_gate_enabled(&settings) {
        return true;
    }
    let strict = sv_strict_mode(&settings);
    let profile = match load_owner_voiceprint_profile() {
        Ok(Some(p)) => p,
        Ok(None) => {
            l3_spawn::write_voice_companion_debug("rust", "sv.wake_profile_missing", reason, "");
            return !strict;
        }
        Err(e) => {
            l3_spawn::write_voice_companion_debug("rust", "sv.wake_profile_error", &e, "");
            return !strict;
        }
    };
    let slice = build_wake_slice_from_ring(ring_buffer);
    if slice.is_empty() {
        l3_spawn::write_voice_companion_debug("rust", "sv.wake_slice_empty", reason, "");
        return !strict;
    }
    let wav = pcm_f32_to_wav(&slice, SAMPLE_RATE);
    let base = match ensure_jvs_blocking(app) {
        Ok(v) => v,
        Err(e) => {
            l3_spawn::write_voice_companion_debug("rust", "sv.wake_jvs_fail", &e, "");
            return !strict;
        }
    };
    let threshold = profile.wake_threshold_high();
    match jvs_verify_blocking(&base, &wav, &profile, threshold) {
        Ok(v) => {
            l3_spawn::write_voice_companion_debug(
                "rust",
                if v.is_match {
                    "sv.wake_accept"
                } else {
                    "sv.wake_reject"
                },
                &format!("score={:.3}, reason={reason}", v.score),
                "",
            );
            v.is_match
        }
        Err(e) => {
            l3_spawn::write_voice_companion_debug("rust", "sv.wake_verify_fail", &e, "");
            !strict
        }
    }
}

fn apply_owner_track_filter(
    base_url: &str,
    source_wav: &[u8],
    settings: &UserSettings,
) -> Result<Option<Vec<u8>>, String> {
    if !sv_gate_enabled(settings) || !sv_owner_track_enabled(settings) {
        return Ok(Some(source_wav.to_vec()));
    }
    let strict = sv_strict_mode(settings);
    let profile = match load_owner_voiceprint_profile() {
        Ok(Some(p)) => p,
        Ok(None) => {
            l3_spawn::write_voice_companion_debug("rust", "sv.owner_profile_missing", "", "");
            return Ok(if strict {
                None
            } else {
                Some(source_wav.to_vec())
            });
        }
        Err(e) => {
            l3_spawn::write_voice_companion_debug("rust", "sv.owner_profile_error", &e, "");
            return Ok(if strict {
                None
            } else {
                Some(source_wav.to_vec())
            });
        }
    };

    let owner_filter = match jvs_filter_owner_track_blocking(base_url, source_wav, &profile) {
        Ok(v) => v,
        Err(e) => {
            l3_spawn::write_voice_companion_debug("rust", "sv.owner_filter_fail", &e, "");
            if strict {
                return Ok(None);
            }
            return Ok(Some(source_wav.to_vec()));
        }
    };
    let Some(owner_wav) = owner_filter.wav else {
        l3_spawn::write_voice_companion_debug(
            "rust",
            "sv.owner_empty",
            &format!(
                "owner_duration_ms={} skipped={}",
                owner_filter.owner_duration_ms, owner_filter.skipped_segments_count
            ),
            "",
        );
        return Ok(None);
    };

    let threshold = profile.window_threshold_high();
    match jvs_verify_blocking(base_url, &owner_wav, &profile, threshold) {
        Ok(v) => {
            if v.is_match {
                l3_spawn::write_voice_companion_debug(
                    "rust",
                    "sv.owner_accept",
                    &format!("score={:.3}", v.score),
                    "",
                );
                Ok(Some(owner_wav))
            } else {
                l3_spawn::write_voice_companion_debug(
                    "rust",
                    "sv.owner_reject",
                    &format!("score={:.3}", v.score),
                    "",
                );
                Ok(None)
            }
        }
        Err(e) => {
            l3_spawn::write_voice_companion_debug("rust", "sv.owner_verify_fail", &e, "");
            Ok(if strict { None } else { Some(owner_wav) })
        }
    }
}

fn play_verbal_ack_if_enabled() {
    let settings = UserSettings::load();
    if let Some(bytes) = wake_ack::pick_wake_ack_bytes(&settings) {
        l3_spawn::write_voice_companion_debug(
            "rust",
            "wake.verbal_ack",
            &format!("bytes={}", bytes.len()),
            "",
        );
        play_wav_bytes(&bytes);
    }
}

fn enter_wake_capture(
    app: &AppHandle,
    wake_word: &str,
    endpointing: &mut EndpointingMachine,
    barge: &mut BargeInDetector,
) -> WakePhase {
    let _ = barge;
    if voice_playback::is_playing() || voice_session::companion_phase_is_thinking_or_speaking() {
        // enter_wake_capture 由调用方传入 ring/barge 状态；此处仅停播并唤醒 UI
        voice_playback::stop_playback_sync();
        emit_barge_in(app, "rust_vad");
    }
    emit_wake_up(app, wake_word);
    play_wake_earcon();
    play_verbal_ack_if_enabled();
    endpointing.reset();
    endpointing.state = RecordingState::Idle;
    endpointing.audio_buffer.clear();
    endpointing.silence_frames = 0;
    endpointing.total_frames = 0;
    endpointing.vad_engine.reset_states();
    WakePhase::WakeCapture
}

fn process_utterance(app: &AppHandle, audio: Vec<f32>, wake_word: &str) -> bool {
    let wav = pcm_f32_to_wav(&audio, SAMPLE_RATE);
    l3_spawn::write_voice_companion_debug(
        "rust",
        "wake.utterance_ready",
        &format!("bytes={}", wav.len()),
        "",
    );
    match ensure_jvs_blocking(app) {
        Ok(base) => {
            let settings = UserSettings::load();
            let sv_wav = match apply_owner_track_filter(&base, &wav, &settings) {
                Ok(Some(v)) => v,
                Ok(None) => {
                    play_timeout_earcon();
                    l3_spawn::write_voice_companion_debug(
                        "rust",
                        "sv.owner_drop_utterance",
                        "owner track missing/rejected",
                        "",
                    );
                    return false;
                }
                Err(e) => {
                    play_timeout_earcon();
                    l3_spawn::write_voice_companion_debug("rust", "sv.owner_filter_error", &e, "");
                    return false;
                }
            };
            match blocking_jvs_stt(&base, &sv_wav) {
                Ok(text) => {
                    let trimmed = text.trim();
                    if trimmed.is_empty() {
                        play_timeout_earcon();
                        l3_spawn::write_voice_companion_debug("rust", "wake.stt_empty", "", "");
                        return false;
                    }
                    let cmd = strip_wake_prefix(trimmed, wake_word);
                    if cmd.trim().is_empty() {
                        l3_spawn::write_voice_companion_debug(
                            "rust",
                            "wake.stt_only_wake",
                            trimmed,
                            "",
                        );
                        return false;
                    }
                    l3_spawn::write_voice_companion_debug(
                        "rust",
                        "wake.stt_ok",
                        &cmd.chars().take(120).collect::<String>(),
                        "",
                    );
                    crate::voice_wake_bridge::inject_companion_user(app, &cmd);
                    true
                }
                Err(e) => {
                    play_timeout_earcon();
                    l3_spawn::write_voice_companion_debug("rust", "wake.stt_fail", &e, "");
                    false
                }
            }
        }
        Err(e) => {
            l3_spawn::write_voice_companion_debug("rust", "wake.jvs_fail", &e, "");
            false
        }
    }
}

fn extend_conversation(deadline: &mut Option<Instant>) {
    *deadline = Some(Instant::now() + Duration::from_secs(CONVERSATION_WINDOW_SEC));
}

pub struct WakePipelineGuard {
    _stream: cpal::Stream,
    running: Arc<AtomicBool>,
    _join_processor: thread::JoinHandle<()>,
    _join_main: thread::JoinHandle<()>,
}

impl Drop for WakePipelineGuard {
    fn drop(&mut self) {
        self.running.store(false, Ordering::Relaxed);
    }
}

pub fn start_wake_pipeline(
    app: AppHandle,
    config: WakePipelineConfig,
    rx_manual: Receiver<()>,
) -> Result<WakePipelineGuard, String> {
    let vad_path = resolve_vad_model_path();
    if !vad_path.exists() {
        return Err(format!(
            "VAD 模型不存在: {:?}。请将 silero_vad.onnx 放入该路径，或设置 {}",
            vad_path, VAD_DEBUG_PATH_ENV
        ));
    }

    let (stream, rx_raw, sample_rate, source_channels) = start_capture()?;
    let (tx_chunk, rx_chunk) = unbounded::<[f32; CHUNK_LEN]>();
    let (tx_kws_hit, rx_kws_hit) = unbounded::<()>();

    let running = Arc::new(AtomicBool::new(true));
    let running_p = Arc::clone(&running);
    let running_m = Arc::clone(&running);

    let join_processor = thread::spawn(move || {
        if let Ok(mut processor) = AudioProcessor::new(sample_rate, source_channels) {
            let _ = processor.process_stream(&rx_raw, &running_p, |chunk| {
                let _ = tx_chunk.send(chunk);
            });
        }
    });

    let wake_word = config.wake_word.clone();
    let join_main = thread::spawn(move || {
        let mut phase = WakePhase::KwsIdle;
        let mut kws = SttAssistedKws::new(wake_word.clone());
        let mut endpointing = match SileroVadEngine::new(&vad_path) {
            Ok(v) => EndpointingMachine::new(v),
            Err(e) => {
                l3_spawn::write_voice_companion_debug("rust", "wake.vad_fail", &e, "");
                return;
            }
        };
        let mut barge_detector = BargeInDetector::new();
        let mut wake_capture_started: Option<Instant> = None;
        let mut speech_started = false;
        let mut kws_cooldown_until: Option<Instant> = None;
        let mut conversation_until: Option<Instant> = None;
        let mut barge_latched = false;
        let mut barge_latched_until: Option<Instant> = None;
        let mut ring_buffer: Vec<[f32; CHUNK_LEN]> = Vec::new();
        let ring_max_chunks = ((RING_BUFFER_SEC * SAMPLE_RATE as f64) / CHUNK_LEN as f64)
            .ceil()
            .max(8.0) as usize;

        while running_m.load(Ordering::Relaxed) {
            clear_barge_latch_if_expired(
                &mut barge_latched,
                &mut barge_latched_until,
                &mut endpointing,
            );

            if rx_manual.try_recv().is_ok() && phase == WakePhase::KwsIdle {
                if pass_wake_speaker_gate(&app, &ring_buffer, "manual") {
                    phase =
                        enter_wake_capture(&app, &wake_word, &mut endpointing, &mut barge_detector);
                    wake_capture_started = Some(Instant::now());
                    speech_started = false;
                    conversation_until = None;
                    barge_latched = false;
                    barge_latched_until = None;
                }
            }

            if rx_kws_hit.try_recv().is_ok() {
                if phase == WakePhase::KwsIdle {
                    if pass_wake_speaker_gate(&app, &ring_buffer, "kws") {
                        phase = enter_wake_capture(
                            &app,
                            &wake_word,
                            &mut endpointing,
                            &mut barge_detector,
                        );
                        wake_capture_started = Some(Instant::now());
                        speech_started = false;
                        conversation_until = None;
                        barge_latched = false;
                        barge_latched_until = None;
                    }
                } else if voice_playback::is_playing()
                    || voice_session::companion_phase_is_thinking_or_speaking()
                {
                    let _ = trigger_barge_in(
                        &app,
                        &mut endpointing,
                        &mut barge_detector,
                        &ring_buffer,
                        &mut barge_latched,
                        &mut barge_latched_until,
                    );
                    play_wake_earcon();
                    play_verbal_ack_if_enabled();
                    extend_conversation(&mut conversation_until);
                }
            }

            if phase == WakePhase::Cooldown {
                if let Some(until) = kws_cooldown_until {
                    if Instant::now() >= until {
                        phase = WakePhase::KwsIdle;
                        kws_cooldown_until = None;
                    }
                }
                thread::sleep(Duration::from_millis(50));
                continue;
            }

            if phase == WakePhase::Conversation {
                if let Some(until) = conversation_until {
                    if Instant::now() >= until
                        && !voice_playback::is_playing()
                        && !barge_latched
                        && !voice_session::companion_phase_is_thinking_or_speaking()
                    {
                        phase = WakePhase::KwsIdle;
                        conversation_until = None;
                        l3_spawn::write_voice_companion_debug(
                            "rust",
                            "wake.conversation_end",
                            "",
                            "",
                        );
                    }
                } else {
                    phase = WakePhase::KwsIdle;
                }
            }

            match rx_chunk.recv_timeout(Duration::from_millis(100)) {
                Ok(chunk) => {
                    append_ring(&mut ring_buffer, chunk, ring_max_chunks);

                    let masking = voice_playback::is_playing();
                    let thinking = voice_session::companion_phase_is_thinking_or_speaking();
                    let listen_phase =
                        phase == WakePhase::WakeCapture || phase == WakePhase::Conversation;
                    let monitor_barge = listen_phase && should_monitor_barge_in();

                    let vad_prob = if listen_phase || monitor_barge || barge_latched {
                        endpointing.vad_engine.process_chunk(&chunk).unwrap_or(0.0)
                    } else {
                        0.0
                    };

                    let mut latched = barge_latched;
                    let mut barged_this_chunk = false;
                    if listen_phase && !latched && (masking || thinking) {
                        if barge_detector.feed(vad_prob, masking) {
                            barged_this_chunk = trigger_barge_in(
                                &app,
                                &mut endpointing,
                                &mut barge_detector,
                                &ring_buffer,
                                &mut barge_latched,
                                &mut barge_latched_until,
                            );
                            if barged_this_chunk {
                                latched = true;
                            }
                        }
                    }

                    if phase == WakePhase::KwsIdle && !masking && !thinking {
                        if let Some(window) = kws.feed(&chunk) {
                            let app_c = app.clone();
                            let ww = wake_word.clone();
                            let tx_hit = tx_kws_hit.clone();
                            thread::spawn(move || {
                                let base = match ensure_jvs_blocking(&app_c) {
                                    Ok(url) => url,
                                    Err(e) => {
                                        l3_spawn::write_voice_companion_debug(
                                            "rust",
                                            "wake.kws_jvs_unready",
                                            &e,
                                            "",
                                        );
                                        resolve_jvs_base_url(&app_c)
                                    }
                                };
                                let wav = pcm_f32_to_wav(&window, SAMPLE_RATE);
                                match blocking_jvs_stt(&base, &wav) {
                                    Ok(text) => {
                                        l3_spawn::write_voice_companion_debug(
                                            "rust",
                                            "wake.kws_stt",
                                            &text.chars().take(80).collect::<String>(),
                                            "",
                                        );
                                        if transcript_matches_wake(&text, &ww) {
                                            let _ = tx_hit.send(());
                                        }
                                    }
                                    Err(e) => {
                                        l3_spawn::write_voice_companion_debug(
                                            "rust",
                                            "wake.kws_stt_fail",
                                            &e,
                                            "",
                                        );
                                    }
                                }
                            });
                        }
                    }

                    if listen_phase {
                        if masking && !latched {
                            continue;
                        }
                        if thinking && !latched {
                            continue;
                        }
                        if barged_this_chunk {
                            continue;
                        }
                        if let Ok(Some(audio)) =
                            endpointing.feed_chunk_with_prob(&chunk, vad_prob, latched)
                        {
                            barge_latched = false;
                            barge_latched_until = None;
                            let ok = process_utterance(&app, audio, &wake_word);
                            if ok {
                                phase = WakePhase::Conversation;
                                extend_conversation(&mut conversation_until);
                            } else {
                                phase = WakePhase::Cooldown;
                                kws_cooldown_until =
                                    Some(Instant::now() + Duration::from_millis(COOLDOWN_MS));
                            }
                            wake_capture_started = None;
                            speech_started = false;
                        } else if endpointing.state == RecordingState::Speaking {
                            speech_started = true;
                        }
                    }
                }
                Err(crossbeam_channel::RecvTimeoutError::Timeout) => {}
                Err(crossbeam_channel::RecvTimeoutError::Disconnected) => break,
            }

            if phase == WakePhase::WakeCapture {
                if let Some(started) = wake_capture_started {
                    if !speech_started
                        && started.elapsed() > Duration::from_millis(LISTENING_TIMEOUT_MS)
                    {
                        play_timeout_earcon();
                        l3_spawn::write_voice_companion_debug("rust", "wake.timeout", "", "");
                        phase = WakePhase::Cooldown;
                        kws_cooldown_until =
                            Some(Instant::now() + Duration::from_millis(COOLDOWN_MS));
                        wake_capture_started = None;
                    }
                }
            }
        }
    });

    Ok(WakePipelineGuard {
        _stream: stream,
        running,
        _join_processor: join_processor,
        _join_main: join_main,
    })
}

fn strip_wake_prefix(text: &str, wake_word: &str) -> String {
    let t = text.trim();
    let w = wake_word.trim();
    if t.eq_ignore_ascii_case(w) {
        return String::new();
    }
    let tn = normalize_wake_text(t);
    let wn = normalize_wake_text(w);
    if !wn.is_empty() && tn.starts_with(&wn) {
        if t.len() > w.len() {
            return t[w.len().min(t.len())..].trim().to_string();
        }
        return String::new();
    }
    t.to_string()
}

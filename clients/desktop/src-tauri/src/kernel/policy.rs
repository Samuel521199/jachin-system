//! Policy - 策略生成与用户覆盖合并
//!
//! 根据 HardwareProfile 和 UserSettings 生成 RuntimeConfig，
//! 用户覆盖优先于自动检测。

use crate::config::UserSettings;
use serde::{Deserialize, Serialize};
use sysinfo::System;

/// 硬件配置快照（用于策略决策）
#[derive(Debug, Clone)]
pub struct HardwareProfile {
    /// 是否有足够 VRAM/显存运行本地 LLM（当前用 RAM 作为代理检测）
    pub vram_ok: bool,
    /// 是否有足够 RAM 运行本地 TTS/STT（>= 1GB 可用）
    pub ram_ok: bool,
}

impl HardwareProfile {
    /// 从当前系统检测硬件配置
    pub fn detect() -> Self {
        let mut sys = System::new_all();
        sys.refresh_memory();

        let free_ram = sys.available_memory();
        let ram_ok = free_ram >= 1024 * 1024 * 1024; // 1GB

        // VRAM 检测：当前无 GPU 库，用 RAM >= 4GB 作为本地 LLM 能力代理
        let vram_ok = free_ram >= 4 * 1024 * 1024 * 1024; // 4GB

        Self { vram_ok, ram_ok }
    }
}

/// 运行时配置（策略输出）
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RuntimeConfig {
    pub llm_provider: String,
    pub tts_provider: String,
    pub stt_provider: String,
    pub run_mode: String,
}

/// 根据硬件配置和用户设置生成运行时策略
///
/// 合并策略：用户覆盖 > 自动检测
pub fn generate_policy(profile: HardwareProfile, settings: &UserSettings) -> RuntimeConfig {
    let llm_provider = resolve_llm_provider(&profile, settings);
    let tts_provider = resolve_tts_provider(&profile, settings);
    let stt_provider = resolve_stt_provider(&profile, settings);
    let run_mode = resolve_run_mode(settings);

    RuntimeConfig {
        llm_provider,
        tts_provider,
        stt_provider,
        run_mode,
    }
}

fn resolve_llm_provider(profile: &HardwareProfile, settings: &UserSettings) -> String {
    if let Some(ref choice) = settings.llm_provider_override {
        let c = choice.to_lowercase().trim().to_string();
        eprintln!(
            "[Kernel] LLM Provider: {} (User Override)",
            capitalize_first(&c)
        );
        return c;
    }
    let choice = if profile.vram_ok { "local" } else { "cloud" };
    eprintln!(
        "[Kernel] LLM Provider: {} (Auto-Detected)",
        capitalize_first(choice)
    );
    choice.to_string()
}

fn resolve_tts_provider(profile: &HardwareProfile, settings: &UserSettings) -> String {
    if let Some(ref choice) = settings.tts_provider_override {
        let c = choice.to_lowercase().trim().to_string();
        eprintln!(
            "[Kernel] TTS Provider: {} (User Override)",
            capitalize_first(&c)
        );
        return c;
    }
    let choice = if profile.ram_ok { "local" } else { "edge" };
    eprintln!(
        "[Kernel] TTS Provider: {} (Auto-Detected)",
        capitalize_first(choice)
    );
    choice.to_string()
}

fn resolve_stt_provider(profile: &HardwareProfile, settings: &UserSettings) -> String {
    if let Some(ref choice) = settings.stt_provider_override {
        let c = choice.to_lowercase().trim().to_string();
        eprintln!(
            "[Kernel] STT Provider: {} (User Override)",
            capitalize_first(&c)
        );
        return c;
    }
    let choice = if profile.ram_ok { "local" } else { "cloud" };
    eprintln!(
        "[Kernel] STT Provider: {} (Auto-Detected)",
        capitalize_first(choice)
    );
    choice.to_string()
}

fn resolve_run_mode(settings: &UserSettings) -> String {
    if let Some(ref mode) = settings.run_mode_override {
        let m = mode.trim().to_string();
        eprintln!("[Kernel] Run Mode: {} (User Override)", m);
        return m;
    }
    eprintln!("[Kernel] Run Mode: Standalone (Default)");
    "standalone".to_string()
}

fn capitalize_first(s: &str) -> String {
    let mut c = s.chars();
    match c.next() {
        None => String::new(),
        Some(f) => f.to_uppercase().chain(c).collect(),
    }
}

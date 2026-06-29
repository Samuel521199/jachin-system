//! 唤醒链路用 WAV 编码与提示音生成。

#![cfg(feature = "ambient")]

pub fn pcm_f32_to_wav(samples: &[f32], sample_rate: u32) -> Vec<u8> {
    use std::io::Write;
    let mut buf = Vec::new();
    let num_channels = 1u16;
    let bits_per_sample = 16u16;
    let byte_rate = sample_rate * num_channels as u32 * bits_per_sample as u32 / 8;
    let block_align = num_channels * bits_per_sample / 8;
    let data_size = samples.len() * 2;

    let _ = buf.write_all(b"RIFF");
    let _ = buf.write_all(&(36 + data_size as u32).to_le_bytes());
    let _ = buf.write_all(b"WAVE");
    let _ = buf.write_all(b"fmt ");
    let _ = buf.write_all(&16u32.to_le_bytes());
    let _ = buf.write_all(&1u16.to_le_bytes());
    let _ = buf.write_all(&num_channels.to_le_bytes());
    let _ = buf.write_all(&sample_rate.to_le_bytes());
    let _ = buf.write_all(&byte_rate.to_le_bytes());
    let _ = buf.write_all(&block_align.to_le_bytes());
    let _ = buf.write_all(&bits_per_sample.to_le_bytes());
    let _ = buf.write_all(b"data");
    let _ = buf.write_all(&(data_size as u32).to_le_bytes());
    for &s in samples {
        let clamped = (s * 32767.0).clamp(-32768.0, 32767.0) as i16;
        let _ = buf.write_all(&clamped.to_le_bytes());
    }
    buf
}

/// 生成短促提示音 WAV（16kHz mono）。
pub fn generate_tone_wav(freq_hz: f32, duration_ms: u32, sample_rate: u32) -> Vec<u8> {
    let n = (sample_rate as f64 * duration_ms as f64 / 1000.0).round() as usize;
    let mut samples = Vec::with_capacity(n);
    for i in 0..n {
        let t = i as f32 / sample_rate as f32;
        let env = if i < n / 8 {
            i as f32 / (n as f32 / 8.0)
        } else if i > n - n / 8 {
            (n - i) as f32 / (n as f32 / 8.0)
        } else {
            1.0
        };
        let s = (2.0 * std::f32::consts::PI * freq_hz * t).sin() * 0.35 * env;
        samples.push(s);
    }
    pcm_f32_to_wav(&samples, sample_rate)
}

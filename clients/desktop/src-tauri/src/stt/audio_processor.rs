//! 音频重采样与 16kHz / 512 样本切片
//!
//! 从采集线程的 Receiver 消费数据，转为单声道、16kHz，并按 512 样本一块送出，供 VAD 使用。

#![cfg(feature = "ambient")]
#![allow(dead_code)]

use crossbeam_channel::Receiver;
use std::time::Duration;
use rubato::{Resampler, SincFixedIn, SincInterpolationParameters, SincInterpolationType, WindowFunction};
use std::sync::atomic::{AtomicBool, Ordering};

const TARGET_SAMPLE_RATE: u32 = 16000;
const CHUNK_SAMPLES: usize = 512;

/// 将源采样率重采样到 16kHz，并输出 512 样本的固定长度切片。
pub struct AudioProcessor {
    source_sample_rate: u32,
    source_channels: usize,
    resampler: SincFixedIn<f32>,
    /// 预分配的重采样输入块（单声道，按 channel 分）
    resampler_input_buf: Vec<Vec<f32>>,
    /// 预分配的重采样输出
    resampler_output_buf: Vec<Vec<f32>>,
    /// 单声道输入缓冲：累积到 resampler 所需帧数再送入
    input_buffer: Vec<f32>,
    /// 16kHz 输出缓冲：累积到 >= 512 再切块送出
    output_buffer: Vec<f32>,
}

impl AudioProcessor {
    /// 创建处理器。`source_sample_rate` 为麦克风采样率（如 48000），`source_channels` 为 1 或 2。
    pub fn new(source_sample_rate: u32, source_channels: usize) -> Result<Self, String> {
        let ratio = TARGET_SAMPLE_RATE as f64 / source_sample_rate as f64;
        let params = SincInterpolationParameters {
            sinc_len: 256,
            f_cutoff: 0.95,
            oversampling_factor: 256,
            interpolation: SincInterpolationType::Linear,
            window: WindowFunction::BlackmanHarris2,
        };
        let chunk_size = 1024;
        let resampler = SincFixedIn::<f32>::new(
            ratio,
            2.0,
            params,
            chunk_size,
            1,
        ).map_err(|e| format!("创建重采样器失败: {}", e))?;

        let resampler_input_buf = resampler.input_buffer_allocate(false);
        let mut resampler_output_buf = resampler.output_buffer_allocate(false);
        resampler_output_buf[0].resize(resampler.output_frames_max(), 0.0);

        Ok(Self {
            source_sample_rate,
            source_channels,
            resampler,
            resampler_input_buf,
            resampler_output_buf,
            input_buffer: Vec::with_capacity(chunk_size * 2),
            output_buffer: Vec::with_capacity(CHUNK_SAMPLES * 2),
        })
    }

    /// 将一段 PCM 转为单声道（若为双声道则取平均）。
    fn to_mono(&self, raw: &[f32]) -> Vec<f32> {
        if self.source_channels <= 1 {
            return raw.to_vec();
        }
        raw.chunks_exact(self.source_channels)
            .map(|c| c.iter().sum::<f32>() / self.source_channels as f32)
            .collect()
    }

    /// 处理一块已转为单声道的样本：送入重采样，输出写入内部 output_buffer。
    fn process_mono_chunk(&mut self, mono: &[f32]) -> Result<(), String> {
        self.input_buffer.extend_from_slice(mono);

        let needed = self.resampler.input_frames_next();
        while self.input_buffer.len() >= needed {
            let (take, _) = self.input_buffer.split_at(needed);
            self.resampler_input_buf[0].clear();
            self.resampler_input_buf[0].extend_from_slice(take);
            self.input_buffer.drain(..needed);

            let (_, out_len) = self
                .resampler
                .process_into_buffer(
                    &self.resampler_input_buf[..],
                    &mut self.resampler_output_buf[..],
                    None,
                )
                .map_err(|e| format!("重采样失败: {}", e))?;

            if out_len > 0 && !self.resampler_output_buf[0].is_empty() {
                let out = &self.resampler_output_buf[0][..out_len];
                self.output_buffer.extend_from_slice(out);
            }
        }
        Ok(())
    }

    /// 从内部 output_buffer 中取出 512 样本块，通过回调送出；返回本轮送出的块数。
    fn emit_chunks<F>(&mut self, mut on_chunk: F) -> usize
    where
        F: FnMut([f32; CHUNK_SAMPLES]),
    {
        let mut emitted = 0;
        while self.output_buffer.len() >= CHUNK_SAMPLES {
            let mut arr = [0f32; CHUNK_SAMPLES];
            arr.copy_from_slice(&self.output_buffer[..CHUNK_SAMPLES]);
            self.output_buffer.drain(..CHUNK_SAMPLES);
            on_chunk(arr);
            emitted += 1;
        }
        emitted
    }

    /// 在独立线程中运行：从 `rx` 持续取数据，重采样并切 512 块，每块调用 `on_chunk`。
    /// `running` 为 false 时退出。
    pub fn process_stream<F>(
        &mut self,
        rx: &Receiver<Vec<f32>>,
        running: &AtomicBool,
        mut on_chunk: F,
    ) -> Result<(), String>
    where
        F: FnMut([f32; CHUNK_SAMPLES]),
    {
        while running.load(Ordering::Relaxed) {
            match rx.recv_timeout(Duration::from_millis(100)) {
                Ok(raw) => {
                    let mono = self.to_mono(&raw);
                    self.process_mono_chunk(&mono)?;
                    self.emit_chunks(&mut on_chunk);
                }
                Err(crossbeam_channel::RecvTimeoutError::Timeout) => continue,
                Err(crossbeam_channel::RecvTimeoutError::Disconnected) => break,
            }
        }
        Ok(())
    }
}

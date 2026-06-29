//! 麦克风采集与无锁数据流
//!
//! 遵循 057-voice-endpointing：在 cpal 回调内仅做拷贝并发送到 channel，不做任何阻塞操作。

#![cfg(feature = "ambient")]
#![allow(dead_code)]

use crossbeam_channel::{unbounded, Receiver, Sender};
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use cpal::{SampleFormat, Stream};
use std::sync::Arc;

/// 启动配置：使用系统默认麦克风，将 PCM 送入无界 channel。
/// 返回 (流句柄, 接收端, 原始采样率, 原始声道数)。流句柄必须保持存活，否则采集停止。
pub fn start_capture() -> Result<(Stream, Receiver<Vec<f32>>, u32, usize), String> {
    let host = cpal::default_host();
    let device = host
        .default_input_device()
        .ok_or_else(|| "未找到默认麦克风".to_string())?;

    let config = device
        .default_input_config()
        .map_err(|e| format!("获取默认输入配置失败: {}", e))?;

    let stream_config: cpal::StreamConfig = config.clone().into();
    let sample_rate = config.sample_rate().0;
    let source_channels = stream_config.channels as usize;
    let (tx, rx) = unbounded::<Vec<f32>>();

    let stream = match config.sample_format() {
        SampleFormat::F32 => build_stream_f32(&device, &stream_config, tx)?,
        SampleFormat::I16 => build_stream_i16(&device, &stream_config, tx)?,
        SampleFormat::U16 => build_stream_u16(&device, &stream_config, tx)?,
        _ => return Err("不支持的麦克风采样格式（仅支持 f32/i16/u16）".to_string()),
    };

    stream.play().map_err(|e| format!("启动音频流失败: {}", e))?;

    Ok((stream, rx, sample_rate, source_channels))
}

fn build_stream_f32(
    device: &cpal::Device,
    config: &cpal::StreamConfig,
    tx: Sender<Vec<f32>>,
) -> Result<Stream, String> {
    let tx = Arc::new(tx);
    let stream = device
        .build_input_stream(
            config,
            move |data: &[f32], _: &cpal::InputCallbackInfo| {
                let _ = tx.send(data.to_vec());
            },
            move |err| {
                let _ = err;
            },
            None,
        )
        .map_err(|e| format!("构建输入流失败: {}", e))?;
    Ok(stream)
}

fn build_stream_i16(
    device: &cpal::Device,
    config: &cpal::StreamConfig,
    tx: Sender<Vec<f32>>,
) -> Result<Stream, String> {
    let tx = Arc::new(tx);
    let stream = device
        .build_input_stream(
            config,
            move |data: &[i16], _: &cpal::InputCallbackInfo| {
                let out: Vec<f32> = data
                    .iter()
                    .map(|&s| s as f32 / 32768.0f32)
                    .collect();
                let _ = tx.send(out);
            },
            move |_| {},
            None,
        )
        .map_err(|e| format!("构建输入流失败: {}", e))?;
    Ok(stream)
}

fn build_stream_u16(
    device: &cpal::Device,
    config: &cpal::StreamConfig,
    tx: Sender<Vec<f32>>,
) -> Result<Stream, String> {
    let tx = Arc::new(tx);
    let stream = device
        .build_input_stream(
            config,
            move |data: &[u16], _: &cpal::InputCallbackInfo| {
                let out: Vec<f32> = data
                    .iter()
                    .map(|&s| (s as f32 / 32768.0f32) - 1.0)
                    .collect();
                let _ = tx.send(out);
            },
            move |_| {},
            None,
        )
        .map_err(|e| format!("构建输入流失败: {}", e))?;
    Ok(stream)
}

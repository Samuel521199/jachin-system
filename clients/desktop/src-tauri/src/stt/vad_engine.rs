//! Silero VAD ONNX 推理引擎
//!
//! 带 RNN 状态记忆：每帧将上一帧的 state 传入，并更新为 stateN。支持 64 样本上下文 + 512 块。

#![cfg(feature = "ambient")]
#![allow(dead_code)]

use ort::session::Session;
use ort::value::Tensor;
use std::path::Path;

/// Silero VAD 官方模型：input [1, 576]（64 上下文 + 512），state [2, 1, 128]，sr [1] i64。
const CONTEXT_SAMPLES: usize = 64;
const CHUNK_SAMPLES: usize = 512;
const EFFECTIVE_INPUT: usize = CONTEXT_SAMPLES + CHUNK_SAMPLES; // 576
const STATE_SHAPE: [usize; 3] = [2, 1, 128];
const STATE_LEN: usize = 2 * 1 * 128;
const SAMPLE_RATE: i64 = 16000;

pub struct SileroVadEngine {
    session: Session,
    /// 隐状态 [2, 1, 128] 展平，每帧后由 stateN 更新
    state: Vec<f32>,
    /// 上一块的有效输入末尾 64 样本，作为本块输入的上下文
    context: Vec<f32>,
}

impl SileroVadEngine {
    /// 从 ONNX 模型路径加载，状态与上下文置零。
    pub fn new(model_path: &Path) -> Result<Self, String> {
        let session = Session::builder()
            .map_err(|e| e.to_string())?
            .commit_from_file(model_path)
            .map_err(|e| format!("加载 Silero VAD 模型失败: {}", e))?;

        let state = vec![0.0f32; STATE_LEN];
        let context = vec![0.0f32; CONTEXT_SAMPLES];

        Ok(Self {
            session,
            state,
            context,
        })
    }

    /// 新对话开始前调用，将隐状态与上下文清零。
    pub fn reset_states(&mut self) {
        self.state.fill(0.0);
        self.context.fill(0.0);
    }

    /// 处理一帧 512 样本，返回人声概率 [0.0, 1.0]。内部会拼 64 上下文 + 512 送入模型。
    pub fn process_chunk(&mut self, chunk: &[f32]) -> Result<f32, String> {
        if chunk.len() != CHUNK_SAMPLES {
            return Err(format!(
                "chunk 长度须为 {}，当前 {}",
                CHUNK_SAMPLES,
                chunk.len()
            ));
        }

        let mut input_buf = Vec::with_capacity(EFFECTIVE_INPUT);
        input_buf.extend_from_slice(&self.context);
        input_buf.extend_from_slice(chunk);

        let input_tensor = Tensor::from_array(([1usize, EFFECTIVE_INPUT], input_buf.clone()))
            .map_err(|e| e.to_string())?;
        let state_tensor =
            Tensor::from_array((STATE_SHAPE, self.state.clone())).map_err(|e| e.to_string())?;
        let sr_arr: Vec<i64> = vec![SAMPLE_RATE];
        let sr_tensor = Tensor::from_array(([1usize], sr_arr)).map_err(|e| e.to_string())?;

        let outputs = self
            .session
            .run(ort::inputs![
                "input" => input_tensor,
                "state" => state_tensor,
                "sr" => sr_tensor,
            ])
            .map_err(|e| format!("VAD 推理失败: {}", e))?;

        let prob = outputs
            .get("output")
            .and_then(|v| v.try_extract_tensor::<f32>().ok())
            .map(|(_, s)| s.iter().next().copied().unwrap_or(0.0))
            .unwrap_or(0.0);

        if let Some(state_out) = outputs.get("stateN") {
            let (_, state_slice) = state_out
                .try_extract_tensor::<f32>()
                .map_err(|e| e.to_string())?;
            let n = STATE_LEN.min(state_slice.len());
            self.state[..n].copy_from_slice(&state_slice[..n]);
        }

        let ctx_start = input_buf.len() - CONTEXT_SAMPLES;
        self.context.copy_from_slice(&input_buf[ctx_start..]);

        Ok(prob)
    }
}

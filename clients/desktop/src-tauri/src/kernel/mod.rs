//! Kernel - 策略与运行时配置

pub mod policy;

#[allow(unused_imports)]
pub use policy::{generate_policy, HardwareProfile, RuntimeConfig};

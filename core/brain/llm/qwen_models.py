"""
Qwen Models Configuration - Qwen模型配置

定义Qwen支持的各种模型及其特性
"""

from enum import Enum
from typing import Dict, List, Set
from dataclasses import dataclass


class QwenModel(str, Enum):
    """Qwen支持的模型"""
    # 文本模型
    QWEN_TURBO = "qwen-turbo"
    QWEN_PLUS = "qwen-plus"
    QWEN_MAX = "qwen-max"
    QWEN_FLASH = "qwen-flash"
    QWEN_CODER = "qwen-coder"
    
    # 视觉模型
    QWEN_VL = "qwen-vl"
    QWEN_VL_MAX = "qwen-vl-max"
    QWEN_VL_PLUS = "qwen-vl-plus"
    
    # 全模态模型
    QWEN_OMNI = "qwen-omni"
    QWEN3_OMNI = "qwen3-omni"
    
    # 音频模型
    QWEN_AUDIO = "qwen-audio"
    QWEN3_OMNI_CAPTIONER = "qwen3-omni-captioner"
    
    # 其他
    QVQ = "qvq"
    TEXT_EMBEDDING_V2 = "text-embedding-v2"


@dataclass
class ModelCapabilities:
    """模型能力"""
    supports_text: bool = True
    supports_stream: bool = True
    supports_image: bool = False
    supports_video: bool = False
    supports_audio: bool = False
    supports_web_search: bool = False
    supports_tool_call: bool = False
    supports_async: bool = False
    supports_document: bool = False
    requires_stream: bool = False  # 某些模型仅支持流式输出


# 模型能力映射
MODEL_CAPABILITIES: Dict[QwenModel, ModelCapabilities] = {
    # 文本模型
    QwenModel.QWEN_TURBO: ModelCapabilities(
        supports_text=True,
        supports_stream=True,
        supports_tool_call=True,
    ),
    QwenModel.QWEN_PLUS: ModelCapabilities(
        supports_text=True,
        supports_stream=True,
        supports_tool_call=True,
        supports_web_search=True,
    ),
    QwenModel.QWEN_MAX: ModelCapabilities(
        supports_text=True,
        supports_stream=True,
        supports_tool_call=True,
        supports_web_search=True,
    ),
    QwenModel.QWEN_FLASH: ModelCapabilities(
        supports_text=True,
        supports_stream=True,
        supports_tool_call=True,
    ),
    QwenModel.QWEN_CODER: ModelCapabilities(
        supports_text=True,
        supports_stream=True,
    ),
    
    # 视觉模型
    QwenModel.QWEN_VL: ModelCapabilities(
        supports_text=True,
        supports_stream=True,
        supports_image=True,
        requires_stream=True,  # Qwen-VL仅支持流式输出
    ),
    QwenModel.QWEN_VL_MAX: ModelCapabilities(
        supports_text=True,
        supports_stream=True,
        supports_image=True,
        requires_stream=True,
    ),
    QwenModel.QWEN_VL_PLUS: ModelCapabilities(
        supports_text=True,
        supports_stream=True,
        supports_image=True,
        requires_stream=True,
    ),
    
    # 全模态模型
    QwenModel.QWEN_OMNI: ModelCapabilities(
        supports_text=True,
        supports_stream=True,
        supports_image=True,
        supports_video=True,
        supports_audio=True,
        requires_stream=True,  # Qwen-Omni仅支持流式输出
    ),
    QwenModel.QWEN3_OMNI: ModelCapabilities(
        supports_text=True,
        supports_stream=True,
        supports_image=True,
        supports_video=True,
        supports_audio=True,
        supports_web_search=True,
        supports_tool_call=True,
        requires_stream=True,
    ),
    
    # 音频模型
    QwenModel.QWEN_AUDIO: ModelCapabilities(
        supports_text=True,
        supports_stream=True,
        supports_audio=True,
    ),
    QwenModel.QWEN3_OMNI_CAPTIONER: ModelCapabilities(
        supports_text=True,
        supports_stream=True,
        supports_audio=True,
    ),
    
    # 其他
    QwenModel.QVQ: ModelCapabilities(
        supports_text=True,
        supports_stream=True,
        supports_image=True,
        requires_stream=True,
    ),
    
    # 嵌入模型
    QwenModel.TEXT_EMBEDDING_V2: ModelCapabilities(
        supports_text=True,
        supports_stream=False,
    ),
}


def get_model_capabilities(model: str) -> ModelCapabilities:
    """
    获取模型能力
    
    Args:
        model: 模型名称
        
    Returns:
        ModelCapabilities对象
    """
    try:
        qwen_model = QwenModel(model.lower())
        return MODEL_CAPABILITIES.get(qwen_model, ModelCapabilities())
    except ValueError:
        # 未知模型，返回默认能力（仅文本和流式）
        return ModelCapabilities()


def is_stream_required(model: str) -> bool:
    """
    检查模型是否仅支持流式输出
    
    Args:
        model: 模型名称
        
    Returns:
        是否仅支持流式
    """
    capabilities = get_model_capabilities(model)
    return capabilities.requires_stream


def supports_modality(model: str, modality: str) -> bool:
    """
    检查模型是否支持某种模态
    
    Args:
        model: 模型名称
        modality: 模态类型（image, video, audio等）
        
    Returns:
        是否支持
    """
    capabilities = get_model_capabilities(model)
    modality_map = {
        "image": capabilities.supports_image,
        "video": capabilities.supports_video,
        "audio": capabilities.supports_audio,
        "web_search": capabilities.supports_web_search,
        "tool_call": capabilities.supports_tool_call,
        "document": capabilities.supports_document,
    }
    return modality_map.get(modality, False)

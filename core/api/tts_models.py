"""
TTS Models API - 供 Tier 3 获取 TTS 模型文件

GET /api/v2/tts/models/{filename} 返回模型资源文件
"""

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from core.config import settings

router = APIRouter(prefix="/api/v2/tts", tags=["tts-models"])

ALLOWED_FILES = {"kokoro-v0_19.onnx", "voices.json", "config.json", "zm.bin"}


def _models_dir() -> Path:
    base = settings.TTS_MODELS_DIR or os.path.join(os.getcwd(), "data", "tts")
    return Path(base)


@router.get("/models/{filename}")
async def get_model_file(filename: str):
    """
    获取 TTS 模型文件（供 Tier 3 桌面客户端下载）

    支持: tts 模型文件白名单
    """
    if filename not in ALLOWED_FILES:
        raise HTTPException(status_code=404, detail=f"Unknown file: {filename}")
    path = _models_dir() / filename
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"File not found: {filename}. Place it in {_models_dir()}",
        )
    return FileResponse(path, filename=filename)

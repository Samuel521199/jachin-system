"""
Token 计数工具 - 供 Context 统计使用
优先使用 tiktoken 精确统计，否则回退到 len/4 估算（中文更准确）
"""

import logging

logger = logging.getLogger(__name__)

_encoder = None


def _get_encoder():
    """延迟加载 tiktoken 编码器"""
    global _encoder
    if _encoder is not None:
        return _encoder
    try:
        import tiktoken
        _encoder = tiktoken.get_encoding("cl100k_base")
        return _encoder
    except ImportError:
        logger.debug("tiktoken not installed, using len/4 fallback")
        return None
    except Exception as e:
        logger.debug("tiktoken init failed: %s", e)
        return None


def count_tokens(text: str) -> int:
    """
    统计文本 token 数
    - 若 tiktoken 可用：使用 cl100k_base 精确统计
    - 否则：len/4 估算（中文约 1.5 字/token，英文约 4 字/token，取折中）
    """
    if not text:
        return 0
    enc = _get_encoder()
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception as e:
            logger.debug("tiktoken encode failed: %s", e)
    return max(1, len(text) // 4)


def count_messages_tokens(messages: list) -> int:
    """统计消息列表的 token 数（含 role 等开销）"""
    total = 0
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, str):
            total += count_tokens(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and "text" in part:
                    total += count_tokens(part["text"])
        total += 4  # 每条消息的 role/content 等开销
    return total

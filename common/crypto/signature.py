"""
Signature Verification - 签名验证工具
用于验证 .jsp 插件的开发者签名

注意：此文件仅包含签名验证逻辑
严禁包含业务逻辑代码
"""

import base64
import hashlib
import logging
from typing import Optional
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.backends import default_backend

logger = logging.getLogger(__name__)


class SignatureVerifier:
    """
    签名验证器
    
    用于验证 Tier 1 Market 签发的插件签名
    """
    
    @staticmethod
    def verify_signature(
        data: bytes,
        signature: str,
        public_key_pem: bytes
    ) -> bool:
        """
        验证签名
        
        Args:
            data: 原始数据（通常是 manifest.yaml 的内容）
            signature: Base64 编码的签名
            public_key_pem: PEM 格式的公钥
            
        Returns:
            是否验证通过
        """
        try:
            # 解码签名
            signature_bytes = base64.b64decode(signature)
            
            # 加载公钥
            public_key = serialization.load_pem_public_key(
                public_key_pem,
                backend=default_backend()
            )
            
            # 计算数据哈希
            hash_algorithm = hashes.SHA256()
            hasher = hashes.Hash(hash_algorithm, backend=default_backend())
            hasher.update(data)
            digest = hasher.finalize()
            
            # 验证签名
            public_key.verify(
                signature_bytes,
                digest,
                padding.PSS(
                    mgf=padding.MGF1(hash_algorithm),
                    salt_length=padding.PSS.MAX_LENGTH
                ),
                hash_algorithm
            )
            
            return True
            
        except Exception as e:
            logger.error(f"Signature verification failed: {e}")
            return False
    
    @staticmethod
    def compute_hash(data: bytes) -> str:
        """
        计算数据的 SHA256 哈希值
        
        Args:
            data: 原始数据
            
        Returns:
            Base64 编码的哈希值
        """
        hash_obj = hashlib.sha256(data)
        return base64.b64encode(hash_obj.digest()).decode('utf-8')

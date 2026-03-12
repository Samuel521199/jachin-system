"""
Jachin Nexus V2 - 零信任密钥加密管理

L3 节点注册时生成 RSA 密钥对，将公钥发给 L2。
L2 下发 API Key 时，使用 L3 公钥加密，仅 L3 私钥可解密。
L2 本地存储用 Master Key 对称加密，绝不暴露明文。
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
from typing import Optional

logger = logging.getLogger(__name__)

# Master Key 用于 L2 本地加密存储，从环境变量读取，缺省时生成临时（仅开发）
_MASTER_KEY_ENV = "JACHIN_L2_MASTER_KEY"


def _get_master_key() -> bytes:
    """获取 L2 Master Key（32 字节，用于 AES-256）"""
    key = os.environ.get(_MASTER_KEY_ENV)
    if key:
        raw = key.encode("utf-8") if isinstance(key, str) else key
        return hashlib.sha256(raw).digest()
    # 开发环境：生成并记录，生产必须设置 JACHIN_L2_MASTER_KEY
    fallback = secrets.token_bytes(32)
    logger.warning(
        "[CryptoManager] JACHIN_L2_MASTER_KEY 未设置，使用临时 Key。生产环境必须设置环境变量。"
    )
    return fallback


def hash_key_for_audit(plain_key: str) -> str:
    """对明文 Key 做 SHA-256 哈希，用于审计和防重、防篡改"""
    return hashlib.sha256(plain_key.encode("utf-8")).hexdigest()


def encrypt_for_l3(plain_key: str, l3_public_key_pem: str) -> str:
    """
    使用 L3 节点的公钥加密 API Key。
    L3 持私钥，可解密；第三方无法破解。

    :param plain_key: 明文 API Key
    :param l3_public_key_pem: L3 的 RSA 公钥（PEM 格式）
    :return: Base64 编码的密文
    """
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.backends import default_backend
    except ImportError:
        raise RuntimeError("需要安装 cryptography: pip install cryptography")

    import hashlib as _hl
    public_key = serialization.load_pem_public_key(
        l3_public_key_pem.encode("utf-8"), backend=default_backend()
    )
    ciphertext = public_key.encrypt(
        plain_key.encode("utf-8"),
        padding.OAEP(
            mgf=padding.MGF1(algorithm=_hl.sha256()),
            algorithm=_hl.sha256(),
            label=None,
        ),
    )
    return base64.b64encode(ciphertext).decode("ascii")


def decrypt_with_l3_private_key(encrypted_b64: str, l3_private_key_pem: str) -> str:
    """
    L3 使用本地私钥解密 API Key。
    仅内存中使用，禁止落盘或打入日志。

    :param encrypted_b64: Base64 编码的密文
    :param l3_private_key_pem: L3 的 RSA 私钥（PEM 格式）
    :return: 明文 API Key
    """
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.backends import default_backend
    except ImportError:
        raise RuntimeError("需要安装 cryptography: pip install cryptography")

    import hashlib as _hl
    private_key = serialization.load_pem_private_key(
        l3_private_key_pem.encode("utf-8"), password=None, backend=default_backend()
    )
    ciphertext = base64.b64decode(encrypted_b64.encode("ascii"))
    plaintext = private_key.decrypt(
        ciphertext,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=_hl.sha256()),
            algorithm=_hl.sha256(),
            label=None,
        ),
    )
    return plaintext.decode("utf-8")


def encrypt_for_storage(plain_key: str) -> str:
    """
    L2 使用 Master Key 对称加密存储。
    用于 api_keys_vault.encrypted_key 字段。
    """
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        raise RuntimeError("需要安装 cryptography: pip install cryptography")

    master = _get_master_key()
    nonce = secrets.token_bytes(12)
    aesgcm = AESGCM(master)
    ct = aesgcm.encrypt(nonce, plain_key.encode("utf-8"), None)
    combined = nonce + ct
    return base64.b64encode(combined).decode("ascii")


def decrypt_from_storage(encrypted_b64: str) -> str:
    """L2 从存储解密 API Key（仅内部使用，用于加密后下发给 L3）"""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        raise RuntimeError("需要安装 cryptography: pip install cryptography")

    master = _get_master_key()
    raw = base64.b64decode(encrypted_b64.encode("ascii"))
    nonce, ct = raw[:12], raw[12:]
    aesgcm = AESGCM(master)
    pt = aesgcm.decrypt(nonce, ct, None)
    return pt.decode("utf-8")


def generate_rsa_keypair() -> tuple[str, str]:
    """
    生成 RSA 密钥对，供 L3 节点注册时使用。
    :return: (private_key_pem, public_key_pem)
    """
    try:
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.backends import default_backend
    except ImportError:
        raise RuntimeError("需要安装 cryptography: pip install cryptography")

    private_key = rsa.generate_private_key(
        public_exponent=65537, key_size=2048, backend=default_backend()
    )
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    return private_pem, public_pem

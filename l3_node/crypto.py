"""
L3 本地解密（与 core.security.crypto_manager 对称）

L3 持私钥，解密 L2 下发的密文 Key。
严禁将明文落盘或打入日志。
"""
from __future__ import annotations


def decrypt_with_private_key(encrypted_b64: str, private_key_pem: str) -> str:
    """
    L3 使用本地私钥解密 API Key。
    仅内存中使用，禁止落盘或打入日志。
    """
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.backends import default_backend
    except ImportError:
        raise RuntimeError("需要安装 cryptography: pip install cryptography")

    import base64
    import hashlib as _hl

    private_key = serialization.load_pem_private_key(
        private_key_pem.encode("utf-8"), password=None, backend=default_backend()
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


def generate_rsa_keypair() -> tuple[str, str]:
    """生成 RSA 密钥对，供 L3 注册时使用。"""
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


def public_key_from_private(private_key_pem: str) -> str:
    """从私钥导出公钥（PEM 格式）。"""
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.backends import default_backend
    except ImportError:
        raise RuntimeError("需要安装 cryptography: pip install cryptography")

    private_key = serialization.load_pem_private_key(
        private_key_pem.encode("utf-8"), password=None, backend=default_backend()
    )
    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    return public_pem

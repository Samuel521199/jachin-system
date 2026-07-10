"""
mTLS Certificate Manager
证书签发与验证管理器

职责：
- 管理 Tier 2 服务器证书
- 验证 Tier 3 客户端证书
- 生成自签名 CA（开发环境）或与 Tier 1 CA 通信
- 签发客户端证书（基于 CSR）
- 生成配对二维码（Server ID + Token）
"""

import logging
import json
import secrets
import ipaddress
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Tuple, Dict, Any
from cryptography import x509
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend
from cryptography.x509.oid import NameOID

logger = logging.getLogger(__name__)


class MTLSManager:
    """
    mTLS 证书管理器

    负责：
    - Tier 2 服务器证书管理
    - Tier 3 客户端证书验证
    - 自签名 CA 生成（开发环境）
    - Client CSR 签发
    - 与 Tier 1 CA 的通信（生产环境）
    """

    def __init__(self, cert_dir: Path, server_id: str = "jachin-hive-001"):
        """
        初始化证书管理器

        Args:
            cert_dir: 证书存储目录
            server_id: 服务器唯一标识
        """
        self.cert_dir = Path(cert_dir)
        self.cert_dir.mkdir(parents=True, exist_ok=True)
        self.server_id = server_id

        # 证书路径
        self.ca_cert_path = self.cert_dir / "ca.crt"
        self.ca_key_path = self.cert_dir / "ca.key"
        self.server_cert_path = self.cert_dir / "server.crt"
        self.server_key_path = self.cert_dir / "server.key"

        # 客户端证书存储
        self.client_certs_dir = self.cert_dir / "clients"
        self.client_certs_dir.mkdir(parents=True, exist_ok=True)

    def generate_ca(self, common_name: str = "Jachin Root CA", validity_days: int = 3650) -> Tuple[x509.Certificate, rsa.RSAPrivateKey]:
        """
        生成自签名 CA 证书（用于开发环境）

        Args:
            common_name: CA 通用名称
            validity_days: 有效期（天）

        Returns:
            (CA 证书, CA 私钥) 元组
        """
        # 如果 CA 已存在，直接加载
        if self.ca_cert_path.exists() and self.ca_key_path.exists():
            logger.info("Loading existing CA certificate")
            return self._load_ca_certificate()

        logger.info("Generating new CA certificate...")

        # 生成 CA 私钥
        ca_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
            backend=default_backend()
        )

        # 创建 CA 证书
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COUNTRY_NAME, "CN"),
            x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Beijing"),
            x509.NameAttribute(NameOID.LOCALITY_NAME, "Beijing"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Jachin System"),
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        ])

        ca_cert = x509.CertificateBuilder().subject_name(
            subject
        ).issuer_name(
            issuer
        ).public_key(
            ca_key.public_key()
        ).serial_number(
            x509.random_serial_number()
        ).not_valid_before(
            datetime.utcnow()
        ).not_valid_after(
            datetime.utcnow() + timedelta(days=validity_days)
        ).add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("jachin-ca.local"),
            ]),
            critical=False,
        ).add_extension(
            x509.BasicConstraints(ca=True, path_length=None),
            critical=True,
        ).add_extension(
            x509.KeyUsage(
                key_cert_sign=True,
                crl_sign=True,
                digital_signature=True,
                key_encipherment=False,
                content_commitment=False,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        ).sign(ca_key, hashes.SHA256(), default_backend())

        # 保存 CA 证书和私钥
        self._save_certificate(self.ca_cert_path, ca_cert)
        self._save_private_key(self.ca_key_path, ca_key)

        logger.info(f"CA certificate generated: {common_name}")
        return ca_cert, ca_key

    def generate_server_certificate(self, hostname: str = "localhost", validity_days: int = 365) -> Tuple[x509.Certificate, rsa.RSAPrivateKey]:
        """
        生成 Tier 2 服务器证书

        Args:
            hostname: 服务器主机名
            validity_days: 有效期（天）

        Returns:
            (服务器证书, 服务器私钥) 元组
        """
        # 如果服务器证书已存在，直接加载
        if self.server_cert_path.exists() and self.server_key_path.exists():
            logger.info("Loading existing server certificate")
            return self.load_server_certificate()

        logger.info(f"Generating server certificate for {hostname}...")

        # 确保 CA 存在
        ca_cert, ca_key = self.generate_ca()

        # 生成服务器私钥
        server_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
            backend=default_backend()
        )

        # 创建服务器证书请求
        subject = x509.Name([
            x509.NameAttribute(NameOID.COUNTRY_NAME, "CN"),
            x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Beijing"),
            x509.NameAttribute(NameOID.LOCALITY_NAME, "Beijing"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Jachin System"),
            x509.NameAttribute(NameOID.COMMON_NAME, hostname),
        ])

        # 创建服务器证书
        server_cert = x509.CertificateBuilder().subject_name(
            subject
        ).issuer_name(
            ca_cert.subject
        ).public_key(
            server_key.public_key()
        ).serial_number(
            x509.random_serial_number()
        ).not_valid_before(
            datetime.utcnow()
        ).not_valid_after(
            datetime.utcnow() + timedelta(days=validity_days)
        ).add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName(hostname),
                x509.DNSName("localhost"),
                x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
            ]),
            critical=False,
        ).add_extension(
            x509.ExtendedKeyUsage([
                x509.ExtendedKeyUsageOID.SERVER_AUTH,
            ]),
            critical=True,
        ).sign(ca_key, hashes.SHA256(), default_backend())

        # 保存服务器证书和私钥
        self._save_certificate(self.server_cert_path, server_cert)
        self._save_private_key(self.server_key_path, server_key)

        logger.info(f"Server certificate generated for {hostname}")
        return server_cert, server_key

    def sign_client_csr(self, csr: x509.CertificateSigningRequest, device_id: str, validity_days: int = 365) -> Tuple[x509.Certificate, bytes]:
        """
        签发客户端证书（基于 CSR）

        Args:
            csr: 客户端证书签名请求
            device_id: 设备唯一标识
            validity_days: 有效期（天）

        Returns:
            (客户端证书, PEM 格式证书字节) 元组
        """
        logger.info(f"Signing client CSR for device: {device_id}")

        # 确保 CA 存在
        ca_cert, ca_key = self.generate_ca()

        # 创建客户端证书
        client_cert = x509.CertificateBuilder().subject_name(
            csr.subject
        ).issuer_name(
            ca_cert.subject
        ).public_key(
            csr.public_key()
        ).serial_number(
            x509.random_serial_number()
        ).not_valid_before(
            datetime.utcnow()
        ).not_valid_after(
            datetime.utcnow() + timedelta(days=validity_days)
        ).add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName(f"{device_id}.jachin.local"),
            ]),
            critical=False,
        ).add_extension(
            x509.ExtendedKeyUsage([
                x509.ExtendedKeyUsageOID.CLIENT_AUTH,
            ]),
            critical=True,
        ).add_extension(
            # 在证书中嵌入设备 ID
            x509.UnrecognizedExtension(
                oid=x509.ObjectIdentifier("1.3.6.1.4.1.99999.1"),  # 私有 OID
                value=device_id.encode('utf-8')
            ),
            critical=False,
        ).sign(ca_key, hashes.SHA256(), default_backend())

        # 保存客户端证书
        client_cert_path = self.client_certs_dir / f"{device_id}.crt"
        self._save_certificate(client_cert_path, client_cert)

        # 返回 PEM 格式
        cert_pem = client_cert.public_bytes(serialization.Encoding.PEM)

        logger.info(f"Client certificate signed for device: {device_id}")
        return client_cert, cert_pem

    def load_server_certificate(self) -> Tuple[Optional[x509.Certificate], Optional[rsa.RSAPrivateKey]]:
        """
        加载 Tier 2 服务器证书和私钥

        Returns:
            (证书, 私钥) 元组，如果不存在则返回 (None, None)
        """
        if not self.server_cert_path.exists() or not self.server_key_path.exists():
            return None, None

        try:
            # 加载证书
            with open(self.server_cert_path, "rb") as f:
                cert = x509.load_pem_x509_certificate(f.read(), default_backend())

            # 加载私钥
            with open(self.server_key_path, "rb") as f:
                key = serialization.load_pem_private_key(f.read(), password=None, backend=default_backend())

            return cert, key
        except Exception as e:
            logger.error(f"Failed to load server certificate: {e}")
            return None, None

    def _load_ca_certificate(self) -> Tuple[x509.Certificate, rsa.RSAPrivateKey]:
        """加载 CA 证书和私钥"""
        with open(self.ca_cert_path, "rb") as f:
            ca_cert = x509.load_pem_x509_certificate(f.read(), default_backend())
        with open(self.ca_key_path, "rb") as f:
            ca_key = serialization.load_pem_private_key(f.read(), password=None, backend=default_backend())
        return ca_cert, ca_key

    def _save_certificate(self, path: Path, cert: x509.Certificate):
        """保存证书到文件"""
        with open(path, "wb") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))

    def _save_private_key(self, path: Path, key: rsa.RSAPrivateKey):
        """保存私钥到文件"""
        with open(path, "wb") as f:
            f.write(key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()
            ))

    def verify_server_certificate(self, server_cert: x509.Certificate) -> Tuple[bool, Optional[str]]:
        """
        验证 Tier 2 服务器证书

        Args:
            server_cert: 服务器证书

        Returns:
            (验证是否通过, 服务器 ID) 元组
        """
        try:
            # 1. 加载 CA 证书
            if not self.ca_cert_path.exists():
                logger.error("CA certificate not found")
                return False, None

            with open(self.ca_cert_path, "rb") as f:
                ca_cert = x509.load_pem_x509_certificate(f.read(), default_backend())

            # 2. 验证证书链（检查是否由 CA 签名）
            try:
                # 使用证书的签名算法来验证
                signature_algorithm = server_cert.signature_algorithm_oid

                # 根据签名算法选择对应的哈希算法
                # 注意：需要使用 HashAlgorithm 类，而不是实例
                if signature_algorithm == x509.SignatureAlgorithmOID.RSA_WITH_SHA256:
                    hash_algorithm = hashes.SHA256()
                elif signature_algorithm == x509.SignatureAlgorithmOID.RSA_WITH_SHA384:
                    hash_algorithm = hashes.SHA384()
                elif signature_algorithm == x509.SignatureAlgorithmOID.RSA_WITH_SHA512:
                    hash_algorithm = hashes.SHA512()
                else:
                    # 默认使用 SHA256
                    hash_algorithm = hashes.SHA256()

                # 验证签名
                # 注意：对于 RSA 密钥，需要使用 padding
                from cryptography.hazmat.primitives.asymmetric import padding
                ca_public_key = ca_cert.public_key()

                if isinstance(ca_public_key, rsa.RSAPublicKey):
                    # RSA 密钥需要 padding
                    ca_public_key.verify(
                        server_cert.signature,
                        server_cert.tbs_certificate_bytes,
                        padding.PKCS1v15(),
                        hash_algorithm
                    )
                else:
                    # 其他类型的密钥（如 ECDSA）
                    ca_public_key.verify(
                        server_cert.signature,
                        server_cert.tbs_certificate_bytes,
                        hash_algorithm
                    )
            except Exception as e:
                logger.error(f"Certificate signature verification failed: {e}")
                return False, None

            # 3. 检查证书是否过期
            from datetime import timezone
            now = datetime.now(timezone.utc)
            if server_cert.not_valid_before_utc > now or server_cert.not_valid_after_utc < now:
                logger.error("Certificate expired or not yet valid")
                return False, None

            # 4. 提取服务器 ID（从 CN 中）
            server_id = None
            for attr in server_cert.subject:
                if attr.oid == NameOID.COMMON_NAME:
                    server_id = attr.value
                    break

            logger.info(f"Server certificate verified for: {server_id}")
            return True, server_id

        except Exception as e:
            logger.error(f"Certificate verification error: {e}")
            return False, None

    def verify_client_certificate(self, client_cert: x509.Certificate) -> Tuple[bool, Optional[str]]:
        """
        验证 Tier 3 客户端证书

        Args:
            client_cert: 客户端证书

        Returns:
            (验证是否通过, 设备 ID) 元组
        """
        try:
            # 1. 加载 CA 证书
            if not self.ca_cert_path.exists():
                logger.error("CA certificate not found")
                return False, None

            with open(self.ca_cert_path, "rb") as f:
                ca_cert = x509.load_pem_x509_certificate(f.read(), default_backend())

            # 2. 验证证书链（检查是否由 CA 签名）
            try:
                # 使用证书的签名算法来验证
                signature_algorithm = client_cert.signature_algorithm_oid

                # 根据签名算法选择对应的哈希算法
                if signature_algorithm == x509.SignatureAlgorithmOID.RSA_WITH_SHA256:
                    hash_algorithm = hashes.SHA256()
                elif signature_algorithm == x509.SignatureAlgorithmOID.RSA_WITH_SHA384:
                    hash_algorithm = hashes.SHA384()
                elif signature_algorithm == x509.SignatureAlgorithmOID.RSA_WITH_SHA512:
                    hash_algorithm = hashes.SHA512()
                else:
                    # 默认使用 SHA256
                    hash_algorithm = hashes.SHA256()

                # 验证签名
                # 注意：对于 RSA 密钥，需要使用 padding
                from cryptography.hazmat.primitives.asymmetric import padding
                ca_public_key = ca_cert.public_key()

                if isinstance(ca_public_key, rsa.RSAPublicKey):
                    # RSA 密钥需要 padding
                    ca_public_key.verify(
                        client_cert.signature,
                        client_cert.tbs_certificate_bytes,
                        padding.PKCS1v15(),
                        hash_algorithm
                    )
                else:
                    # 其他类型的密钥（如 ECDSA）
                    ca_public_key.verify(
                        client_cert.signature,
                        client_cert.tbs_certificate_bytes,
                        hash_algorithm
                    )
            except Exception as e:
                logger.error(f"Certificate signature verification failed: {e}")
                return False, None

            # 3. 检查证书是否过期
            from datetime import timezone
            now = datetime.now(timezone.utc)
            if client_cert.not_valid_before_utc > now or client_cert.not_valid_after_utc < now:
                logger.error("Certificate expired or not yet valid")
                return False, None

            # 4. 提取设备 ID（从扩展中）
            device_id = None
            try:
                # 尝试从自定义扩展中提取设备 ID
                for ext in client_cert.extensions:
                    if isinstance(ext.value, x509.UnrecognizedExtension):
                        oid_str = str(ext.value.oid)
                        if "99999.1" in oid_str:  # 我们的私有 OID
                            device_id = ext.value.value.decode('utf-8')
                            break
            except Exception as e:
                logger.warning(f"Failed to extract device ID from certificate: {e}")

            # 如果没有从扩展中提取到，尝试从 CN 中提取
            if not device_id:
                for attr in client_cert.subject:
                    if attr.oid == NameOID.COMMON_NAME:
                        device_id = attr.value
                        break

            logger.info(f"Client certificate verified for device: {device_id}")
            return True, device_id

        except Exception as e:
            logger.error(f"Certificate verification error: {e}")
            return False, None

    def generate_pairing_qr_code(self, expires_in: int = 300) -> Dict[str, Any]:
        """
        生成配对二维码数据

        二维码包含：
        - Server ID
        - 临时 Token（用于 Tier 1 验证）
        - 过期时间

        Args:
            expires_in: Token 有效期（秒），默认 5 分钟

        Returns:
            二维码数据字典
        """
        token = secrets.token_urlsafe(32)
        expires_at = datetime.utcnow() + timedelta(seconds=expires_in)

        qr_data = {
            "server_id": self.server_id,
            "token": token,
            "expires_at": expires_at.isoformat(),
            "expires_in": expires_in,
            "ca_fingerprint": self._get_ca_fingerprint() if self.ca_cert_path.exists() else None
        }

        logger.info(f"Generated pairing QR code for server: {self.server_id}")
        return qr_data

    def _get_ca_fingerprint(self) -> str:
        """获取 CA 证书指纹（SHA256）"""
        try:
            with open(self.ca_cert_path, "rb") as f:
                ca_cert = x509.load_pem_x509_certificate(f.read(), default_backend())
            fingerprint = ca_cert.fingerprint(hashes.SHA256())
            return fingerprint.hex()
        except Exception as e:
            logger.error(f"Failed to get CA fingerprint: {e}")
            return ""

    async def request_ca_root_certificate(self, tier1_ca_url: str) -> bool:
        """
        从 Tier 1 CA 请求根证书（生产环境）

        Args:
            tier1_ca_url: Tier 1 CA 服务 URL

        Returns:
            是否成功获取
        """
        # TODO: 实现与 Tier 1 CA 的通信
        # - 使用 HTTPS 请求根证书
        # - 验证 Tier 1 CA 的身份
        # - 保存到 ca_cert_path
        logger.info(f"Requesting CA root certificate from Tier 1: {tier1_ca_url}")
        return False

    def get_server_ssl_context(self) -> Optional[object]:
        """
        获取服务器 SSL Context（用于 gRPC Server）

        Returns:
            SSL Context 对象，如果证书不存在则返回 None
        """
        try:
            import ssl

            cert, key = self.load_server_certificate()
            if not cert or not key:
                logger.error("Server certificate not found, generating new one...")
                cert, key = self.generate_server_certificate()

            # 创建 SSL Context
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_3

            # 加载服务器证书和私钥
            context.load_cert_chain(
                str(self.server_cert_path),
                str(self.server_key_path)
            )

            # 加载 CA 证书（用于验证客户端）
            if self.ca_cert_path.exists():
                context.load_verify_locations(str(self.ca_cert_path))

            # 强制要求客户端证书验证
            context.verify_mode = ssl.CERT_REQUIRED

            logger.info("SSL context created for server")
            return context

        except Exception as e:
            logger.error(f"Failed to create SSL context: {e}")
            return None

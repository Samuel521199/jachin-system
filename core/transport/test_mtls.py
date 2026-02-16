"""
测试 mTLS 证书生成和验证
用于验证 Jachin Link 的证书管理功能
"""

import sys
import logging
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from core.transport.mtls_manager import MTLSManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def test_certificate_generation():
    """测试证书生成"""
    cert_dir = Path("data/certs/test")
    cert_dir.mkdir(parents=True, exist_ok=True)
    
    logger.info("=" * 60)
    logger.info("测试 1: 生成 CA 证书")
    logger.info("=" * 60)
    
    mtls_manager = MTLSManager(cert_dir=cert_dir)
    
    # 生成 CA
    ca_cert, ca_key = mtls_manager.generate_ca()
    logger.info(f"✓ CA 证书已生成: {mtls_manager.ca_cert_path}")
    logger.info(f"  Subject: {ca_cert.subject}")
    logger.info(f"  Issuer: {ca_cert.issuer}")
    
    # 生成服务器证书
    logger.info("\n" + "=" * 60)
    logger.info("测试 2: 生成服务器证书")
    logger.info("=" * 60)
    
    server_cert, server_key = mtls_manager.generate_server_certificate()
    logger.info(f"✓ 服务器证书已生成: {mtls_manager.server_cert_path}")
    logger.info(f"  Subject: {server_cert.subject}")
    logger.info(f"  Issuer: {server_cert.issuer}")
    
    # 验证服务器证书
    logger.info("\n" + "=" * 60)
    logger.info("测试 3: 验证服务器证书")
    logger.info("=" * 60)
    
    verified, _ = mtls_manager.verify_server_certificate(server_cert)
    if verified:
        logger.info("✓ 服务器证书验证通过")
    else:
        logger.error("✗ 服务器证书验证失败")
    
    # 生成客户端 CSR
    logger.info("\n" + "=" * 60)
    logger.info("测试 4: 生成客户端 CSR 并签发证书")
    logger.info("=" * 60)
    
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.backends import default_backend
    from cryptography.x509.oid import NameOID
    
    # 生成客户端密钥对
    client_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend()
    )
    
    # 创建 CSR
    csr = x509.CertificateSigningRequestBuilder().subject_name(
        x509.Name([
            x509.NameAttribute(NameOID.COUNTRY_NAME, "CN"),
            x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Beijing"),
            x509.NameAttribute(NameOID.LOCALITY_NAME, "Beijing"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Jachin Device"),
            x509.NameAttribute(NameOID.COMMON_NAME, "test-device-001"),
        ])
    ).sign(client_key, hashes.SHA256(), default_backend())
    
    logger.info("✓ 客户端 CSR 已生成")
    
    # 签发客户端证书
    device_id = "test-device-001"
    client_cert, client_cert_pem = mtls_manager.sign_client_csr(csr, device_id)
    logger.info(f"✓ 客户端证书已签发: {mtls_manager.client_certs_dir / f'{device_id}.crt'}")
    logger.info(f"  Subject: {client_cert.subject}")
    logger.info(f"  Issuer: {client_cert.issuer}")
    
    # 验证客户端证书
    logger.info("\n" + "=" * 60)
    logger.info("测试 5: 验证客户端证书")
    logger.info("=" * 60)
    
    # verify_client_certificate 接受 x509.Certificate 对象，不是 PEM 字符串
    # 如果传入的是 PEM 字符串，需要先解析
    if isinstance(client_cert_pem, bytes):
        from cryptography import x509
        from cryptography.hazmat.backends import default_backend
        client_cert_obj = x509.load_pem_x509_certificate(client_cert_pem, default_backend())
    else:
        client_cert_obj = client_cert_pem
    
    verified, extracted_device_id = mtls_manager.verify_client_certificate(client_cert_obj)
    if verified:
        logger.info(f"✓ 客户端证书验证通过")
        logger.info(f"  提取的设备 ID: {extracted_device_id}")
        if extracted_device_id == device_id:
            logger.info("✓ 设备 ID 匹配")
        else:
            logger.error(f"✗ 设备 ID 不匹配: 期望 {device_id}, 实际 {extracted_device_id}")
    else:
        logger.error("✗ 客户端证书验证失败")
    
    # 测试 SSL Context
    logger.info("\n" + "=" * 60)
    logger.info("测试 6: 生成服务器 SSL Context")
    logger.info("=" * 60)
    
    import ssl
    ssl_context = mtls_manager.get_server_ssl_context()
    if ssl_context:
        logger.info("✓ 服务器 SSL Context 已生成")
        logger.info(f"  验证模式: {ssl_context.verify_mode}")
        logger.info(f"  需要客户端证书: {ssl_context.verify_mode == ssl.CERT_REQUIRED}")
    else:
        logger.error("✗ 服务器 SSL Context 生成失败")
    
    logger.info("\n" + "=" * 60)
    logger.info("所有测试完成！")
    logger.info("=" * 60)


if __name__ == "__main__":
    test_certificate_generation()

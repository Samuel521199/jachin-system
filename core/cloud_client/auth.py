"""
Cloud Auth Client - Layer 1 认证与 License 同步

职责：
- login(): OAuth2 流程，获取 Access Token
- sync_licenses(): 从云端拉取当前家庭域已购买的技能列表 (License Tokens)
"""

import json
import logging
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Optional, List
from urllib.parse import urlencode, parse_qs, urlparse

from core.config import settings
from common.schemas.license import LicenseToken, SyncLicensesResponse, LicenseStatus

logger = logging.getLogger(__name__)

# 本地 Token 缓存路径
_DEFAULT_TOKEN_PATH = Path.home() / ".jachin" / "cloud_token.json"
_DEFAULT_LICENSES_PATH = Path.home() / ".jachin" / "licenses.json"


class CloudAuthClient:
    """
    Layer 1 云认证客户端
    
    OAuth2 授权码流程 + License 同步
    """

    def __init__(
        self,
        market_url: Optional[str] = None,
        auth_url: Optional[str] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        token_path: Optional[Path] = None,
        licenses_path: Optional[Path] = None,
    ):
        self.market_url = (market_url or settings.CLOUD_MARKET_URL).rstrip("/")
        self.auth_url = (auth_url or settings.CLOUD_AUTH_URL).rstrip("/")
        self.client_id = client_id or getattr(settings, "CLOUD_CLIENT_ID", "")
        self.client_secret = client_secret or getattr(settings, "CLOUD_CLIENT_SECRET", "")
        self.token_path = token_path or _DEFAULT_TOKEN_PATH
        self.licenses_path = licenses_path or _DEFAULT_LICENSES_PATH
        self._access_token: Optional[str] = None
        self._licenses: List[LicenseToken] = []

    def _load_cached_token(self) -> Optional[str]:
        """从本地缓存加载 Access Token"""
        if not self.token_path.exists():
            return None
        try:
            data = json.loads(self.token_path.read_text(encoding="utf-8"))
            expires = data.get("expires_at")
            if expires:
                exp_dt = datetime.fromisoformat(expires)
                if datetime.utcnow() >= exp_dt:
                    return None
            return data.get("access_token")
        except Exception as e:
            logger.warning(f"Failed to load cached token: {e}")
            return None

    def _save_token(self, access_token: str, expires_in: int = 3600) -> None:
        """保存 Token 到本地"""
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        from datetime import timedelta
        expires_at = (datetime.utcnow() + timedelta(seconds=expires_in)).isoformat()
        self.token_path.write_text(
            json.dumps({"access_token": access_token, "expires_at": expires_at}, indent=2),
            encoding="utf-8",
        )

    def login(self, use_browser: bool = True) -> bool:
        """
        处理 OAuth2 授权码流程，获取 Access Token
        
        Args:
            use_browser: 是否打开浏览器完成授权（否则返回 auth_url 供手动操作）
        
        Returns:
            是否成功获取 Token
        """
        # 1. 检查缓存
        cached = self._load_cached_token()
        if cached:
            self._access_token = cached
            logger.info("Using cached access token")
            return True

        # 2. 构建授权 URL（OAuth2 Authorization Code）
        redirect_uri = f"{self.market_url}/oauth/callback"
        auth_params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "scope": "openid profile licenses",
            "state": "jachin_l2_login",
        }
        auth_endpoint = f"{self.auth_url}/authorize?{urlencode(auth_params)}"

        if use_browser:
            logger.info("Opening browser for OAuth2 authorization...")
            webbrowser.open(auth_endpoint)
            # 实际实现需启动本地回调服务接收 code，此处简化
            # TODO: 启动 localhost 回调服务，等待 code，再 exchange token
            logger.warning("OAuth2 callback server not implemented. Use manual token or mock.")
            return False

        logger.info(f"Please visit: {auth_endpoint}")
        return False

    def set_access_token(self, token: str) -> None:
        """手动设置 Access Token（用于测试或已有 Token）"""
        self._access_token = token
        self._save_token(token)

    def _get_access_token(self) -> Optional[str]:
        """获取当前有效的 Access Token"""
        if self._access_token:
            return self._access_token
        return self._load_cached_token()

    def sync_licenses(self, home_domain_id: Optional[str] = None) -> List[LicenseToken]:
        """
        从云端拉取当前家庭域已购买的技能列表 (License Tokens)
        
        Args:
            home_domain_id: 家庭域 ID，不传则使用 settings.HOME_DOMAIN_ID
        
        Returns:
            有效的 License Token 列表
        """
        domain_id = home_domain_id or settings.HOME_DOMAIN_ID
        token = self._get_access_token()

        if not token:
            logger.warning("No access token. Call login() first.")
            return self._load_local_licenses()

        # 调用 L1 API: GET /api/v1/licenses?home_domain_id=xxx
        url = f"{self.market_url}/api/v1/licenses"
        params = {}
        if domain_id:
            params["home_domain_id"] = domain_id

        try:
            import urllib.request
            req_url = f"{url}?{urlencode(params)}" if params else url
            req = urllib.request.Request(req_url)
            req.add_header("Authorization", f"Bearer {token}")
            req.add_header("Accept", "application/json")

            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
        except Exception as e:
            logger.warning(f"sync_licenses API failed: {e}, using local cache")
            return self._load_local_licenses()

        # 解析响应：{ "licenses": [...], "synced_at": "..." }
        raw = data.get("licenses", data) if isinstance(data, dict) else data
        if not isinstance(raw, list):
            raw = []

        # 过滤有效 License 并转为 LicenseToken
        licenses = []
        for item in raw:
            d = item if isinstance(item, dict) else {}
            status = d.get("status", "active")
            if status != "active":
                continue
            try:
                licenses.append(LicenseToken(**d))
            except Exception:
                pass

        self._licenses = licenses
        self._save_licenses(licenses)
        logger.info(f"Synced {len(licenses)} licenses for home_domain={domain_id or 'default'}")
        return licenses

    def _save_licenses(self, licenses: List[LicenseToken]) -> None:
        """保存 License 到本地（供 DRM 离线校验）"""
        self.licenses_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "licenses": [
                lic.model_dump(mode="json") if hasattr(lic, "model_dump") else lic
                for lic in licenses
            ],
            "synced_at": datetime.utcnow().isoformat(),
        }
        self.licenses_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def _load_local_licenses(self) -> List[LicenseToken]:
        """从本地缓存加载 License"""
        if not self.licenses_path.exists():
            return []
        try:
            data = json.loads(self.licenses_path.read_text(encoding="utf-8"))
            items = data.get("licenses", [])
            self._licenses = [LicenseToken(**item) for item in items]
            return self._licenses
        except Exception as e:
            logger.warning(f"Failed to load local licenses: {e}")
            return []

    def get_licenses(self) -> List[LicenseToken]:
        """获取当前 License 列表（优先内存，否则加载本地缓存）"""
        if self._licenses:
            return self._licenses
        return self._load_local_licenses()


# 单例
_cloud_auth_client: Optional[CloudAuthClient] = None


def get_cloud_auth_client() -> CloudAuthClient:
    """获取 CloudAuthClient 单例"""
    global _cloud_auth_client
    if _cloud_auth_client is None:
        _cloud_auth_client = CloudAuthClient()
    return _cloud_auth_client

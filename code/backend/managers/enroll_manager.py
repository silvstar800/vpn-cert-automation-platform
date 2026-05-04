"""VPN enrollment and client registration management."""

import hashlib
import hmac
import string
import secrets
import tarfile
import tempfile
from typing import Optional
from pathlib import Path
from datetime import datetime, timezone


class EnrollManager:
    """Manages VPN client enrollment process."""

    def __init__(self, pki_dir: str = None, secret: str = ""):
        """Initialize enrollment manager.
        
        Args:
            pki_dir: Path to PKI directory
            secret: Enrollment secret for HMAC
        """
        self.pki_dir = Path(pki_dir) if pki_dir else None
        self.secret = secret
        self.alphabet = string.ascii_letters + string.digits + "!@#$%^&*-_=+"

    def make_sfos_username(self, hostname: str) -> str:
        """Build SFOS APC username from hostname hash."""
        return "SRV_" + hashlib.sha1(hostname.encode()).hexdigest().upper()

    def make_sfos_password(self) -> str:
        """Build a high-entropy SFOS APC password."""
        return "".join(secrets.choice(self.alphabet) for _ in range(24))

    def hash_stored_secret(self, secret_value: str) -> str:
        """Hash a secret before persisting it to reduce at-rest exposure."""
        digest = hashlib.sha256(secret_value.encode("utf-8")).hexdigest()
        return f"sha256${digest}"

    def sign(self, msg: str) -> str:
        """Generate HMAC signature for enrollment request."""
        return hmac.new(
            self.secret.encode("utf-8"),
            msg.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def verify_signature(self, msg: str, signature: str) -> bool:
        """Verify HMAC signature for enrollment request."""
        expected = self.sign(msg)
        return hmac.compare_digest(expected, signature)

    def verify_enroll_timestamp(
        self, timestamp: int, allowed_drift_seconds: int = 300
    ) -> bool:
        """Verify timestamp is within allowed drift."""
        import time
        now = int(time.time())
        return abs(now - timestamp) <= allowed_drift_seconds

    def build_client_cert(
        self, hostname: str, cert_path: str, key_path: str
    ) -> bool:
        """Build/generate client certificate if missing.
        
        Args:
            hostname: Client hostname
            cert_path: Path to certificate file
            key_path: Path to private key file
            
        Returns:
            True if cert exists or was created, False otherwise
        """
        cert = Path(cert_path)
        key = Path(key_path)
        
        # If both exist, we're good
        if cert.exists() and key.exists():
            return True
        
        # In a real implementation, would call EasyRSA here
        # For now, just return False to indicate not implemented
        return False

    def build_enroll_bundle(
        self, hostname: str, vpn_type: str, bundle_dir: str = None
    ) -> Optional[bytes]:
        """Build enrollment tar.gz bundle with certs and config.
        
        Args:
            hostname: Client hostname
            vpn_type: 'openvpn', 'openvpn-legacy', or 'sfos'
            bundle_dir: Temporary directory for bundle files
            
        Returns:
            Tar.gz bundle as bytes, or None if failed
        """
        if not bundle_dir:
            bundle_dir = tempfile.mkdtemp(prefix=f"enroll_{hostname}_")
        
        try:
            bundle_path = Path(bundle_dir)
            
            # Create client config, certs, keys in bundle_path
            # This would be fully implemented in production
            
            # Create tar.gz of bundle
            tar_path = bundle_path.parent / f"{hostname}_enroll.tar.gz"
            with tarfile.open(tar_path, "w:gz") as tar:
                tar.add(bundle_path, arcname=hostname)
            
            # Return tar.gz bytes
            return tar_path.read_bytes()
        except Exception:
            return None

    def verify_enroll_request(
        self, hostname: str, mac: str, signature: str, timestamp: int
    ) -> bool:
        """Verify enrollment request signature and params.
        
        Args:
            hostname: Client hostname
            mac: MAC address
            signature: HMAC signature
            timestamp: Request timestamp
            
        Returns:
            True if signature and timestamp are valid
        """
        # Check timestamp validity
        if not self.verify_enroll_timestamp(timestamp):
            return False
        
        # Reconstruct message and verify signature
        msg = f"{hostname}:{mac}:{timestamp}"
        return self.verify_signature(msg, signature)


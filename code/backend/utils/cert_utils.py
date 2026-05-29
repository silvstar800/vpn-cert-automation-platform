"""Certificate-related utility helpers."""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from .time_utils import estimate_expire_date


def get_certificate_expire_at(cert_cn: str, created_at: Any = None, *, pki_dir: str) -> str:
    """Return cert expiry date from issued cert, fallback to estimated expiry."""
    cert_cn = (cert_cn or "").strip()
    candidate_paths = [
        Path(pki_dir) / "pki" / "issued" / f"{cert_cn}.crt",
        Path(pki_dir) / "issued" / f"{cert_cn}.crt",
        Path("/etc/openvpn/certs") / f"{cert_cn}.crt",
    ]
    for cert_path in candidate_paths:
        if not cert_path.exists():
            continue
        try:
            result = subprocess.run(
                ["openssl", "x509", "-enddate", "-noout", "-in", str(cert_path)],
                check=True,
                capture_output=True,
                text=True,
            )
            raw = result.stdout.strip()
            if raw.startswith("notAfter="):
                value = raw.split("=", 1)[1].strip()
                parsed = datetime.strptime(value, "%b %d %H:%M:%S %Y %Z")
                return parsed.date().isoformat()
        except Exception:
            continue
    return estimate_expire_date(created_at)

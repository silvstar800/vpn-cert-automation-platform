"""Network utility helpers."""

from __future__ import annotations

import ipaddress


def canonical_ip(value: str) -> str:
    """Normalize IP/INET text into bare IP string (strip /32, etc.)."""
    text_value = str(value).strip()
    try:
        return str(ipaddress.ip_interface(text_value).ip)
    except ValueError:
        try:
            return str(ipaddress.ip_address(text_value))
        except ValueError as exc:
            raise ValueError(f"invalid IP address: {text_value!r}") from exc

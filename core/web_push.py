"""Server-side Web Push primitives for the Workbench PWA."""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from config import paths

logger = logging.getLogger(__name__)

_VAPID_FILE = "web_push_vapid.json"
DEFAULT_WEB_PUSH_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class VapidKeys:
    public_key: str
    private_key_pem: str


def _b64url_no_padding(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def vapid_key_path() -> Path:
    return paths.get_state_dir() / _VAPID_FILE


def load_or_create_vapid_keys(path: Path | None = None) -> VapidKeys:
    key_path = path or vapid_key_path()
    if key_path.exists():
        payload = json.loads(key_path.read_text(encoding="utf-8"))
        public_key = payload.get("public_key")
        private_key_pem = payload.get("private_key_pem")
        if isinstance(public_key, str) and isinstance(private_key_pem, str):
            return VapidKeys(public_key=public_key, private_key_pem=private_key_pem)
        raise ValueError("invalid_vapid_key_file")

    key_path.parent.mkdir(parents=True, exist_ok=True)
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key()
    public_numbers = public_key.public_numbers()
    raw_public = (
        b"\x04"
        + public_numbers.x.to_bytes(32, "big")
        + public_numbers.y.to_bytes(32, "big")
    )
    private_key_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    keys = VapidKeys(public_key=_b64url_no_padding(raw_public), private_key_pem=private_key_pem)
    key_path.write_text(
        json.dumps(
            {
                "public_key": keys.public_key,
                "private_key_pem": keys.private_key_pem,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    try:
        key_path.chmod(0o600)
    except OSError:
        logger.debug("Could not chmod VAPID key file %s", key_path, exc_info=True)
    return keys


def send_web_push(
    *,
    subscription: dict[str, Any],
    payload: dict[str, Any],
    vapid_keys: VapidKeys | None = None,
    subject: str = "mailto:notifications@avibe.bot",
    timeout: float = DEFAULT_WEB_PUSH_TIMEOUT_SECONDS,
) -> None:
    """Send one Web Push payload.

    The pywebpush dependency is imported lazily so tests that only exercise key
    generation or subscription APIs do not need network-capable setup.
    """

    from py_vapid import Vapid
    from pywebpush import webpush

    keys = vapid_keys or load_or_create_vapid_keys()
    vapid_signer = Vapid.from_pem(keys.private_key_pem.encode("ascii"))
    subscription_info = {
        "endpoint": subscription["endpoint"],
        "keys": {
            "p256dh": subscription["p256dh"],
            "auth": subscription["auth"],
        },
    }
    webpush(
        subscription_info=subscription_info,
        data=json.dumps(payload, separators=(",", ":")),
        vapid_private_key=vapid_signer,
        vapid_claims={"sub": subject},
        timeout=timeout,
    )

"""Autenticação simples por login/senha fixo + JWT.

- Um único usuário (definido por env). Login retorna um token JWT.
- Os endpoints protegidos exigem o header Authorization: Bearer <token>.
- Implementação de JWT própria (HMAC-SHA256), sem dependências externas, para
  manter o backend leve e fácil de empacotar.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from . import config


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def create_token(username: str) -> str:
    """Cria um JWT assinado (HS256) com expiração."""
    header = {"alg": config.JWT_ALGORITHM, "typ": "JWT"}
    now = int(time.time())
    payload = {
        "sub": username,
        "iat": now,
        "exp": now + config.JWT_EXPIRE_HOURS * 3600,
    }
    seg_h = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    seg_p = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{seg_h}.{seg_p}".encode()
    sig = hmac.new(config.JWT_SECRET.encode(), signing_input, hashlib.sha256).digest()
    return f"{seg_h}.{seg_p}.{_b64url_encode(sig)}"


def verify_token(token: str) -> dict | None:
    """Valida assinatura e expiração. Retorna o payload ou None."""
    try:
        seg_h, seg_p, seg_s = token.split(".")
    except ValueError:
        return None
    signing_input = f"{seg_h}.{seg_p}".encode()
    expected = hmac.new(config.JWT_SECRET.encode(), signing_input, hashlib.sha256).digest()
    try:
        got = _b64url_decode(seg_s)
    except Exception:  # noqa: BLE001
        return None
    if not hmac.compare_digest(expected, got):
        return None
    try:
        payload = json.loads(_b64url_decode(seg_p))
    except Exception:  # noqa: BLE001
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    return payload


def check_credentials(username: str, password: str) -> bool:
    """Compara com o usuário/senha fixo, resistente a timing attack."""
    u_ok = hmac.compare_digest(username or "", config.WEB_USERNAME)
    p_ok = hmac.compare_digest(password or "", config.WEB_PASSWORD)
    return u_ok and p_ok

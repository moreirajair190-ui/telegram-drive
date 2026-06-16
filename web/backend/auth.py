"""Autenticação multiusuário: registro, login, JWT, hashing e rate limiting.

- Senhas: PBKDF2-HMAC-SHA256 com salt aleatório (200k iterações). Sem texto puro.
- JWT: HS256 assinado localmente (sem dependências externas). O ``sub`` carrega
  o ``user_id`` (inteiro), com ``email`` auxiliar.
- Rate limiting / brute-force: contagem de falhas por identificador (e-mail+IP)
  em janela deslizante, persistida em ``login_attempts`` (WebDatabase).
- Sanitização: NUNCA logamos senha, token ou credencial.

Este módulo NÃO conhece Telegram nem criptografia de credenciais — só cuida da
identidade do usuário do site.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import time

import config
from services.web_db import User, WebDatabase

log = logging.getLogger("tgplayer.web.auth")

_PBKDF2_ITER = 200_000
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ----------------------------------------------------------------- base64url
def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


# ----------------------------------------------------------------- JWT
def create_token(user_id: int, email: str, is_admin: bool = False) -> str:
    header = {"alg": config.JWT_ALGORITHM, "typ": "JWT"}
    now = int(time.time())
    payload = {
        "sub": str(user_id),
        "email": email,
        "adm": bool(is_admin),
        "iat": now,
        "exp": now + config.JWT_EXPIRE_HOURS * 3600,
    }
    seg_h = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    seg_p = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{seg_h}.{seg_p}".encode()
    sig = hmac.new(config.JWT_SECRET.encode(), signing_input, hashlib.sha256).digest()
    return f"{seg_h}.{seg_p}.{_b64url_encode(sig)}"


def verify_token(token: str) -> dict | None:
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


# ----------------------------------------------------------------- senhas
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITER)
    return f"pbkdf2_sha256${_PBKDF2_ITER}${_b64url_encode(salt)}${_b64url_encode(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iter_s, salt_b64, hash_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        iters = int(iter_s)
        salt = _b64url_decode(salt_b64)
        expected = _b64url_decode(hash_b64)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iters)
        return hmac.compare_digest(dk, expected)
    except Exception:  # noqa: BLE001
        return False


# ----------------------------------------------------------------- validação
def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def validate_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(normalize_email(email)))


def validate_password(password: str) -> str | None:
    """Retorna mensagem de erro ou ``None`` se a senha é aceitável."""
    if len(password or "") < config.PASSWORD_MIN_LENGTH:
        return f"A senha precisa ter ao menos {config.PASSWORD_MIN_LENGTH} caracteres."
    if password.isdigit() or password.isalpha():
        return "A senha deve combinar letras e números."
    return None


# ----------------------------------------------------------------- registro
class AuthError(Exception):
    """Erro de autenticação com mensagem segura (sem dados sensíveis)."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def register_user(db: WebDatabase, email: str, password: str, is_admin: bool = False) -> User:
    email = normalize_email(email)
    if not validate_email(email):
        raise AuthError("E-mail inválido.")
    err = validate_password(password)
    if err:
        raise AuthError(err)
    if db.get_user_by_email(email):
        raise AuthError("Já existe uma conta com este e-mail.", status=409)
    user_id = db.create_user(email, hash_password(password), is_admin=1 if is_admin else 0)
    user = db.get_user(user_id)
    assert user is not None
    return user


# ----------------------------------------------------------------- rate limit
def _rl_identifiers(email: str, ip: str) -> list[str]:
    out = []
    if email:
        out.append(f"email:{normalize_email(email)}")
    if ip:
        out.append(f"ip:{ip}")
    return out or ["unknown"]


def check_login_rate_limit(db: WebDatabase, email: str, ip: str) -> None:
    """Lança ``AuthError(429)`` se houver falhas demais na janela."""
    for ident in _rl_identifiers(email, ip):
        fails = db.count_recent_failures(ident, config.LOGIN_WINDOW_SECONDS)
        if fails >= config.LOGIN_MAX_FAILURES:
            raise AuthError(
                "Muitas tentativas de login. Tente novamente mais tarde.",
                status=429,
            )


def record_login_result(db: WebDatabase, email: str, ip: str, success: bool) -> None:
    for ident in _rl_identifiers(email, ip):
        db.record_login_attempt(ident, success)
        if success:
            db.clear_login_attempts(ident)


# ----------------------------------------------------------------- login
def authenticate(db: WebDatabase, email: str, password: str) -> User:
    """Valida credenciais. Mensagem genérica para não vazar existência da conta."""
    email = normalize_email(email)
    user = db.get_user_by_email(email)
    # Comparação resistente a timing mesmo quando o usuário não existe.
    stored = user.password_hash if user else hash_password(secrets.token_hex(8))
    ok = verify_password(password, stored)
    if not user or not ok:
        raise AuthError("E-mail ou senha inválidos.", status=401)
    if not user.is_active:
        raise AuthError("Conta desativada. Contate o administrador.", status=403)
    return user

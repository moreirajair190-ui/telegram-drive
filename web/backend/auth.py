"""Autenticação por login/senha + JWT.

Suporta dois modos, combinados:

1. **Conta criada pelo usuário** (auto-cadastro): o usuário define login e senha
   na primeira vez (tela "Criar conta"), junto com os dados do Telegram. A senha
   é guardada como hash PBKDF2-HMAC-SHA256 (com salt) na tabela `settings`.
2. **Conta fixa por env** (`TGWEB_USER`/`TGWEB_PASSWORD`): continua funcionando
   como fallback/retrocompatibilidade. Se uma conta foi criada no banco, ela tem
   prioridade.

JWT é implementado aqui mesmo (HMAC-SHA256), sem dependências externas, para
manter o backend leve e fácil de empacotar.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from . import config

# Chaves usadas na tabela `settings` para guardar a conta do site.
ACCOUNT_USER_KEY = "web_account_user"
ACCOUNT_HASH_KEY = "web_account_pwd"  # formato: pbkdf2_sha256$<iter>$<salt_b64>$<hash_b64>

_PBKDF2_ITER = 200_000


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


# ----------------------------------------------------------------- JWT
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


# ----------------------------------------------------------------- senhas
def hash_password(password: str) -> str:
    """Gera um hash PBKDF2 com salt aleatório."""
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITER)
    return f"pbkdf2_sha256${_PBKDF2_ITER}${_b64url_encode(salt)}${_b64url_encode(dk)}"


def _verify_hash(password: str, stored: str) -> bool:
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


# ----------------------------------------------------------------- conta (DB)
def account_exists(db) -> bool:
    """True se já existe uma conta criada no banco."""
    u = db.get_setting(ACCOUNT_USER_KEY)
    p = db.get_setting(ACCOUNT_HASH_KEY)
    return bool(u and p)


def create_account(db, username: str, password: str) -> None:
    """Cria (ou redefine) a conta do site no banco."""
    db.set_setting(ACCOUNT_USER_KEY, (username or "").strip())
    db.set_setting(ACCOUNT_HASH_KEY, hash_password(password or ""))


def check_credentials(db, username: str, password: str) -> bool:
    """Valida o login.

    Prioridade para a conta criada no banco; se não houver, usa a conta fixa
    definida por env (retrocompatibilidade). Resistente a timing attack.
    """
    username = (username or "").strip()
    password = password or ""

    stored_user = db.get_setting(ACCOUNT_USER_KEY) if db is not None else None
    stored_hash = db.get_setting(ACCOUNT_HASH_KEY) if db is not None else None
    if stored_user and stored_hash:
        u_ok = hmac.compare_digest(username, stored_user)
        p_ok = _verify_hash(password, stored_hash)
        return u_ok and p_ok

    # Fallback: conta fixa por env.
    u_ok = hmac.compare_digest(username, config.WEB_USERNAME)
    p_ok = hmac.compare_digest(password, config.WEB_PASSWORD)
    return u_ok and p_ok

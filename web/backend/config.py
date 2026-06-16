"""Configuração do TgPlayer Web (backend FastAPI).

Todas as opções vêm de variáveis de ambiente (ou de um arquivo .env), o que
facilita o deploy. Os valores padrão funcionam para desenvolvimento local.

Login do site é FIXO (um usuário/senha definidos por você). A conexão com o
Telegram usa a SUA conta (API ID/HASH + sessão Pyrogram já existente do app
desktop, reaproveitada automaticamente).
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

# Carrega .env se existir (sem depender de python-dotenv).
_ENV_FILE = Path(__file__).resolve().parent / ".env"
if _ENV_FILE.exists():
    for _line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))


def _get(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


# ---------------------------------------------------------------- login do site
# Defina estes no arquivo web/backend/.env para produção!
WEB_USERNAME = _get("TGWEB_USER", "admin")
WEB_PASSWORD = _get("TGWEB_PASSWORD", "tgplayer123")

# Segredo para assinar o JWT. Em produção, defina TGWEB_SECRET fixo (se mudar,
# todos os tokens emitidos são invalidados).
JWT_SECRET = _get("TGWEB_SECRET") or secrets.token_hex(32)
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = int(_get("TGWEB_TOKEN_HOURS", "168"))  # 7 dias

# ---------------------------------------------------------------- credenciais TG
# API ID/HASH da SUA conta (https://my.telegram.org). Se já existir no banco do
# app desktop (settings), serão reaproveitados automaticamente.
TELEGRAM_API_ID = _get("TGWEB_API_ID", "")
TELEGRAM_API_HASH = _get("TGWEB_API_HASH", "")

# ---------------------------------------------------------------- CORS / servidor
HOST = _get("TGWEB_HOST", "0.0.0.0")
PORT = int(_get("TGWEB_PORT", "8800"))

# Origens permitidas (frontend). Use "*" em dev; em produção liste o domínio
# do seu site na Cloudflare (ex.: https://meu-tgplayer.pages.dev).
CORS_ORIGINS = [
    o.strip()
    for o in _get("TGWEB_CORS", "*").split(",")
    if o.strip()
] or ["*"]

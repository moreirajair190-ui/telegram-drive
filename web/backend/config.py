"""Configuração do TgPlayer Web (backend FastAPI) — versão MULTIUSUÁRIO.

Todas as opções vêm de variáveis de ambiente (ou de um arquivo .env), o que
facilita o deploy (Render/containers). Cada USUÁRIO cria sua própria conta e
conecta sua PRÓPRIA conta do Telegram (API ID/HASH próprios). NÃO existe mais
um API_ID/API_HASH global compartilhado.

Variáveis sensíveis:
- ``ENCRYPTION_KEY``  -> chave de criptografia (obrigatória) usada para cifrar
                         API_ID/API_HASH/session/telefone antes de gravar no BD.
- ``JWT_SECRET``      -> segredo de assinatura do JWT (obrigatório em produção).
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


# ---------------------------------------------------------------- banco de dados
# Em produção (Render Free + Supabase) NÃO há filesystem persistente. Toda a
# persistência (usuários, credenciais cifradas, sessões, cursos, progresso)
# vai para o Postgres do Supabase via ``DATABASE_URL``.
#
# Aceita ``DATABASE_URL`` ou ``SUPABASE_DB_URL`` no formato:
#   postgresql://USER:PASSWORD@HOST:5432/postgres?sslmode=require
#
# Se NENHUMA estiver definida, caímos para SQLite local (uso desktop/dev). Nesse
# caso o caminho do arquivo vem de ``TGPLAYER_DATA`` (core) — mas no servidor
# isso NÃO é necessário nem recomendado.
DATABASE_URL = _get("DATABASE_URL") or _get("SUPABASE_DB_URL")

# Caminho do SQLite usado APENAS quando NÃO há DATABASE_URL (dev/desktop local).
# Em produção (Render Free) NUNCA usamos este caminho — a persistência vai
# inteira para o Postgres. O padrão fica ao lado do backend (gravável em dev) e
# JAMAIS aponta para /var/data. Configurável via TGWEB_SQLITE_PATH.
SQLITE_PATH = _get(
    "TGWEB_SQLITE_PATH",
    str(Path(__file__).resolve().parent / "tgplayer_web.sqlite3"),
)

# Cache de streaming é SEMPRE efêmero (buffer temporário de bytes do vídeo).
# Em produção fica em /tmp (some no restart, e tudo bem). Configurável.
STREAM_CACHE_DIR = _get("TGWEB_STREAM_CACHE_DIR", "/tmp/tgplayer-streams")

# ---------------------------------------------------------------- criptografia
# Aceita ENCRYPTION_KEY (uma) ou ENCRYPTION_KEYS (várias, p/ rotação).
# Se nenhuma estiver definida, geramos uma EFÊMERA apenas em dev (com aviso),
# o que torna os dados cifrados ilegíveis após reiniciar — NÃO use em produção.
ENCRYPTION_KEY = _get("ENCRYPTION_KEY")
ENCRYPTION_KEYS = _get("ENCRYPTION_KEYS")
ALLOW_EPHEMERAL_ENCRYPTION = _get("TGWEB_ALLOW_EPHEMERAL_KEY", "0") == "1"

# ---------------------------------------------------------------- JWT (sessão)
# Em produção defina TGWEB_SECRET fixo (se mudar, todos os tokens caem).
JWT_SECRET = _get("TGWEB_SECRET") or secrets.token_hex(32)
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = int(_get("TGWEB_TOKEN_HOURS", "72"))  # expiração da sessão (3 dias)

# ---------------------------------------------------------------- admin inicial
# Permite provisionar um administrador no primeiro boot (opcional). O admin NÃO
# vê dados sensíveis de ninguém — apenas o painel administrativo agregado.
ADMIN_EMAIL = _get("TGWEB_ADMIN_EMAIL", "")
ADMIN_PASSWORD = _get("TGWEB_ADMIN_PASSWORD", "")

# ---------------------------------------------------------------- registro
# Permite que novos usuários se cadastrem livremente (padrão: sim).
ALLOW_REGISTRATION = _get("TGWEB_ALLOW_REGISTRATION", "1") != "0"

# ---------------------------------------------------------------- segurança login
# Rate limiting / proteção contra brute force no login (do site e do Telegram).
LOGIN_MAX_FAILURES = int(_get("TGWEB_LOGIN_MAX_FAILURES", "5"))
LOGIN_WINDOW_SECONDS = int(_get("TGWEB_LOGIN_WINDOW_SECONDS", "900"))  # 15 min
TELEGRAM_SENDCODE_MAX = int(_get("TGWEB_TG_SENDCODE_MAX", "4"))
TELEGRAM_SENDCODE_WINDOW = int(_get("TGWEB_TG_SENDCODE_WINDOW", "3600"))  # 1h

# Política de senha do site.
PASSWORD_MIN_LENGTH = int(_get("TGWEB_PASSWORD_MIN_LENGTH", "8"))

# ---------------------------------------------------------------- CORS / servidor
HOST = _get("TGWEB_HOST", "0.0.0.0")
PORT = int(_get("PORT", "") or _get("TGWEB_PORT", "8800"))  # Render injeta PORT

CORS_ORIGINS = [
    o.strip()
    for o in _get("TGWEB_CORS", "*").split(",")
    if o.strip()
] or ["*"]

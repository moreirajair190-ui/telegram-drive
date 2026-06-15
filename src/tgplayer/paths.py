from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "TgPlayer"
# Nome legado (v6.1 e anteriores). Usado para migrar dados de usuário antigos
# (banco, sessão do Telegram, configurações) sem perder nada ao atualizar.
LEGACY_APP_NAME = "TGClassPlayer"


def app_root() -> Path:
    """Pasta raiz do app (ao lado do .exe quando empacotado, ou raiz do repo)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _default_data_dir() -> Path:
    """Onde guardar dados de usuário.

    - Em modo congelado (.exe), usa %LOCALAPPDATA%\\TgPlayer para evitar
      problemas de permissão quando o app fica em "Program Files".
    - Em desenvolvimento, usa ./data ao lado do código.
    - Pode ser sobrescrito pelas variáveis de ambiente TGPLAYER_DATA ou
      TGCLASSPLAYER_DATA (legado).
    """
    override = os.environ.get("TGPLAYER_DATA") or os.environ.get("TGCLASSPLAYER_DATA")
    if override:
        return Path(override)

    if getattr(sys, "frozen", False):
        base = (
            os.environ.get("LOCALAPPDATA")
            or os.environ.get("APPDATA")
            or str(Path.home())
        )
        new_dir = Path(base) / APP_NAME
        legacy_dir = Path(base) / LEGACY_APP_NAME
        # Migração suave: se ainda não há pasta nova mas existe a antiga,
        # reutilizamos a antiga (mantém login e progresso do usuário).
        if not new_dir.exists() and legacy_dir.exists():
            return legacy_dir
        return new_dir
    return app_root() / "data"


ROOT_DIR = app_root()
DATA_DIR = _default_data_dir().resolve()
SESSION_DIR = DATA_DIR / "sessions"
LOG_DIR = DATA_DIR / "logs"
# Mantém o mesmo nome de arquivo do banco para não perder dados ao atualizar.
DB_PATH = DATA_DIR / "tgclassplayer.sqlite3"
CACHE_DIR = DATA_DIR / "cache"


def ensure_dirs() -> None:
    for path in (DATA_DIR, SESSION_DIR, LOG_DIR, CACHE_DIR):
        path.mkdir(parents=True, exist_ok=True)

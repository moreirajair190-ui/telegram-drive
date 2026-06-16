"""Script de migração: modelo SINGLE-USER (legado) -> MULTIUSUÁRIO cifrado.

O que ele faz (idempotente — pode rodar várias vezes sem estragar nada):

1. Cria as tabelas novas (``users``, ``telegram_accounts``, ``login_attempts``).
2. Detecta a conta de site legada guardada em ``settings``
   (``web_account_user`` / ``web_account_pwd``) e/ou a conta fixa do ``.env``
   (``TGWEB_USER``/``TGWEB_PASSWORD``) e cria um usuário real na tabela
   ``users`` (reaproveitando o hash PBKDF2 quando possível).
3. Migra as credenciais Telegram que estavam em CLARO em ``settings``
   (``api_id``/``api_hash``) cifrando-as numa ``telegram_accounts`` vinculada
   àquele usuário.
4. Migra a sessão Pyrogram antiga:
   - se houver um arquivo de sessão local (``sessions/tgclassplayer.session``),
     converte-o em *session string* e grava CIFRADA;
   - exige API ID/HASH para abrir a sessão. Se não houver, apenas registra os
     dados disponíveis (o usuário refaz o login pelo site).

Uso:
    ENCRYPTION_KEY=... python -m web.backend.migrate           # executa
    ENCRYPTION_KEY=... python -m web.backend.migrate --dry-run # só mostra

Importante: defina ``ENCRYPTION_KEY`` (a MESMA usada pelo backend) para que os
dados migrados possam ser decifrados em produção.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

_THIS = Path(__file__).resolve()
_REPO_ROOT = _THIS.parents[2]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from tgplayer.db import Database  # noqa: E402
from tgplayer.paths import DB_PATH, SESSION_DIR  # noqa: E402

from . import auth, config  # noqa: E402
from .services import EncryptionService, TelegramAccountService  # noqa: E402
from .services.web_db import WebDatabase  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("tgplayer.web.migrate")

LEGACY_SESSION_NAME = "tgclassplayer"
LEGACY_USER_KEY = "web_account_user"
LEGACY_HASH_KEY = "web_account_pwd"


def _derive_email(login: str) -> str:
    """Converte um login legado em um e-mail plausível (placeholder local)."""
    login = (login or "admin").strip().lower()
    if "@" in login:
        return login
    return f"{login}@tgplayer.local"


async def _legacy_session_to_string(api_id: str, api_hash: str) -> str | None:
    """Abre o arquivo de sessão legado e exporta a session string."""
    base = SESSION_DIR / LEGACY_SESSION_NAME
    if not (base.with_suffix(".session").exists()):
        return None
    try:
        from pyrogram import Client

        client = Client(
            LEGACY_SESSION_NAME,
            api_id=int(str(api_id).strip()),
            api_hash=str(api_hash).strip(),
            workdir=str(SESSION_DIR),
            no_updates=True,
        )
        await client.connect()
        try:
            session_string = await client.export_session_string()
        finally:
            await client.disconnect()
        return session_string
    except Exception as exc:  # noqa: BLE001
        log.warning("Não foi possível converter a sessão legada: %s", type(exc).__name__)
        return None


def migrate(dry_run: bool = False) -> dict:
    enc = EncryptionService()  # exige ENCRYPTION_KEY
    core = Database()  # garante schema do core + acesso a settings
    web = WebDatabase(str(DB_PATH))
    accounts = TelegramAccountService(web, enc)

    report: dict = {
        "created_user": None,
        "migrated_api_credentials": False,
        "migrated_session": False,
        "notes": [],
    }

    # 1) Descobrir conta legada (settings) ou env.
    legacy_user = core.get_setting(LEGACY_USER_KEY)
    legacy_hash = core.get_setting(LEGACY_HASH_KEY)

    login = legacy_user or (
        config.ADMIN_EMAIL or "admin"
    )
    email = _derive_email(login)

    existing = web.get_user_by_email(email)
    if existing:
        report["notes"].append(f"Usuário {email} já existe (id={existing.id}).")
        user_id = existing.id
    else:
        # Reaproveita o hash PBKDF2 legado quando compatível; senão, exige uma
        # senha temporária via env ADMIN_PASSWORD para criar com segurança.
        if legacy_hash and legacy_hash.startswith("pbkdf2_sha256$"):
            password_hash = legacy_hash
            report["notes"].append("Hash de senha legado reaproveitado.")
        elif config.ADMIN_PASSWORD:
            password_hash = auth.hash_password(config.ADMIN_PASSWORD)
            report["notes"].append("Senha definida a partir de TGWEB_ADMIN_PASSWORD.")
        else:
            # Cria com senha aleatória que o usuário deverá redefinir (login
            # legado por env continua disponível só durante a transição).
            import secrets

            temp = secrets.token_urlsafe(12)
            password_hash = auth.hash_password(temp)
            report["notes"].append(
                "Nenhuma senha conhecida: criada senha temporária aleatória "
                "(defina TGWEB_ADMIN_PASSWORD ou redefina pelo site)."
            )
        if dry_run:
            report["created_user"] = f"(dry-run) {email}"
            user_id = -1
        else:
            user_id = web.create_user(email, password_hash, is_admin=1)
            report["created_user"] = email

    # 2) Migrar credenciais Telegram (claro -> cifrado).
    api_id = core.get_setting("api_id")
    api_hash = core.get_setting("api_hash")

    if user_id != -1:
        acc = accounts.ensure_account(user_id, label="Conta migrada")
    else:
        acc = None

    if api_id and api_hash:
        if dry_run or acc is None:
            report["notes"].append("(dry-run) API ID/HASH seriam cifrados.")
        else:
            accounts.set_api_credentials(acc.id, str(api_id), str(api_hash))
            report["migrated_api_credentials"] = True

        # 3) Migrar sessão legada -> session string cifrada.
        if not dry_run and acc is not None:
            session_string = _run(
                _legacy_session_to_string(str(api_id), str(api_hash))
            )
            if session_string:
                accounts.set_session(acc.id, session_string)
                accounts.set_status(acc.id, "connected")
                report["migrated_session"] = True
            else:
                report["notes"].append(
                    "Sessão legada não encontrada/convertida — o usuário fará "
                    "login pelo site (telefone + código)."
                )
    else:
        report["notes"].append(
            "Sem api_id/api_hash em settings — nada de Telegram a migrar."
        )

    return report


def _run(coro):
    """Executa uma corrotina mesmo sem loop ativo."""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        try:
            loop.close()
        except Exception:  # noqa: BLE001
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Migra TgPlayer Web para multiusuário.")
    parser.add_argument("--dry-run", action="store_true", help="Apenas simula.")
    args = parser.parse_args()

    try:
        report = migrate(dry_run=args.dry_run)
    except RuntimeError as exc:
        log.error("%s", exc)
        sys.exit(1)

    print("\n==== Relatório de migração ====")
    print(f"Usuário criado:           {report['created_user']}")
    print(f"Credenciais migradas:     {report['migrated_api_credentials']}")
    print(f"Sessão migrada:           {report['migrated_session']}")
    for note in report["notes"]:
        print(f"  - {note}")
    if args.dry_run:
        print("\n(dry-run: nenhuma alteração foi gravada)")


if __name__ == "__main__":
    main()

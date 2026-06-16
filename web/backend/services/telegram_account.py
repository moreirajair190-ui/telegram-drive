"""TelegramAccountService — CRUD das contas Telegram por usuário.

Única camada autorizada a cifrar/decifrar as credenciais Telegram. As rotas
da API NUNCA acessam ``encrypted_*`` diretamente; elas pedem aqui:

- ``get_credentials(account_id)`` -> ``{api_id, api_hash, session, phone}`` em
  claro (apenas em memória, para uso imediato pelo TelegramAuthService).
- ``safe_view(account)``          -> dict SEM nada sensível (para o usuário e
  principalmente para o painel administrativo).

Relacionamento: 1 usuário -> N contas Telegram. Nenhuma sessão é compartilhada
entre usuários.
"""

from __future__ import annotations

import logging
from typing import Any

from .encryption import EncryptionService
from .web_db import TelegramAccount, WebDatabase

log = logging.getLogger("tgplayer.web.account")

__all__ = ["TelegramAccountService"]


class TelegramAccountService:
    def __init__(self, db: WebDatabase, enc: EncryptionService) -> None:
        self.db = db
        self.enc = enc

    # ------------------------------------------------------------------ criação
    def ensure_account(self, user_id: int, label: str | None = None) -> TelegramAccount:
        """Devolve a primeira conta do usuário, criando uma vazia se não existir."""
        acc = self.db.first_account_for_user(user_id)
        if acc:
            return acc
        acc_id = self.db.create_telegram_account(user_id, label=label)
        return self.db.get_account(acc_id)  # type: ignore[return-value]

    def create_account(self, user_id: int, label: str | None = None) -> TelegramAccount:
        acc_id = self.db.create_telegram_account(user_id, label=label)
        return self.db.get_account(acc_id)  # type: ignore[return-value]

    def list_for_user(self, user_id: int) -> list[TelegramAccount]:
        return self.db.list_accounts_for_user(user_id)

    def get(self, account_id: int) -> TelegramAccount | None:
        return self.db.get_account(account_id)

    def get_owned(self, account_id: int, user_id: int) -> TelegramAccount | None:
        """Busca a conta garantindo que pertence ao usuário (isolamento)."""
        acc = self.db.get_account(account_id)
        if acc and acc.user_id == user_id:
            return acc
        return None

    def delete(self, account_id: int) -> None:
        self.db.delete_account(account_id)

    # --------------------------------------------------------------- credenciais
    def set_api_credentials(self, account_id: int, api_id: str, api_hash: str) -> None:
        """Cifra e grava API_ID/API_HASH. Reinicia o status para 'disconnected'."""
        self.db.update_account_fields(
            account_id,
            encrypted_api_id=self.enc.encrypt(str(api_id).strip()),
            encrypted_api_hash=self.enc.encrypt(str(api_hash).strip()),
            status="disconnected",
        )

    def set_session(self, account_id: int, session_string: str | None) -> None:
        """Cifra e grava a session string do Pyrogram (ou limpa se ``None``)."""
        self.db.update_account_fields(
            account_id,
            encrypted_session=self.enc.encrypt(session_string) if session_string else None,
        )

    def set_phone(self, account_id: int, phone: str | None) -> None:
        self.db.update_account_fields(
            account_id,
            encrypted_phone=self.enc.encrypt(phone) if phone else None,
        )

    def set_status(self, account_id: int, status: str) -> None:
        self.db.update_account_fields(account_id, status=status)

    def set_profile(
        self,
        account_id: int,
        tg_user_id: int | None,
        tg_username: str | None,
        tg_first_name: str | None,
    ) -> None:
        """Dados PÚBLICOS do perfil Telegram (não sensíveis)."""
        self.db.update_account_fields(
            account_id,
            tg_user_id=tg_user_id,
            tg_username=tg_username,
            tg_first_name=tg_first_name,
        )

    def touch_sync(self, account_id: int) -> None:
        self.db.update_account_fields(account_id, last_sync_at=self.db.now())

    # ------------------------------------------------------------------ leitura
    def get_credentials(self, account_id: int) -> dict[str, Any]:
        """Decifra as credenciais para uso imediato (somente em memória).

        Retorna ``{api_id, api_hash, session, phone}``. Valores ausentes vêm
        como ``None``. Nunca logamos o conteúdo.
        """
        acc = self.db.get_account(account_id)
        if not acc:
            return {"api_id": None, "api_hash": None, "session": None, "phone": None}
        return {
            "api_id": self.enc.try_decrypt(acc.encrypted_api_id),
            "api_hash": self.enc.try_decrypt(acc.encrypted_api_hash),
            "session": self.enc.try_decrypt(acc.encrypted_session),
            "phone": self.enc.try_decrypt(acc.encrypted_phone),
        }

    def has_api_credentials(self, account_id: int) -> bool:
        creds = self.get_credentials(account_id)
        return bool(creds["api_id"] and creds["api_hash"])

    # --------------------------------------------------------------- visão segura
    def safe_view(self, account: TelegramAccount) -> dict[str, Any]:
        """Visão SEM dados sensíveis.

        Expõe apenas o que é seguro mostrar (inclusive no painel admin):
        conta conectada, status, perfil público, datas. NUNCA expõe API_ID,
        API_HASH, session string, telefone ou tokens.
        """
        return {
            "id": account.id,
            "label": account.label or (account.tg_first_name or "Conta Telegram"),
            "status": account.status,
            "connected": account.status == "connected",
            "has_credentials": bool(account.encrypted_api_id and account.encrypted_api_hash),
            "has_session": bool(account.encrypted_session),
            "tg_user_id": account.tg_user_id,
            "tg_username": account.tg_username,
            "tg_first_name": account.tg_first_name,
            "last_sync_at": account.last_sync_at,
            "created_at": account.created_at,
            "updated_at": account.updated_at,
        }

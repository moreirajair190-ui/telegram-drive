"""Camada de serviços do TgPlayer Web (multiusuário).

Separação de responsabilidades:

- ``EncryptionService``       -> criptografia simétrica (Fernet) de dados sensíveis.
- ``TelegramAccountService``  -> CRUD das contas Telegram por usuário (sempre
                                 cifrando/decifrando via EncryptionService).
- ``TelegramAuthService``     -> sessões Pyrogram isoladas POR usuário
                                 (login, código, senha 2FA, streaming).

Nenhuma rota da API deve acessar diretamente os dados cifrados sem passar por
estes serviços.
"""

from .encryption import EncryptionService  # noqa: F401
from .telegram_account import TelegramAccountService  # noqa: F401
from .telegram_auth import TelegramAuthService  # noqa: F401

__all__ = [
    "EncryptionService",
    "TelegramAccountService",
    "TelegramAuthService",
]

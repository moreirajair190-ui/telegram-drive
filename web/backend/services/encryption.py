"""EncryptionService — criptografia simétrica forte para dados sensíveis.

Toda credencial sensível (API_ID, API_HASH, session string, tokens, telefone)
é cifrada com ``cryptography.fernet`` ANTES de ser gravada no banco e só é
decifrada por este serviço, sob demanda.

Chave
-----
A chave vem da variável de ambiente ``ENCRYPTION_KEY`` (obrigatória em
produção). Aceitamos dois formatos:

1. Uma chave Fernet já pronta (44 chars, base64 urlsafe de 32 bytes) — é o
   formato recomendado, gerado com ``Fernet.generate_key()``.
2. Uma passphrase arbitrária — neste caso derivamos uma chave Fernet
   determinística via SHA-256 (32 bytes) + base64 urlsafe. Isso permite que
   o operador defina algo "humano", mas a chave efetiva continua com 256 bits.

Rotação de chave
----------------
``ENCRYPTION_KEYS`` (plural) pode conter VÁRIAS chaves separadas por vírgula.
A PRIMEIRA é usada para cifrar (chave corrente); todas são tentadas ao
decifrar (``MultiFernet``), permitindo rotacionar a chave sem perder dados
antigos. Se só ``ENCRYPTION_KEY`` estiver definida, ela é a única.

Segurança
---------
- Nunca logamos valores em claro nem a própria chave.
- ``encrypt`` retorna texto ASCII (token Fernet) pronto para a coluna TEXT.
- ``decrypt`` lança ``InvalidToken`` se o dado foi adulterado/cifrado com
  outra chave (autenticidade garantida pelo HMAC interno do Fernet).
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
from typing import Iterable

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

log = logging.getLogger("tgplayer.web.encryption")

__all__ = ["EncryptionService", "InvalidToken"]


class EncryptionService:
    """Serviço central de cifragem/decifragem (Fernet / MultiFernet)."""

    def __init__(self, keys: Iterable[str] | None = None) -> None:
        raw_keys = list(keys) if keys is not None else self._keys_from_env()
        fernets = [self._build_fernet(k) for k in raw_keys if k and k.strip()]
        if not fernets:
            raise RuntimeError(
                "ENCRYPTION_KEY ausente. Defina ENCRYPTION_KEY (ou ENCRYPTION_KEYS) "
                "no ambiente. Gere uma com: python -c \"from cryptography.fernet "
                "import Fernet; print(Fernet.generate_key().decode())\""
            )
        self._fernet = MultiFernet(fernets)
        self._key_count = len(fernets)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _keys_from_env() -> list[str]:
        plural = os.environ.get("ENCRYPTION_KEYS", "")
        if plural.strip():
            return [k.strip() for k in plural.split(",") if k.strip()]
        single = os.environ.get("ENCRYPTION_KEY", "")
        return [single.strip()] if single.strip() else []

    @staticmethod
    def _build_fernet(key: str) -> Fernet:
        """Aceita uma chave Fernet pronta OU deriva uma de uma passphrase."""
        key = key.strip()
        # Tenta usar diretamente como chave Fernet (44 chars base64 de 32 bytes).
        try:
            return Fernet(key.encode("ascii"))
        except Exception:  # noqa: BLE001
            pass
        # Caso contrário, deriva determinÍsticamente de uma passphrase.
        digest = hashlib.sha256(key.encode("utf-8")).digest()  # 32 bytes
        derived = base64.urlsafe_b64encode(digest)
        return Fernet(derived)

    @staticmethod
    def generate_key() -> str:
        """Gera uma nova chave Fernet (para o operador colocar no .env)."""
        return Fernet.generate_key().decode("ascii")

    @property
    def key_count(self) -> int:
        return self._key_count

    # ------------------------------------------------------------------ API
    def encrypt(self, plaintext: str | None) -> str | None:
        """Cifra uma string. ``None``/"" passam direto como ``None``."""
        if plaintext is None or plaintext == "":
            return None
        token = self._fernet.encrypt(str(plaintext).encode("utf-8"))
        return token.decode("ascii")

    def decrypt(self, token: str | None) -> str | None:
        """Decifra um token. ``None``/"" devolve ``None``.

        Lança ``InvalidToken`` se o dado foi adulterado ou cifrado com outra
        chave fora do conjunto atual.
        """
        if token is None or token == "":
            return None
        plain = self._fernet.decrypt(str(token).encode("ascii"))
        return plain.decode("utf-8")

    def try_decrypt(self, token: str | None) -> str | None:
        """Igual a ``decrypt`` mas devolve ``None`` em vez de lançar erro.

        Útil em fluxos defensivos (ex.: dado legado em claro que ainda não foi
        migrado). NUNCA loga o conteúdo.
        """
        try:
            return self.decrypt(token)
        except InvalidToken:
            return None
        except Exception:  # noqa: BLE001
            return None

    def rotate(self, token: str | None) -> str | None:
        """Re-cifra um token com a chave corrente (para rotação de chaves)."""
        if token is None or token == "":
            return None
        try:
            rotated = self._fernet.rotate(str(token).encode("ascii"))
            return rotated.decode("ascii")
        except InvalidToken:
            # Pode ser dado em claro (legado): cifra do zero.
            return self.encrypt(token)

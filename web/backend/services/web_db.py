"""WebDatabase — schema multiusuário do TgPlayer Web.

Estende o mesmo arquivo SQLite usado pelo app desktop (``tgplayer.db.Database``)
com as tabelas necessárias para multiusuário, SEM quebrar o schema legado:

    users
    -----
    id              INTEGER PK
    email           TEXT UNIQUE          (login do usuário; e-mail ou username)
    password_hash   TEXT                 (PBKDF2-HMAC-SHA256 com salt)
    is_admin        INTEGER              (0/1)
    is_active       INTEGER              (0/1 — permite desativar sem apagar)
    created_at      TEXT
    updated_at      TEXT
    last_login_at   TEXT

    telegram_accounts
    -----------------
    id                  INTEGER PK
    user_id             INTEGER FK -> users(id)   (1 usuário -> N contas)
    label               TEXT                       (apelido amigável)
    encrypted_api_id    TEXT                       (cifrado)
    encrypted_api_hash  TEXT                       (cifrado)
    encrypted_session   TEXT                       (session string cifrada)
    encrypted_phone     TEXT                       (telefone cifrado, opcional)
    tg_user_id          INTEGER                    (id público do Telegram, NÃO sensível)
    tg_username         TEXT                       (público)
    tg_first_name       TEXT                       (público)
    status              TEXT                       (disconnected/awaiting_code/awaiting_password/connected)
    last_sync_at        TEXT
    created_at          TEXT
    updated_at          TEXT

    login_attempts
    --------------
    Registro leve para rate limiting / proteção brute-force (por
    identificador: e-mail e/ou IP).

Importante: NENHUM dado sensível é gravado em claro aqui. O serviço de conta
(`TelegramAccountService`) é o único que cifra/decifra.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator

__all__ = ["WebDatabase", "User", "TelegramAccount"]


@dataclass
class User:
    id: int
    email: str
    password_hash: str
    is_admin: int = 0
    is_active: int = 1
    created_at: str | None = None
    updated_at: str | None = None
    last_login_at: str | None = None


@dataclass
class TelegramAccount:
    id: int
    user_id: int
    label: str | None = None
    encrypted_api_id: str | None = None
    encrypted_api_hash: str | None = None
    encrypted_session: str | None = None
    encrypted_phone: str | None = None
    tg_user_id: int | None = None
    tg_username: str | None = None
    tg_first_name: str | None = None
    status: str = "disconnected"
    last_sync_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class WebDatabase:
    """Acesso às tabelas multiusuário. Compartilha o arquivo SQLite do core."""

    def __init__(self, path: str) -> None:
        self.path = str(path)
        self.init()

    # ------------------------------------------------------------------ conexão
    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=4000")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    # ------------------------------------------------------------------ schema
    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;

                CREATE TABLE IF NOT EXISTS users (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    email         TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    is_admin      INTEGER NOT NULL DEFAULT 0,
                    is_active     INTEGER NOT NULL DEFAULT 1,
                    created_at    TEXT NOT NULL,
                    updated_at    TEXT,
                    last_login_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

                CREATE TABLE IF NOT EXISTS telegram_accounts (
                    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id            INTEGER NOT NULL,
                    label              TEXT,
                    encrypted_api_id   TEXT,
                    encrypted_api_hash TEXT,
                    encrypted_session  TEXT,
                    encrypted_phone    TEXT,
                    tg_user_id         INTEGER,
                    tg_username        TEXT,
                    tg_first_name      TEXT,
                    status             TEXT NOT NULL DEFAULT 'disconnected',
                    last_sync_at       TEXT,
                    created_at         TEXT NOT NULL,
                    updated_at         TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_tgacc_user ON telegram_accounts(user_id);

                CREATE TABLE IF NOT EXISTS login_attempts (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    identifier  TEXT NOT NULL,
                    success     INTEGER NOT NULL DEFAULT 0,
                    created_at  REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_login_attempts_id ON login_attempts(identifier, created_at);
                """
            )

    # =================================================================== users
    def create_user(
        self, email: str, password_hash: str, is_admin: int = 0
    ) -> int:
        now = self.now()
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO users(email, password_hash, is_admin, is_active, "
                "created_at, updated_at) VALUES(?,?,?,1,?,?)",
                (email.strip().lower(), password_hash, int(is_admin), now, now),
            )
            return int(cur.lastrowid)

    def count_users(self) -> int:
        with self.connect() as conn:
            return int(conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"])

    def get_user_by_email(self, email: str) -> User | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE email=?", (email.strip().lower(),)
            ).fetchone()
        return self._row_to_user(row) if row else None

    def get_user(self, user_id: int) -> User | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return self._row_to_user(row) if row else None

    def list_users(self) -> list[User]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY id ASC").fetchall()
        return [self._row_to_user(r) for r in rows]

    def set_password_hash(self, user_id: int, password_hash: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET password_hash=?, updated_at=? WHERE id=?",
                (password_hash, self.now(), user_id),
            )

    def touch_last_login(self, user_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET last_login_at=? WHERE id=?", (self.now(), user_id)
            )

    def set_user_active(self, user_id: int, active: bool) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET is_active=?, updated_at=? WHERE id=?",
                (1 if active else 0, self.now(), user_id),
            )

    # ======================================================== telegram_accounts
    def create_telegram_account(self, user_id: int, label: str | None = None) -> int:
        now = self.now()
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO telegram_accounts(user_id, label, status, created_at, "
                "updated_at) VALUES(?,?,?,?,?)",
                (user_id, label, "disconnected", now, now),
            )
            return int(cur.lastrowid)

    def get_account(self, account_id: int) -> TelegramAccount | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM telegram_accounts WHERE id=?", (account_id,)
            ).fetchone()
        return self._row_to_account(row) if row else None

    def list_accounts_for_user(self, user_id: int) -> list[TelegramAccount]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM telegram_accounts WHERE user_id=? ORDER BY id ASC",
                (user_id,),
            ).fetchall()
        return [self._row_to_account(r) for r in rows]

    def list_all_accounts(self) -> list[TelegramAccount]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM telegram_accounts ORDER BY id ASC"
            ).fetchall()
        return [self._row_to_account(r) for r in rows]

    def first_account_for_user(self, user_id: int) -> TelegramAccount | None:
        accounts = self.list_accounts_for_user(user_id)
        return accounts[0] if accounts else None

    def update_account_fields(self, account_id: int, **fields: Any) -> None:
        if not fields:
            return
        allowed = {
            "label",
            "encrypted_api_id",
            "encrypted_api_hash",
            "encrypted_session",
            "encrypted_phone",
            "tg_user_id",
            "tg_username",
            "tg_first_name",
            "status",
            "last_sync_at",
        }
        sets = []
        values: list[Any] = []
        for key, val in fields.items():
            if key not in allowed:
                continue
            sets.append(f"{key}=?")
            values.append(val)
        if not sets:
            return
        sets.append("updated_at=?")
        values.append(self.now())
        values.append(account_id)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE telegram_accounts SET {', '.join(sets)} WHERE id=?",
                tuple(values),
            )

    def delete_account(self, account_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM telegram_accounts WHERE id=?", (account_id,))

    # ========================================================== login_attempts
    def record_login_attempt(self, identifier: str, success: bool) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO login_attempts(identifier, success, created_at) "
                "VALUES(?,?,?)",
                (identifier.lower(), 1 if success else 0, time.time()),
            )

    def count_recent_failures(self, identifier: str, window_seconds: int) -> int:
        cutoff = time.time() - window_seconds
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM login_attempts "
                "WHERE identifier=? AND success=0 AND created_at>=?",
                (identifier.lower(), cutoff),
            ).fetchone()
        return int(row["n"] or 0)

    def clear_login_attempts(self, identifier: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM login_attempts WHERE identifier=?", (identifier.lower(),)
            )

    def prune_login_attempts(self, older_than_seconds: int = 86400) -> None:
        cutoff = time.time() - older_than_seconds
        with self.connect() as conn:
            conn.execute("DELETE FROM login_attempts WHERE created_at<?", (cutoff,))

    # ------------------------------------------------------------ row -> objeto
    @staticmethod
    def _row_to_user(row: sqlite3.Row) -> User:
        d = dict(row)
        return User(
            id=int(d["id"]),
            email=d["email"],
            password_hash=d["password_hash"],
            is_admin=int(d.get("is_admin") or 0),
            is_active=int(d.get("is_active") or 0),
            created_at=d.get("created_at"),
            updated_at=d.get("updated_at"),
            last_login_at=d.get("last_login_at"),
        )

    @staticmethod
    def _row_to_account(row: sqlite3.Row) -> TelegramAccount:
        d = dict(row)
        return TelegramAccount(
            id=int(d["id"]),
            user_id=int(d["user_id"]),
            label=d.get("label"),
            encrypted_api_id=d.get("encrypted_api_id"),
            encrypted_api_hash=d.get("encrypted_api_hash"),
            encrypted_session=d.get("encrypted_session"),
            encrypted_phone=d.get("encrypted_phone"),
            tg_user_id=d.get("tg_user_id"),
            tg_username=d.get("tg_username"),
            tg_first_name=d.get("tg_first_name"),
            status=d.get("status") or "disconnected",
            last_sync_at=d.get("last_sync_at"),
            created_at=d.get("created_at"),
            updated_at=d.get("updated_at"),
        )

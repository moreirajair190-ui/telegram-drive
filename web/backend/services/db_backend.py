"""Adaptador de banco unificado: Postgres (Supabase) OU SQLite (dev/desktop).

Objetivo: remover por completo a dependência de filesystem persistente no
servidor. Em produção (Render Free), a persistência vai para o Postgres do
Supabase via ``DATABASE_URL``. Em desenvolvimento/desktop, sem ``DATABASE_URL``,
cai automaticamente para SQLite local (comportamento histórico).

Como funciona
-------------
- ``Db`` expõe um ``connect()`` (context manager) que entrega uma conexão com
  ``row_factory`` equivalente a dicionário e tradução de placeholders.
- Escrevemos o SQL uma única vez usando ``?`` como placeholder; o adaptador
  converte para ``%s`` quando o backend é Postgres.
- Pequenas diferenças de dialeto (AUTOINCREMENT, UPSERT, ``lastrowid``) são
  resolvidas por helpers (``autoincrement_pk``, ``returning_id``...).

Nenhuma credencial sensível é tratada aqui — isso é responsabilidade do
``EncryptionService``/``TelegramAccountService``. Este módulo só cuida de I/O.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Iterator

log = logging.getLogger("tgplayer.web.db")

try:  # psycopg (v3) é opcional — só necessário quando há DATABASE_URL.
    import psycopg
    from psycopg.rows import dict_row

    _HAS_PSYCOPG = True
except Exception:  # noqa: BLE001
    psycopg = None  # type: ignore[assignment]
    dict_row = None  # type: ignore[assignment]
    _HAS_PSYCOPG = False


__all__ = ["Db", "make_db", "is_postgres_url"]


def is_postgres_url(url: str | None) -> bool:
    if not url:
        return False
    return url.startswith("postgres://") or url.startswith("postgresql://")


def _normalize_pg_url(url: str) -> str:
    """Normaliza a URL e garante SSL (Supabase exige TLS)."""
    # psycopg aceita 'postgresql://'; alguns provedores usam 'postgres://'.
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if "sslmode=" not in url:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}sslmode=require"
    return url


class _DictCursorWrapper:
    """Faz o cursor parecer com o do sqlite3: ``.fetchone()`` -> dict-like.

    Também traduz placeholders ``?`` -> ``%s`` no Postgres e expõe
    ``lastrowid`` via RETURNING quando aplicável.
    """

    def __init__(self, cursor: Any, is_pg: bool) -> None:
        self._cur = cursor
        self._is_pg = is_pg
        self.lastrowid: int | None = None

    @staticmethod
    def _translate(sql: str) -> str:
        # Converte placeholders posicionais ? -> %s, ignorando os que estão
        # dentro de strings literais simples (raros no nosso SQL controlado).
        return re.sub(r"\?", "%s", sql)

    def execute(self, sql: str, params: tuple | list | None = None) -> "_DictCursorWrapper":
        params = tuple(params or ())
        if self._is_pg:
            sql_pg = self._translate(sql)
            # Captura INSERTs simples para preencher lastrowid via RETURNING id.
            wants_id = (
                sql_pg.lstrip().lower().startswith("insert")
                and " returning " not in sql_pg.lower()
            )
            if wants_id:
                sql_pg = sql_pg.rstrip().rstrip(";") + " RETURNING id"
            try:
                self._cur.execute(sql_pg, params)
            except Exception:
                if wants_id:
                    # Tabela pode não ter coluna id (ex.: settings/moov_cache):
                    # repete sem RETURNING.
                    self._cur.execute(self._translate(sql), params)
                else:
                    raise
            else:
                if wants_id:
                    row = self._cur.fetchone()
                    if row is not None:
                        self.lastrowid = int(row["id"] if isinstance(row, dict) else row[0])
        else:
            self._cur.execute(sql, params)
            self.lastrowid = self._cur.lastrowid
        return self

    def executescript(self, script: str) -> None:
        if self._is_pg:
            for stmt in script.split(";"):
                if stmt.strip():
                    self._cur.execute(stmt)
        else:
            self._cur.executescript(script)

    def fetchone(self) -> Any:
        return self._cur.fetchone()

    def fetchall(self) -> Any:
        return self._cur.fetchall()


class _ConnWrapper:
    """Conexão que entrega ``_DictCursorWrapper`` e replica a API do sqlite3."""

    def __init__(self, raw: Any, is_pg: bool) -> None:
        self._raw = raw
        self._is_pg = is_pg

    def execute(self, sql: str, params: tuple | list | None = None) -> _DictCursorWrapper:
        cur = self._raw.cursor() if self._is_pg else self._raw.cursor()
        wrapper = _DictCursorWrapper(cur, self._is_pg)
        return wrapper.execute(sql, params)

    def executescript(self, script: str) -> None:
        cur = self._raw.cursor()
        _DictCursorWrapper(cur, self._is_pg).executescript(script)

    def cursor(self) -> _DictCursorWrapper:
        return _DictCursorWrapper(self._raw.cursor(), self._is_pg)

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def close(self) -> None:
        self._raw.close()


class Db:
    """Backend de banco unificado (Postgres ou SQLite)."""

    def __init__(self, url: str | None = None, sqlite_path: str | None = None) -> None:
        self.url = url
        self.sqlite_path = sqlite_path
        self.is_postgres = is_postgres_url(url)
        if self.is_postgres and not _HAS_PSYCOPG:
            raise RuntimeError(
                "DATABASE_URL é Postgres mas 'psycopg' não está instalado. "
                "Adicione 'psycopg[binary]' ao requirements.txt."
            )
        if self.is_postgres:
            self._pg_url = _normalize_pg_url(url)  # type: ignore[arg-type]
        elif not sqlite_path:
            raise RuntimeError("Sem DATABASE_URL e sem caminho SQLite definido.")

    # Tipos / dialeto -------------------------------------------------------
    @property
    def autoincrement_pk(self) -> str:
        """Definição de PK auto-incremento conforme o backend."""
        return "BIGSERIAL PRIMARY KEY" if self.is_postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"

    @property
    def real_type(self) -> str:
        return "DOUBLE PRECISION" if self.is_postgres else "REAL"

    @property
    def insert_or_ignore(self) -> str:
        return "INSERT" if self.is_postgres else "INSERT OR IGNORE"

    # Conexão ---------------------------------------------------------------
    @contextmanager
    def connect(self) -> Iterator[_ConnWrapper]:
        if self.is_postgres:
            conn = psycopg.connect(
                self._pg_url,
                row_factory=dict_row,
                autocommit=False,
                # Garante codec de texto UTF-8 -> colunas TEXT voltam como str
                # (e não bytes) independentemente do server_encoding. Supabase
                # já é UTF8; isto só reforça a robustez.
                client_encoding="UTF8",
            )
            wrapper = _ConnWrapper(conn, True)
            try:
                yield wrapper
                wrapper.commit()
            except Exception:
                wrapper.rollback()
                raise
            finally:
                wrapper.close()
        else:
            last_exc: Exception | None = None
            raw = None
            for attempt in range(5):
                try:
                    raw = sqlite3.connect(self.sqlite_path, timeout=5.0)
                    break
                except sqlite3.OperationalError as exc:  # noqa: PERF203
                    last_exc = exc
                    time.sleep(0.15 * (2 ** attempt))
            if raw is None:
                assert last_exc is not None
                raise last_exc
            raw.row_factory = sqlite3.Row
            raw.execute("PRAGMA foreign_keys=ON")
            raw.execute("PRAGMA busy_timeout=4000")
            wrapper = _ConnWrapper(raw, False)
            try:
                yield wrapper
                wrapper.commit()
            finally:
                wrapper.close()


def make_db(database_url: str | None, sqlite_path: str | None) -> Db:
    """Cria o backend adequado: Postgres se houver URL, senão SQLite."""
    if is_postgres_url(database_url):
        log.info("Persistência: PostgreSQL (Supabase).")
        return Db(url=database_url)
    log.info("Persistência: SQLite local em %s", sqlite_path)
    return Db(sqlite_path=sqlite_path)

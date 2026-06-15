"""Camada de banco de dados (SQLite, WAL) do TgPlayer.

Modelo de dados:
- courses    : um curso por chat do Telegram (com is_forum, color, sort_order).
- subjects   : MATÉRIAS / tópicos de um curso (com telegram_topic_id,
               summary_text, sort_order). Em canal/grupo simples há 1 matéria.
- videos     : aulas (module, lesson, type, position_ms, progress, watched,
               favorite, note, sort_order, manual).
- settings   : preferências (tema, etc.).
- pomodoro_sessions / tasks / study_log : módulo de produtividade.

Princípios:
- Migrações suaves via `_ensure_column` (bancos antigos da v4/v5 continuam a
  funcionar).
- `replace_videos` PRESERVA edições do usuário (título, matéria, favorito,
  progresso, anotações) ao re-sincronizar.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator

from .paths import DB_PATH, ensure_dirs

log = logging.getLogger(__name__)

# Quantas entradas manter no cache de metadados `moov` (cursos têm muitas aulas).
MOOV_CACHE_LIMIT = 200
# Tentativas/backoff ao abrir o banco (anti "database is locked").
DB_OPEN_RETRIES = 5
DB_OPEN_BACKOFF = 0.15


# --------------------------------------------------------------------------- DTOs
@dataclass
class Course:
    id: int
    chat_id: str
    title: str
    username: str | None = None
    chat_type: str | None = None
    is_forum: int = 0
    added_at: str | None = None
    last_sync: str | None = None
    color: str | None = None
    sort_order: int = 0


@dataclass
class Subject:
    """Uma matéria/tópico dentro de um curso."""

    id: int
    course_id: int
    title: str
    telegram_topic_id: str | None = None
    summary_text: str | None = None
    sort_order: int = 0
    manual: int = 0


@dataclass
class Video:
    id: int
    course_id: int
    subject_id: int | None
    chat_id: str
    message_id: int
    title: str
    file_name: str
    mime_type: str | None
    size: int | None
    duration: int | None
    date: str | None
    width: int | None = None
    height: int | None = None
    hashtags: list[str] = field(default_factory=list)
    caption: str | None = None
    watched_at: str | None = None
    module: str | None = None
    lesson: str | None = None
    type: str | None = None
    position_ms: int = 0
    progress: float = 0.0
    favorite: int = 0
    note: str | None = None
    sort_order: int = 0
    manual: int = 0


# --------------------------------------------------------------------- Database
class Database:
    def __init__(self, path=DB_PATH):
        ensure_dirs()
        self.path = path
        self.init()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Abre a conexão com RETRY + BACKOFF exponencial.

        Ideia portada de `db.rs` do projeto de referência: bancos SQLite em WAL
        podem retornar "database is locked" sob concorrência (sincronização +
        salvamento de progresso). Tentamos algumas vezes com espera crescente
        antes de desistir, tornando a abertura resiliente.
        """
        conn = self._connect_with_retry()
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=4000")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _connect_with_retry(self) -> sqlite3.Connection:
        last_exc: Exception | None = None
        for attempt in range(DB_OPEN_RETRIES):
            try:
                return sqlite3.connect(self.path, timeout=5.0)
            except sqlite3.OperationalError as exc:  # noqa: PERF203
                last_exc = exc
                wait = DB_OPEN_BACKOFF * (2 ** attempt)
                log.warning(
                    "Banco ocupado (tentativa %d/%d): %s — aguardando %.2fs",
                    attempt + 1, DB_OPEN_RETRIES, exc, wait,
                )
                time.sleep(wait)
        # Esgotou as tentativas: propaga o último erro.
        assert last_exc is not None
        raise last_exc

    # ------------------------------------------------------------------ schema
    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;

                CREATE TABLE IF NOT EXISTS settings (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                );

                CREATE TABLE IF NOT EXISTS courses (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id    TEXT NOT NULL UNIQUE,
                    title      TEXT NOT NULL,
                    username   TEXT,
                    chat_type  TEXT,
                    is_forum   INTEGER DEFAULT 0,
                    added_at   TEXT NOT NULL,
                    last_sync  TEXT,
                    color      TEXT,
                    sort_order INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS subjects (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    course_id         INTEGER NOT NULL,
                    title             TEXT NOT NULL,
                    telegram_topic_id TEXT,
                    summary_text      TEXT,
                    sort_order        INTEGER DEFAULT 0,
                    manual            INTEGER DEFAULT 0,
                    FOREIGN KEY(course_id) REFERENCES courses(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_subjects_course ON subjects(course_id);

                CREATE TABLE IF NOT EXISTS videos (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    course_id   INTEGER NOT NULL,
                    subject_id  INTEGER,
                    chat_id     TEXT NOT NULL,
                    message_id  INTEGER NOT NULL,
                    title       TEXT NOT NULL,
                    file_name   TEXT NOT NULL,
                    mime_type   TEXT,
                    size        INTEGER,
                    duration    INTEGER,
                    width       INTEGER,
                    height      INTEGER,
                    date        TEXT,
                    hashtags_json TEXT,
                    caption     TEXT,
                    watched_at  TEXT,
                    module      TEXT,
                    lesson      TEXT,
                    type        TEXT,
                    position_ms INTEGER DEFAULT 0,
                    progress    REAL DEFAULT 0,
                    favorite    INTEGER DEFAULT 0,
                    note        TEXT,
                    sort_order  INTEGER DEFAULT 0,
                    manual      INTEGER DEFAULT 0,
                    UNIQUE(chat_id, message_id),
                    FOREIGN KEY(course_id) REFERENCES courses(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_videos_course  ON videos(course_id);
                CREATE INDEX IF NOT EXISTS idx_videos_subject ON videos(subject_id);
                CREATE INDEX IF NOT EXISTS idx_videos_msg     ON videos(chat_id, message_id);

                CREATE TABLE IF NOT EXISTS pomodoro_sessions (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at   TEXT NOT NULL,
                    ended_at     TEXT,
                    seconds      INTEGER NOT NULL DEFAULT 0,
                    kind         TEXT DEFAULT 'foco',
                    course_id    INTEGER,
                    subject_id   INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_pomodoro_date ON pomodoro_sessions(started_at);

                CREATE TABLE IF NOT EXISTS tasks (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    text       TEXT NOT NULL,
                    done       INTEGER DEFAULT 0,
                    priority   INTEGER DEFAULT 1,
                    due_date   TEXT,
                    course_id  INTEGER,
                    sort_order INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    done_at    TEXT
                );

                CREATE TABLE IF NOT EXISTS study_log (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    day        TEXT NOT NULL,
                    seconds    INTEGER NOT NULL DEFAULT 0,
                    course_id  INTEGER,
                    subject_id INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_studylog_day ON study_log(day);

                -- Cache de metadados do átomo `moov` do MP4 (boot instantâneo na
                -- 2ª vez). Ideia portada de `moovCache.ts` (IndexedDB/LRU).
                CREATE TABLE IF NOT EXISTS moov_cache (
                    chat_id      TEXT NOT NULL,
                    message_id   INTEGER NOT NULL,
                    file_size    INTEGER,
                    moov_offset  INTEGER,
                    moov_size    INTEGER,
                    located      INTEGER DEFAULT 0,
                    duration_ms  INTEGER,
                    width        INTEGER,
                    height       INTEGER,
                    codec        TEXT,
                    tracks       INTEGER,
                    updated_at   REAL NOT NULL,
                    PRIMARY KEY(chat_id, message_id)
                );
                CREATE INDEX IF NOT EXISTS idx_moov_updated ON moov_cache(updated_at);
                """
            )
            # ---- Migrações suaves de bancos antigos (v4/v5) -----------------
            self._ensure_column(conn, "courses", "is_forum", "INTEGER DEFAULT 0")
            self._ensure_column(conn, "courses", "color", "TEXT")
            self._ensure_column(conn, "courses", "sort_order", "INTEGER DEFAULT 0")
            self._ensure_column(conn, "videos", "subject_id", "INTEGER")
            self._ensure_column(conn, "videos", "module", "TEXT")
            self._ensure_column(conn, "videos", "lesson", "TEXT")
            self._ensure_column(conn, "videos", "type", "TEXT")
            self._ensure_column(conn, "videos", "position_ms", "INTEGER DEFAULT 0")
            self._ensure_column(conn, "videos", "progress", "REAL DEFAULT 0")
            self._ensure_column(conn, "videos", "favorite", "INTEGER DEFAULT 0")
            self._ensure_column(conn, "videos", "note", "TEXT")
            self._ensure_column(conn, "videos", "sort_order", "INTEGER DEFAULT 0")
            self._ensure_column(conn, "videos", "manual", "INTEGER DEFAULT 0")
            # Migração v6.2 -> v6.3: resolução + timestamp do último acesso.
            self._ensure_column(conn, "videos", "width", "INTEGER")
            self._ensure_column(conn, "videos", "height", "INTEGER")
            self._ensure_column(conn, "videos", "last_watched_at", "TEXT")
            # Migração v5 -> v6: campos antigos topic_id/topic_title viram subjects.
            self._migrate_topics_to_subjects(conn)

    def _ensure_column(
        self, conn: sqlite3.Connection, table: str, column: str, column_type: str
    ) -> None:
        cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")

    def _migrate_topics_to_subjects(self, conn: sqlite3.Connection) -> None:
        """Converte o antigo `topics_json`/`topic_title` (v5) em subjects (v6)."""
        course_cols = {
            row["name"] for row in conn.execute("PRAGMA table_info(courses)").fetchall()
        }
        if "topics_json" not in course_cols:
            return  # banco já novo
        video_cols = {
            row["name"] for row in conn.execute("PRAGMA table_info(videos)").fetchall()
        }
        for course in conn.execute("SELECT id, topics_json FROM courses").fetchall():
            has_subjects = conn.execute(
                "SELECT 1 FROM subjects WHERE course_id=? LIMIT 1", (course["id"],)
            ).fetchone()
            if has_subjects:
                continue
            try:
                topics = json.loads(course["topics_json"] or "[]")
            except Exception:
                topics = []
            title_to_subject: dict[str, int] = {}
            for order, topic in enumerate(topics if isinstance(topics, list) else []):
                title = (topic or {}).get("title") or "Matéria"
                cur = conn.execute(
                    "INSERT INTO subjects(course_id, title, telegram_topic_id, "
                    "summary_text, sort_order, manual) VALUES(?,?,?,?,?,?)",
                    (
                        course["id"],
                        title,
                        str((topic or {}).get("telegram_topic_id") or ""),
                        (topic or {}).get("summary_text") or "",
                        order,
                        1 if (topic or {}).get("manual") else 0,
                    ),
                )
                title_to_subject[title] = int(cur.lastrowid)
            # Liga vídeos antigos pela coluna topic_title, se existir.
            if "topic_title" in video_cols and title_to_subject:
                for title, sid in title_to_subject.items():
                    conn.execute(
                        "UPDATE videos SET subject_id=? WHERE course_id=? "
                        "AND subject_id IS NULL AND topic_title=?",
                        (sid, course["id"], title),
                    )

    def now(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def today(self) -> str:
        return datetime.now().strftime("%Y-%m-%d")

    # ----------------------------------------------------------------- settings
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value: str | None) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    # --------------------------------------------------------------- moov_cache
    def get_moov_cache(self, chat_id: str, message_id: int) -> dict[str, Any] | None:
        """Retorna os metadados `moov` cacheados desta aula (ou None)."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM moov_cache WHERE chat_id=? AND message_id=?",
                (str(chat_id), int(message_id)),
            ).fetchone()
            if not row:
                return None
            # Atualiza o "last access" para o LRU (toca a entrada lida).
            conn.execute(
                "UPDATE moov_cache SET updated_at=? WHERE chat_id=? AND message_id=?",
                (time.time(), str(chat_id), int(message_id)),
            )
            return dict(row)

    def set_moov_cache(
        self,
        chat_id: str,
        message_id: int,
        file_size: int | None = None,
        moov_offset: int | None = None,
        moov_size: int | None = None,
        located: int | None = None,
        duration_ms: int | None = None,
        width: int | None = None,
        height: int | None = None,
        codec: str | None = None,
        tracks: int | None = None,
    ) -> None:
        """Grava/atualiza os metadados `moov` e poda o cache (LRU 200).

        Todos os campos além de (chat_id, message_id) são opcionais e usam
        COALESCE no UPSERT — isto permite uma atualização apenas de metadados
        (ex.: pré-busca de resolução) sem apagar um `moov_offset` já descoberto.
        """
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO moov_cache(
                    chat_id, message_id, file_size, moov_offset, moov_size,
                    located, duration_ms, width, height, codec, tracks, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(chat_id, message_id) DO UPDATE SET
                    file_size=COALESCE(excluded.file_size, moov_cache.file_size),
                    moov_offset=COALESCE(excluded.moov_offset, moov_cache.moov_offset),
                    moov_size=COALESCE(excluded.moov_size, moov_cache.moov_size),
                    located=COALESCE(excluded.located, moov_cache.located),
                    duration_ms=COALESCE(excluded.duration_ms, moov_cache.duration_ms),
                    width=COALESCE(excluded.width, moov_cache.width),
                    height=COALESCE(excluded.height, moov_cache.height),
                    codec=COALESCE(excluded.codec, moov_cache.codec),
                    tracks=COALESCE(excluded.tracks, moov_cache.tracks),
                    updated_at=excluded.updated_at
                """,
                (
                    str(chat_id), int(message_id), file_size, moov_offset, moov_size,
                    (int(located) if located is not None else None),
                    duration_ms, width, height, codec, tracks,
                    time.time(),
                ),
            )
            # Poda LRU: mantém apenas as MOOV_CACHE_LIMIT entradas mais recentes.
            count = conn.execute("SELECT COUNT(*) AS n FROM moov_cache").fetchone()["n"]
            if count > MOOV_CACHE_LIMIT:
                conn.execute(
                    "DELETE FROM moov_cache WHERE rowid IN ("
                    "SELECT rowid FROM moov_cache ORDER BY updated_at ASC LIMIT ?)",
                    (int(count - MOOV_CACHE_LIMIT),),
                )

    def clear_moov_cache(self, chat_id: str | None = None, message_id: int | None = None) -> None:
        with self.connect() as conn:
            if chat_id is not None and message_id is not None:
                conn.execute(
                    "DELETE FROM moov_cache WHERE chat_id=? AND message_id=?",
                    (str(chat_id), int(message_id)),
                )
            else:
                conn.execute("DELETE FROM moov_cache")

    # ------------------------------------------------------------------ courses
    def upsert_course(self, data: dict[str, Any]) -> int:
        with self.connect() as conn:
            now = self.now()
            conn.execute(
                """
                INSERT INTO courses(chat_id, title, username, chat_type, is_forum, added_at, last_sync)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    username=excluded.username,
                    chat_type=excluded.chat_type,
                    is_forum=excluded.is_forum
                """,
                (
                    str(data["chat_id"]),
                    data.get("title") or str(data["chat_id"]),
                    data.get("username"),
                    data.get("chat_type"),
                    1 if data.get("is_forum") else 0,
                    now,
                    data.get("last_sync"),
                ),
            )
            row = conn.execute(
                "SELECT id FROM courses WHERE chat_id=?", (str(data["chat_id"]),)
            ).fetchone()
            return int(row["id"])

    def list_courses(self) -> list[Course]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM courses ORDER BY sort_order ASC, title COLLATE NOCASE"
            ).fetchall()
        return [self._row_to_course(row) for row in rows]

    def get_course(self, course_id: int) -> Course | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM courses WHERE id=?", (course_id,)).fetchone()
        return self._row_to_course(row) if row else None

    def rename_course(self, course_id: int, title: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE courses SET title=? WHERE id=?", (title.strip() or "Curso", course_id)
            )

    def set_course_color(self, course_id: int, color: str | None) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE courses SET color=? WHERE id=?", (color, course_id))

    def delete_course(self, course_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM videos WHERE course_id=?", (course_id,))
            conn.execute("DELETE FROM subjects WHERE course_id=?", (course_id,))
            conn.execute("DELETE FROM courses WHERE id=?", (course_id,))

    def reorder_courses(self, ordered_ids: list[int]) -> None:
        with self.connect() as conn:
            for index, course_id in enumerate(ordered_ids):
                conn.execute("UPDATE courses SET sort_order=? WHERE id=?", (index, course_id))

    def touch_course_sync(self, course_id: int) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE courses SET last_sync=? WHERE id=?", (self.now(), course_id))

    # ----------------------------------------------------------------- subjects
    def list_subjects(self, course_id: int) -> list[Subject]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM subjects WHERE course_id=? "
                "ORDER BY sort_order ASC, title COLLATE NOCASE",
                (course_id,),
            ).fetchall()
        return [self._row_to_subject(r) for r in rows]

    def get_subject(self, subject_id: int) -> Subject | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM subjects WHERE id=?", (subject_id,)).fetchone()
        return self._row_to_subject(row) if row else None

    def add_subject(
        self,
        course_id: int,
        title: str,
        summary_text: str | None = None,
        telegram_topic_id: str | None = None,
        manual: int = 1,
    ) -> int:
        with self.connect() as conn:
            order = conn.execute(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM subjects WHERE course_id=?",
                (course_id,),
            ).fetchone()["n"]
            cur = conn.execute(
                "INSERT INTO subjects(course_id, title, telegram_topic_id, summary_text, "
                "sort_order, manual) VALUES(?,?,?,?,?,?)",
                (course_id, title.strip() or "Matéria", telegram_topic_id, summary_text, order, manual),
            )
            return int(cur.lastrowid)

    def rename_subject(self, subject_id: int, title: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE subjects SET title=? WHERE id=?", (title.strip() or "Matéria", subject_id)
            )

    def update_subject_summary(self, subject_id: int, summary_text: str | None) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE subjects SET summary_text=? WHERE id=?", (summary_text, subject_id)
            )

    def delete_subject(self, subject_id: int) -> None:
        """Exclui a matéria; os vídeos viram 'Sem matéria' (subject_id=NULL)."""
        with self.connect() as conn:
            conn.execute("UPDATE videos SET subject_id=NULL WHERE subject_id=?", (subject_id,))
            conn.execute("DELETE FROM subjects WHERE id=?", (subject_id,))

    def reorder_subjects(self, ordered_ids: list[int]) -> None:
        with self.connect() as conn:
            for index, subject_id in enumerate(ordered_ids):
                conn.execute("UPDATE subjects SET sort_order=? WHERE id=?", (index, subject_id))

    def find_or_create_subject(
        self,
        course_id: int,
        title: str,
        telegram_topic_id: str | None = None,
        summary_text: str | None = None,
        manual: int = 0,
    ) -> int:
        """Retorna o id da matéria correspondente (por telegram_topic_id ou título)."""
        with self.connect() as conn:
            if telegram_topic_id:
                row = conn.execute(
                    "SELECT id FROM subjects WHERE course_id=? AND telegram_topic_id=?",
                    (course_id, str(telegram_topic_id)),
                ).fetchone()
                if row:
                    return int(row["id"])
            row = conn.execute(
                "SELECT id FROM subjects WHERE course_id=? AND title=? COLLATE NOCASE",
                (course_id, title),
            ).fetchone()
            if row:
                return int(row["id"])
        return self.add_subject(course_id, title, summary_text, telegram_topic_id, manual)

    # ------------------------------------------------------------------- videos
    def replace_videos(self, course_id: int, videos: list[dict[str, Any]]) -> None:
        """Insere/atualiza vídeos da sincronização preservando edições do usuário.

        Cada `video` deve trazer um `subject_id` resolvido (matéria correta).
        Campos que o usuário pode ter editado (title, subject_id, favorito,
        progresso, anotações, manual) NÃO são sobrescritos se o vídeo já existir.
        """
        with self.connect() as conn:
            for video in videos:
                existing = conn.execute(
                    "SELECT id FROM videos WHERE chat_id=? AND message_id=?",
                    (str(video["chat_id"]), int(video["message_id"])),
                ).fetchone()
                if existing:
                    conn.execute(
                        """
                        UPDATE videos SET
                            course_id=?, file_name=?, mime_type=?, size=?, duration=?,
                            width=?, height=?, date=?, hashtags_json=?, caption=?
                        WHERE chat_id=? AND message_id=?
                        """,
                        (
                            course_id,
                            video.get("file_name") or "video.mp4",
                            video.get("mime_type"),
                            video.get("size"),
                            video.get("duration"),
                            video.get("width"),
                            video.get("height"),
                            video.get("date"),
                            json.dumps(video.get("hashtags") or [], ensure_ascii=False),
                            video.get("caption"),
                            str(video["chat_id"]),
                            int(video["message_id"]),
                        ),
                    )
                    # Só preenche a matéria se ela ainda estiver vazia (respeita edição manual).
                    if video.get("subject_id") is not None:
                        conn.execute(
                            "UPDATE videos SET subject_id=? "
                            "WHERE chat_id=? AND message_id=? AND subject_id IS NULL",
                            (video.get("subject_id"), str(video["chat_id"]), int(video["message_id"])),
                        )
                    # Atualiza módulo/aula/tipo só se ainda não definidos.
                    for col in ("module", "lesson", "type"):
                        if video.get(col):
                            conn.execute(
                                f"UPDATE videos SET {col}=? WHERE chat_id=? AND message_id=? "
                                f"AND ({col} IS NULL OR {col}='')",
                                (video.get(col), str(video["chat_id"]), int(video["message_id"])),
                            )
                else:
                    conn.execute(
                        """
                        INSERT INTO videos(
                            course_id, subject_id, chat_id, message_id, title, file_name,
                            mime_type, size, duration, width, height, date, hashtags_json,
                            caption, module, lesson, type
                        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            course_id,
                            video.get("subject_id"),
                            str(video["chat_id"]),
                            int(video["message_id"]),
                            video.get("title") or video.get("file_name") or "Aula",
                            video.get("file_name") or "video.mp4",
                            video.get("mime_type"),
                            video.get("size"),
                            video.get("duration"),
                            video.get("width"),
                            video.get("height"),
                            video.get("date"),
                            json.dumps(video.get("hashtags") or [], ensure_ascii=False),
                            video.get("caption"),
                            video.get("module"),
                            video.get("lesson"),
                            video.get("type"),
                        ),
                    )
            conn.execute("UPDATE courses SET last_sync=? WHERE id=?", (self.now(), course_id))

    def list_videos(self, course_id: int) -> list[Video]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM videos WHERE course_id=? "
                "ORDER BY sort_order ASC, message_id ASC",
                (course_id,),
            ).fetchall()
        return [self._row_to_video(row) for row in rows]

    def list_videos_for_subject(self, subject_id: int) -> list[Video]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM videos WHERE subject_id=? ORDER BY sort_order ASC, message_id ASC",
                (subject_id,),
            ).fetchall()
        return [self._row_to_video(row) for row in rows]

    def get_video(self, video_id: int) -> Video | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone()
        return self._row_to_video(row) if row else None

    def mark_watched(self, video_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE videos SET watched_at=?, progress=1.0 WHERE id=?", (self.now(), video_id)
            )

    def mark_unwatched(self, video_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE videos SET watched_at=NULL, position_ms=0, progress=0 WHERE id=?",
                (video_id,),
            )

    def rename_video(self, video_id: int, title: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE videos SET title=? WHERE id=?", (title.strip() or "Aula", video_id)
            )

    def set_video_subject(self, video_id: int, subject_id: int | None) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE videos SET subject_id=? WHERE id=?", (subject_id, video_id))

    def set_video_dimensions(
        self,
        chat_id: str,
        message_id: int,
        width: int | None = None,
        height: int | None = None,
        duration: int | None = None,
    ) -> None:
        """Atualiza resolução/duração da aula (apenas quando ainda vazias).

        Usado pela pré-busca de metadados em 2º plano; preserva valores já
        existentes via COALESCE para não regredir dados importados.
        """
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE videos SET
                    width=COALESCE(?, width),
                    height=COALESCE(?, height),
                    duration=COALESCE(NULLIF(duration, 0), ?)
                WHERE chat_id=? AND message_id=?
                """,
                (width, height, duration, str(chat_id), int(message_id)),
            )

    def set_video_meta(
        self,
        video_id: int,
        module: str | None = None,
        lesson: str | None = None,
        type_: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE videos SET module=?, lesson=?, type=? WHERE id=?",
                (module, lesson, type_, video_id),
            )

    def set_video_hashtags(self, video_id: int, hashtags: list[str]) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE videos SET hashtags_json=? WHERE id=?",
                (json.dumps(hashtags or [], ensure_ascii=False), video_id),
            )

    def set_video_note(self, video_id: int, note: str | None) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE videos SET note=? WHERE id=?", (note, video_id))

    def toggle_favorite(self, video_id: int) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT favorite FROM videos WHERE id=?", (video_id,)).fetchone()
            new_value = 0 if (row and row["favorite"]) else 1
            conn.execute("UPDATE videos SET favorite=? WHERE id=?", (new_value, video_id))
            return new_value

    def save_progress(self, video_id: int, position_ms: int, duration_ms: int | None) -> None:
        progress = 0.0
        if duration_ms and duration_ms > 0:
            progress = max(0.0, min(1.0, position_ms / duration_ms))
        now = self.now()
        with self.connect() as conn:
            if progress >= 0.92:
                conn.execute(
                    "UPDATE videos SET position_ms=?, progress=?, last_watched_at=?, "
                    "watched_at=COALESCE(watched_at, ?) WHERE id=?",
                    (int(position_ms), progress, now, now, video_id),
                )
            else:
                conn.execute(
                    "UPDATE videos SET position_ms=?, progress=?, last_watched_at=? WHERE id=?",
                    (int(position_ms), progress, now, video_id),
                )

    def continue_watching(self, limit: int = 12) -> list[Video]:
        """Aulas em andamento (resume): progresso parcial, mais recentes primeiro.

        Ideia portada do 'continuar assistindo' do projeto de referência.
        """
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM videos WHERE progress > 0.02 AND progress < 0.95 "
                "AND last_watched_at IS NOT NULL "
                "ORDER BY last_watched_at DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [self._row_to_video(dict(r)) for r in rows]

    def course_progress(self, course_id: int) -> tuple[int, int]:
        """Retorna (assistidas, total) de um curso para exibir o % de progresso."""
        with self.connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) AS n FROM videos WHERE course_id=?", (course_id,)
            ).fetchone()["n"]
            done = conn.execute(
                "SELECT COUNT(*) AS n FROM videos WHERE course_id=? AND "
                "(watched_at IS NOT NULL OR progress >= 0.92)",
                (course_id,),
            ).fetchone()["n"]
        return int(done), int(total)

    def reorder_videos(self, ordered_ids: list[int]) -> None:
        with self.connect() as conn:
            for index, video_id in enumerate(ordered_ids):
                conn.execute("UPDATE videos SET sort_order=? WHERE id=?", (index, video_id))

    def delete_video(self, video_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM videos WHERE id=?", (video_id,))

    # =================================================== módulo de produtividade
    # ---- Pomodoro -----------------------------------------------------------
    def add_pomodoro_session(
        self,
        seconds: int,
        kind: str = "foco",
        course_id: int | None = None,
        subject_id: int | None = None,
    ) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO pomodoro_sessions(started_at, ended_at, seconds, kind, "
                "course_id, subject_id) VALUES(?,?,?,?,?,?)",
                (self.now(), self.now(), int(seconds), kind, course_id, subject_id),
            )
            return int(cur.lastrowid)

    def count_pomodoros_today(self) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM pomodoro_sessions "
                "WHERE kind='foco' AND substr(started_at,1,10)=?",
                (datetime.now(timezone.utc).strftime("%Y-%m-%d"),),
            ).fetchone()
            return int(row["n"] or 0)

    # ---- study_log (tempo de estudo por dia) --------------------------------
    def log_study_time(
        self,
        seconds: int,
        course_id: int | None = None,
        subject_id: int | None = None,
        day: str | None = None,
    ) -> None:
        if seconds <= 0:
            return
        day = day or self.today()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id, seconds FROM study_log WHERE day=? AND "
                "IFNULL(course_id,-1)=IFNULL(?, -1) AND IFNULL(subject_id,-1)=IFNULL(?, -1)",
                (day, course_id, subject_id),
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE study_log SET seconds=seconds+? WHERE id=?",
                    (int(seconds), row["id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO study_log(day, seconds, course_id, subject_id) VALUES(?,?,?,?)",
                    (day, int(seconds), course_id, subject_id),
                )

    def study_seconds_by_day(self, days: int = 7) -> list[tuple[str, int]]:
        """Retorna [(YYYY-MM-DD, segundos)] dos últimos `days` dias (cronológico)."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT day, SUM(seconds) AS s FROM study_log GROUP BY day"
            ).fetchall()
        by_day = {r["day"]: int(r["s"] or 0) for r in rows}
        out: list[tuple[str, int]] = []
        from datetime import timedelta

        today = datetime.now()
        for i in range(days - 1, -1, -1):
            d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            out.append((d, by_day.get(d, 0)))
        return out

    def total_study_seconds(self) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT COALESCE(SUM(seconds),0) AS s FROM study_log").fetchone()
            return int(row["s"] or 0)

    def study_streak_days(self) -> int:
        """Sequência de dias consecutivos (terminando hoje) com algum estudo."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT day FROM study_log WHERE seconds>0"
            ).fetchall()
        days = {r["day"] for r in rows}
        from datetime import timedelta

        streak = 0
        cursor = datetime.now()
        # Permite que a sequência conte mesmo se hoje ainda não houve estudo.
        if cursor.strftime("%Y-%m-%d") not in days:
            cursor -= timedelta(days=1)
        while cursor.strftime("%Y-%m-%d") in days:
            streak += 1
            cursor -= timedelta(days=1)
        return streak

    def watched_count_by_course(self) -> list[tuple[str, int]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT c.title AS title, "
                "SUM(CASE WHEN v.watched_at IS NOT NULL THEN 1 ELSE 0 END) AS watched "
                "FROM courses c LEFT JOIN videos v ON v.course_id=c.id "
                "GROUP BY c.id ORDER BY watched DESC"
            ).fetchall()
        return [(r["title"], int(r["watched"] or 0)) for r in rows]

    # ---- tasks --------------------------------------------------------------
    def list_tasks(self, include_done: bool = True) -> list[dict[str, Any]]:
        with self.connect() as conn:
            sql = "SELECT * FROM tasks"
            if not include_done:
                sql += " WHERE done=0"
            sql += " ORDER BY done ASC, sort_order ASC, id ASC"
            rows = conn.execute(sql).fetchall()
        return [dict(r) for r in rows]

    def add_task(
        self,
        text: str,
        priority: int = 1,
        due_date: str | None = None,
        course_id: int | None = None,
    ) -> int:
        with self.connect() as conn:
            order = conn.execute(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM tasks"
            ).fetchone()["n"]
            cur = conn.execute(
                "INSERT INTO tasks(text, priority, due_date, course_id, sort_order, created_at) "
                "VALUES(?,?,?,?,?,?)",
                (text.strip(), int(priority), due_date, course_id, order, self.now()),
            )
            return int(cur.lastrowid)

    def update_task(
        self,
        task_id: int,
        text: str | None = None,
        priority: int | None = None,
        due_date: str | None = None,
    ) -> None:
        with self.connect() as conn:
            current = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not current:
                return
            conn.execute(
                "UPDATE tasks SET text=?, priority=?, due_date=? WHERE id=?",
                (
                    (text if text is not None else current["text"]).strip() or current["text"],
                    int(priority if priority is not None else current["priority"]),
                    due_date if due_date is not None else current["due_date"],
                    task_id,
                ),
            )

    def toggle_task(self, task_id: int) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT done FROM tasks WHERE id=?", (task_id,)).fetchone()
            new_value = 0 if (row and row["done"]) else 1
            conn.execute(
                "UPDATE tasks SET done=?, done_at=? WHERE id=?",
                (new_value, self.now() if new_value else None, task_id),
            )
            return new_value

    def delete_task(self, task_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))

    def reorder_tasks(self, ordered_ids: list[int]) -> None:
        with self.connect() as conn:
            for index, task_id in enumerate(ordered_ids):
                conn.execute("UPDATE tasks SET sort_order=? WHERE id=?", (index, task_id))

    # ------------------------------------------------------------ row -> objeto
    def _row_to_course(self, row: sqlite3.Row) -> Course:
        data = dict(row)
        return Course(
            id=int(data["id"]),
            chat_id=str(data["chat_id"]),
            title=data.get("title") or str(data.get("chat_id")),
            username=data.get("username"),
            chat_type=data.get("chat_type"),
            is_forum=int(data.get("is_forum") or 0),
            added_at=data.get("added_at"),
            last_sync=data.get("last_sync"),
            color=data.get("color"),
            sort_order=int(data.get("sort_order") or 0),
        )

    def _row_to_subject(self, row: sqlite3.Row) -> Subject:
        data = dict(row)
        return Subject(
            id=int(data["id"]),
            course_id=int(data["course_id"]),
            title=data.get("title") or "Matéria",
            telegram_topic_id=data.get("telegram_topic_id"),
            summary_text=data.get("summary_text"),
            sort_order=int(data.get("sort_order") or 0),
            manual=int(data.get("manual") or 0),
        )

    def _row_to_video(self, row: sqlite3.Row) -> Video:
        data = dict(row)
        try:
            tags = json.loads(data.get("hashtags_json") or "[]")
        except Exception:
            tags = []
        return Video(
            id=int(data["id"]),
            course_id=int(data["course_id"]),
            subject_id=(int(data["subject_id"]) if data.get("subject_id") is not None else None),
            chat_id=str(data["chat_id"]),
            message_id=int(data["message_id"]),
            title=data.get("title") or "Aula",
            file_name=data.get("file_name") or "video.mp4",
            mime_type=data.get("mime_type"),
            size=data.get("size"),
            duration=data.get("duration"),
            date=data.get("date"),
            width=data.get("width"),
            height=data.get("height"),
            hashtags=tags,
            caption=data.get("caption"),
            watched_at=data.get("watched_at"),
            module=data.get("module"),
            lesson=data.get("lesson"),
            type=data.get("type"),
            position_ms=int(data.get("position_ms") or 0),
            progress=float(data.get("progress") or 0.0),
            favorite=int(data.get("favorite") or 0),
            note=data.get("note"),
            sort_order=int(data.get("sort_order") or 0),
            manual=int(data.get("manual") or 0),
        )

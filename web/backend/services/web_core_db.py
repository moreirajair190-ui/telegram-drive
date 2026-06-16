"""WebCoreDatabase — cursos/matérias/vídeos/estudo persistidos no Postgres.

Esta classe reimplementa, de forma DIALECT-AGNOSTIC (Postgres ou SQLite), todos
os métodos do core (`tgplayer.db.Database`) usados pela camada web. Assim a
aplicação web roda 100% no Supabase (Render Free, sem filesystem), enquanto o
core do app desktop permanece intacto.

Compatibilidade: expõe a MESMA API pública usada por ``main.py`` e pelo
``TelegramService`` (via ``helper.db``), de modo que nenhuma rota precise mudar.

Sem filesystem persistente: a conexão vem do adaptador ``Db`` (Postgres quando
há ``DATABASE_URL``; SQLite local apenas em dev).
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

try:
    from services.db_backend import Db, make_db
except Exception:  # noqa: BLE001
    from .db_backend import Db, make_db  # type: ignore[no-redef]

# Reaproveita os DTOs do core para manter tipos idênticos aos esperados.
from tgplayer.db import Course, Subject, Video  # noqa: E402

log = logging.getLogger("tgplayer.web.core_db")

MOOV_CACHE_LIMIT = 200


class WebCoreDatabase:
    """Persistência de cursos/vídeos/estudo no backend unificado (Postgres/SQLite)."""

    def __init__(self, db: Db | None = None, *, database_url: str | None = None,
                 sqlite_path: str | None = None) -> None:
        self.db: Db = db if db is not None else make_db(database_url, sqlite_path)
        self.init()

    # ------------------------------------------------------------------ conexão
    @contextmanager
    def connect(self) -> Iterator[Any]:
        with self.db.connect() as conn:
            yield conn

    def now(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def today(self) -> str:
        return datetime.now().strftime("%Y-%m-%d")

    # ------------------------------------------------------------------ schema
    def init(self) -> None:
        pk = self.db.autoincrement_pk
        real = self.db.real_type
        with self.connect() as conn:
            conn.executescript(
                f"""
                CREATE TABLE IF NOT EXISTS settings (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                );

                CREATE TABLE IF NOT EXISTS courses (
                    id         {pk},
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
                    id                {pk},
                    course_id         BIGINT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
                    title             TEXT NOT NULL,
                    telegram_topic_id TEXT,
                    summary_text      TEXT,
                    sort_order        INTEGER DEFAULT 0,
                    manual            INTEGER DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_subjects_course ON subjects(course_id);

                CREATE TABLE IF NOT EXISTS videos (
                    id          {pk},
                    course_id   BIGINT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
                    subject_id  BIGINT,
                    chat_id     TEXT NOT NULL,
                    message_id  BIGINT NOT NULL,
                    title       TEXT NOT NULL,
                    file_name   TEXT NOT NULL,
                    mime_type   TEXT,
                    size        BIGINT,
                    duration    INTEGER,
                    width       INTEGER,
                    height      INTEGER,
                    date        TEXT,
                    hashtags_json TEXT,
                    caption     TEXT,
                    file_id     TEXT,
                    file_unique_id TEXT,
                    watched_at  TEXT,
                    last_watched_at TEXT,
                    module      TEXT,
                    lesson      TEXT,
                    type        TEXT,
                    position_ms BIGINT DEFAULT 0,
                    progress    {real} DEFAULT 0,
                    favorite    INTEGER DEFAULT 0,
                    note        TEXT,
                    sort_order  INTEGER DEFAULT 0,
                    manual      INTEGER DEFAULT 0,
                    UNIQUE(chat_id, message_id)
                );
                CREATE INDEX IF NOT EXISTS idx_videos_course  ON videos(course_id);
                CREATE INDEX IF NOT EXISTS idx_videos_subject ON videos(subject_id);
                CREATE INDEX IF NOT EXISTS idx_videos_msg     ON videos(chat_id, message_id);

                CREATE TABLE IF NOT EXISTS pomodoro_sessions (
                    id           {pk},
                    started_at   TEXT NOT NULL,
                    ended_at     TEXT,
                    seconds      INTEGER NOT NULL DEFAULT 0,
                    kind         TEXT DEFAULT 'foco',
                    course_id    BIGINT,
                    subject_id   BIGINT
                );
                CREATE INDEX IF NOT EXISTS idx_pomodoro_date ON pomodoro_sessions(started_at);

                CREATE TABLE IF NOT EXISTS tasks (
                    id         {pk},
                    text       TEXT NOT NULL,
                    done       INTEGER DEFAULT 0,
                    priority   INTEGER DEFAULT 1,
                    due_date   TEXT,
                    course_id  BIGINT,
                    sort_order INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    done_at    TEXT
                );

                CREATE TABLE IF NOT EXISTS study_log (
                    id         {pk},
                    day        TEXT NOT NULL,
                    seconds    INTEGER NOT NULL DEFAULT 0,
                    course_id  BIGINT,
                    subject_id BIGINT
                );
                CREATE INDEX IF NOT EXISTS idx_studylog_day ON study_log(day);

                CREATE TABLE IF NOT EXISTS moov_cache (
                    chat_id      TEXT NOT NULL,
                    message_id   BIGINT NOT NULL,
                    file_size    BIGINT,
                    moov_offset  BIGINT,
                    moov_size    BIGINT,
                    located      INTEGER DEFAULT 0,
                    duration_ms  BIGINT,
                    width        INTEGER,
                    height       INTEGER,
                    codec        TEXT,
                    tracks       INTEGER,
                    updated_at   {real} NOT NULL,
                    PRIMARY KEY(chat_id, message_id)
                );
                CREATE INDEX IF NOT EXISTS idx_moov_updated ON moov_cache(updated_at);
                """
            )

    # ----------------------------------------------------------------- settings
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return (dict(row)["value"] if row else default)

    def set_setting(self, key: str, value: str | None) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    # --------------------------------------------------------------- moov_cache
    def get_moov_cache(self, chat_id: str, message_id: int) -> dict[str, Any] | None:
        import time

        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM moov_cache WHERE chat_id=? AND message_id=?",
                (str(chat_id), int(message_id)),
            ).fetchone()
            if not row:
                return None
            conn.execute(
                "UPDATE moov_cache SET updated_at=? WHERE chat_id=? AND message_id=?",
                (time.time(), str(chat_id), int(message_id)),
            )
            return dict(row)

    def set_moov_cache(self, chat_id: str, message_id: int, **kw: Any) -> None:
        import time

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
                    str(chat_id), int(message_id), kw.get("file_size"),
                    kw.get("moov_offset"), kw.get("moov_size"),
                    (int(kw["located"]) if kw.get("located") is not None else None),
                    kw.get("duration_ms"), kw.get("width"), kw.get("height"),
                    kw.get("codec"), kw.get("tracks"), time.time(),
                ),
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
            return int(dict(row)["id"])

    def list_courses(self) -> list[Course]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM courses ORDER BY sort_order ASC, lower(title) ASC"
            ).fetchall()
        return [self._row_to_course(r) for r in rows]

    def get_course(self, course_id: int) -> Course | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM courses WHERE id=?", (course_id,)).fetchone()
        return self._row_to_course(row) if row else None

    def rename_course(self, course_id: int, title: str) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE courses SET title=? WHERE id=?", (title.strip() or "Curso", course_id))

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
                "SELECT * FROM subjects WHERE course_id=? ORDER BY sort_order ASC, lower(title) ASC",
                (course_id,),
            ).fetchall()
        return [self._row_to_subject(r) for r in rows]

    def get_subject(self, subject_id: int) -> Subject | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM subjects WHERE id=?", (subject_id,)).fetchone()
        return self._row_to_subject(row) if row else None

    def add_subject(self, course_id: int, title: str, summary_text: str | None = None,
                    telegram_topic_id: str | None = None, manual: int = 1) -> int:
        with self.connect() as conn:
            order = dict(conn.execute(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM subjects WHERE course_id=?",
                (course_id,),
            ).fetchone())["n"]
            cur = conn.execute(
                "INSERT INTO subjects(course_id, title, telegram_topic_id, summary_text, "
                "sort_order, manual) VALUES(?,?,?,?,?,?)",
                (course_id, title.strip() or "Matéria", telegram_topic_id, summary_text, order, manual),
            )
            return int(cur.lastrowid)

    def rename_subject(self, subject_id: int, title: str) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE subjects SET title=? WHERE id=?", (title.strip() or "Matéria", subject_id))

    def update_subject_summary(self, subject_id: int, summary_text: str | None) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE subjects SET summary_text=? WHERE id=?", (summary_text, subject_id))

    def delete_subject(self, subject_id: int) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE videos SET subject_id=NULL WHERE subject_id=?", (subject_id,))
            conn.execute("DELETE FROM subjects WHERE id=?", (subject_id,))

    def reorder_subjects(self, ordered_ids: list[int]) -> None:
        with self.connect() as conn:
            for index, subject_id in enumerate(ordered_ids):
                conn.execute("UPDATE subjects SET sort_order=? WHERE id=?", (index, subject_id))

    def find_or_create_subject(self, course_id: int, title: str,
                               telegram_topic_id: str | None = None,
                               summary_text: str | None = None, manual: int = 0) -> int:
        with self.connect() as conn:
            if telegram_topic_id:
                row = conn.execute(
                    "SELECT id FROM subjects WHERE course_id=? AND telegram_topic_id=?",
                    (course_id, str(telegram_topic_id)),
                ).fetchone()
                if row:
                    return int(dict(row)["id"])
            row = conn.execute(
                "SELECT id FROM subjects WHERE course_id=? AND lower(title)=lower(?)",
                (course_id, title),
            ).fetchone()
            if row:
                return int(dict(row)["id"])
        return self.add_subject(course_id, title, summary_text, telegram_topic_id, manual)

    # ------------------------------------------------------------------- videos
    def replace_videos(self, course_id: int, videos: list[dict[str, Any]]) -> None:
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
                            width=?, height=?, date=?, hashtags_json=?, caption=?,
                            file_id=COALESCE(NULLIF(?, ''), file_id),
                            file_unique_id=COALESCE(NULLIF(?, ''), file_unique_id),
                            sort_order=COALESCE(?, sort_order)
                        WHERE chat_id=? AND message_id=?
                        """,
                        (
                            course_id, video.get("file_name") or "video.mp4",
                            video.get("mime_type"), video.get("size"), video.get("duration"),
                            video.get("width"), video.get("height"), video.get("date"),
                            json.dumps(video.get("hashtags") or [], ensure_ascii=False),
                            video.get("caption"), video.get("file_id"), video.get("file_unique_id"),
                            video.get("sort_order"),
                            str(video["chat_id"]), int(video["message_id"]),
                        ),
                    )
                    if video.get("subject_id") is not None:
                        conn.execute(
                            "UPDATE videos SET subject_id=? "
                            "WHERE chat_id=? AND message_id=? AND COALESCE(manual,0)=0",
                            (video.get("subject_id"), str(video["chat_id"]), int(video["message_id"])),
                        )
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
                            caption, file_id, file_unique_id, module, lesson, type, sort_order
                        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            course_id, video.get("subject_id"), str(video["chat_id"]),
                            int(video["message_id"]),
                            video.get("title") or video.get("file_name") or "Aula",
                            video.get("file_name") or "video.mp4", video.get("mime_type"),
                            video.get("size"), video.get("duration"), video.get("width"),
                            video.get("height"), video.get("date"),
                            json.dumps(video.get("hashtags") or [], ensure_ascii=False),
                            video.get("caption"), video.get("file_id"), video.get("file_unique_id"),
                            video.get("module"), video.get("lesson"), video.get("type"),
                            video.get("sort_order"),
                        ),
                    )
            conn.execute("UPDATE courses SET last_sync=? WHERE id=?", (self.now(), course_id))

    def list_videos(self, course_id: int) -> list[Video]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM videos WHERE course_id=? ORDER BY sort_order ASC, message_id ASC",
                (course_id,),
            ).fetchall()
        return [self._row_to_video(r) for r in rows]

    def list_videos_for_subject(self, subject_id: int) -> list[Video]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM videos WHERE subject_id=? ORDER BY sort_order ASC, message_id ASC",
                (subject_id,),
            ).fetchall()
        return [self._row_to_video(r) for r in rows]

    def get_video(self, video_id: int) -> Video | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone()
        return self._row_to_video(row) if row else None

    def mark_watched(self, video_id: int) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE videos SET watched_at=?, progress=1.0 WHERE id=?", (self.now(), video_id))

    def mark_unwatched(self, video_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE videos SET watched_at=NULL, position_ms=0, progress=0 WHERE id=?", (video_id,)
            )

    def rename_video(self, video_id: int, title: str) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE videos SET title=?, manual=1 WHERE id=?", (title.strip() or "Aula", video_id))

    def set_video_subject(self, video_id: int, subject_id: int | None) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE videos SET subject_id=?, manual=1 WHERE id=?", (subject_id, video_id))

    def set_video_dimensions(self, chat_id: str, message_id: int, width: int | None = None,
                             height: int | None = None, duration: int | None = None) -> None:
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

    def set_video_meta(self, video_id: int, module: str | None = None,
                       lesson: str | None = None, type_: str | None = None) -> None:
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
            new_value = 0 if (row and dict(row)["favorite"]) else 1
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
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM videos WHERE progress > 0.02 AND progress < 0.95 "
                "AND last_watched_at IS NOT NULL ORDER BY last_watched_at DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [self._row_to_video(r) for r in rows]

    def course_progress(self, course_id: int) -> tuple[int, int]:
        with self.connect() as conn:
            total = dict(conn.execute(
                "SELECT COUNT(*) AS n FROM videos WHERE course_id=?", (course_id,)
            ).fetchone())["n"]
            done = dict(conn.execute(
                "SELECT COUNT(*) AS n FROM videos WHERE course_id=? AND "
                "(watched_at IS NOT NULL OR progress >= 0.92)",
                (course_id,),
            ).fetchone())["n"]
        return int(done), int(total)

    def reorder_videos(self, ordered_ids: list[int]) -> None:
        with self.connect() as conn:
            for index, video_id in enumerate(ordered_ids):
                conn.execute("UPDATE videos SET sort_order=? WHERE id=?", (index, video_id))

    def delete_video(self, video_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM videos WHERE id=?", (video_id,))

    # =================================================== produtividade / estudo
    def add_pomodoro_session(self, seconds: int, kind: str = "foco",
                             course_id: int | None = None, subject_id: int | None = None) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO pomodoro_sessions(started_at, ended_at, seconds, kind, "
                "course_id, subject_id) VALUES(?,?,?,?,?,?)",
                (self.now(), self.now(), int(seconds), kind, course_id, subject_id),
            )
            return int(cur.lastrowid)

    def count_pomodoros_today(self) -> int:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM pomodoro_sessions "
                "WHERE kind='foco' AND started_at LIKE ?",
                (today + "%",),
            ).fetchone()
            return int(dict(row)["n"] or 0)

    def log_study_time(self, seconds: int, course_id: int | None = None,
                       subject_id: int | None = None, day: str | None = None) -> None:
        if seconds <= 0:
            return
        day = day or self.today()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id, seconds FROM study_log WHERE day=? AND "
                "COALESCE(course_id,-1)=COALESCE(?, -1) AND COALESCE(subject_id,-1)=COALESCE(?, -1)",
                (day, course_id, subject_id),
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE study_log SET seconds=seconds+? WHERE id=?",
                    (int(seconds), dict(row)["id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO study_log(day, seconds, course_id, subject_id) VALUES(?,?,?,?)",
                    (day, int(seconds), course_id, subject_id),
                )

    def study_seconds_by_day(self, days: int = 7) -> list[tuple[str, int]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT day, SUM(seconds) AS s FROM study_log GROUP BY day").fetchall()
        by_day = {dict(r)["day"]: int(dict(r)["s"] or 0) for r in rows}
        out: list[tuple[str, int]] = []
        today = datetime.now()
        for i in range(days - 1, -1, -1):
            d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            out.append((d, by_day.get(d, 0)))
        return out

    def total_study_seconds(self) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT COALESCE(SUM(seconds),0) AS s FROM study_log").fetchone()
            return int(dict(row)["s"] or 0)

    def study_streak_days(self) -> int:
        with self.connect() as conn:
            rows = conn.execute("SELECT DISTINCT day FROM study_log WHERE seconds>0").fetchall()
        days = {dict(r)["day"] for r in rows}
        streak = 0
        cursor = datetime.now()
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
                "GROUP BY c.id, c.title ORDER BY watched DESC"
            ).fetchall()
        return [(dict(r)["title"], int(dict(r)["watched"] or 0)) for r in rows]

    # ---- dashboard ----------------------------------------------------------
    def today_study_seconds(self) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(seconds),0) AS s FROM study_log WHERE day=?", (self.today(),)
            ).fetchone()
            return int(dict(row)["s"] or 0)

    def week_study_seconds(self) -> int:
        today = datetime.now()
        start = today - timedelta(days=today.weekday())
        start_day = start.strftime("%Y-%m-%d")
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(seconds),0) AS s FROM study_log WHERE day>=?", (start_day,)
            ).fetchone()
            return int(dict(row)["s"] or 0)

    def task_counts(self) -> dict[str, int]:
        with self.connect() as conn:
            rows = conn.execute("SELECT done, COUNT(*) AS n FROM tasks GROUP BY done").fetchall()
        out = {"open": 0, "done": 0, "total": 0}
        for row in rows:
            d = dict(row)
            key = "done" if int(d["done"] or 0) else "open"
            out[key] = int(d["n"] or 0)
        out["total"] = out["open"] + out["done"]
        return out

    def video_totals(self) -> tuple[int, int]:
        with self.connect() as conn:
            total = dict(conn.execute("SELECT COUNT(*) AS n FROM videos").fetchone())["n"]
            done = dict(conn.execute(
                "SELECT COUNT(*) AS n FROM videos WHERE watched_at IS NOT NULL OR progress>=0.92"
            ).fetchone())["n"]
        return int(done or 0), int(total or 0)

    def course_completion_stats(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT c.id, c.title,
                       COUNT(v.id) AS total,
                       SUM(CASE WHEN v.watched_at IS NOT NULL OR v.progress>=0.92 THEN 1 ELSE 0 END) AS done
                FROM courses c
                LEFT JOIN videos v ON v.course_id=c.id
                GROUP BY c.id, c.title
                ORDER BY done DESC, total DESC, lower(c.title) ASC
                """
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            d = dict(row)
            total = int(d["total"] or 0)
            done = int(d["done"] or 0)
            out.append({"id": int(d["id"]), "title": d["title"], "total": total, "done": done,
                        "pct": int(done / total * 100) if total else 0})
        return out

    def subject_completion_stats(self, course_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT COALESCE(s.title, 'Sem matéria') AS title,
                       COUNT(v.id) AS total,
                       SUM(CASE WHEN v.watched_at IS NOT NULL OR v.progress>=0.92 THEN 1 ELSE 0 END) AS done,
                       COALESCE(MIN(s.sort_order), 999999) AS ord
                FROM videos v
                LEFT JOIN subjects s ON s.id=v.subject_id
                WHERE v.course_id=?
                GROUP BY COALESCE(s.title, 'Sem matéria')
                ORDER BY ord ASC, lower(title) ASC
                """,
                (course_id,),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            d = dict(row)
            total = int(d["total"] or 0)
            done = int(d["done"] or 0)
            out.append({"title": d["title"], "total": total, "done": done,
                        "pct": int(done / total * 100) if total else 0})
        return out

    def recent_completed_videos(self, limit: int = 8) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT v.id, v.title, v.watched_at, c.title AS course_title
                FROM videos v
                LEFT JOIN courses c ON c.id=v.course_id
                WHERE v.watched_at IS NOT NULL
                ORDER BY v.watched_at DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [dict(r) for r in rows]

    # ---- tasks --------------------------------------------------------------
    def list_tasks(self, include_done: bool = True) -> list[dict[str, Any]]:
        with self.connect() as conn:
            sql = "SELECT * FROM tasks"
            if not include_done:
                sql += " WHERE done=0"
            sql += " ORDER BY done ASC, sort_order ASC, id ASC"
            rows = conn.execute(sql).fetchall()
        return [dict(r) for r in rows]

    def add_task(self, text: str, priority: int = 1, due_date: str | None = None,
                 course_id: int | None = None) -> int:
        with self.connect() as conn:
            order = dict(conn.execute(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM tasks"
            ).fetchone())["n"]
            cur = conn.execute(
                "INSERT INTO tasks(text, priority, due_date, course_id, sort_order, created_at) "
                "VALUES(?,?,?,?,?,?)",
                (text.strip(), int(priority), due_date, course_id, order, self.now()),
            )
            return int(cur.lastrowid)

    def update_task(self, task_id: int, text: str | None = None, priority: int | None = None,
                    due_date: str | None = None) -> None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not row:
                return
            current = dict(row)
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
            new_value = 0 if (row and dict(row)["done"]) else 1
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
    def _row_to_course(self, row: Any) -> Course:
        d = dict(row)
        return Course(
            id=int(d["id"]), chat_id=str(d["chat_id"]),
            title=d.get("title") or str(d.get("chat_id")),
            username=d.get("username"), chat_type=d.get("chat_type"),
            is_forum=int(d.get("is_forum") or 0), added_at=d.get("added_at"),
            last_sync=d.get("last_sync"), color=d.get("color"),
            sort_order=int(d.get("sort_order") or 0),
        )

    def _row_to_subject(self, row: Any) -> Subject:
        d = dict(row)
        return Subject(
            id=int(d["id"]), course_id=int(d["course_id"]),
            title=d.get("title") or "Matéria", telegram_topic_id=d.get("telegram_topic_id"),
            summary_text=d.get("summary_text"), sort_order=int(d.get("sort_order") or 0),
            manual=int(d.get("manual") or 0),
        )

    def _row_to_video(self, row: Any) -> Video:
        d = dict(row)
        try:
            tags = json.loads(d.get("hashtags_json") or "[]")
        except Exception:  # noqa: BLE001
            tags = []
        return Video(
            id=int(d["id"]), course_id=int(d["course_id"]),
            subject_id=(int(d["subject_id"]) if d.get("subject_id") is not None else None),
            chat_id=str(d["chat_id"]), message_id=int(d["message_id"]),
            title=d.get("title") or "Aula", file_name=d.get("file_name") or "video.mp4",
            mime_type=d.get("mime_type"), size=d.get("size"), duration=d.get("duration"),
            date=d.get("date"), width=d.get("width"), height=d.get("height"),
            hashtags=tags, caption=d.get("caption"), file_id=d.get("file_id"),
            file_unique_id=d.get("file_unique_id"), watched_at=d.get("watched_at"),
            module=d.get("module"), lesson=d.get("lesson"), type=d.get("type"),
            position_ms=int(d.get("position_ms") or 0), progress=float(d.get("progress") or 0.0),
            favorite=int(d.get("favorite") or 0), note=d.get("note"),
            sort_order=int(d.get("sort_order") or 0), manual=int(d.get("manual") or 0),
        )

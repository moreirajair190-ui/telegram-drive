from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator

from .paths import DB_PATH, ensure_dirs


@dataclass
class Course:
    id: int
    chat_id: str
    title: str
    username: str | None = None
    chat_type: str | None = None
    summary_text: str | None = None
    topics_json: str | None = None
    added_at: str | None = None
    last_sync: str | None = None
    color: str | None = None
    sort_order: int = 0

    def topics(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.topics_json or "[]")
            return data if isinstance(data, list) else []
        except Exception:
            return []


@dataclass
class Video:
    id: int
    course_id: int
    chat_id: str
    message_id: int
    title: str
    file_name: str
    mime_type: str | None
    size: int | None
    duration: int | None
    date: str | None
    hashtags: list[str] = field(default_factory=list)
    caption: str | None = None
    watched_at: str | None = None
    topic_id: str | None = None
    topic_title: str | None = None
    position_ms: int = 0
    progress: float = 0.0
    favorite: int = 0
    note: str | None = None
    sort_order: int = 0
    manual: int = 0


class Database:
    def __init__(self, path=DB_PATH):
        ensure_dirs()
        self.path = path
        self.init()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
                CREATE TABLE IF NOT EXISTS courses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    username TEXT,
                    chat_type TEXT,
                    summary_text TEXT,
                    topics_json TEXT,
                    added_at TEXT NOT NULL,
                    last_sync TEXT,
                    color TEXT,
                    sort_order INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS videos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    course_id INTEGER NOT NULL,
                    chat_id TEXT NOT NULL,
                    message_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    mime_type TEXT,
                    size INTEGER,
                    duration INTEGER,
                    width INTEGER,
                    height INTEGER,
                    date TEXT,
                    hashtags_json TEXT,
                    caption TEXT,
                    watched_at TEXT,
                    topic_id TEXT,
                    topic_title TEXT,
                    position_ms INTEGER DEFAULT 0,
                    progress REAL DEFAULT 0,
                    favorite INTEGER DEFAULT 0,
                    note TEXT,
                    sort_order INTEGER DEFAULT 0,
                    manual INTEGER DEFAULT 0,
                    UNIQUE(chat_id, message_id),
                    FOREIGN KEY(course_id) REFERENCES courses(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_videos_course ON videos(course_id);
                CREATE INDEX IF NOT EXISTS idx_videos_msg ON videos(chat_id, message_id);
                CREATE INDEX IF NOT EXISTS idx_videos_topic ON videos(course_id, topic_id);
                """
            )
            # Migrações suaves para bancos antigos (v4).
            self._ensure_column(conn, "courses", "topics_json", "TEXT")
            self._ensure_column(conn, "courses", "color", "TEXT")
            self._ensure_column(conn, "courses", "sort_order", "INTEGER DEFAULT 0")
            self._ensure_column(conn, "videos", "topic_id", "TEXT")
            self._ensure_column(conn, "videos", "topic_title", "TEXT")
            self._ensure_column(conn, "videos", "position_ms", "INTEGER DEFAULT 0")
            self._ensure_column(conn, "videos", "progress", "REAL DEFAULT 0")
            self._ensure_column(conn, "videos", "favorite", "INTEGER DEFAULT 0")
            self._ensure_column(conn, "videos", "note", "TEXT")
            self._ensure_column(conn, "videos", "sort_order", "INTEGER DEFAULT 0")
            self._ensure_column(conn, "videos", "manual", "INTEGER DEFAULT 0")

    def _ensure_column(self, conn: sqlite3.Connection, table: str, column: str, column_type: str) -> None:
        cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")

    def now(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

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

    # ------------------------------------------------------------------ courses
    def upsert_course(self, data: dict[str, Any]) -> int:
        with self.connect() as conn:
            now = self.now()
            conn.execute(
                """
                INSERT INTO courses(chat_id, title, username, chat_type, added_at, last_sync)
                VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    username=excluded.username,
                    chat_type=excluded.chat_type
                """,
                (
                    str(data["chat_id"]),
                    data.get("title") or str(data["chat_id"]),
                    data.get("username"),
                    data.get("chat_type"),
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
            conn.execute("UPDATE courses SET title=? WHERE id=?", (title.strip() or "Curso", course_id))

    def set_course_color(self, course_id: int, color: str | None) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE courses SET color=? WHERE id=?", (color, course_id))

    def delete_course(self, course_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM videos WHERE course_id=?", (course_id,))
            conn.execute("DELETE FROM courses WHERE id=?", (course_id,))

    def reorder_courses(self, ordered_ids: list[int]) -> None:
        with self.connect() as conn:
            for index, course_id in enumerate(ordered_ids):
                conn.execute("UPDATE courses SET sort_order=? WHERE id=?", (index, course_id))

    def update_course_summary(
        self, course_id: int, summary_text: str | None, topics: list[dict[str, Any]] | None = None
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE courses SET summary_text=?, topics_json=?, last_sync=? WHERE id=?",
                (summary_text, json.dumps(topics or [], ensure_ascii=False), self.now(), course_id),
            )

    def set_course_topics(self, course_id: int, topics: list[dict[str, Any]]) -> None:
        """Salva apenas os tópicos (usado pela edição manual de sumários)."""
        with self.connect() as conn:
            from .summary_parser import compact_summary_text

            conn.execute(
                "UPDATE courses SET topics_json=?, summary_text=? WHERE id=?",
                (
                    json.dumps(topics or [], ensure_ascii=False),
                    compact_summary_text(topics),
                    course_id,
                ),
            )

    # ------------------------------------------------------------------- videos
    def replace_videos(self, course_id: int, videos: list[dict[str, Any]]) -> None:
        """Insere/atualiza vídeos da sincronização, preservando edições do usuário.

        Campos que o usuário pode ter editado (title, topic, favorito, progresso)
        NÃO são sobrescritos se o vídeo já existir; só dados técnicos são atualizados.
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
                else:
                    conn.execute(
                        """
                        INSERT INTO videos(
                            course_id, chat_id, message_id, title, file_name, mime_type,
                            size, duration, width, height, date, hashtags_json, caption,
                            topic_id, topic_title
                        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            course_id,
                            str(video["chat_id"]),
                            int(video["message_id"]),
                            video.get("title") or video.get("file_name") or "Video",
                            video.get("file_name") or "video.mp4",
                            video.get("mime_type"),
                            video.get("size"),
                            video.get("duration"),
                            video.get("width"),
                            video.get("height"),
                            video.get("date"),
                            json.dumps(video.get("hashtags") or [], ensure_ascii=False),
                            video.get("caption"),
                            video.get("topic_id"),
                            video.get("topic_title"),
                        ),
                    )
            conn.execute("UPDATE courses SET last_sync=? WHERE id=?", (self.now(), course_id))

    def list_videos(self, course_id: int) -> list[Video]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM videos WHERE course_id=? "
                "ORDER BY COALESCE(topic_title, ''), sort_order ASC, message_id ASC",
                (course_id,),
            ).fetchall()
        return [self._row_to_video(row) for row in rows]

    def get_video(self, video_id: int) -> Video | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone()
        return self._row_to_video(row) if row else None

    def mark_watched(self, video_id: int) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE videos SET watched_at=? WHERE id=?", (self.now(), video_id))

    def mark_unwatched(self, video_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE videos SET watched_at=NULL, position_ms=0, progress=0 WHERE id=?",
                (video_id,),
            )

    def rename_video(self, video_id: int, title: str) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE videos SET title=? WHERE id=?", (title.strip() or "Aula", video_id))

    def set_video_topic(self, video_id: int, topic_id: str | None, topic_title: str | None) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE videos SET topic_id=?, topic_title=? WHERE id=?",
                (topic_id, topic_title, video_id),
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
        with self.connect() as conn:
            watched = "watched_at=COALESCE(watched_at, ?)" if progress >= 0.92 else "watched_at=watched_at"
            conn.execute(
                f"UPDATE videos SET position_ms=?, progress=?, {watched} WHERE id=?",
                (int(position_ms), progress, self.now(), video_id)
                if progress >= 0.92
                else (int(position_ms), progress, video_id),
            )

    def reorder_videos(self, ordered_ids: list[int]) -> None:
        with self.connect() as conn:
            for index, video_id in enumerate(ordered_ids):
                conn.execute("UPDATE videos SET sort_order=? WHERE id=?", (index, video_id))

    def delete_video(self, video_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM videos WHERE id=?", (video_id,))

    # ------------------------------------------------------------ row -> objeto
    def _row_to_course(self, row: sqlite3.Row) -> Course:
        data = dict(row)
        return Course(
            id=int(data["id"]),
            chat_id=str(data["chat_id"]),
            title=data.get("title") or str(data.get("chat_id")),
            username=data.get("username"),
            chat_type=data.get("chat_type"),
            summary_text=data.get("summary_text"),
            topics_json=data.get("topics_json"),
            added_at=data.get("added_at"),
            last_sync=data.get("last_sync"),
            color=data.get("color"),
            sort_order=int(data.get("sort_order") or 0),
        )

    def _row_to_video(self, row: sqlite3.Row) -> Video:
        data = dict(row)
        try:
            tags = json.loads(data.pop("hashtags_json") or "[]")
        except Exception:
            tags = []
        data["hashtags"] = tags
        data.pop("width", None)
        data.pop("height", None)
        return Video(
            id=int(data["id"]),
            course_id=int(data["course_id"]),
            chat_id=str(data["chat_id"]),
            message_id=int(data["message_id"]),
            title=data.get("title") or "Aula",
            file_name=data.get("file_name") or "video.mp4",
            mime_type=data.get("mime_type"),
            size=data.get("size"),
            duration=data.get("duration"),
            date=data.get("date"),
            hashtags=tags,
            caption=data.get("caption"),
            watched_at=data.get("watched_at"),
            topic_id=data.get("topic_id"),
            topic_title=data.get("topic_title"),
            position_ms=int(data.get("position_ms") or 0),
            progress=float(data.get("progress") or 0.0),
            favorite=int(data.get("favorite") or 0),
            note=data.get("note"),
            sort_order=int(data.get("sort_order") or 0),
            manual=int(data.get("manual") or 0),
        )

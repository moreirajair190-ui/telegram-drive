from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from typing import Any
from urllib.parse import quote

from aiohttp import web

from .paths import CACHE_DIR, SESSION_DIR, ensure_dirs
from .stream_cache import BLOCK_SIZE, StreamSession
from .summary_parser import (
    compact_summary_text,
    derive_menu_title,
    looks_like_menu,
    menu_score,
    split_summary_candidates,
    tag_prefix,
)
from .utils import (
    ensure_extension,
    extract_hashtags,
    first_non_empty,
    infer_hashtags,
    safe_filename,
)

log = logging.getLogger(__name__)

# Tamanho do bloco entregue por requisição quando o player não pede um range
# específico. Mantido pequeno para iniciar a reprodução rapidamente.
INITIAL_RANGE_CHUNK = 4 * BLOCK_SIZE


class TelegramService:
    """Camada assíncrona: Pyrogram + servidor HTTP local, no mesmo event loop."""

    def __init__(self) -> None:
        ensure_dirs()
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(
            target=self._run_loop, name="TGClassPlayerAsync", daemon=True
        )
        self.thread.start()
        self.client = None
        self.api_id: int | None = None
        self.api_hash: str | None = None
        self.phone_code_hash: str | None = None
        self.phone_number: str | None = None
        self.runner: web.AppRunner | None = None
        self.site: web.TCPSite | None = None
        self.port: int | None = None
        self.sessions: dict[str, StreamSession] = {}
        self.stream_cache_dir = CACHE_DIR / "streams"
        self.stream_cache_dir.mkdir(parents=True, exist_ok=True)
        self._cleanup_old_cache()

    def _cleanup_old_cache(self) -> None:
        """Remove restos de cache de execuções anteriores (vídeos não ficam guardados)."""
        try:
            for item in self.stream_cache_dir.glob("*"):
                try:
                    item.unlink()
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def call(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    async def close(self) -> None:
        for token in list(self.sessions):
            await self.release_stream(token)
        if self.runner:
            await self.runner.cleanup()
            self.runner = None
            self.site = None
            self.port = None
        if self.client:
            try:
                await self.client.disconnect()
            except Exception:  # noqa: BLE001
                log.exception("Falha ao desconectar Pyrogram")
            self.client = None

    def stop(self) -> None:
        try:
            future = self.call(self.close())
            future.result(timeout=10)
        except Exception:  # noqa: BLE001
            log.exception("Erro ao fechar serviço")
        self.loop.call_soon_threadsafe(self.loop.stop)

    # ------------------------------------------------------------------- login
    async def ensure_connected(self, api_id: str | int, api_hash: str) -> dict[str, Any]:
        from pyrogram import Client
        from pyrogram.errors import Unauthorized

        self.api_id = int(str(api_id).strip())
        self.api_hash = str(api_hash).strip()

        if self.client:
            try:
                me = await self.client.get_me()
                if me:
                    return {"authorized": True, "me": self._user_to_dict(me)}
            except Exception:  # noqa: BLE001
                try:
                    await self.client.disconnect()
                except Exception:  # noqa: BLE001
                    pass
                self.client = None

        self.client = Client(
            "tgclassplayer",
            api_id=self.api_id,
            api_hash=self.api_hash,
            workdir=str(SESSION_DIR),
            no_updates=True,
            sleep_threshold=60,
        )
        await self.client.connect()

        try:
            me = await self.client.get_me()
            return {"authorized": True, "me": self._user_to_dict(me)}
        except Unauthorized:
            return {"authorized": False}

    async def send_code(self, phone_number: str) -> dict[str, Any]:
        if not self.client:
            raise RuntimeError("Cliente Telegram não conectado.")
        phone_number = phone_number.strip().replace(" ", "")
        sent_code = await self.client.send_code(phone_number)
        self.phone_number = phone_number
        self.phone_code_hash = sent_code.phone_code_hash
        return {"sent": True, "phone_code_hash": self.phone_code_hash}

    async def sign_in(self, code: str) -> dict[str, Any]:
        from pyrogram.errors import SessionPasswordNeeded

        if not self.client or not self.phone_number or not self.phone_code_hash:
            raise RuntimeError("Código não solicitado ainda.")
        code = code.strip().replace(" ", "").replace("-", "")
        try:
            await self.client.sign_in(self.phone_number, self.phone_code_hash, code)
        except SessionPasswordNeeded:
            return {"authorized": False, "needs_password": True}
        me = await self.client.get_me()
        return {"authorized": True, "me": self._user_to_dict(me)}

    async def check_password(self, password: str) -> dict[str, Any]:
        if not self.client:
            raise RuntimeError("Cliente Telegram não conectado.")
        await self.client.check_password(password)
        me = await self.client.get_me()
        return {"authorized": True, "me": self._user_to_dict(me)}

    async def logout(self) -> dict[str, Any]:
        if self.client:
            try:
                await self.client.log_out()
            except Exception:  # noqa: BLE001
                log.exception("Erro ao deslogar")
            self.client = None
        return {"ok": True}

    async def get_me(self) -> dict[str, Any] | None:
        if not self.client:
            return None
        me = await self.client.get_me()
        return self._user_to_dict(me) if me else None

    # ----------------------------------------------------------------- cursos
    async def list_dialog_courses(self, limit: int = 500) -> list[dict[str, Any]]:
        if not self.client:
            raise RuntimeError("Entre no Telegram primeiro.")
        from pyrogram.enums import ChatType

        allowed = {ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL}
        courses: list[dict[str, Any]] = []
        async for dialog in self.client.get_dialogs(limit=limit):
            chat = dialog.chat
            if chat.type not in allowed:
                continue
            title = first_non_empty(
                [getattr(chat, "title", None), getattr(chat, "first_name", None)], str(chat.id)
            )
            courses.append(
                {
                    "chat_id": str(chat.id),
                    "title": title,
                    "username": getattr(chat, "username", None),
                    "chat_type": str(chat.type).split(".")[-1],
                }
            )
        courses.sort(key=lambda item: item["title"].lower())
        return courses

    async def sync_course(
        self, chat_id: str | int, limit: int = 99999, progress_cb=None
    ) -> dict[str, Any]:
        """Sincroniza vídeos e múltiplos menus/sumários por tópico/matéria."""
        if not self.client:
            raise RuntimeError("Entre no Telegram primeiro.")
        chat_id = int(chat_id) if str(chat_id).lstrip("-").isdigit() else chat_id
        chat = await self.client.get_chat(chat_id)

        videos: list[dict[str, Any]] = []
        candidates: list[dict[str, Any]] = []
        topic_titles: dict[str, str] = {}
        scanned = 0
        history_limit = max(0, int(limit or 0))

        pinned = getattr(chat, "pinned_message", None)
        if pinned:
            text = self._message_text(pinned)
            if looks_like_menu(text):
                topic_id = self._message_topic_id(pinned)
                title = self._message_topic_title(pinned, chat, topic_id)
                topic_titles[topic_id] = title
                self._add_summary_candidate(
                    candidates, topic_id, title, text, getattr(pinned, "id", 0)
                )

        async for message in self.client.get_chat_history(chat_id, limit=history_limit):
            scanned += 1
            if progress_cb and scanned % 200 == 0:
                try:
                    progress_cb(scanned, len(videos))
                except Exception:  # noqa: BLE001
                    pass
            topic_id = self._message_topic_id(message)
            topic_title = self._message_topic_title(message, chat, topic_id)
            if topic_title:
                topic_titles[topic_id] = topic_title

            text = self._message_text(message)
            if looks_like_menu(text):
                self._add_summary_candidate(
                    candidates, topic_id, topic_titles.get(topic_id, topic_title), text, message.id
                )

            video = self._message_to_video(
                message, chat_id, topic_id, topic_titles.get(topic_id, topic_title)
            )
            if video:
                videos.append(video)

        # Busca complementar por hashtags (menus antigos/fixados).
        try:
            async for msg in self.client.search_messages(chat_id, query="#", limit=1000):
                text = self._message_text(msg)
                if not looks_like_menu(text):
                    continue
                topic_id = self._message_topic_id(msg)
                topic_title = self._message_topic_title(msg, chat, topic_id)
                if topic_title:
                    topic_titles[topic_id] = topic_title
                self._add_summary_candidate(
                    candidates, topic_id, topic_titles.get(topic_id, topic_title), text, msg.id
                )
        except Exception:  # noqa: BLE001
            log.exception("Não foi possível pesquisar mensagens com hashtags")

        topics = self._build_topics(candidates, topic_titles)
        videos.reverse()
        return {
            "chat": {
                "chat_id": str(chat.id),
                "title": getattr(chat, "title", None) or str(chat.id),
                "username": getattr(chat, "username", None),
                "chat_type": str(getattr(chat, "type", "")).split(".")[-1],
            },
            "summary_text": compact_summary_text(topics),
            "topics": topics,
            "videos": videos,
            "scanned": scanned,
        }

    def _message_text(self, message) -> str:
        return (getattr(message, "text", None) or getattr(message, "caption", None) or "").strip()

    def _add_summary_candidate(
        self,
        candidates: list[dict[str, Any]],
        topic_id: str,
        topic_title: str | None,
        text: str | None,
        message_id: int,
    ) -> None:
        if not text:
            return
        text = text.strip()
        if not text:
            return
        parts = split_summary_candidates(text)
        if not parts:
            parts = [(derive_menu_title(text, topic_title or "Sumário"), text)]
        for part_title, part_text in parts:
            tags = extract_hashtags(part_text)
            candidates.append(
                {
                    "topic_id": topic_id or "general",
                    "topic_title": part_title or topic_title or "Geral",
                    "summary_text": part_text,
                    "message_id": int(message_id or 0),
                    "score": menu_score(part_text),
                    "prefix": tag_prefix(part_text),
                    "tags": tags,
                }
            )

    def _build_topics(
        self, candidates: list[dict[str, Any]], topic_titles: dict[str, str]
    ) -> list[dict[str, Any]]:
        unique: dict[str, dict[str, Any]] = {}
        for c in candidates:
            text_key = " ".join((c.get("summary_text") or "").split())
            prefix = str(c.get("prefix") or "").upper()
            key = f"{prefix}:{text_key[:2200]}"
            prev = unique.get(key)
            if not prev or c.get("score", 0) > prev.get("score", 0):
                unique[key] = c
        clean = sorted(
            unique.values(),
            key=lambda item: (item.get("topic_title") or "", -int(item.get("score") or 0)),
        )
        if not clean:
            return []

        has_real_topic = any((c.get("topic_id") or "general") != "general" for c in clean)
        grouped: dict[str, dict[str, Any]] = {}
        for c in clean:
            topic_id = c.get("topic_id") or "general"
            prefix = c.get("prefix") or derive_menu_title(
                c.get("summary_text"), c.get("topic_title") or "Sumário"
            )
            if has_real_topic and topic_id != "general":
                key = f"topic:{topic_id}:prefix:{str(prefix).upper()}"
            else:
                key = f"menu:{str(prefix).upper()}"
            prev = grouped.get(key)
            if not prev or int(c.get("score") or 0) > int(prev.get("score") or 0):
                grouped[key] = c

        topics: list[dict[str, Any]] = []
        for key, c in grouped.items():
            raw_topic_id = c.get("topic_id") or "general"
            title = c.get("topic_title")
            if not title or title == "Geral" or str(title).startswith("Tópico "):
                title = derive_menu_title(c.get("summary_text"), title or "Sumário")
            tags = c.get("tags") or extract_hashtags(c.get("summary_text"))
            topics.append(
                {
                    "id": key if key.startswith("menu:") else str(raw_topic_id),
                    "telegram_topic_id": str(raw_topic_id),
                    "title": title,
                    "summary_text": c.get("summary_text") or "",
                    "tags": tags,
                    "tag_count": len(tags),
                    "message_id": c.get("message_id"),
                    "score": c.get("score"),
                    "prefix": c.get("prefix"),
                }
            )
        topics.sort(key=lambda t: (self._topic_sort_key(t), str(t.get("title") or "").lower()))
        return topics

    def _topic_sort_key(self, topic: dict[str, Any]) -> tuple[int, str]:
        title = str(topic.get("title") or "").upper()
        prefix = str(topic.get("prefix") or "").upper()
        common = [
            "ANAT", "CIR", "INF", "ATB", "CAR", "DER", "END", "GAS", "GIN", "HEM",
            "HEP", "NEF", "NEU", "OBS", "OFT", "ORT", "PED", "PNE", "PSI", "REU", "VMED",
        ]
        for i, item in enumerate(common):
            if prefix == item or title.startswith(item):
                return (i, item)
        return (999, title)

    def _message_topic_id(self, message) -> str:
        for attr in ("message_thread_id", "reply_to_top_message_id", "top_msg_id", "topic_id"):
            value = getattr(message, attr, None)
            if value:
                return str(value)
        reply_to = getattr(message, "reply_to_message", None)
        if reply_to:
            for attr in ("message_thread_id", "reply_to_top_message_id", "top_msg_id", "topic_id"):
                value = getattr(reply_to, attr, None)
                if value:
                    return str(value)
        if getattr(message, "forum_topic_created", None):
            return str(getattr(message, "id", "general") or "general")
        return "general"

    def _message_topic_title(self, message, chat, topic_id: str) -> str:
        created = getattr(message, "forum_topic_created", None)
        for obj in (created, getattr(message, "forum_topic", None), getattr(message, "topic", None)):
            if not obj:
                continue
            title = getattr(obj, "title", None) or getattr(obj, "name", None)
            if title:
                return str(title)
        if topic_id and topic_id != "general":
            return f"Tópico {topic_id}"
        return "Geral"

    def _message_to_video(
        self,
        message,
        chat_id: str | int,
        topic_id: str | None = None,
        topic_title: str | None = None,
    ) -> dict[str, Any] | None:
        media = None
        media_kind = None
        if getattr(message, "video", None):
            media = message.video
            media_kind = "video"
        elif getattr(message, "document", None):
            doc = message.document
            mime_type = getattr(doc, "mime_type", None) or ""
            file_name = getattr(doc, "file_name", None) or ""
            if mime_type.startswith("video/") or file_name.lower().endswith(
                (".mp4", ".mkv", ".mov", ".avi", ".webm")
            ):
                media = doc
                media_kind = "document"
        elif getattr(message, "animation", None):
            media = message.animation
            media_kind = "animation"

        if media is None:
            return None

        caption = getattr(message, "caption", None) or ""
        file_name = getattr(media, "file_name", None) or ""
        title = first_non_empty(
            [file_name, caption.splitlines()[0] if caption else None], f"video_{message.id}.mp4"
        )
        mime_type = getattr(media, "mime_type", None) or (
            "video/mp4" if media_kind == "video" else None
        )
        file_name = ensure_extension(safe_filename(file_name or title), mime_type)
        tags = infer_hashtags("\n".join([caption, file_name, title]))
        date = getattr(message, "date", None)
        return {
            "chat_id": str(chat_id),
            "message_id": int(message.id),
            "title": safe_filename(title, fallback=f"Video {message.id}"),
            "file_name": file_name,
            "mime_type": mime_type,
            "size": getattr(media, "file_size", None),
            "duration": getattr(media, "duration", None),
            "width": getattr(media, "width", None),
            "height": getattr(media, "height", None),
            "date": date.isoformat() if date else None,
            "hashtags": tags,
            "caption": caption,
            "topic_id": topic_id or "general",
            "topic_title": topic_title or "Geral",
        }

    # --------------------------------------------------------------- streaming
    async def prepare_stream(self, video: dict[str, Any]) -> dict[str, Any]:
        if not self.client:
            raise RuntimeError("Entre no Telegram primeiro.")
        await self._ensure_stream_server()
        token = uuid.uuid4().hex
        filename = safe_filename(video.get("file_name") or video.get("title") or "video.mp4")
        cache_path = self.stream_cache_dir / f"{token}.part"
        session = StreamSession(
            token=token,
            client=self.client,
            chat_id=video["chat_id"],
            message_id=int(video["message_id"]),
            size=int(video.get("size") or 0),
            cache_path=cache_path,
            mime_type=video.get("mime_type"),
        )
        self.sessions[token] = session
        quoted = quote(filename)
        url = f"http://127.0.0.1:{self.port}/stream/{token}/{quoted}"
        return {"url": url, "token": token, "port": self.port}

    async def release_stream(self, token: str, delete_file: bool = True) -> dict[str, Any]:
        session = self.sessions.pop(token, None)
        if not session:
            return {"released": False}
        await session.close()
        return {"released": True}

    async def _ensure_stream_server(self) -> None:
        if self.runner and self.port:
            return
        app = web.Application(client_max_size=1024**3)
        app.router.add_get("/stream/{token}/{filename:.*}", self._handle_stream, allow_head=True)
        app.router.add_get("/health", self._handle_health)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        sockets = self.site._server.sockets if self.site and self.site._server else []
        if not sockets:
            raise RuntimeError("Não foi possível iniciar o servidor local.")
        self.port = sockets[0].getsockname()[1]
        log.info("Servidor HTTP local iniciado na porta %s", self.port)

    async def _handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "time": time.time()})

    async def _handle_stream(self, request: web.Request) -> web.StreamResponse:
        token = request.match_info.get("token")
        session = self.sessions.get(token or "")
        if not session:
            return web.Response(status=404, text="Stream expirado ou inexistente.")
        session.last_access = time.time()

        total = session.size
        range_header = request.headers.get("Range")
        start, end = self._parse_range(range_header, total)

        if total and start >= total:
            return web.Response(status=416, headers={"Content-Range": f"bytes */{total}"})
        if total and end >= total:
            end = total - 1
        if end < start:
            end = start

        is_partial = bool(range_header) or (total and (start != 0 or end != total - 1))
        status = 206 if is_partial else 200
        length = (end - start + 1) if total else None

        headers = {
            "Accept-Ranges": "bytes",
            "Content-Type": session.mime_type,
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Access-Control-Allow-Origin": "*",
            "Connection": "keep-alive",
        }
        if total:
            if is_partial:
                headers["Content-Range"] = f"bytes {start}-{end}/{total}"
            headers["Content-Length"] = str(length)

        if request.method == "HEAD":
            return web.Response(status=status, headers=headers)

        response = web.StreamResponse(status=status, headers=headers)
        await response.prepare(request)

        try:
            async for data in session.read_range(start, end):
                if session.closed:
                    break
                await response.write(data)
                session.last_access = time.time()
            await response.write_eof()
        except (ConnectionResetError, asyncio.CancelledError):
            log.info("Cliente de vídeo desconectado (token %s)", token)
        except Exception:  # noqa: BLE001
            log.exception("Erro durante streaming HTTP")
            try:
                await response.write_eof()
            except Exception:  # noqa: BLE001
                pass
        return response

    def _parse_range(self, header: str | None, total: int) -> tuple[int, int]:
        """Interpreta o header Range. Sem range, entrega só o bloco inicial."""
        default_end = (
            max(total - 1, 0)
            if total and total < INITIAL_RANGE_CHUNK
            else INITIAL_RANGE_CHUNK - 1
        )
        if not header or not header.startswith("bytes="):
            return 0, default_end
        try:
            value = header.split("=", 1)[1].split(",", 1)[0].strip()
            if "-" not in value:
                return 0, default_end
            start_s, end_s = value.split("-", 1)
            if start_s == "":
                suffix = int(end_s or "0")
                if total:
                    return max(total - suffix, 0), total - 1
                return 0, default_end
            start = int(start_s or "0")
            if end_s:
                end = int(end_s)
            else:
                # Range aberto: entrega um pedaço a partir de start, sem travar.
                end = (
                    total - 1
                    if total and total < start + INITIAL_RANGE_CHUNK
                    else start + INITIAL_RANGE_CHUNK - 1
                )
            return max(start, 0), max(end, start)
        except Exception:  # noqa: BLE001
            log.warning("Range HTTP inválido recebido do player: %r", header)
            return 0, default_end

    def _user_to_dict(self, me) -> dict[str, Any]:
        return {
            "id": getattr(me, "id", None),
            "first_name": getattr(me, "first_name", None),
            "last_name": getattr(me, "last_name", None),
            "username": getattr(me, "username", None),
            "phone_number": getattr(me, "phone_number", None),
        }

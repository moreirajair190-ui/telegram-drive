"""Camada assíncrona: Pyrogram + servidor HTTP local (streaming + player).

Principais responsabilidades:
- Login no Telegram (API ID/HASH do próprio usuário).
- DETECÇÃO AUTOMÁTICA do tipo de chat ao sincronizar:
    A) Supergrupo com FÓRUM (tópicos) -> cada tópico vira uma MATÉRIA.
       Usa a RAW API `raw.functions.channels.GetForumTopics` (o Pyrogram do
       PyPI não tem método de alto nível). Para cada tópico, lista os vídeos
       daquele tópico (filtrando por message_thread_id / reply_to.top_message)
       e pega o sumário daquele tópico (mensagem fixada do tópico ou a melhor
       candidata a "menu").
    B) Supergrupo/grupo NORMAL (sem fórum) -> uma matéria única; sumário = pin
       do grupo (ou melhor candidata a menu); aulas = todos os vídeos.
    C) Canal (broadcast) -> lista linear de aulas (cronológica); sumário = pin.
- Streaming sob demanda em blocos (HTTP Range) servido em http://127.0.0.1:PORTA.
- Player HTML servido PELO MESMO servidor (rota /player/{token}) para que a
  página e o vídeo tenham a MESMA ORIGEM (corrige bloqueio do QtWebEngine).
"""

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
from .player_html import build_player_html
from .stream_cache import BLOCK_SIZE, StreamSession
from .summary_parser import looks_like_menu, menu_score
from .utils import (
    ensure_extension,
    first_non_empty,
    infer_hashtags,
    safe_filename,
)

log = logging.getLogger(__name__)

# Tamanho do bloco entregue quando o player não pede um range específico.
INITIAL_RANGE_CHUNK = 4 * BLOCK_SIZE


class TelegramService:
    """Pyrogram + servidor HTTP local, no mesmo event loop (thread dedicada)."""

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
        self.session_meta: dict[str, dict[str, Any]] = {}
        self.stream_cache_dir = CACHE_DIR / "streams"
        self.stream_cache_dir.mkdir(parents=True, exist_ok=True)
        self._cleanup_old_cache()

    def _cleanup_old_cache(self) -> None:
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
                [getattr(chat, "title", None), getattr(chat, "first_name", None)],
                str(chat.id),
            )
            courses.append(
                {
                    "chat_id": str(chat.id),
                    "title": title,
                    "username": getattr(chat, "username", None),
                    "chat_type": str(chat.type).split(".")[-1],
                    "is_forum": 1 if getattr(chat, "is_forum", False) else 0,
                }
            )
        courses.sort(key=lambda item: item["title"].lower())
        return courses

    # ------------------------------------------------------- detecção de fórum
    async def _is_forum(self, chat) -> bool:
        """Detecta fórum via flag do chat; confirma com GetForumTopics."""
        if getattr(chat, "is_forum", False):
            return True
        # Algumas versões não preenchem is_forum; tentamos a RAW API.
        try:
            await self._raw_get_forum_topics(chat, limit=1)
            return True
        except Exception:  # noqa: BLE001
            return False

    async def _raw_get_forum_topics(self, chat, limit: int = 100) -> list[dict[str, Any]]:
        """Lista tópicos do fórum usando a RAW API do Telegram.

        Retorna [{id, title}] para cada tópico. Lança exceção (ex.:
        CHANNEL_FORUM_MISSING) quando o chat não é um fórum.
        """
        from pyrogram.raw import functions, types as raw_types

        peer = await self.client.resolve_peer(chat.id)
        topics: list[dict[str, Any]] = []
        offset_date = 0
        offset_id = 0
        offset_topic = 0
        guard = 0
        while True:
            guard += 1
            if guard > 50:
                break
            result = await self.client.invoke(
                functions.channels.GetForumTopics(
                    channel=peer,
                    offset_date=offset_date,
                    offset_id=offset_id,
                    offset_topic=offset_topic,
                    limit=min(100, max(1, limit)),
                )
            )
            page = getattr(result, "topics", []) or []
            if not page:
                break
            for topic in page:
                # Tópicos normais têm id e title; pula tópicos "deletados".
                if isinstance(topic, getattr(raw_types, "ForumTopicDeleted", tuple)):
                    continue
                tid = getattr(topic, "id", None)
                title = getattr(topic, "title", None)
                if tid is None:
                    continue
                topics.append({"id": int(tid), "title": title or f"Tópico {tid}"})
                offset_topic = int(tid)
            # Atualiza offsets de paginação a partir das mensagens retornadas.
            messages = getattr(result, "messages", []) or []
            if messages:
                last = messages[-1]
                offset_id = getattr(last, "id", offset_id) or offset_id
                date = getattr(last, "date", None)
                if date:
                    offset_date = int(date) if isinstance(date, int) else offset_date
            if len(page) < min(100, max(1, limit)) or len(topics) >= limit:
                break
        # Garante o "General" (tópico 1) caso não venha listado.
        if not any(t["id"] == 1 for t in topics):
            topics.insert(0, {"id": 1, "title": "Geral"})
        return topics

    # ------------------------------------------------------------ sincronização
    async def sync_course(
        self, chat_id: str | int, limit: int = 99999, progress_cb=None
    ) -> dict[str, Any]:
        """Sincroniza um curso, detectando automaticamente o tipo do chat."""
        if not self.client:
            raise RuntimeError("Entre no Telegram primeiro.")
        from pyrogram.enums import ChatType

        chat_id = int(chat_id) if str(chat_id).lstrip("-").isdigit() else chat_id
        chat = await self.client.get_chat(chat_id)
        chat_type = getattr(chat, "type", None)
        is_forum = False
        if chat_type == ChatType.SUPERGROUP:
            is_forum = await self._is_forum(chat)

        if is_forum:
            subjects, videos, scanned = await self._sync_forum(chat, limit, progress_cb)
        elif chat_type == ChatType.CHANNEL:
            subjects, videos, scanned = await self._sync_channel(chat, limit, progress_cb)
        else:
            subjects, videos, scanned = await self._sync_group(chat, limit, progress_cb)

        return {
            "chat": {
                "chat_id": str(chat.id),
                "title": getattr(chat, "title", None) or str(chat.id),
                "username": getattr(chat, "username", None),
                "chat_type": str(chat_type).split(".")[-1] if chat_type else "",
                "is_forum": 1 if is_forum else 0,
            },
            "subjects": subjects,
            "videos": videos,
            "scanned": scanned,
            "detected": "forum" if is_forum else (
                "channel" if chat_type == ChatType.CHANNEL else "group"
            ),
        }

    # ---- A) Fórum: cada tópico = uma matéria --------------------------------
    async def _sync_forum(self, chat, limit, progress_cb):
        topics = await self._raw_get_forum_topics(chat, limit=200)
        topic_titles = {int(t["id"]): t["title"] for t in topics}

        videos: list[dict[str, Any]] = []
        # candidatos de sumário por tópico: {topic_id: [(score, text, mid)]}
        candidates: dict[int, list[tuple[int, str, int]]] = {}
        scanned = 0
        history_limit = max(0, int(limit or 0))

        async for message in self.client.get_chat_history(chat.id, limit=history_limit):
            scanned += 1
            if progress_cb and scanned % 200 == 0:
                try:
                    progress_cb(scanned, len(videos))
                except Exception:  # noqa: BLE001
                    pass
            tid = self._message_topic_id_int(message)
            text = self._message_text(message)
            if text and looks_like_menu(text):
                candidates.setdefault(tid, []).append((menu_score(text), text, message.id))
            video = self._message_to_video(message, chat.id, tid)
            if video:
                videos.append(video)

        # Sumário fixado de cada tópico (prioritário sobre candidatas).
        pinned_summaries = await self._collect_topic_pins(chat, list(topic_titles))

        subjects: list[dict[str, Any]] = []
        for order, t in enumerate(topics):
            tid = int(t["id"])
            title = t["title"]
            summary = pinned_summaries.get(tid)
            if not summary:
                cand = candidates.get(tid) or []
                cand.sort(key=lambda c: c[0], reverse=True)
                summary = cand[0][1] if cand else ""
            subjects.append(
                {
                    "telegram_topic_id": str(tid),
                    "title": title,
                    "summary_text": summary or "",
                    "sort_order": order,
                }
            )

        videos.reverse()
        return subjects, videos, scanned

    async def _collect_topic_pins(self, chat, topic_ids: list[int]) -> dict[int, str]:
        """Tenta obter a mensagem fixada de cada tópico do fórum."""
        pins: dict[int, str] = {}
        for tid in topic_ids:
            try:
                # get_chat_history com message_thread_id traz só o tópico;
                # a 1ª mensagem fixada costuma aparecer com flag pinned.
                async for msg in self.client.get_chat_history(
                    chat.id, limit=60, message_thread_id=tid
                ):
                    if getattr(msg, "pinned", False):
                        text = self._message_text(msg)
                        if text and looks_like_menu(text):
                            pins[tid] = text
                            break
            except Exception:  # noqa: BLE001
                # message_thread_id pode não ser suportado em todas as versões.
                continue
        return pins

    # ---- B) Grupo/supergrupo normal: uma matéria ----------------------------
    async def _sync_group(self, chat, limit, progress_cb):
        videos, summary, scanned = await self._scan_linear(chat, limit, progress_cb)
        subjects = [
            {
                "telegram_topic_id": "general",
                "title": getattr(chat, "title", None) or "Aulas",
                "summary_text": summary or "",
                "sort_order": 0,
            }
        ]
        return subjects, videos, scanned

    # ---- C) Canal broadcast: lista linear -----------------------------------
    async def _sync_channel(self, chat, limit, progress_cb):
        videos, summary, scanned = await self._scan_linear(chat, limit, progress_cb)
        subjects = [
            {
                "telegram_topic_id": "general",
                "title": getattr(chat, "title", None) or "Aulas",
                "summary_text": summary or "",
                "sort_order": 0,
            }
        ]
        return subjects, videos, scanned

    async def _scan_linear(self, chat, limit, progress_cb):
        """Varre o histórico inteiro (sem tópicos) e escolhe o melhor sumário."""
        videos: list[dict[str, Any]] = []
        best_summary = ""
        best_score = -1
        scanned = 0
        history_limit = max(0, int(limit or 0))

        # Sumário fixado tem prioridade.
        pinned = getattr(chat, "pinned_message", None)
        if pinned:
            text = self._message_text(pinned)
            if text and looks_like_menu(text):
                best_summary = text
                best_score = menu_score(text) + 50  # bônus por ser o pin

        async for message in self.client.get_chat_history(chat.id, limit=history_limit):
            scanned += 1
            if progress_cb and scanned % 200 == 0:
                try:
                    progress_cb(scanned, len(videos))
                except Exception:  # noqa: BLE001
                    pass
            text = self._message_text(message)
            if text and looks_like_menu(text):
                score = menu_score(text)
                if score > best_score:
                    best_score = score
                    best_summary = text
            video = self._message_to_video(message, chat.id, None)
            if video:
                videos.append(video)

        videos.reverse()
        return videos, best_summary, scanned

    # ---- helpers de mensagem ------------------------------------------------
    def _message_text(self, message) -> str:
        return (
            getattr(message, "text", None) or getattr(message, "caption", None) or ""
        ).strip()

    def _message_topic_id_int(self, message) -> int:
        """Id do tópico (thread) de uma mensagem em fórum. 1 = Geral."""
        for attr in ("message_thread_id", "topic_id"):
            value = getattr(message, attr, None)
            if value:
                return int(value)
        reply_to = getattr(message, "reply_to_message", None)
        if reply_to is not None:
            for attr in ("message_thread_id", "top_message_id"):
                value = getattr(reply_to, attr, None)
                if value:
                    return int(value)
        # Pyrogram às vezes expõe reply_to.reply_to_top_id via objeto interno.
        rt = getattr(message, "reply_to", None) or getattr(message, "reply_to_top_message_id", None)
        if isinstance(rt, int) and rt:
            return int(rt)
        return 1

    def _message_to_video(self, message, chat_id, topic_id: int | None) -> dict[str, Any] | None:
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
            [file_name, caption.splitlines()[0] if caption else None],
            f"video_{message.id}.mp4",
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
            "title": safe_filename(title, fallback=f"Aula {message.id}"),
            "file_name": file_name,
            "mime_type": mime_type,
            "size": getattr(media, "file_size", None),
            "duration": getattr(media, "duration", None),
            "width": getattr(media, "width", None),
            "height": getattr(media, "height", None),
            "date": date.isoformat() if date else None,
            "hashtags": tags,
            "caption": caption,
            "telegram_topic_id": str(topic_id) if topic_id else "general",
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
        self.session_meta[token] = {
            "title": video.get("title") or filename,
            "start_position_ms": int(video.get("start_position_ms") or 0),
        }
        quoted = quote(filename)
        stream_url = f"http://127.0.0.1:{self.port}/stream/{token}/{quoted}"
        player_url = f"http://127.0.0.1:{self.port}/player/{token}"
        return {
            "stream_url": stream_url,
            "player_url": player_url,
            "url": stream_url,  # compat
            "token": token,
            "port": self.port,
        }

    async def release_stream(self, token: str, delete_file: bool = True) -> dict[str, Any]:
        self.session_meta.pop(token, None)
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
        app.router.add_get("/player/{token}", self._handle_player)
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

    async def _handle_player(self, request: web.Request) -> web.Response:
        """Serve a PÁGINA do player na MESMA origem do vídeo (corrige bloqueio)."""
        token = request.match_info.get("token") or ""
        if token not in self.sessions:
            return web.Response(status=404, text="Player expirado ou inexistente.")
        meta = self.session_meta.get(token, {})
        filename = quote(safe_filename(meta.get("title") or "video.mp4"))
        stream_url = f"/stream/{token}/{filename}"  # relativo = mesma origem
        html_text = build_player_html(
            title=meta.get("title") or "Aula",
            url=stream_url,
            start_position_ms=int(meta.get("start_position_ms") or 0),
        )
        return web.Response(
            text=html_text,
            content_type="text/html",
            charset="utf-8",
            headers={"Cache-Control": "no-store"},
        )

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

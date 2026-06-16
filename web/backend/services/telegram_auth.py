"""TelegramAuthService — sessões Pyrogram ISOLADAS por conta de usuário.

Cada usuário usa SUA própria conta Telegram (API_ID/API_HASH próprios). Aqui
mantemos, em memória, um Pyrogram ``Client`` POR conta (``account_id``), cada
um com sua própria *session string* (nunca um arquivo de sessão global,
nunca credenciais compartilhadas).

Fluxo de autenticação individual:
    1. set_credentials(account)  -> grava API_ID/API_HASH cifrados.
    2. send_code(account, phone)  -> envia o código pelo Telegram.
    3. sign_in(account, code)     -> confirma login (pode pedir senha 2FA).
    4. check_password(account, p) -> conclui login com 2FA.
    -> ao logar, exportamos a *session string* e a guardamos CIFRADA no banco,
       vinculada apenas àquela conta.

Tudo roda num único event loop dedicado (thread daemon), como no core desktop,
mas os clients são indexados por ``account_id`` para garantir o isolamento.

Reaproveitamos ``StreamSession`` (do core) para o streaming HTTP por conta.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .encryption import EncryptionService
from .telegram_account import TelegramAccountService

log = logging.getLogger("tgplayer.web.tgauth")

_AUTH_ERROR_MARKERS = (
    "AUTH_KEY_UNREGISTERED",
    "AUTH_KEY_INVALID",
    "AUTH_KEY_DUPLICATED",
    "SESSION_REVOKED",
    "SESSION_EXPIRED",
    "USER_DEACTIVATED",
)


class SessionRevokedError(RuntimeError):
    """Sessão do Telegram foi revogada/expirada no servidor."""


def _is_auth_revoked_error(exc: BaseException) -> bool:
    try:
        from pyrogram.errors import Unauthorized

        if isinstance(exc, Unauthorized):
            return True
    except Exception:  # noqa: BLE001
        pass
    text = f"{type(exc).__name__}: {exc}".upper()
    return any(marker in text for marker in _AUTH_ERROR_MARKERS)


class _AccountState:
    """Estado em memória de uma conta (client + dados transitórios de login)."""

    __slots__ = (
        "client",
        "phone_number",
        "phone_code_hash",
        "last_code_sent_at",
        "flood_wait_until",
        "lock",
    )

    def __init__(self) -> None:
        self.client = None
        self.phone_number: str | None = None
        self.phone_code_hash: str | None = None
        self.last_code_sent_at: float = 0.0
        self.flood_wait_until: float = 0.0
        self.lock = asyncio.Lock()


class TelegramAuthService:
    """Gerencia clients Pyrogram por conta, no mesmo event loop dedicado."""

    def __init__(
        self,
        accounts: TelegramAccountService,
        enc: EncryptionService,
        cache_dir: str | Path,
        core_db: Any = None,
    ) -> None:
        self.accounts = accounts
        self.enc = enc
        self.core_db = core_db  # Banco do core (cache moov etc.), opcional.
        # IMPORTANTE: este diretório é EFÊMERO — apenas buffer temporário de
        # bytes do vídeo durante o streaming. NÃO é persistência. Em produção
        # (Render Free) fica em /tmp e pode sumir no restart sem qualquer perda.
        # Se o caminho indicado não for gravável, caímos para /tmp como rede de
        # segurança, garantindo que o servidor SEMPRE suba.
        self.stream_cache_dir = self._resolve_stream_cache_dir(cache_dir)

        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(
            target=self._run_loop, name="TgWebAsync", daemon=True
        )
        self.thread.start()

        # Estado por conta.
        self._states: dict[int, _AccountState] = {}
        # Servidor HTTP local para streaming (compartilhado; tokens isolam contas).
        self.runner = None
        self.site = None
        self.port: int | None = None
        self.sessions: dict[str, Any] = {}
        self.session_meta: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _resolve_stream_cache_dir(cache_dir: str | Path) -> Path:
        """Garante um diretório de cache de streaming GRAVÁVEL e EFÊMERO.

        Tenta o caminho indicado (ex.: ``/tmp/tgplayer-streams``). Se já vier
        terminando em ``streams`` usamos como está; senão, criamos um subdir
        ``streams``. Em caso de ``PermissionError`` (filesystem read-only),
        caímos para ``/tmp/tgplayer-streams`` — nunca derrubamos o servidor por
        causa de um diretório de cache temporário.
        """
        candidate = Path(cache_dir)
        if candidate.name != "streams":
            candidate = candidate / "streams"
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except OSError:
            fallback = Path("/tmp/tgplayer-streams/streams")
            fallback.mkdir(parents=True, exist_ok=True)
            log.warning(
                "Cache de streaming %s não gravável; usando %s (efêmero).",
                candidate, fallback,
            )
            return fallback

    # ------------------------------------------------------------------ loop
    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def call(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    async def run(self, coro):
        """Atalho para aguardar uma corrotina a partir do mundo síncrono/async."""
        return await asyncio.wrap_future(self.call(coro))

    def _state(self, account_id: int) -> _AccountState:
        st = self._states.get(account_id)
        if st is None:
            st = _AccountState()
            self._states[account_id] = st
        return st

    # ------------------------------------------------------------ client mgmt
    async def _build_client(self, account_id: int):
        """Cria um Pyrogram Client em memória (session string), sem conectar."""
        from pyrogram import Client

        creds = self.accounts.get_credentials(account_id)
        api_id = creds.get("api_id")
        api_hash = creds.get("api_hash")
        if not api_id or not api_hash:
            raise RuntimeError("API ID/HASH não configurados para esta conta.")
        session_string = creds.get("session")
        kwargs: dict[str, Any] = dict(
            api_id=int(str(api_id).strip()),
            api_hash=str(api_hash).strip(),
            in_memory=True,  # NUNCA grava arquivo de sessão global.
            no_updates=True,
            sleep_threshold=60,
        )
        if session_string:
            kwargs["session_string"] = session_string
        # Nome único por conta (apenas rótulo interno; in_memory não cria arquivo).
        return Client(name=f"acc_{account_id}", **kwargs)

    async def _get_connected_client(self, account_id: int):
        """Devolve um client conectado para a conta (reaproveita se já existe)."""
        st = self._state(account_id)
        if st.client is not None:
            return st.client
        client = await self._build_client(account_id)
        await client.connect()
        st.client = client
        return client

    async def _persist_session(self, account_id: int, client) -> None:
        """Exporta a session string e grava CIFRADA, vinculada à conta."""
        try:
            session_string = await client.export_session_string()
            self.accounts.set_session(account_id, session_string)
        except Exception:  # noqa: BLE001
            log.warning("Não foi possível exportar a session string (conta %s)", account_id)

    async def _disconnect(self, account_id: int) -> None:
        st = self._states.get(account_id)
        if st and st.client is not None:
            try:
                await st.client.disconnect()
            except Exception:  # noqa: BLE001
                pass
            st.client = None

    # ------------------------------------------------------------------ status
    async def status(self, account_id: int) -> dict[str, Any]:
        """Estado da conexão da conta (sem expor nada sensível)."""
        if not self.accounts.has_api_credentials(account_id):
            return {"has_credentials": False, "connected": False, "me": None}
        creds = self.accounts.get_credentials(account_id)
        if not creds.get("session"):
            return {"has_credentials": True, "connected": False, "me": None}
        try:
            client = await self._get_connected_client(account_id)
            me = await client.get_me()
            if me:
                self.accounts.set_status(account_id, "connected")
                self.accounts.set_profile(
                    account_id,
                    getattr(me, "id", None),
                    getattr(me, "username", None),
                    getattr(me, "first_name", None),
                )
                return {
                    "has_credentials": True,
                    "connected": True,
                    "me": self._user_to_dict(me),
                }
        except Exception as exc:  # noqa: BLE001
            if _is_auth_revoked_error(exc):
                await self._handle_revoked(account_id)
                return {"has_credentials": True, "connected": False, "me": None,
                        "session_revoked": True}
            log.info("status: conta %s não conectada (%s)", account_id, type(exc).__name__)
        return {"has_credentials": True, "connected": False, "me": None}

    async def _handle_revoked(self, account_id: int) -> None:
        await self._disconnect(account_id)
        self.accounts.set_session(account_id, None)
        self.accounts.set_status(account_id, "disconnected")

    # ------------------------------------------------------------------ login
    async def send_code(self, account_id: int, phone: str) -> dict[str, Any]:
        st = self._state(account_id)
        async with st.lock:
            phone = (phone or "").strip().replace(" ", "")
            self.accounts.set_phone(account_id, phone)
            now = time.time()
            if st.flood_wait_until and now < st.flood_wait_until:
                return {"sent": False, "flood_wait": int(st.flood_wait_until - now)}
            # Reaproveita hash recente para o mesmo telefone (evita FLOOD_WAIT).
            if (
                st.phone_code_hash
                and st.phone_number == phone
                and now - st.last_code_sent_at < 300
            ):
                return {"sent": True, "reused": True}
            # (Re)cria um client limpo para iniciar o login.
            await self._disconnect(account_id)
            client = await self._build_client(account_id)
            await client.connect()
            st.client = client
            try:
                sent = await client.send_code(phone)
            except Exception as exc:  # noqa: BLE001
                wait = getattr(exc, "value", None)
                if wait is None:
                    m = re.search(r"FLOOD_WAIT_?(\d+)?|wait of (\d+) seconds",
                                  str(exc), re.I)
                    if m:
                        wait = int(next(g for g in m.groups() if g))
                if wait is not None:
                    st.flood_wait_until = time.time() + int(wait)
                    return {"sent": False, "flood_wait": int(wait)}
                raise
            st.phone_number = phone
            st.phone_code_hash = sent.phone_code_hash
            st.last_code_sent_at = time.time()
            st.flood_wait_until = 0.0
            self.accounts.set_status(account_id, "awaiting_code")
            return {"sent": True}

    async def sign_in(self, account_id: int, code: str) -> dict[str, Any]:
        from pyrogram.errors import SessionPasswordNeeded

        st = self._state(account_id)
        async with st.lock:
            if not st.client or not st.phone_number or not st.phone_code_hash:
                raise RuntimeError("Código não solicitado ainda.")
            code = (code or "").strip().replace(" ", "").replace("-", "")
            try:
                await st.client.sign_in(st.phone_number, st.phone_code_hash, code)
            except SessionPasswordNeeded:
                self.accounts.set_status(account_id, "awaiting_password")
                return {"authorized": False, "needs_password": True}
            me = await st.client.get_me()
            await self._persist_session(account_id, st.client)
            self.accounts.set_status(account_id, "connected")
            self.accounts.set_profile(
                account_id,
                getattr(me, "id", None),
                getattr(me, "username", None),
                getattr(me, "first_name", None),
            )
            return {"authorized": True, "me": self._user_to_dict(me)}

    async def check_password(self, account_id: int, password: str) -> dict[str, Any]:
        st = self._state(account_id)
        async with st.lock:
            if not st.client:
                raise RuntimeError("Cliente Telegram não conectado.")
            await st.client.check_password(password)
            me = await st.client.get_me()
            await self._persist_session(account_id, st.client)
            self.accounts.set_status(account_id, "connected")
            self.accounts.set_profile(
                account_id,
                getattr(me, "id", None),
                getattr(me, "username", None),
                getattr(me, "first_name", None),
            )
            return {"authorized": True, "me": self._user_to_dict(me)}

    async def logout(self, account_id: int) -> dict[str, Any]:
        st = self._states.get(account_id)
        if st and st.client:
            try:
                await st.client.log_out()
            except Exception:  # noqa: BLE001
                pass
            st.client = None
        self.accounts.set_session(account_id, None)
        self.accounts.set_status(account_id, "disconnected")
        return {"ok": True}

    async def get_me(self, account_id: int) -> dict[str, Any] | None:
        try:
            client = await self._get_connected_client(account_id)
            me = await client.get_me()
            return self._user_to_dict(me) if me else None
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------ dialogs
    async def list_dialog_courses(self, account_id: int, limit: int = 500) -> list[dict[str, Any]]:
        from pyrogram.enums import ChatType

        from tgplayer.utils import first_non_empty

        client = await self._get_connected_client(account_id)
        allowed = {ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL}
        courses: list[dict[str, Any]] = []
        try:
            async for dialog in client.get_dialogs(limit=limit):
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
        except Exception as exc:  # noqa: BLE001
            if _is_auth_revoked_error(exc):
                await self._handle_revoked(account_id)
                raise SessionRevokedError(str(exc)) from exc
            raise
        courses.sort(key=lambda item: item["title"].lower())
        return courses

    # ------------------------------------------------------------------ sync
    async def sync_course(
        self, account_id: int, chat_id: str | int, limit: int = 99999
    ) -> dict[str, Any]:
        """Reaproveita o algoritmo de sincronização do core, com o client da conta."""
        client = await self._get_connected_client(account_id)
        helper = self._core_helper(client)
        try:
            return await helper.sync_course(chat_id, limit=limit)
        except Exception as exc:  # noqa: BLE001
            if _is_auth_revoked_error(exc):
                await self._handle_revoked(account_id)
                raise SessionRevokedError(str(exc)) from exc
            raise

    def _core_helper(self, client):
        """Cria um TelegramService 'leve' reutilizando seus métodos de parsing.

        Não iniciamos o loop/thread do core: apenas reaproveitamos a lógica de
        detecção de fórum/canal/grupo, injetando o client desta conta.
        """
        from tgplayer.telegram_service import TelegramService

        helper = TelegramService.__new__(TelegramService)  # sem __init__
        helper.client = client
        helper.db = self.core_db
        return helper

    # ------------------------------------------------------------------ stream
    async def prepare_stream(self, account_id: int, video: dict[str, Any]) -> dict[str, Any]:
        from tgplayer.stream_cache import StreamSession

        client = await self._get_connected_client(account_id)
        await self._ensure_stream_server()
        token = uuid.uuid4().hex
        cache_path = self.stream_cache_dir / f"{token}.part"
        chat_username = (video.get("chat_username") or "").strip().lstrip("@") or None
        session = StreamSession(
            token=token,
            client=client,
            chat_id=video["chat_id"],
            message_id=int(video["message_id"]),
            size=int(video.get("size") or 0),
            cache_path=cache_path,
            mime_type=video.get("mime_type"),
            throttle_kbps=0,
            max_retries=2,
            file_id=video.get("file_id"),
            chat_username=chat_username,
        )
        self.sessions[token] = session
        try:
            session.prefetch_start()
        except Exception:  # noqa: BLE001
            log.exception("Falha ao iniciar pré-busca do stream %s", token)
        try:
            import asyncio as _asyncio

            _asyncio.ensure_future(self._faststart_bg(token))
        except Exception:  # noqa: BLE001
            pass
        self.session_meta[token] = {
            "account_id": account_id,
            "title": video.get("title") or "Aula",
            "start_position_ms": int(video.get("start_position_ms") or 0),
        }
        return {"token": token}

    async def _faststart_bg(self, token: str) -> None:
        session = self.sessions.get(token)
        if not session:
            return
        try:
            await session.ensure_faststart()
        except Exception:  # noqa: BLE001
            pass

    async def _ensure_stream_server(self) -> None:
        if self.runner and self.port:
            return
        from aiohttp import web

        app = web.Application(client_max_size=1024**3)
        app.router.add_get("/stream/{token}/{filename:.*}", self._handle_stream,
                           allow_head=True)
        app.router.add_get("/health", self._handle_health)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        sockets = self.site._server.sockets if self.site and self.site._server else []
        if not sockets:
            raise RuntimeError("Não foi possível iniciar o servidor local.")
        self.port = sockets[0].getsockname()[1]
        log.info("Servidor de streaming local iniciado na porta %s", self.port)

    async def _handle_health(self, request):
        from aiohttp import web

        return web.json_response({"ok": True, "time": time.time()})

    async def _handle_stream(self, request):
        from aiohttp import web

        token = request.match_info.get("token")
        session = self.sessions.get(token or "")
        if not session:
            return web.Response(status=404, text="Stream expirado ou inexistente.")
        session.last_access = time.time()
        try:
            await session.ensure_faststart()
        except Exception:  # noqa: BLE001
            pass
        total = session.logical_size
        range_header = request.headers.get("Range")
        start, end = self._parse_range(range_header, total)
        if total and start >= total:
            return web.Response(status=416, headers={"Content-Range": f"bytes */{total}"})
        if total and end >= total:
            end = total - 1
        if end < start:
            end = start
        is_partial = bool(range_header)
        status = 206 if is_partial else 200
        length = (end - start + 1) if total else None
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Type": session.mime_type,
            "Cache-Control": "no-store",
            "Content-Disposition": "inline",
            "X-Content-Type-Options": "nosniff",
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
            async for data in session.read_logical_range(start, end):
                if session.closed:
                    break
                await response.write(data)
                session.last_access = time.time()
            await response.write_eof()
        except Exception:  # noqa: BLE001
            try:
                await response.write_eof()
            except Exception:  # noqa: BLE001
                pass
        return response

    def _parse_range(self, header: str | None, total: int) -> tuple[int, int]:
        from tgplayer.stream_cache import BLOCK_SIZE

        chunk = 4 * BLOCK_SIZE
        default_end = (max(total - 1, 0) if total and total < chunk else chunk - 1)
        if not header or not header.startswith("bytes="):
            return 0, default_end
        try:
            value = header.split("=", 1)[1].split(",", 1)[0].strip()
            start_s, end_s = value.split("-", 1)
            if start_s == "":
                suffix = int(end_s or "0")
                if total:
                    return max(total - suffix, 0), total - 1
                return 0, default_end
            start = int(start_s or "0")
            end = int(end_s) if end_s else (
                total - 1 if total and total < start + chunk else start + chunk - 1
            )
            return max(start, 0), max(end, start)
        except Exception:  # noqa: BLE001
            return 0, default_end

    async def release_stream(self, token: str) -> None:
        self.session_meta.pop(token, None)
        session = self.sessions.pop(token, None)
        if session:
            try:
                await session.close()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------ shutdown
    async def close(self) -> None:
        for token in list(self.sessions):
            await self.release_stream(token)
        for account_id in list(self._states):
            await self._disconnect(account_id)
        if self.runner:
            try:
                await self.runner.cleanup()
            except Exception:  # noqa: BLE001
                pass
            self.runner = None
            self.port = None

    def stop(self) -> None:
        try:
            self.call(self.close()).result(timeout=10)
        except Exception:  # noqa: BLE001
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _user_to_dict(me) -> dict[str, Any]:
        """Apenas dados de perfil (nada de credenciais)."""
        return {
            "id": getattr(me, "id", None),
            "first_name": getattr(me, "first_name", None),
            "last_name": getattr(me, "last_name", None),
            "username": getattr(me, "username", None),
        }

    def telegram_message_urls(self, username, chat_id, message_id):
        from tgplayer.telegram_service import TelegramService

        helper = TelegramService.__new__(TelegramService)
        return helper.telegram_message_urls(username, chat_id, message_id)

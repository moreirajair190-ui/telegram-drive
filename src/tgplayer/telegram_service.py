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
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

from aiohttp import web

from .paths import CACHE_DIR, SESSION_DIR, ensure_dirs
from .player_html import build_player_html
from .quality import adaptive_quality, throttle_for
from .stream_cache import BLOCK_SIZE, FIRST_CHUNK_SIZE, StreamSession
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


class SessionRevokedError(RuntimeError):
    """Sessão do Telegram foi revogada/expirada no servidor.

    Acontece quando o Telegram invalida a `auth_key` (ex.: o usuário encerrou
    a sessão por outro dispositivo, trocou a senha, ou a sessão expirou por
    inatividade). A chamada falha com erros como `401 AUTH_KEY_UNREGISTERED`,
    `SESSION_REVOKED` ou `USER_DEACTIVATED`. Quando isso ocorre o arquivo de
    sessão local é inútil e precisa ser apagado para um novo login.
    """


# Trechos que identificam, na mensagem do erro, uma sessão inválida no servidor.
# Pyrogram nem sempre levanta a classe `Unauthorized`; em algumas versões o
# erro chega como texto cru ("[401 AUTH_KEY_UNREGISTERED] ..."), por isso
# também casamos pelo conteúdo da string.
_AUTH_ERROR_MARKERS = (
    "AUTH_KEY_UNREGISTERED",
    "AUTH_KEY_INVALID",
    "AUTH_KEY_DUPLICATED",
    "SESSION_REVOKED",
    "SESSION_EXPIRED",
    "USER_DEACTIVATED",
    "USER_DEACTIVATED_BAN",
)


def _is_auth_revoked_error(exc: BaseException) -> bool:
    """Detecta se a exceção indica que a sessão foi revogada no servidor."""
    try:
        from pyrogram.errors import Unauthorized

        if isinstance(exc, Unauthorized):
            return True
    except Exception:  # noqa: BLE001
        pass
    text = f"{type(exc).__name__}: {exc}".upper()
    return any(marker in text for marker in _AUTH_ERROR_MARKERS)


def _natural_key(text: str) -> list[object]:
    """Ordenação natural: PSI 2 vem antes de PSI 10."""
    parts = re.split(r"(\d+)", (text or "").casefold())
    return [int(p) if p.isdigit() else p for p in parts]


def _topic_sort_key(topic: dict[str, Any]) -> tuple[int, list[object], int]:
    title = str(topic.get("title") or "")
    lowered = title.casefold().strip()
    general = 0 if lowered in {"geral", "general"} else 1
    return (general, _natural_key(title), int(topic.get("id") or 0))

class TelegramService:
    """Pyrogram + servidor HTTP local, no mesmo event loop (thread dedicada)."""

    def __init__(self, db: Any = None) -> None:
        ensure_dirs()
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(
            target=self._run_loop, name="TgPlayerAsync", daemon=True
        )
        self.thread.start()
        self.client = None
        self.db = db  # acesso opcional ao banco (cache de moov, configurações).
        self.api_id: int | None = None
        self.api_hash: str | None = None
        self.phone_code_hash: str | None = None
        self.phone_number: str | None = None
        self._last_code_sent_at: float = 0.0
        self._last_code_phone: str | None = None
        self._flood_wait_until: float = 0.0
        self.runner: web.AppRunner | None = None
        self.site: web.TCPSite | None = None
        self.port: int | None = None
        self.sessions: dict[str, StreamSession] = {}
        self.session_meta: dict[str, dict[str, Any]] = {}
        self.stream_cache_dir = CACHE_DIR / "streams"
        self.stream_cache_dir.mkdir(parents=True, exist_ok=True)
        # Cache de miniaturas (persistente, com poda LRU). Ideia portada de
        # `preview.rs` (cache de previews com poda preservando o ativo).
        self.thumbs_dir = CACHE_DIR / "thumbs"
        self.thumbs_dir.mkdir(parents=True, exist_ok=True)
        self._cleanup_old_cache()
        # Qualidade de streaming corrente (definida pelo player/configurações).
        self.streaming_quality: str = "original"
        self.adaptive_mode: bool = False

    # ----------------------------------------------------- proxy / configurações
    def _build_proxy(self) -> dict[str, Any] | None:
        """Lê a configuração de proxy do banco (SOCKS5) para o Pyrogram.

        Ideia portada de `vpn_optimizer.rs` (`ProxyConfig`). Útil para alunos em
        redes restritas. O Pyrogram aceita `proxy={scheme, hostname, port, ...}`.
        """
        if not self.db:
            return None
        try:
            if (self.db.get_setting("proxy_enabled") or "0") != "1":
                return None
            scheme = (self.db.get_setting("proxy_type") or "socks5").lower()
            host = self.db.get_setting("proxy_host") or ""
            port = self.db.get_setting("proxy_port") or ""
            if not host or not port:
                return None
            proxy: dict[str, Any] = {
                "scheme": scheme,
                "hostname": host,
                "port": int(port),
            }
            user = self.db.get_setting("proxy_user")
            pwd = self.db.get_setting("proxy_pass")
            if user:
                proxy["username"] = user
            if pwd:
                proxy["password"] = pwd
            return proxy
        except Exception:  # noqa: BLE001
            log.exception("Configuração de proxy inválida; ignorando.")
            return None

    def _current_throttle_kbps(self) -> int:
        """Throttle (kbps) atual com base na qualidade escolhida (não-adaptativo)."""
        if self.adaptive_mode:
            return 0  # adaptativo ajusta por sessão; começa ilimitado
        # Limite manual de banda (kbps) tem prioridade quando configurado (>0).
        if self.db:
            try:
                manual = int(self.db.get_setting("bandwidth_limit_kbps") or 0)
                if manual > 0:
                    return manual
            except Exception:  # noqa: BLE001
                pass
        return throttle_for(self.streaming_quality)

    def _current_max_retries(self) -> int:
        """Nº de re-tentativas por bloco. Maior no modo 'conexão instável'."""
        if not self.db:
            return 2
        try:
            if (self.db.get_setting("unstable_connection") or "0") == "1":
                return int(self.db.get_setting("network_retries") or 5)
            return int(self.db.get_setting("network_retries") or 2)
        except Exception:  # noqa: BLE001
            return 2

    def set_quality(self, quality: str, adaptive: bool) -> None:
        """Aplica a qualidade/modo adaptativo a TODAS as sessões ativas."""
        self.streaming_quality = (quality or "original").lower()
        self.adaptive_mode = bool(adaptive)
        kbps = self._current_throttle_kbps()
        for session in self.sessions.values():
            session.set_throttle_kbps(kbps)

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

    # ------------------------------------------------------- sessão / recuperação
    # Nome de sessão legado ("tgclassplayer") para que usuários que atualizam do
    # TGClassPlayer continuem logados sem refazer login.
    SESSION_NAME = "tgclassplayer"

    def _session_file_candidates(self) -> list[Path]:
        """Arquivos que compõem a sessão local do Pyrogram."""
        base = SESSION_DIR / self.SESSION_NAME
        return [
            base.with_suffix(".session"),
            base.with_suffix(".session-journal"),
            Path(f"{base}.session"),
            Path(f"{base}.session-journal"),
        ]

    async def clear_session_files(self) -> None:
        """Desconecta e APAGA os arquivos de sessão locais.

        Necessário quando o servidor revoga a chave (AUTH_KEY_UNREGISTERED):
        manter o arquivo antigo só faz o erro se repetir. Após isto o usuário
        precisa entrar novamente.
        """
        if self.client:
            try:
                await self.client.disconnect()
            except Exception:  # noqa: BLE001
                pass
            self.client = None
        removed = 0
        for path in self._session_file_candidates():
            try:
                if path.exists():
                    path.unlink()
                    removed += 1
            except Exception:  # noqa: BLE001
                log.exception("Não foi possível remover arquivo de sessão %s", path)
        if removed:
            log.warning("Sessão revogada: %d arquivo(s) de sessão removido(s).", removed)
        # Esquece estado de login em memória.
        self.phone_code_hash = None
        self.phone_number = None
        self._last_code_phone = None
        self._last_code_sent_at = 0.0

    async def _on_auth_revoked(self, exc: BaseException, context: str) -> None:
        """Limpa a sessão local quando o servidor a revoga e levanta um erro claro."""
        log.warning("Sessão do Telegram revogada (%s): %s", context, exc)
        try:
            await self.clear_session_files()
        except Exception:  # noqa: BLE001
            log.exception("Falha ao limpar sessão após revogação")
        raise SessionRevokedError(
            "Sua sessão do Telegram expirou ou foi encerrada. "
            "Entre novamente em Conta → Entrar / trocar conta."
        ) from exc

    # ------------------------------------------------------------------- login
    async def _new_client(self):
        """Cria (sem conectar) um Pyrogram Client com as configurações atuais."""
        from pyrogram import Client

        proxy = self._build_proxy()
        client_kwargs: dict[str, Any] = dict(
            api_id=self.api_id,
            api_hash=self.api_hash,
            workdir=str(SESSION_DIR),
            no_updates=True,
            sleep_threshold=60,
        )
        if proxy:
            client_kwargs["proxy"] = proxy
            log.info("Usando proxy %s://%s:%s", proxy.get("scheme"),
                     proxy.get("hostname"), proxy.get("port"))
        return Client(self.SESSION_NAME, **client_kwargs)

    async def _connect_fresh(self) -> dict[str, Any]:
        """Conecta um client novo a partir do arquivo de sessão local (se houver).

        Trata sessão revogada apagando o arquivo e devolvendo um client LIMPO já
        conectado, pronto para `send_code`/`sign_in` — assim o login funciona na
        mesma hora, sem o usuário precisar fechar o app.
        """
        from pyrogram.errors import Unauthorized

        self.client = await self._new_client()
        try:
            await self.client.connect()
            me = await self.client.get_me()
            return {"authorized": True, "me": self._user_to_dict(me)}
        except Unauthorized:
            # Não autorizado, mas a sessão é válida o suficiente para enviar
            # código. Mantemos o client conectado.
            return {"authorized": False}
        except Exception as exc:  # noqa: BLE001
            if _is_auth_revoked_error(exc):
                # Arquivo de sessão inútil: apaga e refaz um client limpo,
                # já conectado, para permitir um novo login imediatamente.
                await self.clear_session_files()
                self.client = await self._new_client()
                try:
                    await self.client.connect()
                except Unauthorized:
                    pass
                except Exception:  # noqa: BLE001
                    log.exception("Falha ao reconectar após limpar sessão revogada")
                return {"authorized": False, "session_revoked": True}
            raise

    async def ensure_connected(self, api_id: str | int, api_hash: str) -> dict[str, Any]:
        self.api_id = int(str(api_id).strip())
        self.api_hash = str(api_hash).strip()

        if self.client:
            try:
                me = await self.client.get_me()
                if me:
                    return {"authorized": True, "me": self._user_to_dict(me)}
            except Exception as exc:  # noqa: BLE001
                # Se a sessão foi revogada no servidor, não adianta reconectar
                # com o mesmo arquivo: apagamos e recriamos um client limpo.
                if _is_auth_revoked_error(exc):
                    await self.clear_session_files()
                    result = await self._connect_fresh()
                    result["session_revoked"] = True
                    return result
                try:
                    await self.client.disconnect()
                except Exception:  # noqa: BLE001
                    pass
                self.client = None

        return await self._connect_fresh()

    async def send_code(self, phone_number: str) -> dict[str, Any]:
        """Envia código de login com proteção contra FLOOD_WAIT.

        O Telegram bloqueia novos códigos por horas quando o usuário clica várias
        vezes em "Conectar"/"reenviar". Não existe forma segura de burlar isso;
        a correção é NÃO repetir auth.SendCode enquanto houver hash/cooldown e
        mostrar o tempo restante com clareza.
        """
        if not self.client:
            raise RuntimeError("Cliente Telegram não conectado.")
        phone_number = phone_number.strip().replace(" ", "")

        now = time.time()
        # Cooldown persistente: se o usuário fechar/abrir o app durante um
        # FLOOD_WAIT, não tentamos auth.SendCode de novo por acidente.
        if self.db:
            try:
                stored_until = int(float(self.db.get_setting("auth_flood_wait_until") or 0))
                if stored_until > now:
                    self._flood_wait_until = stored_until
            except Exception:  # noqa: BLE001
                pass
        if self._flood_wait_until and now < self._flood_wait_until:
            wait = int(self._flood_wait_until - now)
            return {"sent": False, "flood_wait": wait}

        # Se acabamos de mandar código para o mesmo telefone, reutiliza o hash em
        # vez de disparar outro auth.SendCode. Isso evita o erro 420 FLOOD_WAIT.
        if (
            self.phone_code_hash
            and self._last_code_phone == phone_number
            and now - self._last_code_sent_at < 300
        ):
            return {
                "sent": True,
                "reused": True,
                "phone_code_hash": self.phone_code_hash,
                "cooldown": int(300 - (now - self._last_code_sent_at)),
            }

        try:
            sent_code = await self.client.send_code(phone_number)
        except Exception as exc:  # noqa: BLE001
            # Pyrogram normalmente levanta FloodWait(value), mas em algumas
            # versões/ambientes a mensagem vem como texto: [420 FLOOD_WAIT_X] ...
            wait = getattr(exc, "value", None)
            if wait is None:
                m = re.search(r"FLOOD_WAIT_?(\d+)?|wait of (\d+) seconds", str(exc), re.I)
                if m:
                    wait = int(next(g for g in m.groups() if g))
            if wait is not None:
                wait = int(wait)
                self._flood_wait_until = time.time() + wait
                if self.db:
                    try:
                        self.db.set_setting("auth_flood_wait_until", str(int(self._flood_wait_until)))
                    except Exception:  # noqa: BLE001
                        pass
                log.warning("Telegram impôs FLOOD_WAIT de %ss ao enviar código", wait)
                return {"sent": False, "flood_wait": wait}
            raise

        self.phone_number = phone_number
        self.phone_code_hash = sent_code.phone_code_hash
        self._last_code_phone = phone_number
        self._last_code_sent_at = time.time()
        self._flood_wait_until = 0
        if self.db:
            try:
                self.db.set_setting("auth_flood_wait_until", "0")
            except Exception:  # noqa: BLE001
                pass
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
        try:
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
        except Exception as exc:  # noqa: BLE001
            # Causa do erro reportado pelos usuários:
            # [401 AUTH_KEY_UNREGISTERED] ... caused by "messages.GetDialogs".
            # A sessão local existe mas o servidor já a revogou. Apagamos o
            # arquivo e pedimos novo login em vez de repetir o erro.
            if _is_auth_revoked_error(exc):
                await self._on_auth_revoked(exc, "messages.GetDialogs")
            raise
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
        try:
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
        except Exception as exc:  # noqa: BLE001
            if _is_auth_revoked_error(exc):
                await self._on_auth_revoked(exc, "sync_course")
            raise

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
        """Sincroniza um fórum percorrendo CADA tópico SEPARADAMENTE.

        Esta é a correção central do bug "tudo caiu em General": em vez de
        varrer o histórico inteiro e tentar adivinhar o tópico de cada mensagem
        (o Pyrogram do PyPI não preenche message_thread_id de forma confiável no
        histórico), nós pedimos o histórico de CADA tópico com o parâmetro
        `message_thread_id=tid`. Assim, cada vídeo e cada sumário pertencem ao
        tópico correto — cada tópico = uma matéria com o SEU próprio sumário.
        """
        topics = await self._raw_get_forum_topics(chat, limit=400)
        # A API costuma devolver tópicos por atividade recente; para um curso isso
        # bagunça a navegação. Ordenamos naturalmente pelo título do tópico para
        # preservar sequências como PSI 1, PSI 2, PRE 1, CIR 10 etc.
        topics = sorted(topics, key=_topic_sort_key)
        history_limit = max(0, int(limit or 0))
        per_topic_limit = history_limit if history_limit else 0  # 0 = tudo

        videos: list[dict[str, Any]] = []
        subjects: list[dict[str, Any]] = []
        scanned = 0

        for order, t in enumerate(topics):
            tid = int(t["id"])
            title = t["title"]
            topic_videos, summary, topic_scanned = await self._scan_topic(
                chat, tid, per_topic_limit, progress_cb, scanned
            )
            scanned += topic_scanned

            # Anexa os vídeos deste tópico (cada um marcado com o topic_id).
            videos.extend(topic_videos)

            subjects.append(
                {
                    "telegram_topic_id": str(tid),
                    "title": title,
                    "summary_text": summary or "",
                    "sort_order": order,
                }
            )

        # Remove tópicos completamente vazios (sem vídeos e sem sumário), exceto
        # quando o usuário pode querer mantê-los; preferimos manter só os úteis.
        used_topic_ids = {v["telegram_topic_id"] for v in videos}
        subjects = [
            s
            for s in subjects
            if s["telegram_topic_id"] in used_topic_ids or s["summary_text"]
        ] or subjects

        return subjects, videos, scanned

    async def _scan_topic(self, chat, tid: int, limit: int, progress_cb, base_scanned: int):
        """Lê o histórico de UM tópico do fórum (via message_thread_id).

        Retorna (videos, summary, scanned). O sumário é a mensagem fixada do
        tópico ou, na falta dela, a melhor candidata a "menu" dentro do tópico.
        """
        topic_videos: list[dict[str, Any]] = []
        best_summary = ""
        best_score = -1
        scanned = 0

        async def _consume(iterator):
            nonlocal scanned, best_summary, best_score
            async for message in iterator:
                scanned += 1
                if progress_cb and (base_scanned + scanned) % 200 == 0:
                    try:
                        progress_cb(base_scanned + scanned, len(topic_videos))
                    except Exception:  # noqa: BLE001
                        pass
                text = self._message_text(message)
                if text and looks_like_menu(text):
                    bonus = 80 if getattr(message, "pinned", False) else 0
                    score = menu_score(text) + bonus
                    if score > best_score:
                        best_score = score
                        best_summary = text
                video = self._message_to_video(message, chat.id, tid)
                if video:
                    topic_videos.append(video)

        # Estratégia 1: histórico filtrado pelo tópico (preferida e correta).
        ok = False
        try:
            await _consume(
                self.client.get_chat_history(
                    chat.id, limit=limit, message_thread_id=tid
                )
            )
            ok = True
        except TypeError:
            # Versão do Pyrogram sem o parâmetro message_thread_id.
            ok = False
        except Exception:  # noqa: BLE001
            log.exception("Falha ao ler histórico do tópico %s", tid)
            ok = False

        # Estratégia 2 (fallback): get_discussion_replies do tópico raiz.
        if not ok:
            try:
                await _consume(
                    self.client.get_discussion_replies(chat.id, tid, limit=limit)
                )
                ok = True
            except Exception:  # noqa: BLE001
                ok = False

        topic_videos.reverse()
        for idx, video in enumerate(topic_videos):
            video["sort_order"] = idx
        return topic_videos, best_summary, scanned

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
        for idx, video in enumerate(videos):
            video["sort_order"] = idx
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
            "file_id": getattr(media, "file_id", None),
            "file_unique_id": getattr(media, "file_unique_id", None),
            "telegram_topic_id": str(topic_id) if topic_id else "general",
        }

    # --------------------------------------------------------------- streaming
    async def prepare_stream(self, video: dict[str, Any]) -> dict[str, Any]:
        if not self.client:
            raise RuntimeError("Entre no Telegram primeiro.")
        await self._ensure_stream_server()
        token = uuid.uuid4().hex
        filename = safe_filename(video.get("file_name") or video.get("title") or "video.mp4")
        chat_username = (video.get("chat_username") or "").strip().lstrip("@") or None
        if not chat_username and self.db and video.get("course_id"):
            try:
                course = self.db.get_course(int(video.get("course_id")))
                chat_username = (getattr(course, "username", None) or "").strip().lstrip("@") or None
            except Exception:  # noqa: BLE001
                chat_username = None
        cache_path = self.stream_cache_dir / f"{token}.part"
        session = StreamSession(
            token=token,
            client=self.client,
            chat_id=video["chat_id"],
            message_id=int(video["message_id"]),
            size=int(video.get("size") or 0),
            cache_path=cache_path,
            mime_type=video.get("mime_type"),
            throttle_kbps=self._current_throttle_kbps(),
            max_retries=self._current_max_retries(),
            file_id=video.get("file_id"),
            chat_username=chat_username,
        )
        self.sessions[token] = session
        # Partida rápida: já dispara o download do início + cauda (moov).
        try:
            session.prefetch_start()
        except Exception:  # noqa: BLE001
            log.exception("Falha ao iniciar pré-busca do stream %s", token)

        # ---- Cache de moov: boot instantâneo na 2ª vez --------------------
        cached_moov = None
        if self.db:
            try:
                cached_moov = self.db.get_moov_cache(
                    str(video["chat_id"]), int(video["message_id"])
                )
            except Exception:  # noqa: BLE001
                cached_moov = None
        if cached_moov and cached_moov.get("moov_size"):
            # Já sabemos onde está o moov: pré-aquece direto, sem redescobrir.
            session.moov_info = {
                "found": True,
                "moov_offset": int(cached_moov.get("moov_offset") or 0),
                "moov_size": int(cached_moov.get("moov_size") or 0),
                "located": int(cached_moov.get("located") or 0),
            }
            try:
                session._prewarm_range(
                    int(cached_moov.get("moov_offset") or 0),
                    int(cached_moov.get("moov_size") or 0),
                )
            except Exception:  # noqa: BLE001
                pass
        else:
            # Descobre o moov em 2º plano e grava no cache para a próxima vez.
            asyncio.ensure_future(self._discover_and_cache_moov(token, video))

        # FASTSTART VIRTUAL: monta o cabeçalho ftyp+moov o quanto antes, em 2º
        # plano. Assim, quando o player fizer o primeiro Range, o header já está
        # pronto e a partida é quase instantânea.
        asyncio.ensure_future(self._build_faststart_bg(token))

        self.session_meta[token] = {
            "title": video.get("title") or filename,
            "start_position_ms": int(video.get("start_position_ms") or 0),
            "chat_id": str(video["chat_id"]),
            "message_id": int(video["message_id"]),
            "width": video.get("width"),
            "height": video.get("height"),
            "duration": video.get("duration"),
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

    async def _build_faststart_bg(self, token: str) -> None:
        """Tenta montar o cabeçalho faststart em 2º plano (best-effort)."""
        session = self.sessions.get(token)
        if not session:
            return
        try:
            await session.ensure_faststart()
        except Exception:  # noqa: BLE001
            log.exception("faststart em 2º plano falhou para %s", token)

    # ------------------------------------------------------- warm-up (pré-busca)
    async def warm_up_video(self, video: dict[str, Any]) -> dict[str, Any]:
        """Pré-aquece o INÍCIO de uma aula ao ser SELECIONADA (antes do clique).

        Descobre o `moov`, monta o cabeçalho faststart e baixa os primeiros
        ~512 KiB do `mdat` numa sessão efêmera. O resultado fica em `moov_cache`,
        de modo que, ao clicar "Assistir", a partida já está quente.

        É leve e tolerante a cancelamento: se o usuário trocar de aula, o app
        simplesmente não chama de novo (debounce no lado da UI). A sessão
        efêmera é liberada ao final.
        """
        if not self.client:
            return {"warmed": False}
        chat_id = video.get("chat_id")
        message_id = int(video.get("message_id") or 0)
        chat_username = (video.get("chat_username") or "").strip().lstrip("@") or None
        if not chat_username and self.db and video.get("course_id"):
            try:
                course = self.db.get_course(int(video.get("course_id")))
                chat_username = (getattr(course, "username", None) or "").strip().lstrip("@") or None
            except Exception:  # noqa: BLE001
                chat_username = None
        size = int(video.get("size") or 0)
        if not chat_id or not message_id or not size:
            return {"warmed": False}
        await self._ensure_stream_server()
        token = "warm_" + uuid.uuid4().hex
        cache_path = self.stream_cache_dir / f"{token}.part"
        session = StreamSession(
            token=token,
            client=self.client,
            chat_id=chat_id,
            message_id=message_id,
            size=size,
            cache_path=cache_path,
            mime_type=video.get("mime_type"),
            throttle_kbps=0,  # warm-up sempre na velocidade máxima
            max_retries=1,
            file_id=video.get("file_id"),
            chat_username=chat_username,
        )
        try:
            # Reaproveita o moov_cache para não redescobrir.
            cached_moov = None
            if self.db:
                try:
                    cached_moov = self.db.get_moov_cache(str(chat_id), message_id)
                except Exception:  # noqa: BLE001
                    cached_moov = None
            if cached_moov and cached_moov.get("moov_size"):
                session.moov_info = {
                    "found": True,
                    "moov_offset": int(cached_moov.get("moov_offset") or 0),
                    "moov_size": int(cached_moov.get("moov_size") or 0),
                    "located": int(cached_moov.get("located") or 0),
                }
            session.prefetch_start()
            await session.ensure_faststart()
            # Persiste a descoberta para o boot instantâneo na hora de assistir.
            info = session.moov_info or {}
            if info.get("found") and self.db:
                try:
                    self.db.set_moov_cache(
                        str(chat_id), message_id,
                        file_size=size,
                        moov_offset=int(info.get("moov_offset") or 0),
                        moov_size=int(info.get("moov_size") or 0),
                        located=int(info.get("located") or 0),
                        duration_ms=(int(video["duration"]) * 1000)
                        if video.get("duration") else None,
                        width=video.get("width"),
                        height=video.get("height"),
                    )
                except Exception:  # noqa: BLE001
                    pass
            # Garante os primeiros ~512 KiB do mdat baixados.
            try:
                await asyncio.wait_for(
                    session._wait_for_bytes(0, FIRST_CHUNK_SIZE), timeout=20.0
                )
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                pass
            return {"warmed": True, "faststart": session.faststart_active}
        except Exception:  # noqa: BLE001
            log.exception("Falha no warm-up de %s/%s", chat_id, message_id)
            return {"warmed": False}
        finally:
            # Sessão efêmera: o cache temporário some; o ganho fica no moov_cache.
            try:
                await session.close()
            except Exception:  # noqa: BLE001
                pass

    async def _discover_and_cache_moov(self, token: str, video: dict[str, Any]) -> None:
        """Descobre o átomo `moov` e persiste no cache SQLite (2º plano)."""
        session = self.sessions.get(token)
        if not session:
            return
        try:
            info = await session.discover_moov()
        except Exception:  # noqa: BLE001
            log.exception("Falha na descoberta do moov para %s", token)
            return
        if not info.get("found") or not self.db:
            return
        try:
            self.db.set_moov_cache(
                str(video["chat_id"]),
                int(video["message_id"]),
                file_size=int(video.get("size") or 0) or None,
                moov_offset=int(info.get("moov_offset") or 0),
                moov_size=int(info.get("moov_size") or 0),
                located=int(info.get("located") or 0),
                duration_ms=(int(video["duration"]) * 1000) if video.get("duration") else None,
                width=video.get("width"),
                height=video.get("height"),
            )
        except Exception:  # noqa: BLE001
            log.exception("Falha ao gravar moov_cache para %s", token)

    # =========================================================== Aba "Arquivos"
    # API de navegação de MÍDIA genérica (vídeo/PDF/imagem/zip/áudio) inspirada
    # no caamer20/Telegram-Drive: lista, baixa (com progresso), envia (com
    # progresso) e gera link t.me. Usada por `files_tab.FilesTab`.
    @staticmethod
    def _classify_media(message) -> dict[str, Any] | None:
        """Mapeia uma mensagem Pyrogram para um item de mídia genérico.

        Retorna ``None`` quando a mensagem não carrega mídia suportada.
        Categorias: ``video``, ``image``, ``pdf``, ``zip``, ``audio``, ``file``.
        """
        media = None
        kind = "file"
        file_name = ""
        mime_type = ""
        size = None
        duration = None
        width = height = None
        has_thumb = False

        if getattr(message, "video", None):
            media = message.video
            kind = "video"
        elif getattr(message, "animation", None):
            media = message.animation
            kind = "video"
        elif getattr(message, "photo", None):
            media = message.photo
            kind = "image"
        elif getattr(message, "audio", None):
            media = message.audio
            kind = "audio"
        elif getattr(message, "voice", None):
            media = message.voice
            kind = "audio"
        elif getattr(message, "document", None):
            media = message.document
            mime_type = (getattr(media, "mime_type", None) or "").lower()
            file_name = (getattr(media, "file_name", None) or "")
            lower = file_name.lower()
            if mime_type.startswith("video/") or lower.endswith(
                (".mp4", ".mkv", ".mov", ".avi", ".webm")
            ):
                kind = "video"
            elif mime_type.startswith("image/") or lower.endswith(
                (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")
            ):
                kind = "image"
            elif mime_type == "application/pdf" or lower.endswith(".pdf"):
                kind = "pdf"
            elif lower.endswith((".zip", ".rar", ".7z", ".tar", ".gz")) or "zip" in mime_type:
                kind = "zip"
            elif mime_type.startswith("audio/") or lower.endswith(
                (".mp3", ".m4a", ".ogg", ".wav", ".flac")
            ):
                kind = "audio"
            else:
                kind = "file"

        if media is None:
            return None

        if not file_name:
            file_name = getattr(media, "file_name", None) or ""
        if not mime_type:
            mime_type = (getattr(media, "mime_type", None) or "")
        size = getattr(media, "file_size", None)
        duration = getattr(media, "duration", None)
        width = getattr(media, "width", None)
        height = getattr(media, "height", None)
        has_thumb = bool(getattr(media, "thumbs", None)) or kind == "image"

        caption = getattr(message, "caption", None) or ""
        if not file_name:
            base = caption.splitlines()[0] if caption else ""
            ext = {
                "video": ".mp4", "image": ".jpg", "pdf": ".pdf",
                "zip": ".zip", "audio": ".mp3",
            }.get(kind, "")
            file_name = safe_filename(base or f"{kind}_{message.id}") + (
                ext if not Path(base or "").suffix else ""
            )

        date = getattr(message, "date", None)
        return {
            "message_id": int(message.id),
            "kind": kind,
            "file_name": file_name,
            "title": safe_filename(file_name, fallback=f"{kind} {message.id}"),
            "mime_type": mime_type or None,
            "size": size,
            "duration": duration,
            "width": width,
            "height": height,
            "has_thumb": has_thumb,
            "caption": caption,
            "date": date.isoformat() if date else None,
        }

    async def list_chat_media(
        self,
        chat_id: str | int,
        limit: int = 300,
        kinds: tuple[str, ...] | None = None,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        """Lista TODA a mídia de um chat (mais recentes primeiro).

        ``kinds`` filtra por categoria; ``query`` filtra por nome/legenda.
        """
        if not self.client:
            raise RuntimeError("Entre no Telegram primeiro.")
        cid = int(chat_id) if str(chat_id).lstrip("-").isdigit() else chat_id
        q = (query or "").strip().lower()
        items: list[dict[str, Any]] = []
        async for message in self.client.get_chat_history(cid, limit=limit):
            entry = self._classify_media(message)
            if entry is None:
                continue
            if kinds and entry["kind"] not in kinds:
                continue
            if q and q not in (
                f"{entry['file_name']} {entry['caption']}".lower()
            ):
                continue
            entry["chat_id"] = str(chat_id)
            items.append(entry)
        return items

    async def ensure_media_thumbnail(
        self, chat_id: str | int, message_id: int
    ) -> str | None:
        """Baixa/cacheia a miniatura de QUALQUER mídia (não só vídeo)."""
        if not self.client:
            return None
        dest = self.thumb_path(chat_id, message_id)
        if dest.exists() and dest.stat().st_size > 0:
            try:
                dest.touch()
            except Exception:  # noqa: BLE001
                pass
            return str(dest)
        try:
            cid = int(chat_id) if str(chat_id).lstrip("-").isdigit() else chat_id
            message = await self.client.get_messages(cid, message_id)
            media = (
                getattr(message, "video", None)
                or getattr(message, "photo", None)
                or getattr(message, "animation", None)
                or getattr(message, "document", None)
                or getattr(message, "audio", None)
            )
            if media is None:
                return None
            # Fotos: o próprio file_id serve como miniatura compacta.
            if getattr(message, "photo", None):
                await self.client.download_media(message.photo, file_name=str(dest))
            else:
                thumbs = getattr(media, "thumbs", None)
                if not thumbs:
                    return None
                best = max(thumbs, key=lambda t: getattr(t, "file_size", 0) or 0)
                await self.client.download_media(best, file_name=str(dest))
            self._prune_thumb_cache(keep=dest)
            return str(dest) if dest.exists() else None
        except Exception:  # noqa: BLE001
            log.exception("Falha ao baixar miniatura de %s/%s", chat_id, message_id)
            return None

    async def download_media_file(
        self,
        chat_id: str | int,
        message_id: int,
        dest_path: str,
        progress_cb: Any = None,
    ) -> dict[str, Any]:
        """Baixa a mídia de uma mensagem para ``dest_path`` (com progresso).

        ``progress_cb(current, total)`` é chamado pelo Pyrogram durante o
        download. Retorna ``{"ok": True, "path": ...}`` ou ``{"ok": False,
        "error": ...}``.
        """
        if not self.client:
            return {"ok": False, "error": "Entre no Telegram primeiro."}
        try:
            cid = int(chat_id) if str(chat_id).lstrip("-").isdigit() else chat_id
            message = await self.client.get_messages(cid, message_id)
            dest = Path(dest_path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            saved = await self.client.download_media(
                message,
                file_name=str(dest),
                progress=progress_cb,
            )
            return {"ok": True, "path": str(saved or dest)}
        except Exception as exc:  # noqa: BLE001
            log.exception("Falha ao baixar mídia %s/%s", chat_id, message_id)
            return {"ok": False, "error": str(exc)}

    async def upload_media_file(
        self,
        chat_id: str | int,
        file_path: str,
        caption: str | None = None,
        progress_cb: Any = None,
    ) -> dict[str, Any]:
        """Envia um arquivo do disco para o chat (com progresso).

        Detecta o tipo (vídeo/foto/áudio/documento) pela extensão. Trata erros
        de permissão (CHAT_WRITE_FORBIDDEN etc.) com mensagem amigável.
        """
        if not self.client:
            return {"ok": False, "error": "Entre no Telegram primeiro."}
        path = Path(file_path)
        if not path.exists():
            return {"ok": False, "error": "Arquivo não encontrado."}
        cid = int(chat_id) if str(chat_id).lstrip("-").isdigit() else chat_id
        suffix = path.suffix.lower()
        try:
            if suffix in (".mp4", ".mkv", ".mov", ".avi", ".webm"):
                msg = await self.client.send_video(
                    cid, str(path), caption=caption or None, progress=progress_cb
                )
            elif suffix in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"):
                msg = await self.client.send_photo(
                    cid, str(path), caption=caption or None, progress=progress_cb
                )
            elif suffix in (".mp3", ".m4a", ".ogg", ".wav", ".flac"):
                msg = await self.client.send_audio(
                    cid, str(path), caption=caption or None, progress=progress_cb
                )
            else:
                msg = await self.client.send_document(
                    cid, str(path), caption=caption or None, progress=progress_cb
                )
            return {"ok": True, "message_id": int(getattr(msg, "id", 0))}
        except Exception as exc:  # noqa: BLE001
            text = str(exc)
            if "FORBIDDEN" in text.upper() or "permission" in text.lower():
                text = (
                    "Sem permissão para enviar mensagens neste chat "
                    "(canal somente leitura ou restrição de administrador)."
                )
            log.exception("Falha ao enviar arquivo para %s", chat_id)
            return {"ok": False, "error": text}

    # ------------------------------------------------------------- miniaturas
    # Limites do cache de miniaturas (poda LRU). Ideia portada de `preview.rs`.
    THUMB_CACHE_MAX_FILES = 400
    THUMB_CACHE_MAX_BYTES = 256 * 1024 * 1024  # 256 MB

    def thumb_path(self, chat_id: str | int, message_id: int) -> Path:
        return self.thumbs_dir / f"{str(chat_id).lstrip('-')}_{int(message_id)}.jpg"

    def cached_thumb(self, chat_id: str | int, message_id: int) -> str | None:
        """Acesso síncrono (para a UI): retorna a miniatura já em cache ou None.

        Não dispara download — apenas verifica o disco. A UI pode usar isto sem
        bloquear, e agendar `ensure_thumbnail` em 2º plano quando faltar.
        """
        try:
            p = self.thumb_path(chat_id, message_id)
            if p.exists() and p.stat().st_size > 0:
                return str(p)
        except Exception:  # noqa: BLE001
            pass
        return None

    async def fetch_video_metadata(self, video: dict[str, Any]) -> dict[str, Any] | None:
        """Pré-busca metadados (duração/resolução) de uma aula em 2º plano.

        Ideia portada do cache de metadados por arquivo do projeto de
        referência. Consulta a mensagem do Telegram, extrai width/height/
        duration do objeto de vídeo e persiste em `moov_cache` para reuso.
        """
        if not self.client:
            return None
        chat_id = video.get("chat_id")
        message_id = int(video.get("message_id") or 0)
        if not chat_id or not message_id:
            return None
        try:
            cid = int(chat_id) if str(chat_id).lstrip("-").isdigit() else chat_id
            message = await self.client.get_messages(cid, message_id)
            media = getattr(message, "video", None) or getattr(message, "document", None)
            if not media:
                return None
            meta = {
                "width": getattr(media, "width", None),
                "height": getattr(media, "height", None),
                "duration": getattr(media, "duration", None),
                "file_size": getattr(media, "file_size", None),
            }
            if self.db and (meta["width"] or meta["height"] or meta["duration"]):
                try:
                    self.db.set_moov_cache(
                        str(chat_id),
                        message_id,
                        file_size=meta["file_size"],
                        duration_ms=(int(meta["duration"]) * 1000) if meta["duration"] else None,
                        width=meta["width"],
                        height=meta["height"],
                    )
                    self.db.set_video_dimensions(
                        str(chat_id),
                        message_id,
                        width=meta["width"],
                        height=meta["height"],
                        duration=meta["duration"],
                    )
                except Exception:  # noqa: BLE001
                    log.exception("Falha ao gravar metadados em moov_cache")
            return meta
        except Exception:  # noqa: BLE001
            log.exception("Falha ao buscar metadados de %s/%s", chat_id, message_id)
            return None

    async def ensure_thumbnail(self, video: dict[str, Any]) -> str | None:
        """Baixa (e cacheia) a miniatura de uma aula. Retorna o caminho local.

        O Pyrogram expõe `message.video.thumbs`; baixamos o maior thumbnail e
        gravamos em `cache/thumbs/{chat}_{msg}.jpg`. Faz poda LRU automática.
        """
        if not self.client:
            return None
        chat_id = video.get("chat_id")
        message_id = int(video.get("message_id") or 0)
        if not chat_id or not message_id:
            return None
        dest = self.thumb_path(chat_id, message_id)
        if dest.exists() and dest.stat().st_size > 0:
            # Toca o arquivo para refletir o uso recente (LRU).
            try:
                dest.touch()
            except Exception:  # noqa: BLE001
                pass
            return str(dest)
        try:
            cid = int(chat_id) if str(chat_id).lstrip("-").isdigit() else chat_id
            message = await self.client.get_messages(cid, message_id)
            media = getattr(message, "video", None) or getattr(message, "document", None)
            thumbs = getattr(media, "thumbs", None) if media else None
            if not thumbs:
                return None
            # Maior thumbnail disponível.
            best = max(thumbs, key=lambda t: getattr(t, "file_size", 0) or 0)
            # O Pyrogram aceita o objeto thumb (ou seu file_id) em download_media.
            await self.client.download_media(best, file_name=str(dest))
            self._prune_thumb_cache(keep=dest)
            return str(dest) if dest.exists() else None
        except Exception:  # noqa: BLE001
            log.exception("Falha ao baixar miniatura de %s/%s", chat_id, message_id)
            return None

    def _prune_thumb_cache(self, keep: Path | None = None) -> None:
        """Poda o cache de miniaturas (LRU), preservando o arquivo `keep`."""
        try:
            files = [p for p in self.thumbs_dir.glob("*.jpg") if p.is_file()]
        except Exception:  # noqa: BLE001
            return
        files.sort(key=lambda p: p.stat().st_mtime)  # mais antigos primeiro
        total_bytes = sum(p.stat().st_size for p in files)
        while files and (
            len(files) > self.THUMB_CACHE_MAX_FILES
            or total_bytes > self.THUMB_CACHE_MAX_BYTES
        ):
            victim = files.pop(0)
            if keep and victim == keep:
                continue
            try:
                total_bytes -= victim.stat().st_size
                victim.unlink()
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _private_tme_id(chat_id: str | int | None) -> str | None:
        """Converte -1001234567890 -> 1234567890 para links t.me/c."""
        if chat_id is None:
            return None
        raw = str(chat_id).strip()
        if not raw:
            return None
        raw = raw.lstrip("+")
        if raw.startswith("-100") and raw[4:].isdigit():
            return raw[4:]
        if raw.startswith("100") and raw[3:].isdigit():
            return raw[3:]
        if raw.startswith("-") and raw[1:].isdigit():
            # Para grupos/canais sem prefixo -100, usa o valor absoluto como último fallback.
            return raw[1:]
        return raw if raw.isdigit() else None

    def telegram_message_urls(
        self, username: str | None, chat_id: str | int | None, message_id: int
    ) -> tuple[str | None, str | None]:
        """Retorna (tg_url, web_url) para abrir a mensagem original.

        Público:  tg://resolve?domain=<username>&post=<id>  e https://t.me/<username>/<id>
        Privado:  tg://privatepost?channel=<id>&post=<id>   e https://t.me/c/<id>/<id>
        """
        mid = int(message_id)
        uname = str(username or "").lstrip("@").strip()
        if uname:
            # O Telegram não documenta deep link que abra diretamente o visualizador
            # de mídia. O parâmetro ?single melhora o foco na mensagem no web/t.me,
            # mas no app desktop ainda pode exigir um clique no vídeo.
            return f"tg://resolve?domain={uname}&post={mid}", f"https://t.me/{uname}/{mid}?single"
        private_id = self._private_tme_id(chat_id)
        if private_id:
            return (
                f"tg://privatepost?channel={private_id}&post={mid}",
                f"https://t.me/c/{private_id}/{mid}?single",
            )
        return None, None

    def telegram_message_link(
        self, username: str | None, message_id: int, chat_id: str | int | None = None
    ) -> str | None:
        """Gera link t.me da mensagem. Funciona em público e, quando possível, em privado."""
        _app_url, web_url = self.telegram_message_urls(username, chat_id, message_id)
        return web_url

    async def release_stream(self, token: str, delete_file: bool = True) -> dict[str, Any]:
        self.session_meta.pop(token, None)
        session = self.sessions.pop(token, None)
        if not session:
            return {"released": False}
        await session.close()
        return {"released": True}

    async def release_stream_later(
        self, token: str, ttl_seconds: int = 7200, delete_file: bool = True
    ) -> dict[str, Any]:
        """Mantém um stream vivo por TTL para VLC externo/URL copiada.

        O VLC abre a URL local fora do ciclo de vida da janela do player. Se o
        player interno libera o token no closeEvent, o VLC recebe 404/stream
        expirado. Esta rotina agenda a liberação sem travar a UI.
        """
        if token not in self.sessions:
            return {"scheduled": False}
        ttl = max(60, int(ttl_seconds or 7200))
        meta = self.session_meta.setdefault(token, {})
        meta["external_until"] = time.time() + ttl

        async def _later() -> None:
            await asyncio.sleep(ttl)
            # Não derruba se a sessão foi reutilizada/acessada muito recentemente.
            session = self.sessions.get(token)
            if not session:
                return
            until = float(self.session_meta.get(token, {}).get("external_until") or 0)
            if until and time.time() < until:
                return
            await self.release_stream(token, delete_file=delete_file)

        asyncio.ensure_future(_later())
        log.info("Stream %s mantido vivo por %ss para player externo", token[:8], ttl)
        return {"scheduled": True, "ttl_seconds": ttl}

    async def _ensure_stream_server(self) -> None:
        if self.runner and self.port:
            return
        app = web.Application(client_max_size=1024**3)
        app.router.add_get("/stream/{token}/{filename:.*}", self._handle_stream, allow_head=True)
        app.router.add_get("/player/{token}", self._handle_player)
        app.router.add_get("/buffer/{token}", self._handle_buffer)
        app.router.add_get("/bandwidth/{token}", self._handle_bandwidth)
        app.router.add_get("/quality/{token}", self._handle_quality)
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

    async def _handle_buffer(self, request: web.Request) -> web.Response:
        """Informa o progresso de buffer de partida (overlay 'Carregando aula…')."""
        token = request.match_info.get("token") or ""
        session = self.sessions.get(token)
        if not session:
            return web.json_response({"ratio": 0.0, "ready": False, "found": False})
        try:
            ratio = float(session.buffer_ratio())
        except Exception:  # noqa: BLE001
            ratio = 0.0
        return web.json_response(
            {
                "ratio": ratio,
                "ready": ratio >= 0.999,
                "found": True,
                "error": session.error,
            },
            headers={
                "Cache-Control": "no-store",
                "Access-Control-Allow-Origin": "*",
            },
        )

    def buffer_ratio(self, token: str) -> float:
        """Acesso síncrono ao progresso de buffer (usado pelo overlay do Qt)."""
        session = self.sessions.get(token)
        if not session:
            return 0.0
        try:
            return float(session.buffer_ratio())
        except Exception:  # noqa: BLE001
            return 0.0

    def measured_kbps(self, token: str) -> float:
        """Acesso síncrono à banda medida (usado pelo widget/overlay de debug)."""
        session = self.sessions.get(token)
        if not session:
            return 0.0
        try:
            return float(session.measured_kbps())
        except Exception:  # noqa: BLE001
            return 0.0

    def total_measured_kbps(self) -> float:
        """Banda agregada (kbps) de TODAS as sessões ativas (widget da barra)."""
        total = 0.0
        for session in list(self.sessions.values()):
            try:
                total += float(session.measured_kbps())
            except Exception:  # noqa: BLE001
                pass
        return total

    def active_sessions(self) -> int:
        """Número de sessões de streaming ativas."""
        return len(self.sessions)

    def session_info(self, token: str) -> dict[str, Any]:
        """Resumo síncrono da sessão (banda, throttle, moov) para o overlay debug."""
        session = self.sessions.get(token)
        meta = self.session_meta.get(token, {})
        if not session:
            return {}
        return {
            "kbps": session.measured_kbps(),
            "throttle_kbps": session.throttle_kbps,
            "quality": self.streaming_quality,
            "adaptive": self.adaptive_mode,
            "buffer_ratio": session.buffer_ratio(),
            "moov": session.moov_info or {},
            "size": session.size,
            "width": meta.get("width"),
            "height": meta.get("height"),
        }

    async def _handle_bandwidth(self, request: web.Request) -> web.Response:
        token = request.match_info.get("token") or ""
        session = self.sessions.get(token)
        kbps = float(session.measured_kbps()) if session else 0.0
        return web.json_response(
            {"kbps": kbps, "found": bool(session)},
            headers={"Cache-Control": "no-store", "Access-Control-Allow-Origin": "*"},
        )

    async def _handle_quality(self, request: web.Request) -> web.Response:
        """Aplica qualidade/adaptativo a uma sessão (e mede a banda para o auto)."""
        token = request.match_info.get("token") or ""
        session = self.sessions.get(token)
        if not session:
            return web.json_response({"ok": False}, status=404)
        quality = request.query.get("quality")
        adaptive = request.query.get("adaptive")
        if adaptive is not None:
            self.adaptive_mode = adaptive in ("1", "true", "yes")
        if self.adaptive_mode:
            chosen = adaptive_quality(session.measured_kbps())
            session.set_throttle_kbps(throttle_for(chosen))
            return web.json_response(
                {"ok": True, "quality": chosen, "adaptive": True,
                 "kbps": session.measured_kbps()},
                headers={"Access-Control-Allow-Origin": "*"},
            )
        if quality:
            self.streaming_quality = quality.lower()
            session.set_throttle_kbps(throttle_for(self.streaming_quality))
        return web.json_response(
            {"ok": True, "quality": self.streaming_quality, "adaptive": False,
             "kbps": session.measured_kbps()},
            headers={"Access-Control-Allow-Origin": "*"},
        )

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

        # FASTSTART VIRTUAL (v6.5): o cabeçalho ftyp+moov é montado em 2º plano
        # (ver _build_faststart_bg em prepare_stream). Aqui NÃO bloqueamos a
        # resposta esperando o faststart ficar pronto — para arquivos grandes a
        # descoberta do moov (baixar a cauda do Telegram) pode levar dezenas de
        # segundos e deixava o player preso na tela de "carregando" / erro.
        #
        # Estratégia:
        #   • Se o faststart JÁ está ativo, usamos (partida instantânea).
        #   • Senão, damos uma janela CURTA (best-effort) para ele ficar pronto;
        #     se não der a tempo, servimos o arquivo ORIGINAL imediatamente
        #     (modo legado). Os Ranges seguintes do navegador já pegam o
        #     faststart assim que o cabeçalho terminar de montar em 2º plano.
        if not session.faststart_active:
            try:
                await asyncio.wait_for(session.ensure_faststart(), timeout=3.0)
            except asyncio.TimeoutError:
                # Faststart ainda montando: segue no modo legado sem travar.
                log.info(
                    "faststart ainda não pronto (token %s); servindo original",
                    token,
                )
            except Exception:  # noqa: BLE001
                log.exception("faststart: ensure falhou; seguindo no modo legado")

        # SNAPSHOT: fixa a decisão de faststart para ESTA resposta inteira. Se o
        # faststart ficar ativo em 2º plano no meio do streaming, não trocamos o
        # mapeamento de bytes no meio do caminho (evita vídeo corrompido). O
        # tamanho total é o MESMO nos dois modos (faststart só reordena bytes).
        use_faststart = bool(session.faststart_active and session.faststart_header is not None)
        total = session.logical_size
        range_header = request.headers.get("Range")
        if range_header:
            start, end = self._parse_range(range_header, total)
        else:
            # Sem Range: HTTP correto é 200 com o tamanho total. A entrega continua
            # em streaming e sob demanda; não baixamos tudo antes de responder.
            start = 0
            end = (total - 1) if total else (INITIAL_RANGE_CHUNK - 1)

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
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Connection": "keep-alive",
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
            # read_logical_range serve o header faststart (se ativo) e mapeia o
            # restante para o mdat físico; sem faststart, delega a read_range.
            async for data in session.read_logical_range(start, end, use_faststart=use_faststart):
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

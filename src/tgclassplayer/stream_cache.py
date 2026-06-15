"""Cache de streaming sob demanda, por blocos.

A grande diferença para a versão antiga: em vez de baixar o vídeo inteiro de
forma sequencial (o que travava ao dar seek para frente), aqui o vídeo é
dividido em blocos fixos. Só os blocos que o player realmente pede (via Range
HTTP) são baixados do Telegram, e o player pode pular para qualquer ponto do
vídeo que o bloco correspondente é buscado na hora.

Características:
- Arquivo de cache esparso em disco (um por aula em reprodução), apagado ao
  fechar o player. O vídeo NUNCA fica armazenado permanentemente.
- Download de blocos sob demanda, com um pequeno "read-ahead" para manter o
  buffer à frente da reprodução sem travar.
- Múltiplos blocos podem ser baixados em paralelo de forma controlada.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from pyrogram import Client

log = logging.getLogger(__name__)

# Tamanho de cada bloco lógico do cache. 2 MiB equilibra latência de seek com
# eficiência de rede. O Pyrogram entrega ~1 MiB por chunk interno.
BLOCK_SIZE = 2 * 1024 * 1024
# Quantos blocos manter à frente da posição atual (read-ahead).
READ_AHEAD_BLOCKS = 4


class StreamSession:
    """Gerencia o download por blocos de uma única aula em reprodução."""

    def __init__(
        self,
        token: str,
        client: "Client",
        chat_id: str | int,
        message_id: int,
        size: int,
        cache_path: Path,
        mime_type: str | None = None,
    ) -> None:
        self.token = token
        self.client = client
        self.chat_id = chat_id
        self.message_id = int(message_id)
        self.size = int(size or 0)
        self.cache_path = cache_path
        self.mime_type = mime_type or "video/mp4"

        self.total_blocks = max(1, (self.size + BLOCK_SIZE - 1) // BLOCK_SIZE) if self.size else 0
        self._block_ready: dict[int, asyncio.Event] = {}
        self._block_downloading: set[int] = set()
        self._message = None
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(3)  # downloads simultâneos
        self.last_access = time.time()
        self.closed = False
        self.error: str | None = None

        # Pré-aloca arquivo esparso para permitir seek/escrita em qualquer offset.
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.cache_path, "wb") as fh:
            if self.size:
                fh.truncate(self.size)

    def _event_for(self, block: int) -> asyncio.Event:
        ev = self._block_ready.get(block)
        if ev is None:
            ev = asyncio.Event()
            self._block_ready[block] = ev
        return ev

    async def _get_message(self):
        if self._message is None:
            chat_id = self.chat_id
            if str(chat_id).lstrip("-").isdigit():
                chat_id = int(chat_id)
            self._message = await self.client.get_messages(chat_id, self.message_id)
        return self._message

    async def _download_block(self, block: int) -> None:
        """Baixa um bloco específico do Telegram para o offset correto no cache."""
        if self.closed or block in self._block_downloading:
            return
        ev = self._event_for(block)
        if ev.is_set():
            return
        self._block_downloading.add(block)
        async with self._semaphore:
            if self.closed:
                return
            block_offset = block * BLOCK_SIZE
            try:
                message = await self._get_message()
                # O Pyrogram trabalha com offset/limit em unidades de 1 MiB.
                one_mib = 1024 * 1024
                pyro_offset = block_offset // one_mib
                # Quantos chunks de 1 MiB cobrem este bloco.
                blocks_in_mib = max(1, BLOCK_SIZE // one_mib)

                buffer = bytearray()
                got = 0
                async for chunk in self.client.stream_media(
                    message, offset=pyro_offset, limit=blocks_in_mib
                ):
                    if self.closed:
                        return
                    if not chunk:
                        continue
                    buffer.extend(chunk)
                    got += len(chunk)
                    if got >= BLOCK_SIZE:
                        break

                data = bytes(buffer[:BLOCK_SIZE])
                # Escreve no offset exato do arquivo esparso.
                await asyncio.to_thread(self._write_at, block_offset, data)
                ev.set()
                self.last_access = time.time()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.error = str(exc)
                log.exception("Falha ao baixar bloco %s do token %s", block, self.token)
                ev.set()  # libera quem espera, com erro registrado
            finally:
                self._block_downloading.discard(block)

    def _write_at(self, offset: int, data: bytes) -> None:
        with open(self.cache_path, "r+b") as fh:
            fh.seek(offset)
            fh.write(data)

    def _read_at(self, offset: int, length: int) -> bytes:
        with open(self.cache_path, "rb") as fh:
            fh.seek(offset)
            return fh.read(length)

    async def ensure_block(self, block: int, timeout: float = 90.0) -> bool:
        """Garante que um bloco esteja disponível, baixando se necessário."""
        if self.size and block >= self.total_blocks:
            return False
        ev = self._event_for(block)
        if not ev.is_set() and block not in self._block_downloading:
            asyncio.create_task(self._download_block(block))
        try:
            await asyncio.wait_for(ev.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return False
        return ev.is_set() and self.error is None

    def _schedule_read_ahead(self, current_block: int) -> None:
        """Agenda o download dos próximos blocos para evitar travar a reprodução."""
        for offset in range(1, READ_AHEAD_BLOCKS + 1):
            nxt = current_block + offset
            if self.size and nxt >= self.total_blocks:
                break
            ev = self._event_for(nxt)
            if not ev.is_set() and nxt not in self._block_downloading:
                asyncio.create_task(self._download_block(nxt))

    async def read_range(self, start: int, end: int):
        """Gerador assíncrono que entrega bytes do intervalo [start, end].

        Vai baixando bloco a bloco conforme necessário e faz read-ahead.
        """
        self.last_access = time.time()
        position = start
        while position <= end and not self.closed:
            block = position // BLOCK_SIZE
            ok = await self.ensure_block(block)
            if not ok:
                if self.error:
                    raise RuntimeError(self.error)
                return
            self._schedule_read_ahead(block)

            block_start = block * BLOCK_SIZE
            block_end = block_start + BLOCK_SIZE - 1
            chunk_end = min(end, block_end)
            length = chunk_end - position + 1
            data = await asyncio.to_thread(self._read_at, position, length)
            if not data:
                return
            yield data
            position += len(data)
            self.last_access = time.time()

    async def close(self) -> None:
        self.closed = True
        for ev in self._block_ready.values():
            ev.set()
        # Apaga o cache temporário: o vídeo não fica armazenado.
        try:
            await asyncio.to_thread(self._unlink)
        except Exception:  # noqa: BLE001
            log.exception("Não foi possível apagar o cache temporário de %s", self.token)

    def _unlink(self) -> None:
        if self.cache_path.exists():
            self.cache_path.unlink()

"""Cache de streaming sob demanda, por blocos — versão ACELERADA (v6.2).

A grande diferença para a versão antiga: em vez de baixar o vídeo inteiro de
forma sequencial (o que travava ao dar seek para frente), aqui o vídeo é
dividido em blocos fixos. Só os blocos que o player realmente pede (via Range
HTTP) são baixados do Telegram, e o player pode pular para qualquer ponto do
vídeo que o bloco correspondente é buscado na hora.

Otimizações de PARTIDA RÁPIDA (v6.2), para o player não ficar preso em
00:00 / 00:00 com tela preta:

1. **Pré-busca do `moov`**: ao preparar o stream, baixamos *imediatamente*
   o bloco 0 (início) **e** os 2 últimos blocos do arquivo. O átomo `moov`
   do MP4 (índice necessário para o player começar) costuma estar no fim
   quando o arquivo NÃO é "faststart"; pré-buscá-lo evita a viagem
   "fim -> início" que travava a partida.

2. **Primeiro byte rápido (yield parcial)**: `read_range` não espera mais o
   bloco inteiro de 2 MiB. Assim que ~256 KiB iniciais de um bloco chegam, os
   bytes já são liberados para o player. O download de um bloco grava em disco
   incrementalmente e marca quantos bytes já estão prontos.

3. **Mais paralelismo**: `Semaphore(6)` e `READ_AHEAD_BLOCKS=6`, com
   cancelamento de todos os downloads ao fechar a sessão.

4. **Progresso de buffer**: `buffer_ratio()` informa, de 0.0 a 1.0, quanto do
   bloco inicial (de partida) já foi baixado — usado pelo overlay
   "Carregando aula… NN%".

O arquivo de cache é esparso, fica em disco apenas durante a reprodução e é
apagado ao fechar o player: o vídeo NUNCA fica armazenado permanentemente.
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
READ_AHEAD_BLOCKS = 6
# Downloads simultâneos permitidos.
MAX_PARALLEL_DOWNLOADS = 6
# Quantos bytes mínimos liberar logo de cara, sem esperar o bloco inteiro.
FAST_FIRST_BYTES = 256 * 1024
# Quantos blocos do FINAL pré-buscar (onde costuma ficar o `moov`).
MOOV_TAIL_BLOCKS = 2


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

        self.total_blocks = (
            max(1, (self.size + BLOCK_SIZE - 1) // BLOCK_SIZE) if self.size else 0
        )
        # Evento "bloco totalmente pronto" (todos os bytes do bloco gravados).
        self._block_ready: dict[int, asyncio.Event] = {}
        # Evento "primeiros bytes do bloco disponíveis" (partida rápida).
        self._block_partial: dict[int, asyncio.Event] = {}
        # Quantos bytes já estão gravados (e válidos) em cada bloco.
        self._block_filled: dict[int, int] = {}
        self._block_downloading: set[int] = set()
        self._tasks: set[asyncio.Task] = set()
        self._message = None
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(MAX_PARALLEL_DOWNLOADS)
        self.last_access = time.time()
        self.closed = False
        self.error: str | None = None

        # Pré-aloca arquivo esparso para permitir seek/escrita em qualquer offset.
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.cache_path, "wb") as fh:
            if self.size:
                fh.truncate(self.size)

    # ----------------------------------------------------------------- eventos
    def _ready_event(self, block: int) -> asyncio.Event:
        ev = self._block_ready.get(block)
        if ev is None:
            ev = asyncio.Event()
            self._block_ready[block] = ev
        return ev

    def _partial_event(self, block: int) -> asyncio.Event:
        ev = self._block_partial.get(block)
        if ev is None:
            ev = asyncio.Event()
            self._block_partial[block] = ev
        return ev

    def _block_size_for(self, block: int) -> int:
        """Tamanho real (em bytes) deste bloco (o último pode ser menor)."""
        if not self.size:
            return BLOCK_SIZE
        start = block * BLOCK_SIZE
        return max(0, min(BLOCK_SIZE, self.size - start))

    # ------------------------------------------------------------ partida rápida
    def prefetch_start(self) -> None:
        """Dispara o download do bloco 0 e dos últimos blocos (onde fica o moov).

        Chamado logo após criar a sessão para que o player tenha o início do
        vídeo e o índice `moov` o quanto antes, eliminando a espera longa.
        """
        if self.closed:
            return
        self._spawn_download(0)
        # Read-ahead inicial dos primeiros blocos.
        for b in range(1, min(READ_AHEAD_BLOCKS, self.total_blocks or READ_AHEAD_BLOCKS)):
            self._spawn_download(b)
        # Pré-busca da cauda (moov de MP4 não-faststart).
        if self.total_blocks:
            for i in range(1, MOOV_TAIL_BLOCKS + 1):
                tail = self.total_blocks - i
                if tail > 0:
                    self._spawn_download(tail)

    def buffer_ratio(self) -> float:
        """Fração (0..1) do bloco de partida (bloco 0) já baixada.

        Usado pelo overlay "Carregando aula… NN%". Combina o quanto do bloco 0
        já chegou com o quanto da cauda (moov) já chegou, para refletir melhor
        o progresso real de "pronto para tocar".
        """
        if self.total_blocks == 0:
            return 0.0
        want = self._block_size_for(0) or 1
        head = min(1.0, self._block_filled.get(0, 0) / want)
        # Considera também a cauda (moov) — sem ela o MP4 não inicia.
        tail_ready = 1.0
        if self.total_blocks > MOOV_TAIL_BLOCKS:
            done = 0
            for i in range(1, MOOV_TAIL_BLOCKS + 1):
                tail = self.total_blocks - i
                if self._ready_event(tail).is_set():
                    done += 1
            tail_ready = done / MOOV_TAIL_BLOCKS
        return max(0.0, min(1.0, 0.6 * head + 0.4 * tail_ready))

    # --------------------------------------------------------------- mensagem TG
    async def _get_message(self):
        if self._message is None:
            chat_id = self.chat_id
            if str(chat_id).lstrip("-").isdigit():
                chat_id = int(chat_id)
            self._message = await self.client.get_messages(chat_id, self.message_id)
        return self._message

    # --------------------------------------------------------------- downloads
    def _spawn_download(self, block: int) -> None:
        """Cria a task de download de um bloco (com rastreio para cancelar)."""
        if self.closed:
            return
        if self.size and (block < 0 or block >= self.total_blocks):
            return
        ev = self._ready_event(block)
        if ev.is_set() or block in self._block_downloading:
            return
        task = asyncio.ensure_future(self._download_block(block))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _download_block(self, block: int) -> None:
        """Baixa um bloco, gravando incrementalmente (libera partida rápida)."""
        if self.closed or block in self._block_downloading:
            return
        ready = self._ready_event(block)
        partial = self._partial_event(block)
        if ready.is_set():
            return
        self._block_downloading.add(block)
        try:
            async with self._semaphore:
                if self.closed:
                    return
                block_offset = block * BLOCK_SIZE
                target = self._block_size_for(block) or BLOCK_SIZE
                message = await self._get_message()

                # O Pyrogram trabalha com offset/limit em unidades de 1 MiB.
                one_mib = 1024 * 1024
                pyro_offset = block_offset // one_mib
                blocks_in_mib = max(1, BLOCK_SIZE // one_mib)

                written = 0
                async for chunk in self.client.stream_media(
                    message, offset=pyro_offset, limit=blocks_in_mib
                ):
                    if self.closed:
                        return
                    if not chunk:
                        continue
                    # Não escreve além do tamanho do bloco.
                    remaining = target - written
                    if remaining <= 0:
                        break
                    if len(chunk) > remaining:
                        chunk = chunk[:remaining]
                    await asyncio.to_thread(
                        self._write_at, block_offset + written, bytes(chunk)
                    )
                    written += len(chunk)
                    self._block_filled[block] = written
                    # Libera a PARTIDA RÁPIDA assim que houver bytes suficientes.
                    if not partial.is_set() and (
                        written >= min(FAST_FIRST_BYTES, target)
                    ):
                        partial.set()
                    self.last_access = time.time()
                    if written >= target:
                        break

                # Garante os eventos no fim (mesmo que o bloco seja pequeno).
                self._block_filled[block] = max(self._block_filled.get(block, 0), written)
                partial.set()
                ready.set()
                self.last_access = time.time()
        except asyncio.CancelledError:
            # Fechamento da sessão: libera quem espera, sem registrar erro.
            partial.set()
            ready.set()
            raise
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)
            log.exception("Falha ao baixar bloco %s do token %s", block, self.token)
            partial.set()
            ready.set()  # libera quem espera, com erro registrado
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

    # ----------------------------------------------------------- disponibilidade
    async def ensure_block(self, block: int, timeout: float = 90.0) -> bool:
        """Garante que um bloco esteja COMPLETO, baixando se necessário."""
        if self.size and block >= self.total_blocks:
            return False
        self._spawn_download(block)
        ev = self._ready_event(block)
        try:
            await asyncio.wait_for(ev.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return False
        return ev.is_set() and self.error is None

    async def _wait_for_bytes(self, block: int, needed_end: int, timeout: float = 90.0) -> bool:
        """Espera até haver `needed_end` bytes gravados no bloco (ou ele completar).

        Permite a PARTIDA RÁPIDA: liberamos bytes assim que disponíveis, sem
        bloquear até o bloco inteiro de 2 MiB chegar.
        """
        if self.size and block >= self.total_blocks:
            return False
        self._spawn_download(block)
        ready = self._ready_event(block)
        partial = self._partial_event(block)
        deadline = time.time() + timeout
        while not self.closed:
            if self.error:
                return False
            filled = self._block_filled.get(block, 0)
            if filled >= needed_end or ready.is_set():
                return True
            remaining = deadline - time.time()
            if remaining <= 0:
                return ready.is_set()
            # Acorda quando chegam os primeiros bytes ou quando completa.
            waiters = [asyncio.ensure_future(ready.wait())]
            if not partial.is_set():
                waiters.append(asyncio.ensure_future(partial.wait()))
            try:
                done, pending = await asyncio.wait(
                    waiters, timeout=min(0.25, remaining),
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                for w in waiters:
                    if not w.done():
                        w.cancel()
        return False

    def _schedule_read_ahead(self, current_block: int) -> None:
        """Agenda o download dos próximos blocos para evitar travar a reprodução."""
        for offset in range(1, READ_AHEAD_BLOCKS + 1):
            nxt = current_block + offset
            if self.size and nxt >= self.total_blocks:
                break
            self._spawn_download(nxt)

    async def read_range(self, start: int, end: int):
        """Gerador assíncrono que entrega bytes do intervalo [start, end].

        Faz YIELD PARCIAL: assim que parte de um bloco chega, os bytes são
        entregues — o player não espera o bloco inteiro de 2 MiB. Também faz
        read-ahead agressivo dos próximos blocos.
        """
        self.last_access = time.time()
        position = start
        while position <= end and not self.closed:
            block = position // BLOCK_SIZE
            block_start = block * BLOCK_SIZE
            block_end = block_start + BLOCK_SIZE - 1
            chunk_end = min(end, block_end)

            # Quanto deste bloco precisamos (offset relativo ao início do bloco).
            need_rel = (chunk_end - block_start) + 1
            ok = await self._wait_for_bytes(block, need_rel)
            if not ok:
                if self.error:
                    raise RuntimeError(self.error)
                return
            self._schedule_read_ahead(block)

            # Quantos bytes deste bloco já estão prontos a partir da posição.
            filled = self._block_filled.get(block, 0)
            ready_abs_end = block_start + filled - 1
            stop = min(chunk_end, ready_abs_end)
            if stop < position:
                # Ainda não há bytes na posição exata; espera um pouco mais.
                if self._ready_event(block).is_set():
                    return
                await asyncio.sleep(0.05)
                continue

            length = stop - position + 1
            data = await asyncio.to_thread(self._read_at, position, length)
            if not data:
                if self._ready_event(block).is_set():
                    return
                await asyncio.sleep(0.05)
                continue
            yield data
            position += len(data)
            self.last_access = time.time()

    async def close(self) -> None:
        self.closed = True
        # Cancela downloads em andamento (libera rede ao fechar a janela).
        for task in list(self._tasks):
            if not task.done():
                task.cancel()
        for ev in self._block_ready.values():
            ev.set()
        for ev in self._block_partial.values():
            ev.set()
        # Apaga o cache temporário: o vídeo não fica armazenado.
        try:
            await asyncio.to_thread(self._unlink)
        except Exception:  # noqa: BLE001
            log.exception("Não foi possível apagar o cache temporário de %s", self.token)

    def _unlink(self) -> None:
        if self.cache_path.exists():
            self.cache_path.unlink()

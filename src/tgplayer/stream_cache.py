"""Cache de streaming sob demanda, por blocos — versão ACELERADA (v6.3).

A grande diferença para a versão antiga: em vez de baixar o vídeo inteiro de
forma sequencial (o que travava ao dar seek para frente), aqui o vídeo é
dividido em blocos fixos. Só os blocos que o player realmente pede (via Range
HTTP) são baixados do Telegram, e o player pode pular para qualquer ponto do
vídeo que o bloco correspondente é buscado na hora.

Otimizações de PARTIDA RÁPIDA, para o player não ficar preso em
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

NOVO na v6.3 (ideias portadas do projeto caamer20/Telegram-Drive):

5. **Alinhamento de offset à fronteira de CDN** (`server.rs::build_media_response`):
   o CDN do Telegram arredonda offsets para baixo até a fronteira de 512 KiB.
   Pedidos `Range` desalinhados podem retornar dados deslocados → corrompem o
   parsing das *boxes* MP4 e quebram o seek. Como aqui já baixamos por blocos de
   2 MiB (múltiplos de 512 KiB) a partir de offsets alinhados ao MiB, o
   invariante "offset alinhado <= offset pedido" é naturalmente respeitado; os
   bytes excedentes são descartados (`bytes_to_skip`) na leitura. Ainda assim
   expomos `CDN_ALIGNMENT` e logamos o cálculo em DEBUG para auditoria.

6. **Descoberta do átomo `moov` em 3 passos** (`useAdaptiveStreaming.ts`):
   `discover_moov()` procura o `moov` lendo primeiro 128 KiB, depois 512 KiB e,
   por fim, a cauda (512 KiB finais), validando a box (`mvhd` como 1º filho) e
   pré-aquecendo o cache dos blocos onde o moov vive. O resultado é guardável em
   SQLite (`moov_cache`) para boot instantâneo na 2ª vez.

7. **Throttle de banda por qualidade** (`types.ts::QUALITY_THROTTLE_MAP`): o
   gerador `read_range` pode limitar a taxa de entrega (kbps) para simular as
   qualidades 360p/480p/720p/1080p sem reencode, e mede a banda real entregue.

O arquivo de cache é esparso, fica em disco apenas durante a reprodução e é
apagado ao fechar o player: o vídeo NUNCA fica armazenado permanentemente.
"""

from __future__ import annotations

import asyncio
import logging
import struct
import time
from pathlib import Path
from typing import TYPE_CHECKING

from .mp4_faststart import FaststartError, build_faststart_header, split_ftyp

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

# ----------------------------------------------------------------- PARTIDA RÁPIDA
# Novidades v6.4: a partida usa pedaços MENORES e mais paralelismo para que o
# player exiba o primeiro frame em poucos segundos (idealmente 2–5 s).
#
# Tamanho do "primeiro pedaço" alinhado à fronteira de CDN (512 KiB). É o
# tamanho com que liberamos os bytes iniciais do `mdat` — menor que o bloco de
# 2 MiB, então o player começa a tocar muito antes.
FIRST_CHUNK_SIZE = 512 * 1024
# Mais downloads concorrentes SÓ durante a partida (separa-se do steady-state).
STARTUP_PARALLEL = 8
# Orçamento de bytes do INÍCIO do mdat que, junto com o header faststart, marca
# "pronto para tocar". Assim que isto chega, o overlay some e a reprodução flui.
STARTUP_BUDGET_BYTES = 2 * 1024 * 1024

# Fronteira de arredondamento do CDN do Telegram (512 KiB). Pedidos de Range
# precisam começar em múltiplos disso para não receber dados deslocados.
CDN_ALIGNMENT = 512 * 1024

# Tamanhos de varredura para a descoberta do átomo `moov` (em bytes).
MOOV_DISCOVERY_BYTES = 128 * 1024
MOOV_RETRY_BYTES = 512 * 1024
MOOV_TAIL_BYTES = 512 * 1024
# Tamanho máximo plausível de um `moov` (evita falso-positivo dentro de `mdat`).
MOOV_MAX_SIZE = 64 * 1024 * 1024


def align_down(value: int, alignment: int = CDN_ALIGNMENT) -> int:
    """Arredonda `value` para baixo até o múltiplo de `alignment`."""
    if alignment <= 0:
        return value
    return (value // alignment) * alignment


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
        throttle_kbps: int = 0,
        max_retries: int = 2,
    ) -> None:
        self.token = token
        self.client = client
        self.chat_id = chat_id
        self.message_id = int(message_id)
        self.size = int(size or 0)
        self.cache_path = cache_path
        self.mime_type = mime_type or "video/mp4"
        # Limite de banda em kbps (0 = ilimitado). Pode ser trocado em tempo real.
        self.throttle_kbps = max(0, int(throttle_kbps or 0))
        # Tentativas extra por bloco em caso de erro de rede ("conexão instável").
        # Ideia portada do tratamento de re-tentativas/backoff do projeto de
        # referência (downloads resilientes em redes ruins).
        self.max_retries = max(0, int(max_retries or 0))

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
        # Mais downloads concorrentes ajudam a "amortecer" a latência de
        # primeiro-byte do stream_media. Usamos STARTUP_PARALLEL (>=
        # MAX_PARALLEL_DOWNLOADS) já que o gargalo é latência, não banda local.
        self._semaphore = asyncio.Semaphore(max(MAX_PARALLEL_DOWNLOADS, STARTUP_PARALLEL))
        self.last_access = time.time()
        self.closed = False
        self.error: str | None = None

        # ---- Medição de banda (janela deslizante) --------------------------
        self._served_bytes = 0
        self._band_window: list[tuple[float, int]] = []  # (timestamp, bytes)

        # ---- Resultado da descoberta do moov -------------------------------
        # {found, moov_offset, moov_size, located} preenchido por discover_moov().
        self.moov_info: dict[str, int | bool] | None = None

        # ---- Faststart virtual (v6.4) --------------------------------------
        # Quando o arquivo NÃO é faststart (moov no fim), montamos em memória um
        # cabeçalho ftyp+moov com offsets corrigidos e o servimos ANTES do mdat,
        # mapeando os Ranges lógicos do player de volta para offsets físicos.
        # `faststart_header` é o cabeçalho pronto; `faststart_active` indica que
        # o modo está ligado para esta sessão.
        self.faststart_header: bytes | None = None
        self.faststart_active: bool = False
        # Início FÍSICO do mdat no arquivo original (geralmente logo após o
        # ftyp, em moov-at-end). Usado para mapear offsets lógicos -> físicos.
        self._mdat_phys_start: int = 0
        # Lock para montar o header uma única vez (evita corrida entre o player
        # e a descoberta em 2º plano).
        self._faststart_lock = asyncio.Lock()
        self._faststart_attempted = False

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
        """Partida rápida v6.4: prioriza o MÍNIMO para o primeiro frame.

        Em vez de baixar 6 blocos de 2 MiB antes de liberar a reprodução, aqui
        focamos no essencial: o cabeçalho (ftyp + início do mdat) e a cauda
        (moov). Quando o faststart virtual está ativo, o player nem precisa
        esperar a cauda — o header já carrega o moov no começo.
        """
        if self.closed:
            return
        # O bloco 0 contém o ftyp + começo do mdat -> primeira coisa a chegar.
        self._spawn_download(0)
        # Read-ahead inicial ENXUTO (não competir com os bytes da partida):
        # só o bloco 1, suficiente para emendar a reprodução. O read-ahead
        # agressivo passa a valer DEPOIS da partida (ver _schedule_read_ahead).
        if (self.total_blocks or 2) > 1:
            self._spawn_download(1)
        # Pré-busca da cauda (moov de MP4 não-faststart) — necessária para
        # MONTAR o header faststart e/ou para players sem faststart.
        if self.total_blocks:
            for i in range(1, MOOV_TAIL_BLOCKS + 1):
                tail = self.total_blocks - i
                if tail > 0:
                    self._spawn_download(tail)

    def buffer_ratio(self) -> float:
        """Fração (0..1) de "pronto para tocar" para o overlay "Carregando…".

        v6.4: "pronto" = header faststart montado (quando aplicável) + pelo
        menos ``STARTUP_BUDGET_BYTES`` do início do mdat baixados. Assim o
        overlay some assim que o player REALMENTE tem o necessário para o
        primeiro frame — sem esperar a cauda inteira nem 6 blocos de 2 MiB.
        """
        if self.total_blocks == 0:
            return 0.0
        # Quanto do orçamento inicial (2 MiB) já temos a partir do offset 0.
        want = min(STARTUP_BUDGET_BYTES, self.size or STARTUP_BUDGET_BYTES) or 1
        have = 0
        blocks_needed = (want + BLOCK_SIZE - 1) // BLOCK_SIZE
        for b in range(blocks_needed):
            have += min(self._block_filled.get(b, 0), self._block_size_for(b))
        head = min(1.0, have / want)

        if self.faststart_active and self.faststart_header is not None:
            # Faststart pronto: a cauda não importa; só o início do mdat conta.
            return max(0.0, min(1.0, head))

        # Sem faststart confirmado, a cauda (moov) ainda pesa na partida.
        tail_ready = 1.0
        if self.total_blocks > MOOV_TAIL_BLOCKS:
            done = 0
            for i in range(1, MOOV_TAIL_BLOCKS + 1):
                tail = self.total_blocks - i
                if self._ready_event(tail).is_set():
                    done += 1
            tail_ready = done / MOOV_TAIL_BLOCKS
        return max(0.0, min(1.0, 0.65 * head + 0.35 * tail_ready))

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

                # O Pyrogram trabalha com offset/limit em unidades de 1 MiB.
                # block_offset é múltiplo de 2 MiB → naturalmente alinhado à
                # fronteira de 512 KiB do CDN (CDN_ALIGNMENT). Sem desalinhamento,
                # sem bytes a descartar no download (o skip ocorre só na leitura).
                one_mib = 1024 * 1024
                pyro_offset = block_offset // one_mib
                blocks_in_mib = max(1, BLOCK_SIZE // one_mib)

                # Loop de re-tentativas (modo "conexão instável"): em erro de
                # rede, espera com backoff e refaz o stream a partir do offset
                # já gravado, sem reiniciar o bloco do zero.
                attempt = 0
                last_exc: Exception | None = None
                while True:
                    written = self._block_filled.get(block, 0)
                    try:
                        message = await self._get_message()
                        # Reposiciona o offset Pyrogram para o ponto já gravado
                        # (em unidades de 1 MiB) na re-tentativa.
                        resume_mib = written // one_mib
                        async for chunk in self.client.stream_media(
                            message,
                            offset=pyro_offset + resume_mib,
                            limit=blocks_in_mib - resume_mib,
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
                            # Libera a PARTIDA RÁPIDA assim que possível.
                            if not partial.is_set() and (
                                written >= min(FAST_FIRST_BYTES, target)
                            ):
                                partial.set()
                            self.last_access = time.time()
                            if written >= target:
                                break
                        # Sucesso (chegou ao fim do stream ou ao alvo).
                        last_exc = None
                        break
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001
                        last_exc = exc
                        if attempt >= self.max_retries or self.closed:
                            break
                        attempt += 1
                        backoff = min(2.0, 0.4 * (2 ** (attempt - 1)))
                        log.warning(
                            "Bloco %s token %s falhou (tentativa %d/%d): %s — "
                            "re-tentando em %.1fs",
                            block, self.token, attempt, self.max_retries, exc, backoff,
                        )
                        await asyncio.sleep(backoff)

                if last_exc is not None:
                    self.error = str(last_exc)
                    log.exception(
                        "Falha ao baixar bloco %s do token %s após %d tentativa(s)",
                        block, self.token, self.max_retries,
                        exc_info=last_exc,
                    )

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

    # ------------------------------------------------- leitura de bytes brutos
    async def read_exact(self, start: int, length: int, timeout: float = 90.0) -> bytes:
        """Lê exatamente [start, start+length) (usado pela descoberta do moov).

        Diferente de `read_range`, retorna um único `bytes` (não streaming) e é
        usado internamente para parsear as boxes do MP4.
        """
        if length <= 0:
            return b""
        if self.size:
            length = min(length, max(0, self.size - start))
        out = bytearray()
        end = start + length - 1
        async for chunk in self.read_range(start, end, measure=False):
            out.extend(chunk)
            if len(out) >= length:
                break
        return bytes(out[:length])

    async def read_range(self, start: int, end: int, measure: bool = True):
        """Gerador assíncrono que entrega bytes do intervalo [start, end].

        Faz YIELD PARCIAL: assim que parte de um bloco chega, os bytes são
        entregues — o player não espera o bloco inteiro de 2 MiB. Também faz
        read-ahead agressivo dos próximos blocos.

        Aplica alinhamento de CDN (o offset real do bloco é múltiplo de 512 KiB)
        e, quando há throttle, limita a taxa de entrega medindo a banda real.
        """
        self.last_access = time.time()

        # --- Alinhamento de CDN (auditoria/DEBUG) ---------------------------
        # Os blocos sempre começam em múltiplos de 2 MiB (≥ 512 KiB), então o
        # `aligned_start` <= `start`. Os bytes entre eles são pulados ao posicionar
        # a leitura no offset exato pedido (já feito naturalmente abaixo).
        aligned_start = align_down(start, CDN_ALIGNMENT)
        bytes_to_skip = start - aligned_start
        if log.isEnabledFor(logging.DEBUG):
            log.debug(
                "read_range token=%s requested=%d cdn_aligned=%d chunk_index=%d skip=%d",
                self.token, start, aligned_start, start // BLOCK_SIZE, bytes_to_skip,
            )

        # Throttle: bytes por segundo permitidos (0 = ilimitado).
        throttle_bps = (self.throttle_kbps * 1000 / 8) if self.throttle_kbps else 0
        window_start = time.time()
        window_bytes = 0

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

            if measure:
                self._record_served(len(data))
            # --- Throttle de banda (qualidade) ------------------------------
            if throttle_bps:
                window_bytes += len(data)
                elapsed = time.time() - window_start
                expected = window_bytes / throttle_bps
                if expected > elapsed:
                    await asyncio.sleep(min(0.5, expected - elapsed))
                if elapsed >= 1.0:
                    window_start = time.time()
                    window_bytes = 0

    # --------------------------------------------------------- medição de banda
    def _record_served(self, n: int) -> None:
        now = time.time()
        self._served_bytes += n
        self._band_window.append((now, n))
        # Mantém só os últimos ~3 s.
        cutoff = now - 3.0
        while self._band_window and self._band_window[0][0] < cutoff:
            self._band_window.pop(0)

    def measured_kbps(self) -> float:
        """Banda real entregue ao player nos últimos ~3 s, em kbps."""
        if not self._band_window:
            return 0.0
        now = time.time()
        span = max(0.001, now - self._band_window[0][0])
        total = sum(n for _, n in self._band_window)
        return (total * 8) / 1000.0 / span

    def set_throttle_kbps(self, kbps: int) -> None:
        self.throttle_kbps = max(0, int(kbps or 0))

    # ------------------------------------------------ descoberta do átomo moov
    async def discover_moov(self) -> dict[str, int | bool]:
        """Localiza o átomo `moov` do MP4 em 3 passos (128K → 512K → cauda).

        Retorna um dicionário {found, moov_offset, moov_size, located} onde
        `located` é 'head' (faststart) ou 'tail' (moov-at-end). Pré-aquece o
        cache dos blocos onde o moov vive para acelerar a partida do player.
        """
        if self.moov_info is not None:
            return self.moov_info
        info: dict[str, int | bool] = {
            "found": False, "moov_offset": 0, "moov_size": 0, "located": 0,
        }
        if not self.size:
            self.moov_info = info
            return info

        # Passo 1 e 2: varre o INÍCIO (128 KiB e depois 512 KiB).
        for scan in (MOOV_DISCOVERY_BYTES, MOOV_RETRY_BYTES):
            head = await self.read_exact(0, min(scan, self.size), timeout=30.0)
            found = self._scan_moov_box(head, base_offset=0)
            if found is not None:
                off, sz = found
                info = {"found": True, "moov_offset": off, "moov_size": sz, "located": 1}
                self.moov_info = info
                self._prewarm_range(off, sz)
                return info

        # Passo 3: varre a CAUDA (512 KiB finais) — caso "moov-at-end".
        tail_len = min(MOOV_TAIL_BYTES, self.size)
        tail_start = self.size - tail_len
        tail = await self.read_exact(tail_start, tail_len, timeout=30.0)
        found = self._scan_moov_box(tail, base_offset=tail_start)
        if found is not None:
            off, sz = found
            info = {"found": True, "moov_offset": off, "moov_size": sz, "located": 2}
            # Pré-aquece os blocos da cauda onde está o moov.
            self._prewarm_range(off, sz)
        self.moov_info = info
        return info

    def _scan_moov_box(self, buf: bytes, base_offset: int) -> tuple[int, int] | None:
        """Varre as boxes MP4 de `buf` procurando um `moov` válido.

        Estratégia 1 — caminhada estruturada de boxes (a partir do começo do
        buffer, válida quando `base_offset == 0` ou quando o buffer começa numa
        fronteira de box). Estratégia 2 — busca direta pela assinatura `moov`
        (necessária ao varrer a CAUDA, onde o buffer começa no meio de `mdat`).

        Em ambos os casos validamos: tamanho plausível (≤ MOOV_MAX_SIZE) e
        primeiro filho == `mvhd` (evita falso-positivo de bytes 'moov' dentro
        de `mdat`).
        """
        n = len(buf)

        def _validate(pos: int) -> tuple[int, int] | None:
            """Valida uma possível box `moov` cujo *tipo* está em buf[pos+4:pos+8]."""
            if pos + 8 > n:
                return None
            box_size = struct.unpack(">I", buf[pos:pos + 4])[0]
            header = 8
            if box_size == 1:  # 64-bit largesize
                if pos + 16 > n:
                    return None
                box_size = struct.unpack(">Q", buf[pos + 8:pos + 16])[0]
                header = 16
            if box_size == 0:
                box_size = (self.size - (base_offset + pos)) if self.size else (n - pos)
            if not (0 < box_size <= MOOV_MAX_SIZE):
                return None
            child = buf[pos + header:pos + header + 8]
            if len(child) >= 8 and child[4:8] == b"mvhd":
                return base_offset + pos, int(box_size)
            # Cauda truncada (filho fora do buffer): confia no tipo.
            if len(child) < 8:
                return base_offset + pos, int(box_size)
            return None

        # Estratégia 1: caminhada estruturada.
        pos = 0
        while pos + 8 <= n:
            box_size = struct.unpack(">I", buf[pos:pos + 4])[0]
            box_type = buf[pos + 4:pos + 8]
            header = 8
            if box_size == 1:
                if pos + 16 > n:
                    break
                box_size = struct.unpack(">Q", buf[pos + 8:pos + 16])[0]
                header = 16
            if box_size == 0:
                box_size = (self.size - (base_offset + pos)) if self.size else (n - pos)
            if box_type == b"moov":
                hit = _validate(pos)
                if hit is not None:
                    return hit
            if box_size < header:
                break
            pos += box_size

        # Estratégia 2: busca direta pela assinatura `moov` (cauda mid-box).
        search = 0
        while True:
            idx = buf.find(b"moov", search)
            if idx < 4:
                if idx == -1:
                    break
                search = idx + 4
                continue
            # O tipo `moov` aparece em [idx, idx+4); o tamanho da box vem 4 bytes antes.
            hit = _validate(idx - 4)
            if hit is not None:
                return hit
            search = idx + 4
        return None

    def _prewarm_range(self, offset: int, size: int) -> None:
        """Dispara o download dos blocos que cobrem [offset, offset+size)."""
        if not self.size or size <= 0:
            return
        first = max(0, offset) // BLOCK_SIZE
        last = min(self.size - 1, offset + size - 1) // BLOCK_SIZE
        for b in range(first, last + 1):
            self._spawn_download(b)

    # ------------------------------------------------------ faststart virtual
    @property
    def logical_size(self) -> int:
        """Tamanho do arquivo LÓGICO apresentado ao player.

        Com faststart ativo, o arquivo lógico é ``header (ftyp+moov)`` + ``mdat``
        (sem o moov original na cauda). Como apenas REPOSICIONAMOS bytes (o moov
        sai do fim e vai para o começo, com o mesmo tamanho), o tamanho total
        permanece IGUAL ao original. Mantemos a propriedade para clareza.
        """
        return self.size

    async def ensure_faststart(self) -> bool:
        """Garante o modo faststart quando o arquivo é moov-at-end.

        Descobre o ``moov`` (se necessário), baixa ``ftyp`` + ``moov`` e monta o
        cabeçalho faststart em memória, corrigindo os offsets de chunk. Retorna
        ``True`` se o faststart ficou ativo. Em QUALQUER falha (arquivo já
        faststart, parsing problemático, moov gigante) retorna ``False`` e a
        sessão segue servindo o arquivo ORIGINAL (fallback seguro).
        """
        if self.faststart_active:
            return True
        async with self._faststart_lock:
            if self.faststart_active:
                return True
            if self._faststart_attempted:
                return self.faststart_active
            self._faststart_attempted = True
            try:
                info = await self.discover_moov()
            except Exception:  # noqa: BLE001
                log.exception("faststart: falha ao descobrir moov (%s)", self.token)
                return False
            if not info.get("found"):
                return False
            located = int(info.get("located") or 0)
            if located == 1:
                # Já é faststart (moov no início): nada a reescrever.
                return False
            moov_off = int(info.get("moov_offset") or 0)
            moov_sz = int(info.get("moov_size") or 0)
            if moov_sz <= 0 or moov_off <= 0:
                return False
            try:
                # 1) Lê o ftyp do começo do arquivo (cabe no primeiro pedaço).
                head = await self.read_exact(0, min(FIRST_CHUNK_SIZE, self.size), timeout=30.0)
                ftyp_bytes, ftyp_size = split_ftyp(head)
                # 2) Lê o moov inteiro da cauda.
                moov_bytes = await self.read_exact(moov_off, moov_sz, timeout=45.0)
                if len(moov_bytes) < moov_sz or moov_bytes[4:8] != b"moov":
                    raise FaststartError("moov incompleto/ inválido na cauda")
                # 3) Monta o header faststart (offsets corrigidos).
                header = build_faststart_header(ftyp_bytes, moov_bytes)
            except FaststartError as exc:
                log.info("faststart desativado para %s: %s", self.token, exc)
                return False
            except Exception:  # noqa: BLE001
                log.exception("faststart: erro inesperado ao montar header (%s)", self.token)
                return False

            self.faststart_header = header
            # O mdat (dados de mídia) fica logo após o ftyp no arquivo original.
            self._mdat_phys_start = ftyp_size
            self.faststart_active = True
            # Pré-aquece o INÍCIO do mdat para a partida fluir imediatamente.
            self._prewarm_range(self._mdat_phys_start, STARTUP_BUDGET_BYTES)
            log.info(
                "faststart ATIVO para %s: header=%dB, mdat_phys_start=%d",
                self.token, len(header), self._mdat_phys_start,
            )
            return True

    def _logical_to_physical(self, logical_off: int) -> int:
        """Converte um offset LÓGICO (visão do player) para FÍSICO (no arquivo).

        Só usado quando o faststart está ativo. A região do header é tratada à
        parte; aqui mapeamos a parte que cai no ``mdat``.

            logical:  [0 .. H) = header   |   [H .. fim) = mdat
            physical: mdat começa em _mdat_phys_start

        Para um offset lógico ``>= H`` (dentro do mdat), o físico é
        ``_mdat_phys_start + (logical_off - H)``.
        """
        h = len(self.faststart_header or b"")
        return self._mdat_phys_start + (logical_off - h)

    async def read_logical_range(self, start: int, end: int, measure: bool = True):
        """Gerador que entrega bytes do arquivo LÓGICO [start, end].

        Quando o faststart está ativo, serve primeiro o cabeçalho em memória
        (ftyp+moov reposicionado) e depois mapeia o restante para o ``mdat``
        físico. Quando NÃO está ativo, delega para :meth:`read_range` (arquivo
        servido como está) — fallback seguro.
        """
        if not (self.faststart_active and self.faststart_header is not None):
            async for chunk in self.read_range(start, end, measure=measure):
                yield chunk
            return

        header = self.faststart_header
        h = len(header)
        pos = start

        # 1) Parte que cai no HEADER (em memória).
        if pos < h:
            head_end = min(end, h - 1)
            chunk = header[pos:head_end + 1]
            if chunk:
                yield chunk
                if measure:
                    self._record_served(len(chunk))
                pos = head_end + 1

        # 2) Parte que cai no MDAT (físico), mapeada de volta.
        if pos <= end:
            phys_start = self._logical_to_physical(pos)
            phys_end = self._logical_to_physical(end)
            async for chunk in self.read_range(phys_start, phys_end, measure=measure):
                yield chunk

    async def close(self) -> None:
        self.closed = True
        # Cancela downloads em andamento (libera rede ao fechar a janela).
        for task in list(self._tasks):
            if not task.done():
                task.cancel()
        # Aguarda brevemente o cancelamento limpo das tasks em voo.
        if self._tasks:
            try:
                await asyncio.wait(list(self._tasks), timeout=2.0)
            except Exception:  # noqa: BLE001
                pass
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

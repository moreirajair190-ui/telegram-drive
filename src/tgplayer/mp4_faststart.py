"""Faststart "virtual" de MP4 — reposiciona o `moov` para o início (v6.4).

PROBLEMA QUE ISTO RESOLVE
=========================
Quando um arquivo MP4 NÃO é "faststart", o átomo de índice ``moov`` fica no
FINAL do arquivo (depois do ``mdat``, que contém os dados de vídeo/áudio). Os
players nativos (libVLC, QMediaPlayer, navegadores) precisam ler o ``moov``
ANTES de começar a tocar — então, com o ``moov`` no fim, o player faz um Range
até o fim do arquivo e ESPERA aqueles megabytes chegarem do Telegram antes de
exibir o primeiro frame. Em arquivos grandes, essa "viagem ao fim" é o maior
vilão da partida lenta (~1 minuto).

A SOLUÇÃO (sem reencode, sem remux fMP4)
========================================
Fazemos o que o utilitário clássico ``qt-faststart`` faz, porém EM MEMÓRIA e
SÓ para o cabeçalho: montamos um novo começo de arquivo na ordem

    ftyp  →  moov (reposicionado)  →  mdat …

Como o ``moov`` agora vem ANTES do ``mdat``, todos os *chunk offset boxes*
(``stco`` de 32 bits e ``co64`` de 64 bits) — que apontam para a posição
absoluta de cada "chunk" de amostras dentro do arquivo — precisam ser somados
do deslocamento que o ``moov`` introduziu no início. É exatamente isso que
``build_faststart_header`` faz: percorre recursivamente
``moov → trak → mdia → minf → stbl → {stco|co64}`` e corrige cada entrada.

É **stream-copy barato**: NÃO recodifica nada, apenas move bytes e ajusta
inteiros. O resultado é um MP4 faststart válido cujo ``mdat`` é byte-a-byte o
mesmo do original (apenas deslocado), de modo que o servidor local
(:mod:`telegram_service`) consegue mapear qualquer Range lógico de volta para o
offset FÍSICO no Telegram.

Nada aqui faz I/O de rede — recebe bytes já baixados (``ftyp`` + ``moov``) e
devolve bytes. Se o parsing encontrar algo inesperado, levanta
:class:`FaststartError`; o chamador deve então cair no comportamento legado
(servir o arquivo como está) — NUNCA quebrar a reprodução.
"""

from __future__ import annotations

import logging
import struct

log = logging.getLogger(__name__)

# Tamanho máximo plausível de um `moov` que aceitamos reescrever em memória.
# Acima disso preferimos o fallback (servir o arquivo original) para não
# estourar memória nem arriscar parsing de algo atípico.
MAX_MOOV_REWRITE = 48 * 1024 * 1024  # 48 MiB

# Boxes "container" cujo conteúdo é uma lista de outras boxes (recursão).
_CONTAINER_BOXES = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"edts", b"mdia"}


class FaststartError(Exception):
    """Erro ao montar o cabeçalho faststart (chamador deve usar o fallback)."""


def _read_box_header(buf: bytes, pos: int) -> tuple[int, bytes, int, int]:
    """Lê o cabeçalho da box em ``pos``.

    Retorna ``(box_size, box_type, header_len, payload_pos)``.
    ``box_size`` já considera o largesize de 64 bits. Levanta
    :class:`FaststartError` se o buffer for curto demais ou o tamanho inválido.
    """
    if pos + 8 > len(buf):
        raise FaststartError("box truncada (cabeçalho)")
    box_size = struct.unpack(">I", buf[pos:pos + 4])[0]
    box_type = bytes(buf[pos + 4:pos + 8])
    header_len = 8
    if box_size == 1:
        if pos + 16 > len(buf):
            raise FaststartError("box truncada (largesize)")
        box_size = struct.unpack(">Q", buf[pos + 8:pos + 16])[0]
        header_len = 16
    elif box_size == 0:
        # Box "até o fim do buffer".
        box_size = len(buf) - pos
    if box_size < header_len:
        raise FaststartError(f"tamanho de box inválido: {box_size}")
    return box_size, box_type, header_len, pos + header_len


def _patch_offsets_in_place(moov: bytearray, pos: int, end: int, delta: int) -> None:
    """Percorre as boxes em ``[pos, end)`` somando ``delta`` aos offsets de chunk.

    Recursivo: desce nos containers e, ao encontrar ``stco``/``co64``,
    reescreve cada entrada de offset. ``moov`` é um ``bytearray`` mutável.
    """
    while pos + 8 <= end:
        box_size, box_type, header_len, payload_pos = _read_box_header(moov, pos)
        box_end = pos + box_size
        if box_end > end:
            box_end = end
        if box_type == b"stco":
            _patch_stco(moov, payload_pos, box_end, delta, width=4)
        elif box_type == b"co64":
            _patch_stco(moov, payload_pos, box_end, delta, width=8)
        elif box_type in _CONTAINER_BOXES:
            _patch_offsets_in_place(moov, payload_pos, box_end, delta)
        pos = box_end


def _patch_stco(moov: bytearray, payload_pos: int, box_end: int, delta: int, width: int) -> None:
    """Soma ``delta`` a cada offset de uma box ``stco`` (width=4) ou ``co64`` (width=8).

    Layout do payload: version(1) + flags(3) + entry_count(4) + entries[width...].
    """
    # version/flags(4) + entry_count(4)
    if payload_pos + 8 > box_end:
        raise FaststartError("stco/co64 sem cabeçalho de entradas")
    entry_count = struct.unpack(">I", moov[payload_pos + 4:payload_pos + 8])[0]
    entries_pos = payload_pos + 8
    fmt = ">I" if width == 4 else ">Q"
    for i in range(entry_count):
        off = entries_pos + i * width
        if off + width > box_end:
            raise FaststartError("stco/co64 com entradas além da box")
        value = struct.unpack(fmt, moov[off:off + width])[0]
        new_value = value + delta
        if width == 4 and new_value > 0xFFFFFFFF:
            # Estouraria 32 bits — não convertemos stco→co64 (raro); fallback.
            raise FaststartError("offset de stco estouraria 32 bits após faststart")
        struct.pack_into(fmt, moov, off, new_value)


def build_faststart_header(ftyp_bytes: bytes, moov_bytes: bytes, mdat_offset: int | None = None) -> bytes:
    """Monta o cabeçalho faststart: ``ftyp`` + ``moov`` com offsets corrigidos.

    Args:
        ftyp_bytes: a box ``ftyp`` completa do começo do arquivo (com cabeçalho).
        moov_bytes: a box ``moov`` completa (com cabeçalho), puxada do fim.

    Returns:
        ``bytes`` prontos para serem o NOVO começo do arquivo lógico. O ``mdat``
        (e qualquer resto) deve ser anexado em seguida pelo servidor, mantendo
        a ordem original dos dados de mídia.

    Args adicionais:
        mdat_offset: offset físico da box ``mdat`` no arquivo original. Quando
        ausente, assume o layout simples ``ftyp + mdat + moov`` para manter
        compatibilidade com versões anteriores.

    Raises:
        FaststartError: se as boxes forem inválidas ou grandes demais — o
            chamador deve então servir o arquivo original sem reescrever.
    """
    if not ftyp_bytes or ftyp_bytes[4:8] != b"ftyp":
        raise FaststartError("box ftyp ausente/ inválida")
    if not moov_bytes or moov_bytes[4:8] != b"moov":
        raise FaststartError("box moov ausente/ inválida")
    if len(moov_bytes) > MAX_MOOV_REWRITE:
        raise FaststartError(f"moov grande demais ({len(moov_bytes)} bytes)")

    # No layout faststart, o mdat físico passa a aparecer depois do novo
    # cabeçalho lógico (ftyp + moov). Em arquivos simples (ftyp+mdat+moov),
    # isso equivale a somar len(moov). Em arquivos com boxes intermediárias
    # (ftyp+free/wide+mdat+moov), precisamos descontar o prefixo removido até
    # o mdat real, senão os offsets ficam errados e alguns players ficam
    # eternamente em buffer.
    if mdat_offset is None:
        mdat_offset = len(ftyp_bytes)
    delta = len(ftyp_bytes) + len(moov_bytes) - int(mdat_offset)

    moov = bytearray(moov_bytes)
    # Valida o cabeçalho do moov e percorre seus filhos corrigindo offsets.
    box_size, box_type, header_len, payload_pos = _read_box_header(moov, 0)
    if box_type != b"moov":
        raise FaststartError("primeira box não é moov")
    _patch_offsets_in_place(moov, payload_pos, min(box_size, len(moov)), delta)

    header = bytes(ftyp_bytes) + bytes(moov)
    log.debug(
        "faststart header montado: ftyp=%dB moov=%dB delta=%d total=%dB",
        len(ftyp_bytes), len(moov_bytes), delta, len(header),
    )
    return header


def split_ftyp(head_bytes: bytes) -> tuple[bytes, int]:
    """Extrai a box ``ftyp`` do começo do arquivo.

    Args:
        head_bytes: bytes iniciais do arquivo (deve conter o ftyp inteiro).

    Returns:
        ``(ftyp_bytes, ftyp_size)``.

    Raises:
        FaststartError: se o ftyp não estiver completo no buffer fornecido.
    """
    box_size, box_type, _hl, _pp = _read_box_header(head_bytes, 0)
    if box_type != b"ftyp":
        raise FaststartError("arquivo não começa com ftyp")
    if box_size > len(head_bytes):
        raise FaststartError("ftyp não está inteiro no buffer inicial")
    return bytes(head_bytes[:box_size]), box_size


def find_top_level_box(head_bytes: bytes, box_type: bytes, start: int = 0) -> tuple[int, int] | None:
    """Procura uma box top-level em ``head_bytes``.

    Retorna ``(offset, size)`` ou ``None``. É propositalmente conservador:
    se o buffer terminar antes da próxima box completa, para e deixa o
    chamador cair no fallback seguro. Usado para achar ``mdat`` quando há
    boxes como ``free``/``wide`` entre ``ftyp`` e ``mdat``.
    """
    pos = max(0, int(start or 0))
    limit = len(head_bytes)
    while pos + 8 <= limit:
        try:
            box_size, current_type, header_len, _payload = _read_box_header(head_bytes, pos)
        except FaststartError:
            return None
        if current_type == box_type:
            return pos, box_size
        if box_size < header_len:
            return None
        next_pos = pos + box_size
        if next_pos <= pos:
            return None
        pos = next_pos
    return None


def find_mdat_start(head_bytes: bytes, start: int = 0) -> int:
    """Retorna o offset físico da box ``mdat`` no começo do MP4.

    Corrige o caso comum ``ftyp + free/wide + mdat + moov``. Versões antigas
    assumiam que ``mdat`` começava logo após ``ftyp``; isso fazia o Range lógico
    apontar para bytes errados e podia deixar o player local lento ou travado.
    """
    hit = find_top_level_box(head_bytes, b"mdat", start=start)
    if hit is None:
        raise FaststartError("box mdat não encontrada no cabeçalho inicial")
    return hit[0]

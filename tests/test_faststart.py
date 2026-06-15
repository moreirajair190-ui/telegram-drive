"""Smoke tests do faststart virtual de MP4 (v6.4).

Cobre:
  1. build_faststart_header produz um MP4 com `moov` ANTES do `mdat`.
  2. Os offsets de `stco` (32-bit) e `co64` (64-bit) são deslocados por
     exatamente len(moov), de modo que continuam apontando para o mesmo
     conteúdo de mídia após a reordenação.
  3. split_ftyp extrai a box ftyp corretamente.
  4. O FaststartVirtualizer mapeia Ranges lógicos -> offsets físicos.

Executar:
    python tests/test_faststart.py
"""

from __future__ import annotations

import asyncio
import os
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tgplayer.mp4_faststart import (  # noqa: E402
    FaststartError,
    build_faststart_header,
    split_ftyp,
)
from tgplayer.stream_cache import BLOCK_SIZE, StreamSession  # noqa: E402


def _box(btype: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload) + 8) + btype + payload


def _build_stco(offsets: list[int]) -> bytes:
    payload = b"\x00\x00\x00\x00"  # version+flags
    payload += struct.pack(">I", len(offsets))
    for off in offsets:
        payload += struct.pack(">I", off)
    return _box(b"stco", payload)


def _build_co64(offsets: list[int]) -> bytes:
    payload = b"\x00\x00\x00\x00"
    payload += struct.pack(">I", len(offsets))
    for off in offsets:
        payload += struct.pack(">Q", off)
    return _box(b"co64", payload)


def _build_moov(stco_box: bytes) -> bytes:
    mvhd = _box(b"mvhd", b"\x00" * 90)
    stbl = _box(b"stbl", stco_box)
    minf = _box(b"minf", stbl)
    mdia = _box(b"mdia", minf)
    trak = _box(b"trak", mdia)
    return _box(b"moov", mvhd + trak)


def _read_first_offset(moov: bytes, width: int) -> int:
    """Lê o primeiro offset do stco/co64 dentro do moov reescrito (busca direta)."""
    sig = b"stco" if width == 4 else b"co64"
    idx = moov.find(sig)
    assert idx >= 0, f"{sig!r} não encontrado"
    entries = idx + 4 + 4 + 4  # type + (version+flags) + entry_count
    fmt = ">I" if width == 4 else ">Q"
    return struct.unpack(fmt, moov[entries:entries + width])[0]


def test_split_ftyp() -> None:
    ftyp = _box(b"ftyp", b"isom" + b"\x00" * 8)
    extra = b"\xAB" * 20
    got, size = split_ftyp(ftyp + extra)
    assert got == ftyp
    assert size == len(ftyp)
    # Sem ftyp -> erro.
    try:
        split_ftyp(_box(b"mdat", b"\x00" * 8))
    except FaststartError:
        pass
    else:
        raise AssertionError("split_ftyp deveria falhar sem ftyp")
    print("[ok] split_ftyp extrai a box ftyp")


def test_stco_offsets_shifted() -> None:
    ftyp = _box(b"ftyp", b"isom" + b"\x00" * 8)
    # offsets originais (apontam para dentro do mdat no arquivo moov-at-end).
    orig_offsets = [1000, 5000, 999999]
    moov = _build_moov(_build_stco(orig_offsets))
    delta = len(moov)

    header = build_faststart_header(ftyp, moov)
    # O header deve ter ftyp seguido de moov.
    assert header[4:8] == b"ftyp"
    moov_pos = len(ftyp)
    assert header[moov_pos + 4:moov_pos + 8] == b"moov"

    rewritten_moov = header[moov_pos:]
    first = _read_first_offset(rewritten_moov, width=4)
    assert first == orig_offsets[0] + delta, (first, orig_offsets[0] + delta)
    print(f"[ok] stco (32-bit) deslocado por delta={delta}")


def test_co64_offsets_shifted() -> None:
    ftyp = _box(b"ftyp", b"isom" + b"\x00" * 8)
    orig_offsets = [4_000_000_000, 8_000_000_000]  # exige 64 bits
    moov = _build_moov(_build_co64(orig_offsets))
    delta = len(moov)

    header = build_faststart_header(ftyp, moov)
    rewritten_moov = header[len(ftyp):]
    first = _read_first_offset(rewritten_moov, width=8)
    assert first == orig_offsets[0] + delta
    print(f"[ok] co64 (64-bit) deslocado por delta={delta}")


def test_moov_before_mdat_layout() -> None:
    """Simula a montagem completa do arquivo lógico faststart."""
    ftyp = _box(b"ftyp", b"isom" + b"\x00" * 8)
    moov = _build_moov(_build_stco([16, 200]))
    mdat = _box(b"mdat", b"\xCD" * 512)
    header = build_faststart_header(ftyp, moov)
    logical = header + mdat
    # Ordem das boxes no arquivo lógico: ftyp, moov, mdat.
    assert logical[4:8] == b"ftyp"
    assert logical[len(ftyp) + 4:len(ftyp) + 8] == b"moov"
    mdat_pos = len(ftyp) + len(moov)
    assert logical[mdat_pos + 4:mdat_pos + 8] == b"mdat"
    print("[ok] layout lógico ftyp -> moov -> mdat")


def test_oversize_moov_fallback() -> None:
    from tgplayer.mp4_faststart import MAX_MOOV_REWRITE

    ftyp = _box(b"ftyp", b"isom")
    # moov realmente grande (acima do limite de reescrita em memória).
    fake_moov = _box(b"moov", b"\x00" * (MAX_MOOV_REWRITE + 100))
    try:
        build_faststart_header(ftyp, fake_moov)
    except FaststartError:
        print("[ok] moov grande demais -> FaststartError (fallback)")
    else:
        raise AssertionError("moov gigante deveria forçar fallback")


class FakeClient:
    """Simula stream_media entregando chunks de 1 MiB (como nos outros testes)."""

    def __init__(self, data: bytes) -> None:
        self.data = data

    async def get_messages(self, chat_id, mid):  # noqa: ANN001
        return {"id": mid}

    async def stream_media(self, message, offset=0, limit=1):  # noqa: ANN001
        one_mib = 1024 * 1024
        start = offset * one_mib
        end = start + limit * one_mib
        pos = start
        while pos < min(end, len(self.data)):
            await asyncio.sleep(0)
            chunk = self.data[pos:pos + one_mib]
            if not chunk:
                break
            yield chunk
            pos += len(chunk)


def _build_moov_at_end_mp4(total: int) -> tuple[bytes, bytes, bytes]:
    """Cria um MP4 moov-at-end: ftyp + mdat + moov. Retorna (file, ftyp, moov)."""
    ftyp = _box(b"ftyp", b"isom" + b"\x00" * 8)
    # offsets de chunk apontam para dentro do mdat (posições físicas originais).
    moov = _build_moov(_build_stco([len(ftyp) + 8, len(ftyp) + 64]))
    pad = total - len(ftyp) - len(moov) - 8
    pad = max(8, pad)
    mdat = _box(b"mdat", bytes((i % 251) for i in range(pad)))
    data = ftyp + mdat + moov
    return data, ftyp, moov


async def _test_end_to_end_faststart() -> None:
    total = 3 * BLOCK_SIZE + 4321
    data, ftyp, moov = _build_moov_at_end_mp4(total)
    total = len(data)
    tmp = tempfile.mktemp(suffix=".part")
    sess = StreamSession("fs", FakeClient(data), 1, 1, total, Path(tmp))
    sess.prefetch_start()

    active = await sess.ensure_faststart()
    assert active, "faststart deveria ativar para moov-at-end"
    assert sess.faststart_header is not None

    # 1) O início LÓGICO deve ser ftyp seguido de moov (faststart).
    head = bytearray()
    h = len(sess.faststart_header)
    async for chunk in sess.read_logical_range(0, h + 32):
        head.extend(chunk)
    assert bytes(head[4:8]) == b"ftyp"
    assert bytes(head[len(ftyp) + 4:len(ftyp) + 8]) == b"moov"
    print("[ok] read_logical_range serve ftyp -> moov no início")

    # 2) O mdat lógico (logo após o header) deve casar com o mdat físico.
    mdat_phys_start = len(ftyp)  # no original, mdat vem após ftyp
    logical_mdat = bytearray()
    async for chunk in sess.read_logical_range(h, h + 200):
        logical_mdat.extend(chunk)
    assert bytes(logical_mdat) == data[mdat_phys_start:mdat_phys_start + len(logical_mdat)]
    print("[ok] mdat lógico mapeado corretamente para o físico")

    # 3) logical_size permanece igual ao tamanho original.
    assert sess.logical_size == total
    await sess.close()
    assert not os.path.exists(tmp)
    print("[ok] faststart end-to-end (mapeamento lógico->físico) correto")


async def _test_faststart_already_fast() -> None:
    """Arquivo já faststart (moov no início) NÃO deve reescrever."""
    ftyp = _box(b"ftyp", b"isom" + b"\x00" * 8)
    moov = _build_moov(_build_stco([100, 200]))
    mdat = _box(b"mdat", b"\xEE" * (BLOCK_SIZE))
    data = ftyp + moov + mdat
    tmp = tempfile.mktemp(suffix=".part")
    sess = StreamSession("ff", FakeClient(data), 1, 1, len(data), Path(tmp))
    sess.prefetch_start()
    active = await sess.ensure_faststart()
    assert not active, "arquivo já faststart não deve ativar reescrita"
    # read_logical_range deve delegar para read_range (arquivo como está).
    got = bytearray()
    async for chunk in sess.read_logical_range(0, len(ftyp) + 3):
        got.extend(chunk)
    assert bytes(got[4:8]) == b"ftyp"
    await sess.close()
    print("[ok] arquivo já faststart -> servido como está (sem reescrita)")


def main() -> None:
    test_split_ftyp()
    test_stco_offsets_shifted()
    test_co64_offsets_shifted()
    test_moov_before_mdat_layout()
    test_oversize_moov_fallback()
    asyncio.run(_test_end_to_end_faststart())
    asyncio.run(_test_faststart_already_fast())
    print("TODOS OS TESTES DE FASTSTART (v6.4) PASSARAM")


if __name__ == "__main__":
    main()

"""Smoke tests das novidades v6.3 portadas do caamer20/Telegram-Drive.

Cobre:
  1. Alinhamento de offset à fronteira de CDN (512 KiB).
  2. Descoberta do átomo `moov` em 3 passos (head e tail), com scanner de boxes.
  3. Cache de moov no SQLite (hit/miss + LRU).
  4. Seleção adaptativa de qualidade por banda.

Executar:
    python tests/test_streaming_v63.py
"""

from __future__ import annotations

import asyncio
import os
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tgplayer.db import MOOV_CACHE_LIMIT, Database
from tgplayer.quality import (
    adaptive_quality,
    cap_to_source,
    throttle_for,
)
from tgplayer.stream_cache import (
    BLOCK_SIZE,
    CDN_ALIGNMENT,
    StreamSession,
    align_down,
)


def _build_mp4(moov_at_end: bool, total: int) -> bytes:
    """Constrói um MP4 sintético com ftyp + (mdat/moov) na ordem desejada."""
    def box(btype: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload) + 8) + btype + payload

    ftyp = box(b"ftyp", b"isom" + b"\x00" * 8)
    mvhd = box(b"mvhd", b"\x00" * 90)  # primeiro filho do moov (validação)
    moov = box(b"moov", mvhd + b"\x00" * 40)
    # Preenche mdat para chegar ao tamanho total desejado.
    pad = total - len(ftyp) - len(moov) - 8
    pad = max(8, pad)
    mdat = box(b"mdat", b"\xAB" * pad)
    data = (ftyp + mdat + moov) if moov_at_end else (ftyp + moov + mdat)
    # Normaliza o tamanho.
    if len(data) < total:
        data += b"\x00" * (total - len(data))
    return data[:total]


class FakeClient:
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
            chunk = self.data[pos: pos + one_mib]
            if not chunk:
                break
            yield chunk
            pos += len(chunk)


def test_cdn_alignment() -> None:
    assert align_down(0) == 0
    assert align_down(CDN_ALIGNMENT - 1) == 0
    assert align_down(CDN_ALIGNMENT) == CDN_ALIGNMENT
    assert align_down(CDN_ALIGNMENT + 123) == CDN_ALIGNMENT
    # invariante: alinhado <= pedido
    for v in (1, 500_000, 1_048_576, 3_000_000):
        assert align_down(v) <= v
    print("[ok] alinhamento de CDN (512 KiB) correto")


async def _test_moov_discovery() -> None:
    size = 4 * BLOCK_SIZE + 1234
    # 1) moov no INÍCIO (faststart).
    head_data = _build_mp4(moov_at_end=False, total=size)
    tmp = tempfile.mktemp(suffix=".part")
    sess = StreamSession("t1", FakeClient(head_data), 1, 1, size, Path(tmp))
    info = await sess.discover_moov()
    assert info["found"], "moov no início não encontrado"
    assert info["located"] == 1
    await sess.close()
    print("[ok] descoberta do moov no INÍCIO (faststart)")

    # 2) moov no FIM (moov-at-end).
    tail_data = _build_mp4(moov_at_end=True, total=size)
    tmp2 = tempfile.mktemp(suffix=".part")
    sess2 = StreamSession("t2", FakeClient(tail_data), 1, 2, size, Path(tmp2))
    info2 = await sess2.discover_moov()
    assert info2["found"], "moov na cauda não encontrado"
    assert info2["located"] == 2
    await sess2.close()
    print("[ok] descoberta do moov na CAUDA (moov-at-end)")


def test_moov_cache() -> None:
    tmp = tempfile.mktemp(suffix=".sqlite3")
    db = Database(tmp)
    assert db.get_moov_cache("c", 1) is None  # miss
    db.set_moov_cache("c", 1, 1000, 800, 200, located=2, width=1920, height=1080)
    hit = db.get_moov_cache("c", 1)
    assert hit and hit["moov_offset"] == 800 and hit["height"] == 1080
    print("[ok] cache de moov: miss -> set -> hit")

    # LRU: insere além do limite e confirma poda.
    for i in range(MOOV_CACHE_LIMIT + 20):
        db.set_moov_cache("c", 1000 + i, 10, 1, 1)
    with db.connect() as conn:
        n = conn.execute("SELECT COUNT(*) AS n FROM moov_cache").fetchone()["n"]
    assert n <= MOOV_CACHE_LIMIT, f"LRU não podou (n={n})"
    print(f"[ok] cache de moov: LRU mantém <= {MOOV_CACHE_LIMIT} entradas (n={n})")
    os.unlink(tmp)


def test_adaptive_quality() -> None:
    assert adaptive_quality(5000) == "1080p"
    assert adaptive_quality(3000) == "720p"
    assert adaptive_quality(1200) == "480p"
    assert adaptive_quality(300) == "360p"
    assert throttle_for("720p") == 2500
    assert throttle_for("original") == 0
    # Não faz upscale acima da fonte.
    assert cap_to_source("1080p", 480) in ("480p", "360p")
    assert cap_to_source("720p", 1080) == "720p"
    print("[ok] seleção adaptativa de qualidade e throttle corretos")


def main() -> None:
    test_cdn_alignment()
    asyncio.run(_test_moov_discovery())
    test_moov_cache()
    test_adaptive_quality()
    print("TODOS OS TESTES v6.3 (streaming/qualidade/moov) PASSARAM")


if __name__ == "__main__":
    main()

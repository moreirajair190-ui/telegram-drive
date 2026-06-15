"""Smoke tests do streaming acelerado (partida rápida + yield parcial).

Executar:
    python tests/test_stream_cache.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tgplayer.stream_cache import BLOCK_SIZE, FAST_FIRST_BYTES, StreamSession


class FakeClient:
    """Simula stream_media entregando chunks de 1 MiB com pequeno atraso."""

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
            await asyncio.sleep(0.01)
            chunk = self.data[pos : pos + one_mib]
            if not chunk:
                break
            yield chunk
            pos += len(chunk)


async def _run() -> None:
    size = 5 * BLOCK_SIZE + 12345
    data = bytes((i % 251) for i in range(size))
    tmp = tempfile.mktemp(suffix=".part")
    sess = StreamSession("tok", FakeClient(data), 123, 7, size, Path(tmp))
    sess.prefetch_start()

    # 1) Partida rápida: primeiros 256 KiB sem esperar o bloco inteiro.
    got = bytearray()
    async for chunk in sess.read_range(0, FAST_FIRST_BYTES - 1):
        got.extend(chunk)
        if len(got) >= FAST_FIRST_BYTES:
            break
    assert bytes(got[:FAST_FIRST_BYTES]) == data[:FAST_FIRST_BYTES]
    print("[ok] partida rápida (256 KiB) lida e correta")

    # 2) Seek para o meio.
    s, e = 3 * BLOCK_SIZE + 100, 3 * BLOCK_SIZE + 5000
    mid = bytearray()
    async for chunk in sess.read_range(s, e):
        mid.extend(chunk)
    assert bytes(mid) == data[s : e + 1]
    print("[ok] seek/intervalo do meio correto")

    # 3) Cauda (onde costuma ficar o moov).
    s, e = size - 1000, size - 1
    tail = bytearray()
    async for chunk in sess.read_range(s, e):
        tail.extend(chunk)
    assert bytes(tail) == data[s : e + 1]
    print("[ok] cauda (moov) correta; buffer_ratio=%.2f" % sess.buffer_ratio())

    # 4) Cache apagado ao fechar.
    await sess.close()
    assert not os.path.exists(tmp)
    print("[ok] cache temporário apagado ao fechar")


def main() -> None:
    asyncio.run(_run())
    print("TODOS OS TESTES DE STREAM PASSARAM")


if __name__ == "__main__":
    main()

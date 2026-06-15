"""Presets de qualidade e lógica adaptativa de banda.

Ideias portadas de `src/types.ts` do projeto caamer20/Telegram-Drive
(`QUALITY_THROTTLE_MAP`, `ADAPTIVE_THRESHOLDS`, `StreamingQuality`).

Como o TgPlayer usa um player NATIVO (libVLC/QMediaPlayer) que lida bem com MP4
progressivo, não há reencode no cliente. A "qualidade" aqui é implementada como
um **throttle de banda** no servidor local de streaming: limitar a taxa de
entrega (kbps) emula a experiência de qualidades menores em redes lentas e
estabiliza a reprodução, sem FFmpeg. O modo **adaptativo** mede a banda real e
escolhe o preset automaticamente.
"""

from __future__ import annotations

# Presets disponíveis (do menor para o maior).
QUALITIES = ["360p", "480p", "720p", "1080p", "original"]

# Throttle por qualidade (kbps). 0 = ilimitado (sem throttle).
QUALITY_THROTTLE_MAP: dict[str, int] = {
    "360p": 500,
    "480p": 1000,
    "720p": 2500,
    "1080p": 5000,
    "original": 0,
}

# Altura (px) aproximada de cada preset, para impedir "upscale" acima da fonte.
QUALITY_HEIGHT: dict[str, int] = {
    "360p": 360,
    "480p": 480,
    "720p": 720,
    "1080p": 1080,
    "original": 100000,
}

# Limiares (kbps) do modo adaptativo: banda medida -> qualidade escolhida.
ADAPTIVE_THRESHOLDS: list[tuple[int, str]] = [
    (4000, "1080p"),
    (2000, "720p"),
    (800, "480p"),
    (0, "360p"),
]


def throttle_for(quality: str) -> int:
    """Retorna o throttle (kbps) para um preset de qualidade."""
    return QUALITY_THROTTLE_MAP.get((quality or "original").lower(), 0)


def adaptive_quality(measured_kbps: float) -> str:
    """Escolhe a qualidade ideal para a banda medida (modo adaptativo)."""
    for threshold, quality in ADAPTIVE_THRESHOLDS:
        if measured_kbps >= threshold:
            return quality
    return "360p"


def cap_to_source(quality: str, source_height: int | None) -> str:
    """Impede selecionar uma qualidade maior que a resolução de origem."""
    if not source_height or source_height <= 0:
        return quality
    if quality == "original":
        return quality
    if QUALITY_HEIGHT.get(quality, 0) > source_height:
        # Encontra o maior preset que ainda cabe na fonte.
        for q in ("1080p", "720p", "480p", "360p"):
            if QUALITY_HEIGHT[q] <= source_height:
                return q
        return "360p"
    return quality


def label_for(quality: str) -> str:
    """Rótulo curto para o badge do player (ex.: '720p · 2.5k')."""
    kbps = QUALITY_THROTTLE_MAP.get(quality, 0)
    if quality == "original" or kbps == 0:
        return "Original"
    return f"{quality} · {kbps / 1000:.1f}k".replace(".0k", "k")

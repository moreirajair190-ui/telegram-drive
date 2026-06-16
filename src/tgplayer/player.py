"""Player local legado removido.

A partir da versão 6.4.4, o TgPlayer não usa mais player local embutido
(QtMultimedia/QtWebEngine/libVLC). O fluxo principal abre a mensagem original no
Telegram Desktop/64Gram/Nekogram, com VLC externo como alternativa.

Este módulo permanece apenas como compatibilidade para imports antigos; ele não
é usado pela interface principal.
"""

from __future__ import annotations


class VideoPlayerDialog:  # pragma: no cover - compatibilidade legada
    def __init__(self, *args, **kwargs) -> None:  # noqa: D401, ANN002, ANN003
        raise RuntimeError(
            "O player local foi removido do TgPlayer. Use Abrir no Telegram ou Abrir no VLC."
        )

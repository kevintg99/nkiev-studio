"""Windows: nada de janela de terminal piscando a cada FFmpeg/ffprobe.

O app (nKiev Studio) roda sem console (pythonw), então cada programa de linha de comando que o bot abre
ganharia uma janela própria do terminal. Aqui todo ``subprocess`` passa a nascer escondido, a menos
que quem chama já tenha escolhido as próprias ``creationflags``.
"""

from __future__ import annotations

import os
import subprocess

CREATE_NO_WINDOW = 0x08000000


def hide_console_windows(popen: type = subprocess.Popen) -> bool:
    if getattr(popen.__init__, "_sem_janela", False):
        return False
    original = popen.__init__

    def init(self, *args, **kwargs):
        if not kwargs.get("creationflags"):
            kwargs["creationflags"] = CREATE_NO_WINDOW
        original(self, *args, **kwargs)

    init._sem_janela = True  # type: ignore[attr-defined]
    popen.__init__ = init
    return True


if os.name == "nt":
    hide_console_windows()

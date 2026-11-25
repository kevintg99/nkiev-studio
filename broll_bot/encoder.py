"""Codificador de vídeo: o chip da placa de vídeo quando existe, senão o processador.

Editores como o CapCut codificam no chip de vídeo (Intel QuickSync, NVIDIA NVENC, AMD AMF),
várias vezes mais rápido que o libx264 no processador. ``output.encoder: auto`` testa cada
um com um vídeo minúsculo e usa o primeiro que funcionar; se nenhum funcionar (driver,
FFmpeg sem suporte), fica o libx264 de sempre. A qualidade segue o ``crf`` da config.
"""

from __future__ import annotations

import subprocess
from functools import lru_cache

from .log import get_logger

log = get_logger("encoder")

# ordem do "auto": placa dedicada primeiro, depois o chip integrado
AUTO_ORDER = ("nvenc", "qsv", "amf")
NAMES = {"nvenc": "NVIDIA NVENC", "qsv": "Intel QuickSync", "amf": "AMD AMF", "cpu": "processador (libx264)"}

_NVENC_PRESET = {"ultrafast": "p1", "superfast": "p1", "veryfast": "p2", "faster": "p3", "fast": "p3",
                 "medium": "p4", "slow": "p5", "slower": "p6", "veryslow": "p7"}
_QSV_PRESET = {"ultrafast": "veryfast", "superfast": "veryfast"}  # o resto tem o mesmo nome
_AMF_QUALITY = {"ultrafast": "speed", "superfast": "speed", "veryfast": "speed", "faster": "speed",
                "fast": "balanced", "medium": "balanced", "slow": "quality", "slower": "quality",
                "veryslow": "quality"}


def encode_args(encoder: str, crf: int, preset: str, fps: int) -> list[str]:
    """Argumentos do FFmpeg para codificar em H.264 com a qualidade ``crf`` (escala do x264)."""
    if encoder == "qsv":
        return ["-c:v", "h264_qsv", "-preset", _QSV_PRESET.get(preset, preset), "-global_quality", str(crf),
                "-pix_fmt", "nv12", "-r", str(fps)]
    if encoder == "nvenc":
        return ["-c:v", "h264_nvenc", "-preset", _NVENC_PRESET.get(preset, "p4"), "-rc", "vbr", "-cq", str(crf),
                "-b:v", "0", "-pix_fmt", "yuv420p", "-r", str(fps)]
    if encoder == "amf":
        return ["-c:v", "h264_amf", "-quality", _AMF_QUALITY.get(preset, "balanced"), "-rc", "cqp",
                "-qp_i", str(crf), "-qp_p", str(crf), "-pix_fmt", "nv12", "-r", str(fps)]
    return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p", "-r", str(fps)]


@lru_cache(maxsize=None)
def works(encoder: str) -> bool:
    """Codifica 10 quadros com as mesmas opções do vídeo de verdade (driver e FFmpeg precisam aceitar)."""
    if encoder == "cpu":
        return True
    cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=320x240:r=30",
           "-frames:v", "10", *encode_args(encoder, 20, "veryfast", 30), "-f", "null", "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def choose(preference: str) -> str:
    """ "auto" | "qsv" | "nvenc" | "amf" | "cpu" -> o codificador que será usado de fato."""
    pref = (preference or "auto").lower()
    if pref in ("cpu", "libx264", "x264"):
        return "cpu"
    wanted = AUTO_ORDER if pref == "auto" else (pref,)
    for enc in wanted:
        if enc in NAMES and works(enc):
            log.info("codificando com o chip de vídeo", extra={"stage": "render", "encoder": NAMES[enc]})
            return enc
    if pref != "auto":
        log.warning("codificador pedido não funciona aqui; usando o processador",
                    extra={"stage": "render", "encoder": pref})
    else:
        log.info("nenhum chip de vídeo disponível; codificando no processador", extra={"stage": "render"})
    return "cpu"

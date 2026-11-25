"""Fontes de frames para os templates: vídeo (lido via FFmpeg) ou imagem (com zoom lento)."""

from __future__ import annotations

import math
import subprocess
from pathlib import Path

from PIL import Image

from ..media import IMAGE_EXTS
from .gfx import cover


class FrameSource:
    """Entrega frames RGB do tamanho pedido, na ordem (frame 0, 1, 2...)."""

    def frame(self, i: int) -> Image.Image:  # pragma: no cover - interface
        raise NotImplementedError

    def close(self) -> None:
        pass


class ImageSource(FrameSource):
    """Imagem com zoom/pan lento (Ken Burns) para não ficar estática."""

    def __init__(self, path: Path, w: int, h: int, frames: int, zoom: float = 1.08, pan: tuple[float, float] = (0.0, 0.0)) -> None:
        with Image.open(path) as img:
            self.base = cover(img.convert("RGB"), round(w * zoom) + 2, round(h * zoom) + 2)
        self.w, self.h, self.n, self.zoom, self.pan = w, h, max(1, frames - 1), zoom, pan

    def frame(self, i: int) -> Image.Image:
        x = min(1.0, i / self.n)
        p = 0.5 - 0.5 * math.cos(math.pi * x)  # acelera e freia suave, como câmera
        z = 1 + (self.zoom - 1) * p  # 1.0 -> zoom
        cw, ch = self.base.width / z, self.base.height / z
        cx = self.base.width / 2 + self.pan[0] * (self.base.width - cw) / 2 * p
        cy = self.base.height / 2 + self.pan[1] * (self.base.height - ch) / 2 * p
        box = (cx - cw / 2, cy - ch / 2, cx + cw / 2, cy + ch / 2)
        # box com fração de pixel: o movimento lento fica contínuo, sem saltos
        return self.base.resize((self.w, self.h), Image.BICUBIC, box=box)


class VideoSource(FrameSource):
    """Lê o vídeo já redimensionado pelo FFmpeg (cover + crop) direto de um pipe."""

    def __init__(self, path: Path, w: int, h: int, fps: int, frames: int, offset: float = 0.0,
                 duration: float | None = None, speed: float = 1.0) -> None:
        self.w, self.h = w, h
        self.frame_bytes = w * h * 3
        # sem loop: clipe curto voltando ao começo "vai e volta" na tela; acabou, congela o último frame
        vf = (f"setpts=(PTS-STARTPTS)*{speed:.4f},fps={fps},"
              f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=bilinear,crop={w}:{h},format=rgb24")
        self.proc = subprocess.Popen(
            ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{offset:.3f}", "-i", str(path),
             "-vf", vf, "-frames:v", str(frames), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        self.pos = -1
        self.last = Image.new("RGB", (w, h), (0, 0, 0))

    def frame(self, i: int) -> Image.Image:
        while self.pos < i:
            assert self.proc.stdout is not None
            data = self.proc.stdout.read(self.frame_bytes)
            self.pos += 1
            if len(data) < self.frame_bytes:  # acabou antes: congela o último frame
                self.pos = i
                break
            self.last = Image.frombytes("RGB", (self.w, self.h), data)
        return self.last

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait()


def open_source(path: str | Path, w: int, h: int, fps: int, frames: int, *, offset: float = 0.0,
                duration: float | None = None, speed: float = 1.0, zoom: float = 1.08,
                pan: tuple[float, float] = (0.0, 0.0)) -> FrameSource:
    path = Path(path)
    if path.suffix.lower() in IMAGE_EXTS:
        return ImageSource(path, w, h, frames, zoom=zoom, pan=pan)
    return VideoSource(path, w, h, fps, frames, offset=offset, duration=duration, speed=speed)

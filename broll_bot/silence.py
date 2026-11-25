"""Corta as pausas da narração (jump cut de áudio) antes de tudo: vídeo curto não pode ter buraco.

Lê o áudio pelo FFmpeg (PCM), mede a energia em janelas de 10 ms e encurta todo trecho sem voz mais
longo que ``max_pause`` para ``keep_pause`` (fica um respiro, senão a fala vira uma metralhadora).
Os cortes têm um cruzamento curto (``fade_ms``) para não estalar. O resto do bot (transcrição, cenas,
avatar) roda no áudio já cortado, então nenhum tempo precisa ser remapeado.

O limiar de silêncio é relativo à própria narração: o mais alto entre ``threshold_db`` e o volume típico
da fala (percentil 90) menos 30 dB — narração de IA tem silêncio quase digital, gravação caseira tem ruído.
"""

from __future__ import annotations

import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .log import get_logger

log = get_logger("silence")

RATE = 48000
CHANNELS = 2
FRAME_S = 0.01  # janela de energia


@dataclass
class SilenceResult:
    path: Path
    before: float
    after: float
    cuts: int

    @property
    def removed(self) -> float:
        return round(self.before - self.after, 2)


def _decode(path: Path) -> np.ndarray:
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-f", "s16le", "-acodec", "pcm_s16le",
           "-ac", str(CHANNELS), "-ar", str(RATE), "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True, timeout=600).stdout
    return np.frombuffer(raw, dtype=np.int16).reshape(-1, CHANNELS)


def silent_spans(samples: np.ndarray, max_pause: float, threshold_db: float = -42.0,
                 rate: int = RATE) -> list[tuple[int, int]]:
    """Trechos (em amostras) sem voz mais longos que ``max_pause``."""
    hop = max(1, round(rate * FRAME_S))
    n = len(samples) // hop
    if n == 0:
        return []
    mono = samples[: n * hop].astype(np.float32).mean(axis=1) / 32768.0
    rms = np.sqrt(np.mean(mono.reshape(n, hop) ** 2, axis=1) + 1e-12)
    db = 20 * np.log10(rms)
    voiced = db[db > -70]
    speech = float(np.percentile(voiced, 90)) if voiced.size else -20.0
    limit = max(threshold_db, speech - 30.0)
    quiet = db < limit
    spans: list[tuple[int, int]] = []
    k = 0
    min_frames = max(1, round(max_pause / FRAME_S))
    while k < n:
        if not quiet[k]:
            k += 1
            continue
        j = k
        while j < n and quiet[j]:
            j += 1
        if j - k >= min_frames:
            spans.append((k * hop, min(len(samples), j * hop)))
        k = j
    return spans


def keep_ranges(total: int, spans: list[tuple[int, int]], keep_pause: float, lead: float = 0.06,
                tail: float = 0.25, rate: int = RATE) -> list[tuple[int, int]]:
    """Pedaços que ficam: cada pausa longa vira ``keep_pause`` (metade de cada lado da fala); o silêncio
    do começo vira ``lead`` e o do fim, ``tail``."""
    half = round(keep_pause * rate / 2)
    out: list[tuple[int, int]] = []
    pos = 0
    for a, b in spans:
        if a <= 0:  # silêncio no começo
            pos = max(0, b - round(lead * rate))
            continue
        if b >= total:  # silêncio no fim
            out.append((pos, min(total, a + round(tail * rate))))
            pos = total
            break
        out.append((pos, a + half))
        pos = b - half
    if pos < total:
        out.append((pos, total))
    return [(a, b) for a, b in out if b > a]


def _splice(samples: np.ndarray, ranges: list[tuple[int, int]], fade: int) -> np.ndarray:
    parts = []
    for a, b in ranges:
        seg = samples[a:b].astype(np.float32)
        f = min(fade, len(seg) // 2)
        if f > 0:
            ramp = np.linspace(0.0, 1.0, f, dtype=np.float32)[:, None]
            seg[:f] *= ramp
            seg[-f:] *= ramp[::-1]
        parts.append(seg)
    out = np.concatenate(parts) if parts else samples[:0].astype(np.float32)
    return np.clip(out, -32768, 32767).astype(np.int16)


def cut_silences(path: Path, dest: Path, max_pause: float = 0.35, keep_pause: float = 0.12,
                 threshold_db: float = -42.0, fade_ms: float = 6.0) -> SilenceResult:
    """Grava em ``dest`` (WAV) a narração sem as pausas longas."""
    samples = _decode(path)
    total = len(samples)
    spans = silent_spans(samples, max_pause, threshold_db)
    ranges = keep_ranges(total, spans, keep_pause)
    out = _splice(samples, ranges, round(fade_ms / 1000 * RATE))
    dest.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(dest), "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)
        wf.setframerate(RATE)
        wf.writeframes(out.tobytes())
    res = SilenceResult(dest, round(total / RATE, 3), round(len(out) / RATE, 3), max(0, len(ranges) - 1))
    log.info("pausas da narração cortadas", extra={"stage": "input", "antes_s": res.before, "depois_s": res.after,
                                                   "cortes": res.cuts, "tirou_s": res.removed})
    return res

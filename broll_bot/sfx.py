"""Efeitos sonoros sincronizados com as animações, transições e overlays.

Os sons são sintetizados com NumPy (sem arquivos de terceiros e sem licença a
cuidar): teclado, whoosh, pop, tick, ding, impacto, glitch, riser, queima de filme
e fita VHS. Para usar sons próprios, ponha arquivos em ``library/sfx`` com o nome
do efeito no começo (``whoosh_1.wav``, ``typing.mp3``, ``ding-soft.wav``...): eles
substituem os gerados (vários arquivos do mesmo efeito são usados em rodízio).

A trilha de efeitos é mixada por baixo da narração num WAV único, que entra no
passo final da montagem.
"""

from __future__ import annotations

import random
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .config import AppConfig, SfxConfig
from .log import get_logger
from .models import Scene, Shot
from .music import MusicSegment, build_music

log = get_logger("sfx")

SR = 48000
NAMES = ("typing", "click", "whoosh", "whoosh_soft", "pop", "tick", "ding", "impact", "impact_soft",
         "glitch", "riser", "burn", "tape", "swipe", "snap", "pencil", "bass", "stamp", "scratch")
LONG_SOUNDS = ("typing", "pencil")  # sons com duração pedida pelo template


@dataclass(slots=True)
class SfxEvent:
    time: float  # segundos na linha do tempo (quando o som começa)
    name: str
    gain: float = 1.0
    length: float = 0.0  # só para "typing"/"pencil": duração da digitação/do desenho


# ---------------------------------------------------------------- síntese
def _env(n: int, attack: float, decay: float) -> np.ndarray:
    t = np.arange(n) / SR
    a = np.clip(t / max(attack, 1e-4), 0, 1)
    return a * np.exp(-t / max(decay, 1e-4))


def _onepole(x: np.ndarray, coef: np.ndarray | float) -> np.ndarray:
    """Passa-baixa de 1 polo (coeficiente pode variar no tempo)."""
    out = np.empty_like(x)
    c = np.broadcast_to(np.asarray(coef, np.float32), x.shape)
    y = 0.0
    for i in range(len(x)):
        y += c[i] * (x[i] - y)
        out[i] = y
    return out


def _norm(x: np.ndarray, peak: float = 0.9) -> np.ndarray:
    m = float(np.max(np.abs(x))) if len(x) else 0.0
    return (x / m * peak).astype(np.float32) if m > 0 else x.astype(np.float32)


def synth(name: str, rng: np.random.Generator, length: float = 0.0) -> np.ndarray:
    t = lambda sec: np.arange(int(sec * SR)) / SR  # noqa: E731
    if name == "click":
        n = int(0.03 * SR)
        noise = rng.standard_normal(n).astype(np.float32)
        hp = np.diff(noise, prepend=0) * _env(n, 0.0005, 0.004)
        thock = np.sin(2 * np.pi * rng.uniform(170, 260) * t(0.03)) * _env(n, 0.001, 0.012) * 0.5
        return _norm(hp + thock, rng.uniform(0.55, 0.9))
    if name == "typing":
        total = max(0.12, length)
        out = np.zeros(int((total + 0.05) * SR), np.float32)
        pos = 0.0
        while pos < total:
            c = synth("click", rng)
            if rng.random() < 0.12:  # barra de espaço: mais grave e forte
                c = _norm(c + np.pad(synth("click", rng), (0, 0)) * 0.5, 1.0)
            i = int(pos * SR)
            out[i:i + len(c)] += c[: len(out) - i]
            pos += rng.uniform(0.045, 0.095)
        return _norm(out, 0.9)
    if name in ("whoosh", "whoosh_soft", "riser", "burn"):
        dur = {"whoosh": 0.5, "whoosh_soft": 0.36, "riser": 0.7, "burn": 0.65}[name]
        n = int(dur * SR)
        x = rng.standard_normal(n).astype(np.float32)
        p = np.linspace(0, 1, n, dtype=np.float32)
        peak = 0.95 if name == "riser" else 0.6
        shape = np.where(p < peak, (p / peak) ** 2, np.clip(1 - (p - peak) / (1 - peak), 0, 1) ** 1.5)
        lo, hi = (0.01, 0.2) if name != "whoosh_soft" else (0.008, 0.09)
        coef = (lo + (hi - lo) * shape).astype(np.float32)
        band = _onepole(x, coef) - _onepole(x, coef * 0.25)
        sig = band * shape
        if name == "riser":
            sig = sig + np.sin(2 * np.pi * np.cumsum(200 + 700 * p ** 2) / SR) * shape * 0.25
        if name == "burn":  # estalos de película por cima
            crackle = (rng.random(n) > 0.9985).astype(np.float32) * rng.uniform(0.3, 1.0, n)
            sig = sig + np.convolve(crackle, np.exp(-np.arange(90) / 12.0), mode="same") * 0.5 * shape
        return _norm(sig, 0.9)
    if name == "pop":
        dur = 0.09
        f = np.linspace(950, 320, int(dur * SR))
        s = np.sin(2 * np.pi * np.cumsum(f) / SR) * _env(len(f), 0.001, 0.025)
        return _norm(s, 0.8)
    if name == "tick":
        s = np.sin(2 * np.pi * 2300 * t(0.018)) * _env(int(0.018 * SR), 0.0003, 0.004)
        return _norm(s, 0.6)
    if name == "ding":
        tt = t(0.9)
        s = (np.sin(2 * np.pi * 1318.5 * tt) + 0.35 * np.sin(2 * np.pi * 2637 * tt)
             + 0.15 * np.sin(2 * np.pi * 3955 * tt)) * _env(len(tt), 0.002, 0.28)
        return _norm(s, 0.7)
    if name in ("impact", "impact_soft"):
        dur = 0.8 if name == "impact" else 0.38
        n = int(dur * SR)
        f = 48 + 70 * np.exp(-np.arange(n) / (0.05 * SR))
        body = np.sin(2 * np.pi * np.cumsum(f) / SR) * _env(n, 0.001, dur * 0.35)
        hit = _onepole(rng.standard_normal(n).astype(np.float32), 0.15) * _env(n, 0.0005, 0.02)
        return _norm(body + hit * 0.8, 0.95 if name == "impact" else 0.7)
    if name == "glitch":
        n = int(0.28 * SR)
        out = np.zeros(n, np.float32)
        pos = 0
        while pos < n:
            seg = int(rng.uniform(0.012, 0.05) * SR)
            kind = rng.integers(0, 3)
            tt = np.arange(seg) / SR
            if kind == 0:
                s = np.sign(np.sin(2 * np.pi * rng.uniform(80, 900) * tt))
            elif kind == 1:
                s = np.round(rng.standard_normal(seg) * 3) / 3  # ruído "bitcrushed"
            else:
                s = np.zeros(seg)
            out[pos:pos + seg] = s[: n - pos] * rng.uniform(0.3, 1.0)
            pos += seg
        return _norm(out, 0.6)
    if name == "tape":
        n = int(0.45 * SR)
        hum = np.sin(2 * np.pi * 60 * np.arange(n) / SR) * 0.2
        hiss = _onepole(rng.standard_normal(n).astype(np.float32), 0.3) * 0.25
        clunk = synth("impact_soft", rng)[: n] * 0.8
        return _norm(hum + hiss + np.pad(clunk, (0, max(0, n - len(clunk))))[:n], 0.7)
    if name == "swipe":  # marca-texto riscando o papel
        n = int(0.2 * SR)
        x = rng.standard_normal(n).astype(np.float32)
        p = np.linspace(0, 1, n, dtype=np.float32)
        shape = np.minimum(1, p / 0.7) * np.clip((1 - p) / 0.3, 0, 1)
        band = _onepole(x, 0.35) - _onepole(x, 0.08)
        return _norm(band * shape, 0.6)
    if name == "pencil":  # lápis riscando: traços curtos de ruído agudo, com pausas
        n = int(max(0.2, length or 1.5) * SR)
        out = np.zeros(n, np.float32)
        at = 0
        while at < n:
            ln = int(rng.uniform(0.07, 0.2) * SR)
            x = rng.standard_normal(ln).astype(np.float32)
            band = _onepole(x, 0.55) - _onepole(x, 0.18)
            p = np.linspace(0, 1, ln, dtype=np.float32)
            stroke = band * np.clip(np.sin(np.pi * p), 0, 1) ** 0.6 * rng.uniform(0.5, 1.0)
            out[at:at + ln] += stroke[: n - at]
            at += ln + int(rng.uniform(0.015, 0.07) * SR)
        out[-int(0.03 * SR):] *= np.linspace(1, 0, int(0.03 * SR), dtype=np.float32)
        return _norm(out, 0.5)
    if name == "snap":  # obturador de câmera: dois cliques e um sopro curto
        n = int(0.16 * SR)
        out = np.zeros(n, np.float32)
        for at, g in ((0, 1.0), (int(0.065 * SR), 0.7)):
            c = synth("click", rng) * g
            out[at:at + len(c)] += c[: n - at]
        air = _onepole(rng.standard_normal(n).astype(np.float32), 0.25) * _env(n, 0.002, 0.03) * 0.5
        return _norm(out + air, 0.75)
    if name == "bass":  # queda grave do gancho (sub que desce de 90 para 35 Hz)
        n = int(0.9 * SR)
        f = 35 + 55 * np.exp(-np.arange(n) / (0.12 * SR))
        body = np.sin(2 * np.pi * np.cumsum(f) / SR) * _env(n, 0.004, 0.32)
        click = _onepole(rng.standard_normal(n).astype(np.float32), 0.3) * _env(n, 0.0005, 0.006) * 0.4
        return _norm(body + click, 0.95)
    if name == "stamp":  # carimbo batendo: baque seco + estalo do papel
        n = int(0.32 * SR)
        thud = synth("impact_soft", rng)[:n]
        thud = np.pad(thud, (0, max(0, n - len(thud))))[:n]
        slap = _onepole(rng.standard_normal(n).astype(np.float32), 0.6) * _env(n, 0.0003, 0.012)
        return _norm(thud * 0.9 + slap * 0.8, 0.9)
    if name == "scratch":  # disco arranhado (parada em preto e branco): ruído com o tom indo e voltando
        n = int(0.42 * SR)
        p = np.linspace(0, 1, n, dtype=np.float32)
        speed = np.sin(np.pi * p * 2.2) * 0.9
        x = rng.standard_normal(n).astype(np.float32)
        coef = np.clip(0.04 + 0.3 * np.abs(speed), 0.02, 0.5).astype(np.float32)
        band = _onepole(x, coef) - _onepole(x, coef * 0.3)
        tone = np.sin(2 * np.pi * np.cumsum(300 + 900 * speed) / SR) * 0.3
        return _norm((band + tone) * np.clip(np.sin(np.pi * p), 0, 1) ** 0.5, 0.75)
    raise KeyError(name)


# ----------------------------------------------------------------- banco
class SfxBank:
    """Sons prontos: arquivos do usuário (se houver) ou síntese com seed fixa."""

    def __init__(self, user_dir: Path | None, seed: int) -> None:
        self.rng = np.random.default_rng(seed)
        self.user: dict[str, list[Path]] = {}
        self.rot: dict[str, int] = {}
        self.cache: dict[str, list[np.ndarray]] = {}
        if user_dir is not None and user_dir.is_dir():
            files = sorted(p for p in user_dir.iterdir() if p.suffix.lower() in {".wav", ".mp3", ".ogg", ".flac", ".m4a"})
            for name in sorted(NAMES, key=len, reverse=True):  # "whoosh_soft" antes de "whoosh"
                for f in files:
                    if f.stem.lower().startswith(name) and not any(f in v for v in self.user.values()):
                        self.user.setdefault(name, []).append(f)

    def _decode(self, path: Path) -> np.ndarray | None:
        try:
            raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "1", "-ar", str(SR), "-"],
                                 capture_output=True, timeout=60, check=True).stdout
        except (subprocess.SubprocessError, OSError):
            log.warning("efeito sonoro ilegível; usando o gerado", extra={"stage": "sfx", "file": str(path)})
            return None
        return np.frombuffer(raw, np.float32).copy()

    def get(self, name: str, length: float = 0.0) -> np.ndarray:
        files = self.user.get(name)
        if files:
            k = self.rot.get(name, 0)
            self.rot[name] = k + 1
            audio = self._decode(files[k % len(files)])
            if audio is not None and len(audio):
                if name in LONG_SOUNDS and length > 0:  # corta/repete no tamanho pedido
                    need = int((length + 0.05) * SR)
                    audio = np.tile(audio, need // len(audio) + 1)[:need]
                    audio[-int(0.04 * SR):] *= np.linspace(1, 0, int(0.04 * SR), dtype=np.float32)
                return audio
        if name in LONG_SOUNDS:
            return synth(name, self.rng, length)
        variants = self.cache.setdefault(name, [])
        if len(variants) < 3:  # 3 variações de cada som, em rodízio
            variants.append(synth(name, self.rng))
        k = self.rot.get(name, 0)
        self.rot[name] = k + 1
        return variants[k % len(variants)]


# ----------------------------------------------------------------- eventos
EFFECT_SOUNDS: dict[str, tuple[str, float, float]] = {
    # efeito de corte -> (som, ganho, adiantamento em s para o pico cair no corte)
    "whip": ("whoosh", 0.7, 0.3), "punch": ("impact_soft", 0.6, 0.0), "glitch": ("glitch", 0.5, 0.02),
    "leak": ("whoosh_soft", 0.4, 0.2), "wash": ("whoosh_soft", 0.55, 0.2), "burn": ("burn", 0.55, 0.3),
    "blackout": ("glitch", 0.55, 0.0),
}
XFADE_WHOOSH = ("slide", "smooth", "wipe", "cover", "reveal", "circle", "zoomin", "hblur")

# tempos aproximados das animações dos templates antigos (s desde o início do shot)
TEMPLATE_SOUNDS: dict[str, list[tuple[float, str, float]]] = {
    "comparison": [(0.0, "whoosh", 0.5), (0.3, "whoosh_soft", 0.4), (0.6, "whoosh_soft", 0.4)],
    "versus": [(0.0, "whoosh", 0.55), (0.45, "impact_soft", 0.55)],
    "specs": [(0.0, "whoosh_soft", 0.45)] + [(0.45 + 0.25 * k, "pop", 0.35) for k in range(4)],
    "stat": [(0.15 + 0.1 * k, "tick", 0.3) for k in range(13)] + [(1.45, "ding", 0.5)],
    "gauge": [(0.15, "riser", 0.45), (1.55, "ding", 0.45)],
    "blueprint": [(0.0, "whoosh_soft", 0.45), (0.6, "tick", 0.4), (0.9, "tick", 0.4)],
    "ranking": [(0.0, "whoosh", 0.5), (0.35, "impact", 0.6)],
    "chapter": [(0.0, "whoosh", 0.55), (0.18, "impact_soft", 0.55)],
    "mosaic": [(0.0, "whoosh_soft", 0.45), (0.4, "tick", 0.35), (0.6, "tick", 0.35)],
    "hud": [(0.1, "tick", 0.45), (0.28, "tick", 0.45), (0.5, "whoosh_soft", 0.4)],
    "card": [(0.0, "glitch", 0.45), (0.2, "whoosh_soft", 0.4)],
    "vhs": [(0.0, "tape", 0.55)],
}


def _template_events(shot: Shot, fps: int) -> list[SfxEvent]:
    from .motion.templates import TemplateSpec
    from .motion.templates_doc import template_cues

    base = shot.start_frame / fps
    data: dict[str, Any] = shot.template_data or {}
    kind = shot.template or ""
    if kind == "checklist":  # um "pop" quando cada ponto aparece (sincronizado com a fala)
        return [SfxEvent(base + max(0.0, c[0]), "pop", 0.4) for c in data.get("captions", [])[:5]]
    cues = TEMPLATE_SOUNDS.get(kind)
    if cues is None:
        spec = TemplateSpec.from_dict({"theme": "navy", **data, "kind": kind, "w": 1920, "h": 1080, "fps": fps,
                                       "frames": shot.frames})
        cues = template_cues(kind, spec)
    return [_cue_event(base, c) for c in cues]


def _cue_event(base: float, cue: tuple[float, str, float]) -> SfxEvent:
    t, name, gain = cue
    if ":" in name:  # "typing:1.2", "pencil:2.5"
        sound, length = name.split(":", 1)
        return SfxEvent(base + t, sound, gain, float(length))
    return SfxEvent(base + t, name, gain)


def plan_events(shots: list[Shot], fps: int, cfg: SfxConfig | None = None, seed: int = 0) -> list[SfxEvent]:
    """Sons de cada shot. Transições: só uma parte delas (sorteio com a seed), mais baixas
    e espaçadas; templates e overlays sempre têm o som da própria animação."""
    from .motion.overlays import overlay_cues

    cfg = cfg or SfxConfig()
    rng = random.Random(seed * 131 + 7)
    events: list[SfxEvent] = []
    last_transition = -1e9
    for shot in shots:
        cut = shot.cut_in
        tr = shot.transition_in
        sound: tuple[str, float, float] | None = None
        if shot.effect_in in EFFECT_SOUNDS:
            sound = EFFECT_SOUNDS[shot.effect_in]
        elif not tr.is_cut and any(tr.kind.startswith(p) for p in XFADE_WHOOSH):
            sound = ("whoosh_soft", 0.5, 0.2)
        if sound is not None and cut - last_transition >= cfg.transition_min_gap \
                and rng.random() < cfg.transition_probability:
            name, gain, lead = sound
            events.append(SfxEvent(max(0.0, cut - lead), name, gain * cfg.transition_volume))
            last_transition = cut
        if shot.template and shot.template_data:
            events += _template_events(shot, fps)
        if shot.overlay:
            base = shot.start_frame / fps
            events += [_cue_event(base, c) for c in overlay_cues(shot.overlay, shot.frames, fps)]
    return sorted(events, key=lambda e: e.time)


# ------------------------------------------------------------------ mixagem
def render_track(events: list[SfxEvent], seconds: float, bank: SfxBank) -> np.ndarray:
    out = np.zeros(int(seconds * SR) + SR, np.float32)
    for ev in events:
        audio = bank.get(ev.name, ev.length) * ev.gain
        i = int(ev.time * SR)
        if i >= len(out):
            continue
        n = min(len(audio), len(out) - i)
        out[i:i + n] += audio[:n]
    return out[: int(seconds * SR)]


def _soft_clip(x: np.ndarray, knee: float = 0.9) -> np.ndarray:
    a = np.abs(x)
    over = a > knee
    x[over] = np.sign(x[over]) * (knee + (1 - knee) * np.tanh((a[over] - knee) / (1 - knee)))
    return x


def load_voice(narration: Path) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(narration), "-f", "f32le", "-ac", "2", "-ar", str(SR), "-"],
                         capture_output=True, timeout=600, check=True).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, 2).copy()


def mix_voice(voice: np.ndarray, track: np.ndarray | None, volume: float, dest: Path,
              music: np.ndarray | None = None) -> Path:
    """Narração (estéreo 48 kHz) + efeitos e música por baixo -> WAV 16 bits."""
    voice = voice.copy()
    n = min(len(voice), len(track)) if track is not None else 0
    if n:
        # o volume dos efeitos acompanha a voz: o pico deles fica em `volume` x o pico
        # típico da narração (percentil 99,5, que ignora estalos isolados)
        voice_ref = float(np.percentile(np.abs(voice), 99.5)) or 0.3
        peak = float(np.max(np.abs(track[:n]))) or 1.0
        fx = track[:n] / peak * voice_ref * volume
        voice[:n] += fx[:, None]
    if music is not None:
        m = min(len(voice), len(music))
        voice[:m] += music[:m]
    _soft_clip(voice)
    pcm = (np.clip(voice, -1, 1) * 32767).astype("<i2")
    with wave.open(str(dest), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(pcm.tobytes())
    return dest


def mix_with_narration(narration: Path, track: np.ndarray, volume: float, dest: Path) -> Path:
    """Narração (estéreo 48 kHz) + efeitos por baixo -> WAV 16 bits."""
    return mix_voice(load_voice(narration), track, volume, dest)


def build_soundtrack(cfg: AppConfig, shots: list[Shot], scenes: list[Scene], narration: Path, seconds: float,
                     work_dir: Path, user_dir: Path | None, music_dir: Path | None = None,
                     extra: list[tuple[float, str, float]] | None = None) -> tuple[Path, int, list[MusicSegment]]:
    """Narração + efeitos + música de fundo num WAV só. Se uma parte falhar, as outras seguem."""
    voice = load_voice(narration)
    track, n_events = None, 0
    if cfg.sfx.enabled:
        try:
            events = plan_events(shots, cfg.output.fps, cfg.sfx, cfg.seed)
            if extra:  # sons dos efeitos do vídeo curto (broll_bot/shortsfx)
                events = sorted(events + [_cue_event(0.0, c) for c in extra], key=lambda e: e.time)
            bank = SfxBank(user_dir, cfg.seed)
            track, n_events = render_track(events, seconds, bank), len(events)
            log.info("efeitos sonoros mixados", extra={"stage": "sfx", "events": n_events,
                                                        "user_sounds": sorted(bank.user)})
        except Exception as exc:  # noqa: BLE001 - efeito sonoro nunca derruba o vídeo
            log.warning("efeitos sonoros falharam", extra={"stage": "sfx", "error": str(exc)[-300:]})
    music, segs = None, []
    if cfg.music.enabled:
        try:
            music, segs = build_music(cfg.music, scenes, voice, music_dir, cfg.seed)
        except Exception as exc:  # noqa: BLE001 - idem para a música
            log.warning("música de fundo falhou", extra={"stage": "music", "error": str(exc)[-300:]})
    dest = work_dir / "narracao_mix.wav"
    mix_voice(voice, track, cfg.sfx.volume, dest, music)
    return dest, n_events, (segs if music is not None else [])

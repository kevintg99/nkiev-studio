"""Música de fundo que acompanha o roteiro.

* Clima por cena: o ``tone`` que a IA escreve no plano ("dramatic", "technical",
  "upbeat"...) vira um de 7 climas (``MOODS``).
* Trechos: a música só troca quando o clima muda de verdade e o trecho atual já
  durou ``min_segment_seconds`` — de preferência num título/capítulo, que é onde o
  roteiro muda de assunto. Trecho muito longo troca de faixa (mesmo clima) para não
  cansar.
* Faixas: vêm de ``library/music``. O clima sai da pasta ou do nome do arquivo
  (``library/music/tensao/faixa.mp3``, ``epic-drive.mp3``); faixa sem clima serve
  para qualquer um. Faixa curta é repetida com crossfade.
* Mixagem: cada faixa é nivelada pela média (RMS) e fica ``volume`` x o nível da
  voz enquanto você fala, subindo para ``pause_volume`` nas pausas (ducking com
  antecipação, para não "bombear" entre palavras). Antes de cada título/capítulo a
  música respira (cai por um instante), o que dá peso à virada de assunto.
"""

from __future__ import annotations

import random
import re
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .config import MusicConfig
from .log import get_logger
from .models import Scene
from .text import normalize

log = get_logger("music")

SR = 48000
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".opus"}
MOODS = ("energetic", "epic", "tense", "calm", "tech", "emotional", "fun")

# começo de palavra (inglês e português, sem acento) -> clima
MOOD_STEMS: dict[str, tuple[str, ...]] = {
    "energetic": ("energ", "upbeat", "excit", "fast", "confiden", "power", "sport", "dynam", "bold", "aggress",
                  "action", "acao", "competi", "intens", "adrenal", "rapid", "empolg", "agit", "veloc", "rock",
                  "drive", "driving", "hype"),
    "epic": ("epic", "dramat", "grand", "inspir", "triumph", "cinemat", "majest", "hero", "impress", "ambit",
             "orchestr", "orquest", "trailer"),
    "tense": ("tens", "suspen", "myster", "misteri", "serious", "serio", "warn", "danger", "perig", "alert",
              "dark", "sombr", "urgen", "concern", "preocup", "critic", "caution", "cuidad", "fear", "medo", "risk",
              "risc", "problem", "thrill"),
    "calm": ("calm", "relax", "neutr", "inform", "friend", "soft", "chill", "lofi", "peace", "smooth", "practic",
             "pratic", "casual", "help", "educat", "didat", "explan", "tranquil", "suave", "ambient", "acoustic"),
    "tech": ("tech", "tecn", "modern", "futur", "analyt", "analit", "precis", "innov", "inov", "digit", "scien",
             "cienti", "detail", "detalh", "electr", "eletr", "corporat", "synth"),
    "emotional": ("emotion", "emoc", "nostalg", "sentim", "warm", "heart", "melanch", "reflect", "touch", "sad",
                  "triste", "tocant", "saudos", "piano"),
    "fun": ("fun", "play", "humor", "light", "cheer", "happy", "feliz", "quirk", "divert", "alegr", "engrac",
            "comic", "comedi"),
}

# se não houver faixa do clima, o que soa mais parecido
FALLBACK: dict[str, tuple[str, ...]] = {
    "energetic": ("energetic", "epic", "fun", "tech", "calm", "tense", "emotional"),
    "epic": ("epic", "energetic", "tense", "emotional", "tech", "calm", "fun"),
    "tense": ("tense", "epic", "tech", "calm", "emotional", "energetic", "fun"),
    "calm": ("calm", "tech", "emotional", "fun", "energetic", "epic", "tense"),
    "tech": ("tech", "calm", "energetic", "epic", "tense", "fun", "emotional"),
    "emotional": ("emotional", "calm", "epic", "tech", "tense", "fun", "energetic"),
    "fun": ("fun", "energetic", "calm", "tech", "emotional", "epic", "tense"),
}

# template sem tom claro: o próprio template sugere o clima
STYLE_MOOD = {"vhs": "emotional", "versus": "energetic", "ranking": "energetic", "counter": "energetic",
              "newsrank": "energetic", "paperrank": "energetic", "wordpop": "energetic", "textmask": "energetic",
              "hud": "tech", "chart": "tech", "checklist": "calm", "quote": "calm"}
TITLE_STYLES = ("chapter", "headline")


def _word_mood(word: str) -> str | None:
    for mood, stems in MOOD_STEMS.items():
        if any(word.startswith(s) for s in stems):
            return mood
    return None


def mood_of(tone: str, style: str = "normal") -> str:
    """"dramatic" -> epic, "technical" -> tech, "sério" -> tense; sem pista -> calm."""
    for word in re.findall(r"[a-z]+", normalize(tone or "")):
        if (mood := _word_mood(word)) is not None:
            return mood
    return STYLE_MOOD.get(style, "calm")


# ------------------------------------------------------------------ biblioteca
@dataclass(slots=True)
class Track:
    path: Path
    moods: set[str] = field(default_factory=set)


def index_library(root: Path | None) -> list[Track]:
    if root is None or not root.is_dir():
        return []
    tracks = []
    for f in sorted(root.rglob("*")):
        if f.is_file() and f.suffix.lower() in AUDIO_EXT:
            rel = f.relative_to(root).with_suffix("")
            words = re.findall(r"[a-z]+", normalize(" ".join(rel.parts)))
            tracks.append(Track(f, {m for w in words if (m := _word_mood(w))}))
    return tracks


# ------------------------------------------------------------------ trechos
@dataclass(slots=True)
class MusicSegment:
    start: float
    end: float
    mood: str
    track: str = ""


def plan_segments(scenes: list[Scene], total: float, cfg: MusicConfig) -> list[MusicSegment]:
    if not scenes:
        return [MusicSegment(0.0, total, "calm")]
    moods = [mood_of(s.analysis.tone, s.analysis.style) for s in scenes]

    def dominant(idx: list[int]) -> str:
        weight: Counter[str] = Counter()
        for i in idx:
            weight[moods[i]] += max(0.5, scenes[i].duration)
        return weight.most_common(1)[0][0]

    segs: list[MusicSegment] = []
    cur_start, cur = 0.0, [0]
    for i in range(1, len(scenes)):
        sc = scenes[i]
        seg_len = sc.start - cur_start
        remaining = total - sc.start
        cur_mood = dominant(cur)
        # o clima dos próximos segundos (não troca por causa de uma frase isolada)
        horizon = sc.start + cfg.min_segment_seconds / 2
        ahead = [j for j in range(i, len(scenes)) if scenes[j].start < horizon] or [i]
        ahead_mood = dominant(ahead)
        title = sc.analysis.style in TITLE_STYLES
        need = cfg.min_segment_seconds * (0.6 if title else 1.0)
        cut = ahead_mood != cur_mood and seg_len >= need and remaining >= cfg.min_segment_seconds / 2
        if cut and not title and any(scenes[j].analysis.style in TITLE_STYLES for j in ahead[1:]):
            cut = False  # tem um título logo adiante: troca nele, junto com o assunto
        # mesmo clima por tempo demais: outra faixa, de preferência na virada de assunto
        long = seg_len >= cfg.max_segment_seconds and (title or seg_len >= 1.5 * cfg.max_segment_seconds) \
            and remaining >= cfg.min_segment_seconds / 2
        if cut or long:
            segs.append(MusicSegment(cur_start, sc.start, cur_mood))
            cur_start, cur = sc.start, []
        cur.append(i)
    segs.append(MusicSegment(cur_start, total, dominant(cur)))
    return segs


def assign_tracks(segs: list[MusicSegment], tracks: list[Track], seed: int) -> None:
    """Faixa do clima (ou a mais parecida), sem repetir a do trecho anterior."""
    if not tracks:
        return
    rng = random.Random(seed)
    uses: Counter[str] = Counter()
    prev = ""
    for seg in segs:
        chain = FALLBACK.get(seg.mood, MOODS)
        # o clima certo, o mais parecido, faixa sem clima (serve para tudo) e o resto
        pools = [[t for t in tracks if m in t.moods] for m in chain[:2]] + [[t for t in tracks if not t.moods]]
        pools += [[t for t in tracks if m in t.moods] for m in chain[2:]]
        pool = next((p for p in pools if [t for t in p if str(t.path) != prev]), None)
        if pool is None:  # só existe a faixa do trecho anterior
            pool = tracks
        options = [t for t in pool if str(t.path) != prev] or pool
        least = min(uses[str(t.path)] for t in options)
        choice = rng.choice([t for t in options if uses[str(t.path)] == least])
        seg.track = prev = str(choice.path)
        uses[prev] += 1


# ------------------------------------------------------------------ áudio
def decode(path: Path, seconds: float | None = None) -> np.ndarray:
    cmd = ["ffmpeg", "-v", "error", "-i", str(path)]
    if seconds:
        cmd += ["-t", f"{seconds:.3f}"]
    raw = subprocess.run(cmd + ["-f", "f32le", "-ac", "2", "-ar", str(SR), "-"],
                         capture_output=True, timeout=600, check=True).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, 2).copy()


def _unit(audio: np.ndarray) -> np.ndarray:
    """Tira o silêncio do começo e nivela pela média (RMS = 1)."""
    mono = np.abs(audio).mean(axis=1)
    peak = float(mono.max()) if len(mono) else 0.0
    if peak <= 1e-6:
        return audio[:0]
    start = int(np.argmax(mono > 0.02 * peak))
    audio = audio[start:]
    rms = float(np.sqrt(np.mean(audio ** 2))) or 1.0
    return audio / rms


def _fit(audio: np.ndarray, n: int, xfade: int) -> np.ndarray:
    """Repete a faixa com crossfade até ``n`` amostras."""
    if len(audio) >= n:
        return audio[:n]
    xfade = min(xfade, len(audio) // 3)
    out = audio.copy()
    ramp = np.linspace(0, 1, xfade, dtype=np.float32)[:, None] if xfade else None
    while len(out) < n:
        if ramp is None:
            out = np.concatenate([out, audio])
            continue
        mixed = out[-xfade:] * np.cos(ramp * np.pi / 2) + audio[:xfade] * np.sin(ramp * np.pi / 2)
        out = np.concatenate([out[:-xfade], mixed, audio[xfade:]])
    return out[:n]


def duck_gain(voice: np.ndarray, cfg: MusicConfig, title_times: list[float] = ()) -> tuple[np.ndarray, float]:
    """Ganho da música por amostra (``volume`` na fala, ``pause_volume`` nas pausas) e o
    nível (RMS) da voz enquanto fala."""
    n = len(voice)
    hop = SR // 50  # quadros de 20 ms
    frames = max(1, n // hop)
    mono = np.abs(voice[: frames * hop]).mean(axis=1)
    if len(mono) < frames * hop:
        mono = np.pad(mono, (0, frames * hop - len(mono)))
    env = mono.reshape(frames, hop).mean(axis=1)
    ref = float(np.percentile(env, 95)) if len(env) else 0.0
    active = env > 0.12 * ref if ref > 1e-5 else np.zeros(frames, bool)
    voice_rms = float(np.sqrt(np.mean(voice[: frames * hop].reshape(frames, hop, -1)[active] ** 2))) \
        if active.any() else 0.1
    # antecipa a fala em 0,1 s e segura o ducking por `duck_hold` depois dela
    look, hold = 5, max(1, int(cfg.duck_hold / 0.02))
    kernel = np.ones(look + hold + 1)
    dil = np.convolve(active.astype(np.float32), kernel)[look: look + frames] > 0
    # suaviza (ataque rápido, soltura lenta)
    a_att, a_rel = 1 - np.exp(-0.02 / 0.08), 1 - np.exp(-0.02 / 0.45)
    sm = np.empty(frames, np.float32)
    level = float(dil[0])
    for i, target in enumerate(dil):
        level += (float(target) - level) * (a_att if target > level else a_rel)
        sm[i] = level
    gain = cfg.pause_volume + (cfg.volume - cfg.pause_volume) * sm
    # respiro antes de cada título/capítulo: cai até 30% e volta junto com o título
    t = np.arange(frames) * 0.02
    for tt in title_times:
        dip = np.clip((t - (tt - 0.45)) / 0.3, 0, 1) * np.clip((tt + 0.15 - t) / 0.15, 0, 1)
        gain *= 1 - 0.7 * np.clip(dip * 1.4, 0, 1)
    centers = (np.arange(frames) + 0.5) * hop
    return np.interp(np.arange(n), centers, gain).astype(np.float32), voice_rms


def render_music(segs: list[MusicSegment], voice: np.ndarray, cfg: MusicConfig,
                 title_times: list[float] = ()) -> np.ndarray:
    """Trilha estéreo do tamanho da narração, já nivelada e com ducking."""
    n = len(voice)
    out = np.zeros((n, 2), np.float32)
    xf = int(cfg.crossfade_seconds * SR)
    cache: dict[str, np.ndarray] = {}
    for k, seg in enumerate(segs):
        if not seg.track:
            continue
        a = max(0, int(seg.start * SR) - (xf // 2 if k else 0))
        b = min(n, int(seg.end * SR) + (xf // 2 if k < len(segs) - 1 else 0))
        if b <= a:
            continue
        if seg.track not in cache:
            cache[seg.track] = _unit(decode(Path(seg.track)))
        src = cache[seg.track]
        if not len(src):
            continue
        part = _fit(src, b - a, xf).astype(np.float32)
        env = np.ones(b - a, np.float32)
        fin = xf if k else int(cfg.fade_in_seconds * SR)
        fout = xf if k < len(segs) - 1 else int(cfg.fade_out_seconds * SR)
        if (fin := min(fin, (b - a) // 2)) > 0:
            env[:fin] = np.sin(np.linspace(0, np.pi / 2, fin))
        if (fout := min(fout, (b - a) // 2)) > 0:
            env[-fout:] *= np.cos(np.linspace(0, np.pi / 2, fout))
        out[a:b] += part * env[:, None]
    gain, voice_rms = duck_gain(voice, cfg, title_times)
    return out * (gain * voice_rms)[:, None]


def build_music(cfg: MusicConfig, scenes: list[Scene], voice: np.ndarray, library: Path | None,
                seed: int) -> tuple[np.ndarray | None, list[MusicSegment]]:
    total = len(voice) / SR
    if cfg.track:
        tracks = [Track(Path(cfg.track))]
        segs = [MusicSegment(0.0, total, "custom")]
    else:
        tracks = index_library(library)
        segs = plan_segments(scenes, total, cfg)
    if not tracks:
        log.warning("sem música de fundo: nenhuma faixa em library/music (veja o README)",
                    extra={"stage": "music", "folder": str(library)})
        return None, segs
    if cfg.track:
        segs[0].track = str(tracks[0].path)
    else:
        assign_tracks(segs, tracks, seed)
    titles = [s.start for s in scenes if s.index > 0 and s.analysis.style in TITLE_STYLES] \
        if cfg.title_breath else []
    track = render_music(segs, voice, cfg, titles)
    log.info("música de fundo", extra={"stage": "music", "trechos": [
        {"de": round(s.start, 1), "ate": round(s.end, 1), "clima": s.mood, "faixa": Path(s.track).name}
        for s in segs]})
    return track, segs

"""Música de fundo: clima por cena, trechos, escolha de faixa e ducking sob a voz."""

import subprocess
import wave
from pathlib import Path

import numpy as np

from broll_bot.config import AppConfig, MusicConfig
from broll_bot.models import Scene, SceneAnalysis, Shot
from broll_bot.music import SR, MusicSegment, Track, assign_tracks, index_library, mood_of, plan_segments
from broll_bot.sfx import build_soundtrack


def sc(i, start, end, tone="calm", style="normal"):
    return Scene(i, "x", start, end, SceneAnalysis("x", ["x"], "x", tone=tone, style=style))


def test_tone_words_map_to_moods():
    assert mood_of("dramatic") == "epic" and mood_of("technical") == "tech"
    assert mood_of("sério, preocupante") == "tense" and mood_of("upbeat") == "energetic"
    assert mood_of("nostálgico") == "emotional" and mood_of("playful") == "fun"
    assert mood_of("", "vhs") == "emotional" and mood_of("neutral") == "calm"


def test_music_changes_only_on_real_mood_changes():
    cfg = MusicConfig(min_segment_seconds=30, max_segment_seconds=120)
    scenes = [sc(0, 0, 12), sc(1, 12, 24), sc(2, 24, 30, "tense"),  # 1 frase tensa isolada: não troca
              sc(3, 30, 40), sc(4, 40, 44, "tense", "chapter"), sc(5, 44, 60, "tense"), sc(6, 60, 80, "tense"),
              sc(7, 80, 100, "tense"), sc(8, 100, 104, "energetic")]  # final curto demais: não troca
    segs = plan_segments(scenes, 104, cfg)
    assert [s.mood for s in segs] == ["calm", "tense"]
    assert segs[1].start == 40  # troca no capítulo
    assert segs[0].start == 0 and segs[-1].end == 104


def test_long_same_mood_switches_track_at_a_title():
    cfg = MusicConfig(min_segment_seconds=30, max_segment_seconds=60)
    scenes = [sc(i, i * 10, i * 10 + 10, style="chapter" if i == 7 else "normal") for i in range(12)]
    segs = plan_segments(scenes, 120, cfg)
    assert [s.start for s in segs] == [0, 70] and {s.mood for s in segs} == {"calm"}


def test_tracks_follow_mood_folders_and_do_not_repeat(tmp_path):
    for rel in ("tensao/a.mp3", "tensao/b.mp3", "calmo/c.wav", "epic-drive.mp3", "sem_clima.mp3", "leia.txt"):
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"")
    tracks = {t.path.name: t.moods for t in index_library(tmp_path)}
    assert tracks == {"a.mp3": {"tense"}, "b.mp3": {"tense"}, "c.wav": {"calm"},
                      "epic-drive.mp3": {"epic", "energetic"}, "sem_clima.mp3": set()}
    segs = [MusicSegment(0, 40, "tense"), MusicSegment(40, 80, "tense"), MusicSegment(80, 120, "calm"),
            MusicSegment(120, 160, "fun")]
    assign_tracks(segs, index_library(tmp_path), seed=1)
    names = [Path(s.track).name for s in segs]  # no Windows o caminho vem com "\"
    assert set(names[:2]) == {"a.mp3", "b.mp3"} and names[2] == "c.wav"
    assert names[3] == "epic-drive.mp3"  # "fun" cai no mais parecido (energetic)


def _tone(path, freq, seconds, volume):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=f={freq}:d={seconds}",
                    "-af", f"volume={volume}", str(path)], check=True)


def test_music_ducks_under_the_voice(tmp_path):
    # voz: 2 s falando, 3 s de pausa, 2 s falando
    voice = tmp_path / "voz.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=220:d=7",
                    "-af", "volume=0.3,volume='if(between(t,2,5),0,1)':eval=frame", str(voice)], check=True)
    (tmp_path / "music" / "calmo").mkdir(parents=True)
    _tone(tmp_path / "music" / "calmo" / "faixa.wav", 660, 3, 0.8)  # faixa curta: repete com crossfade
    cfg = AppConfig()
    cfg.sfx.enabled = False
    cfg.music.title_breath = False
    out, _, segs = build_soundtrack(cfg, [], [sc(0, 0, 7)], voice, 7.0, tmp_path, None, tmp_path / "music")
    assert [s.mood for s in segs] == ["calm"] and segs[0].track.endswith("faixa.wav")
    with wave.open(str(out)) as wf:
        pcm = np.frombuffer(wf.readframes(wf.getnframes()), "<i2").astype(np.float32).reshape(-1, 2) / 32767
    assert len(pcm) == 7 * SR
    # isola a música (660 Hz) da voz (220 Hz) medindo a energia perto de 660 Hz
    def music_level(a, b):
        x = pcm[int(a * SR):int(b * SR), 0]
        spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
        freqs = np.fft.rfftfreq(len(x), 1 / SR)
        return spec[(freqs > 600) & (freqs < 720)].max() / len(x)
    under, pause = music_level(1.2, 1.9), music_level(3.2, 4.4)
    assert pause > 1.8 * under  # sobe na pausa, abaixa sob a voz
    assert np.abs(pcm[int(1.2 * SR):int(1.9 * SR)]).max() < 0.45  # música não cobre a voz


def test_music_missing_library_keeps_voice_only(tmp_path):
    voice = tmp_path / "voz.wav"
    _tone(voice, 220, 2, 0.3)
    cfg = AppConfig()
    cfg.sfx.enabled = False
    out, n, segs = build_soundtrack(cfg, [], [sc(0, 0, 2)], voice, 2.0, tmp_path, None, tmp_path / "nada")
    assert out.exists() and n == 0 and segs == []

"""Disco: sobras de render apagadas, cache de mídias com limite e erro claro quando o disco enche."""

import os
import time

import pytest

from broll_bot import disk


def _file(path, size):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


def test_old_render_leftovers_are_removed_but_logs_stay(tmp_path):
    old = tmp_path / ".work" / "20261001-120000"
    _file(old / "render" / "shot_0001.mp4", 1000)
    _file(old / "pipeline.jsonl", 10)
    current = tmp_path / ".work" / "20261002-130000"
    _file(current / "render" / "shot_0001.mp4", 1000)
    disk.clean_old_work(tmp_path / ".work", keep=current)
    assert not (old / "render").exists() and (old / "pipeline.jsonl").exists()
    assert (current / "render" / "shot_0001.mp4").exists()


def test_media_cache_keeps_the_most_recent(tmp_path):
    media = tmp_path / "media"
    files = [_file(media / f"{k}.mp4", 400) for k in range(5)]
    for k, f in enumerate(files):
        os.utime(f, (time.time() - 1000 + k, time.time() - 1000 + k))
    disk.prune_media(media, max_gb=1000 / disk.GB)  # cabem 2 arquivos
    assert [f.exists() for f in files] == [False, False, False, True, True]


def test_not_enough_space_stops_before_rendering(tmp_path, monkeypatch):
    monkeypatch.setattr(disk, "free_gb", lambda p: 1.0)
    with pytest.raises(disk.DiskFullError) as err:
        disk.ensure_space(tmp_path, 19 * 60, tmp_path / "media", 8.0)
    assert "1.0 GB livres" in str(err.value) and "precisa" in str(err.value)
    monkeypatch.setattr(disk, "free_gb", lambda p: 50.0)
    disk.ensure_space(tmp_path, 19 * 60, tmp_path / "media", 8.0)


def test_ffmpeg_disk_full_message_is_recognised():
    from broll_bot.media import _raise_disk_full

    assert disk.is_disk_full("Error submitting a packet to the muxer: No space left on device")
    with pytest.raises(disk.DiskFullError):
        _raise_disk_full("[out#0/mp4] Error closing file: finished with error code: -28 (No space left on device)")
    _raise_disk_full("Invalid data found when processing input")  # outro erro: segue o fluxo normal


def test_pipe_to_dead_ffmpeg_reports_ffmpeg_error_not_invalid_argument():
    """No Windows, escrever no cano de um FFmpeg que morreu dá OSError 22: o erro tem que ser o do FFmpeg."""
    import sys

    from broll_bot.assembler import _pipe_frames
    from broll_bot.media import FFmpegError

    cmd = [sys.executable, "-c", "import sys; sys.stderr.write('No space left on device'); sys.exit(1)"]
    with pytest.raises(FFmpegError) as err:
        _pipe_frames(cmd, (b"x" * 65536 for _ in range(200)))
    assert isinstance(err.value, disk.DiskFullError)


def test_whisper_gets_audio_decoded_by_ffmpeg(tmp_path, monkeypatch):
    """O faster-whisper recebe as amostras já decodificadas: o PyAV do PC (que quebrava) nem é usado."""
    import subprocess
    import sys
    import types

    import numpy as np

    from broll_bot.config import TranscriptionConfig
    from broll_bot.transcription import transcribe

    audio = tmp_path / "voz.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", str(audio)],
                   check=True)
    seen = {}

    class Model:
        def __init__(self, *a, **k):
            pass

        def transcribe(self, source, **k):
            seen["source"] = source
            word = types.SimpleNamespace(word=" hello", start=0.1, end=0.5)
            return iter([types.SimpleNamespace(words=[word], end=0.5)]), types.SimpleNamespace(duration=2.0)

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=Model))
    words = transcribe(audio, TranscriptionConfig())
    assert isinstance(seen["source"], np.ndarray) and seen["source"].dtype == np.float32
    assert abs(len(seen["source"]) - 32000) < 400 and [w.text for w in words] == ["hello"]

import os
import subprocess
import sys
import types

import pytest

from broll_bot.config import TranscriptionConfig
from broll_bot import transcription


def _voice(tmp_path):
    audio = tmp_path / "voz.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", str(audio)],
                   check=True)
    return audio


def test_gpu_failure_transcribes_on_cpu_instead_of_estimating(tmp_path, monkeypatch):
    """PC com placa NVIDIA, mas sem o cublas64_12.dll: a transcrição falhava e o vídeo inteiro sincronizava por
    estimativa. Agora tenta de novo na CPU."""
    devices = []

    class Model:
        def __init__(self, name, device, compute_type):
            self.device = device
            devices.append((device, compute_type))

        def transcribe(self, source, **k):
            def segs():
                if self.device != "cpu":  # o erro aparece só quando o modelo começa a calcular
                    raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
                word = types.SimpleNamespace(word=" hello", start=0.1, end=0.5)
                yield types.SimpleNamespace(words=[word], end=0.5)
            return segs(), types.SimpleNamespace(duration=2.0)

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=Model))
    words = transcription.transcribe(_voice(tmp_path), TranscriptionConfig(device="auto", compute_type="default"))
    assert [w.text for w in words] == ["hello"]
    assert devices[0][0] == "auto" and devices[-1] == ("cpu", "int8")


def test_cpu_failure_still_falls_back_to_estimate(tmp_path, monkeypatch):
    class Model:
        def __init__(self, *a, **k):
            raise RuntimeError("modelo quebrado")

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=Model))
    assert transcription.transcribe(_voice(tmp_path), TranscriptionConfig(device="cpu")) == []


@pytest.mark.skipif(os.name != "nt", reason="só no Windows")
def test_nvidia_pip_dlls_are_put_on_the_search_path(tmp_path, monkeypatch):
    """As DLLs do nvidia-cublas-cu12 ficam em site-packages/nvidia/cublas/bin, fora do PATH."""
    bin_dir = tmp_path / "nvidia" / "cublas" / "bin"
    bin_dir.mkdir(parents=True)
    monkeypatch.setattr(transcription.site, "getsitepackages", lambda: [str(tmp_path)])
    monkeypatch.setattr(transcription.site, "getusersitepackages", lambda: str(tmp_path / "nada"))
    monkeypatch.setenv("PATH", "C:\Windows")
    assert transcription.add_cuda_dlls() == [str(bin_dir)]
    assert os.environ["PATH"].startswith(str(bin_dir))
    assert transcription.add_cuda_dlls() == []  # já está no caminho: não repete

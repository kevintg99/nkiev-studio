"""Codificador: chip de vídeo quando existe, processador quando não (e quando o chip falha)."""

import shutil
import subprocess

import pytest
from PIL import Image

from broll_bot import encoder
from broll_bot.config import AppConfig
from broll_bot.models import Candidate, MediaType, Shot, Transition

ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg ausente")


def test_quality_and_speed_map_to_each_encoder():
    assert encoder.encode_args("cpu", 20, "medium", 30)[:6] == ["-c:v", "libx264", "-preset", "medium", "-crf", "20"]
    qsv = encoder.encode_args("qsv", 20, "medium", 30)
    assert qsv[:2] == ["-c:v", "h264_qsv"] and "-global_quality" in qsv and "nv12" in qsv
    nv = encoder.encode_args("nvenc", 15, "veryfast", 30)
    assert nv[:2] == ["-c:v", "h264_nvenc"] and nv[nv.index("-preset") + 1] == "p2" and nv[nv.index("-cq") + 1] == "15"
    amf = encoder.encode_args("amf", 20, "medium", 30)
    assert amf[:2] == ["-c:v", "h264_amf"] and amf[amf.index("-quality") + 1] == "balanced"


def test_auto_picks_the_first_encoder_that_works(monkeypatch):
    monkeypatch.setattr(encoder, "works", lambda enc: enc in ("qsv", "cpu"))
    assert encoder.choose("auto") == "qsv"
    monkeypatch.setattr(encoder, "works", lambda enc: enc == "cpu")
    assert encoder.choose("auto") == "cpu"
    assert encoder.choose("nvenc") == "cpu"  # pedido explícito que não funciona: processador
    assert encoder.choose("cpu") == "cpu"


def _shot(tmp_path):
    src = tmp_path / "src.jpg"
    Image.new("RGB", (640, 360), "orange").save(src)
    shot = Shot(3, 0, 0, 1, 1, Transition("cut", 0), 0, 24)
    shot.candidate = Candidate("x", "1", MediaType.IMAGE, 640, 360, None, None, None)
    shot.media_path, shot.layout = str(src), {"name": "full"}
    return shot


def _frames(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v", "-show_entries",
                          "stream=nb_read_frames", "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return out.stdout.strip()


@ffmpeg
def test_broken_video_chip_falls_back_to_the_processor(tmp_path, monkeypatch):
    """Aqui não há QuickSync: forçado, ele falha e cada shot é refeito no processador;
    depois de 3 falhas o resto já vai direto no processador."""
    from broll_bot.assembler import Assembler

    if encoder.works("qsv"):
        pytest.skip("esta máquina tem QuickSync de verdade")
    cfg = AppConfig()
    cfg.output.width, cfg.output.height, cfg.output.encoder = 320, 180, "cpu"
    asm = Assembler(cfg, tmp_path / "w")
    asm.encoder = "qsv"
    for k in range(3):
        out = asm.render_shot(_shot(tmp_path), tmp_path / f"o{k}.mp4")
        assert _frames(out) == "24"
    assert asm.encoder == "cpu"


def _timeline(n, fps, cut_every):
    """n shots de 1 s; a cada ``cut_every`` um corte seco, os outros com 6 quadros de transição."""
    shots, frame = [], 0
    for i in range(n):
        overlap = 0 if i == 0 or i % cut_every == 0 else 6
        start = frame - overlap
        shot = Shot(i, 0, start / fps, (start + fps) / fps, 1, Transition("fade" if overlap else "cut",
                                                                           overlap / fps), start, start + fps)
        shots.append(shot)
        frame = start + fps
    return shots, frame


@ffmpeg
@pytest.mark.parametrize("cut_every,chunk,expected", [(3, 3, "copy"), (100, 2, "encode")])
def test_final_assembly_glues_blocks_at_hard_cuts(tmp_path, cut_every, chunk, expected):
    """Blocos que terminam em corte seco são colados sem recodificar; com transição no meio do
    bloco o passo final recodifica como antes. Nos dois casos, duração e quadros certos."""
    from broll_bot.assembler import Assembler

    fps = 10
    cfg = AppConfig()
    cfg.output.width, cfg.output.height, cfg.output.fps, cfg.output.encoder = 160, 90, fps, "cpu"
    cfg.render.chunk_size = chunk  # chunk 2: bloco de no máximo 8 shots, dividido no meio de uma transição
    asm = Assembler(cfg, tmp_path / "w")
    shots, total = _timeline(9, fps, cut_every)
    paths = [asm.render_generated(s, tmp_path / f"s{s.index}.mp4") for s in shots]
    audio = tmp_path / "a.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=d={total / fps}", str(audio)], check=True)
    out = asm.assemble(shots, paths, audio, tmp_path / "final.mp4", total / fps)
    assert asm.last_assembly == expected
    assert _frames(out) == str(total)
    dur = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
                         capture_output=True, text=True).stdout
    assert abs(float(dur) - total / fps) < 0.15

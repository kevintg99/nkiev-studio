"""Zoom/pan das fotos com fração de pixel: o movimento lento não pode andar aos saltos."""

import numpy as np
from PIL import Image, ImageFilter

from broll_bot.framing import build_filter, kenburns_frames


def _photo(tmp_path):
    rng = np.random.default_rng(0)
    img = Image.fromarray((rng.random((720, 1280, 3)) * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(2))
    path = tmp_path / "foto.png"
    img.save(path)
    return path


def test_kenburns_moves_smoothly_without_integer_steps(tmp_path):
    layout = {"name": "kenburns", "z0": 1.0, "z1": 1.04, "fx0": 0.4, "fx1": 0.6, "fy0": 0.5, "fy1": 0.5}
    frames = [np.frombuffer(b, np.uint8).reshape(180, 320, 3).astype(np.float32)
              for b in kenburns_frames(_photo(tmp_path), layout, 320, 180, 60)]
    assert len(frames) == 60
    diffs = np.array([np.abs(b - a).mean() for a, b in zip(frames, frames[1:])])
    mid = diffs[10:-10]
    # zoompan antigo: quadros iguais seguidos de saltos (diferença oscilando perto de zero)
    assert mid.min() > 0.2 * mid.mean()
    assert np.abs(np.diff(mid)).mean() < 0.15 * mid.mean()


def test_video_push_uses_subpixel_perspective_not_zoompan():
    filt = build_filter({"name": "push", "z0": 1.0, "z1": 1.1, "fx": 0.5, "fy": 0.5}, 1920, 1080, 30, 90)
    assert "perspective=" in filt and "zoompan" not in filt
    inset = build_filter({"name": "inset", "scale": 0.8, "fx": 0.5, "fy": 0.5, "blur": 20, "dim": -0.1,
                          "drift": 0.05}, 1920, 1080, 30, 90)
    assert "perspective=" in inset and "zoompan" not in inset

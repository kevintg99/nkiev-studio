"""Estilo documentário: efeitos de corte, overlays, título sem imagem, sons e ritmo rápido."""

import random
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from broll_bot.config import AppConfig, SfxConfig, _validate, apply_pace
from broll_bot.framing import build_filter
from broll_bot.models import Candidate, MediaType, Scene, SceneAnalysis, Shot, Transition, Word
from broll_bot.motion.overlays import OVERLAYS, OverlaySpec, apply_overlay, overlay_cues, salient_point
from broll_bot.motion.planner import MEDIA_FREE, plan_overlays
from broll_bot.sfx import NAMES, SR, SfxBank, SfxEvent, mix_with_narration, plan_events, render_track, synth

ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg não instalado")
ENC = ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]


# ------------------------------------------------------------ efeitos de corte
@pytest.mark.parametrize("effect", ["wash", "blackout"])
def test_single_input_effects_are_in_the_filter(effect):
    f = build_filter({"name": "full"}, 320, 180, 30, 60, effect_in=effect)
    assert ("colorchannelmixer" if effect == "wash" else "drawbox") in f
    assert "[1:v]" not in f


def test_burn_uses_second_input_in_screen_mode():
    f = build_filter({"name": "full"}, 320, 180, 30, 60, effect_in="burn")
    assert "[1:v]" in f and "blend=all_mode=screen" in f


@ffmpeg
@pytest.mark.parametrize("effect", ["wash", "blackout", "burn"])
def test_effects_render(tmp_path, effect):
    from broll_bot.assembler import Assembler

    cfg = AppConfig()
    cfg.output.width, cfg.output.height = 320, 180
    src = tmp_path / "src.jpg"
    Image.new("RGB", (640, 360), "orange").save(src)
    shot = Shot(3, 0, 0, 1, 1, Transition("cut", 0), 0, 24)
    shot.candidate = Candidate("x", "1", MediaType.IMAGE, 640, 360, None, None, None)
    shot.media_path, shot.layout, shot.effect_in = str(src), {"name": "full"}, effect
    out = Assembler(cfg, tmp_path / "w").render_shot(shot, tmp_path / "o.mp4")
    probe = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v", "-show_entries",
                            "stream=nb_read_frames", "-of", "csv=p=0", str(out)], capture_output=True, text=True)
    assert probe.stdout.strip() == "24"


def test_user_overlay_clips_replace_generated_burn(tmp_path):
    from broll_bot.assembler import Assembler

    a = Assembler(AppConfig(), tmp_path / "w")
    a.overlay_dir = tmp_path / "overlays"
    a.overlay_dir.mkdir()
    for name in ("burn_a.mp4", "burn_b.mov", "readme.txt"):
        (a.overlay_dir / name).write_bytes(b"x")
    assert {a.burn_clip(0).name, a.burn_clip(1).name} == {"burn_a.mp4", "burn_b.mov"}


# ------------------------------------------------------------------ overlays
@pytest.mark.parametrize("kind", sorted(OVERLAYS))
def test_overlays_draw_transparent_frames(kind):
    spec = OverlaySpec(kind, 320, 180, 30, 60, title="51 PSI", detail="máximo", title_b="40 PSI",
                       detail_b="frio", lines=["Confira a lateral", "Use o giz"], start=0.2, target=(0.6, 0.5))
    ov = OVERLAYS[kind](spec)
    before, mid = ov.frame(0), ov.frame(40)
    assert before.mode == "RGBA" and before.getextrema()[3] == (0, 0)  # nada antes do início
    assert mid.getextrema()[3][1] > 0  # algo desenhado
    assert overlay_cues({"kind": kind, "title": "51 PSI", "lines": ["a"]}, 60, 30)


@ffmpeg
def test_apply_overlay_keeps_frame_count(tmp_path):
    base = tmp_path / "b.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=320x180:r=30:d=1",
                    *ENC, str(base)], check=True)
    out = apply_overlay(base, {"kind": "callout", "title": "farol"}, tmp_path / "o.mp4", 320, 180, 30, 30, ENC)
    probe = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v", "-show_entries",
                            "stream=nb_read_frames", "-of", "csv=p=0", str(out)], capture_output=True, text=True)
    assert probe.stdout.strip() == "30"


def test_salient_point_finds_the_bright_object():
    img = Image.new("RGB", (192, 108), (40, 40, 40))
    img.paste((250, 250, 250), (120, 60, 136, 74))
    x, y = salient_point(img)
    assert 0.55 < x < 0.8 and 0.45 < y < 0.8


def _shots_for_overlays():
    shots = []
    for i, (a, b) in enumerate([(0, 2.5), (2.5, 3.2), (3.2, 7.0), (7.0, 10.0)]):
        s = Shot(i, i, a, b, b - a, Transition("cut", 0), round(a * 30), round(b * 30))
        s.candidate = Candidate("x", str(i), MediaType.VIDEO, 1920, 1080, 10.0, None, None)
        shots.append(s)
    return shots


def test_plan_overlays_uses_mention_time_and_skips_short_shots():
    cfg = AppConfig()
    scenes = {i: Scene(i, "t", 0, 10, SceneAnalysis("x", ["x"], "x")) for i in range(4)}
    scenes[1].analysis.overlay = {"kind": "label", "title": "freio"}  # shot de 0,7 s: curto demais
    scenes[2].analysis.overlay = {"kind": "keyword", "title": "pressão do pneu"}
    scenes[3].analysis.overlay = {"kind": "badge", "title": "60"}  # colado no anterior (< 3 s)
    words = [Word("a", 3.3, 3.5), Word("pressão", 4.1, 4.5), Word("do", 4.5, 4.6)]
    shots = _shots_for_overlays()
    assert plan_overlays(shots, scenes, cfg, words) == 1
    assert shots[2].overlay["kind"] == "keyword"
    assert shots[2].overlay["start"] == pytest.approx(4.1 - 3.2, abs=0.01)
    assert not shots[1].overlay and not shots[3].overlay


# ------------------------------------------------------ título sem imagem
def test_headline_needs_no_media():
    from broll_bot.selection import Selector
    from broll_bot.ranking import Ranker

    cfg = AppConfig()
    sel = Selector(cfg, {}, Ranker(cfg, None, None), Path("."), seed=1)
    shot = Shot(0, 0, 0, 2, 2, Transition("cut", 0), 0, 60, template="headline")
    sel.assign([shot], {})
    assert "headline" in MEDIA_FREE and shot.candidate is None and shot.fallback_reason is None


def test_media_free_fallback_when_template_has_no_image():
    from broll_bot.motion.planner import TemplatePlanner

    cfg = AppConfig()
    planner = TemplatePlanner(cfg, selector=None, searcher=None, thumbs=None)  # type: ignore[arg-type]
    sc = Scene(0, "O motor mudou tudo", 0, 5, SceneAnalysis("x", ["x"], "x", style="chart", caption="O motor"))
    shot = Shot(0, 0, 0, 3, 3, Transition("cut", 0), 0, 90, template="chart")
    planner.prepare([shot], {0: sc}, {}, [])
    assert shot.template == "headline" and shot.template_data["media"] == []


# --------------------------------------------------------------------- sons
def test_all_sounds_synthesize_finite_audio():
    rng = np.random.default_rng(0)
    for name in NAMES:
        audio = synth(name, rng, 0.6)
        assert len(audio) > 0 and np.isfinite(audio).all() and np.abs(audio).max() <= 1.0


def test_events_follow_effects_templates_and_overlays():
    a = Shot(0, 0, 0, 2, 2, Transition("cut", 0), 0, 60)
    b = Shot(1, 0, 2, 4, 2, Transition("cut", 0), 60, 120)
    b.effect_in = "burn"
    c = Shot(2, 1, 4, 6, 2, Transition("smoothleft", 0.3), 115, 180, template="headline",
             template_data={"keyword": "Corolla XEi", "subtitle": "sedã"})
    d = Shot(3, 2, 6, 9, 3, Transition("fade", 0.3), 175, 270)
    d.overlay = {"kind": "keyword", "title": "pneu murcho", "start": 0.4}
    always = SfxConfig(transition_probability=1.0)
    names = [(round(e.time, 2), e.name) for e in plan_events([a, b, c, d], 30, always)]
    assert any(n == "burn" and t < 2.0 for t, n in names)  # pico da queima cai no corte
    assert ("typing" in {n for _, n in names}) and any(n == "whoosh_soft" for _, n in names)
    typing = [e for e in plan_events([c, d], 30) if e.name == "typing"]
    assert len(typing) == 1 and all(e.length > 0 for e in typing)  # o título digita; a legenda desliza
    assert any(e.name == "swipe" for e in plan_events([d], 30))


def test_user_sounds_override_by_filename(tmp_path):
    user = tmp_path / "sfx"
    user.mkdir()
    for name in ("whoosh_1.wav", "whoosh_soft.wav"):
        with wave.open(str(user / name), "wb") as wf:
            wf.setnchannels(1), wf.setsampwidth(2), wf.setframerate(SR)
            wf.writeframes((np.ones(SR // 10) * 1000).astype("<i2").tobytes())
    bank = SfxBank(user, 1)
    assert [p.name for p in bank.user["whoosh"]] == ["whoosh_1.wav"]
    assert [p.name for p in bank.user["whoosh_soft"]] == ["whoosh_soft.wav"]


@ffmpeg
def test_sfx_stay_below_the_voice(tmp_path):
    narr = tmp_path / "n.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=220:d=2", "-af", "volume=0.1",
                    str(narr)], check=True)
    track = render_track([SfxEvent(0.5, "impact", 1.0)], 2.0, SfxBank(None, 1))
    out = mix_with_narration(narr, track, 0.55, tmp_path / "m.wav")
    with wave.open(str(out)) as wf:
        pcm = np.frombuffer(wf.readframes(wf.getnframes()), "<i2").astype(np.float32) / 32767
    assert pcm.max() < 0.1 * 1.7  # efeitos não passam muito do nível da voz


# ------------------------------------------------------------------ ritmo
def test_fast_pace_is_valid_and_shorter():
    cfg = AppConfig()
    cfg.timeline.pace = "fast"
    apply_pace(cfg)
    _validate(cfg)
    assert cfg.timeline.max_clip_seconds <= 4.2 and cfg.timeline.min_clip_seconds <= 1.3


def test_effect_weights_favour_signature_effects():
    from broll_bot.timeline import plan_timeline

    cfg = AppConfig().timeline
    cfg.effect_probability, cfg.hard_cut_probability_in_scene = 1.0, 1.0
    scenes = [Scene(0, "x", 0, 120, SceneAnalysis("x", ["x"], "x"))]
    shots = plan_timeline(scenes, [], 120.0, cfg, 30, random.Random(3), ["fade"])
    effects = [s.effect_in for s in shots if s.effect_in]
    assert effects and effects.count("wash") + effects.count("burn") > effects.count("glitch") + effects.count("blackout")


def test_transition_sounds_are_sparse_and_quieter():
    shots = []
    for i in range(40):  # 40 cortes com efeito, a cada 1 s
        s = Shot(i, 0, float(i), float(i + 1), 1.0, Transition("cut", 0), i * 30, i * 30 + 30)
        s.effect_in = "whip" if i else None
        shots.append(s)
    cfg = SfxConfig()
    events = plan_events(shots, 30, cfg, seed=3)
    assert 0 < len(events) <= 40 / cfg.transition_min_gap + 1  # só uma parte, e espaçadas
    assert all(b.time - a.time >= cfg.transition_min_gap - 0.5 for a, b in zip(events, events[1:]))
    assert all(e.gain <= 0.7 * cfg.transition_volume + 1e-9 for e in events)


def test_quote_highlights_and_scrapbook_cues():
    from broll_bot.motion.templates import TemplateSpec
    from broll_bot.motion.templates_doc import template_cues

    spec = TemplateSpec("quote", "carbon", 320, 180, 30, 120, keyword="Nenhum carro chega",
                        labels=["500 mil km", "sem reparos"], subtitle="Nenhum.")
    names = [n for _, n, _ in template_cues("quote", spec)]
    assert names[0].startswith("typing:") and names.count("swipe") == 2 and names[-1] == "impact_soft"
    photos = TemplateSpec("scrapbook", "notebook", 320, 180, 30, 180, media=[])
    assert template_cues("scrapbook", photos) == []


def test_scene_plan_keeps_highlights_and_new_overlays():
    from broll_bot.scenes import _clean_overlay, _OverlayOut

    assert _clean_overlay(_OverlayOut(kind="tag", title="correia dentada", detail="60 mil km"))["kind"] == "tag"
    assert _clean_overlay(_OverlayOut(kind="panel", title="Falhas", lines=["a", "b"]))["lines"] == ["a", "b"]


def test_paperrank_long_caption_wraps_instead_of_cutting():
    from broll_bot.motion.templates_paper import _spaced_wrap
    from broll_bot.motion import gfx

    f = gfx.font("typewriter", 30)
    lines = _spaced_wrap("THE GAS ENGINE STARTS EVEN WHEN THE BATTERY IS FULL", f, 480, 2)
    assert len(lines) == 2 and all(gfx.spaced_width(t, f, 0.06) <= 480 for t in lines)
    assert " ".join(lines).replace("…", "") .startswith("THE GAS ENGINE")

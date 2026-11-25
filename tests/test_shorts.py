"""Vídeo curto em pé: formato vertical, corte das pausas, legendas animadas e templates 9:16."""

from __future__ import annotations

import random
import re
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest

from broll_bot.models import MediaType, Word

ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg não instalado")


# ------------------------------------------------------------------ pausas
def test_keep_ranges_shrinks_long_pauses_and_trims_the_ends():
    from broll_bot.silence import keep_ranges

    rate = 1000
    # voz 0.8–1.8, pausa de 1.5 s, voz 3.3–4.3, silêncio até 6.0
    spans = [(0, 800), (1800, 3300), (4300, 6000)]
    ranges = keep_ranges(6000, spans, keep_pause=0.12, lead=0.06, tail=0.25, rate=rate)
    kept = sum(b - a for a, b in ranges)
    assert ranges[0][0] == 740 and abs(kept - (1000 + 120 + 1000 + 60 + 250)) <= 2


@ffmpeg
def test_cut_silences_removes_dead_air(tmp_path):
    from broll_bot.silence import cut_silences

    rate = 48000

    def tone(s):
        t = np.arange(int(rate * s)) / rate
        return (0.3 * np.sin(2 * np.pi * 220 * t) * 32767).astype(np.int16)

    def sil(s):
        return np.zeros(int(rate * s), dtype=np.int16)

    x = np.concatenate([sil(0.8), tone(1.0), sil(0.2), tone(0.5), sil(1.5), tone(1.0), sil(2.0)])
    src = tmp_path / "in.wav"
    with wave.open(str(src), "wb") as wf:
        wf.setnchannels(2), wf.setsampwidth(2), wf.setframerate(rate)
        wf.writeframes(np.stack([x, x], 1).tobytes())
    res = cut_silences(src, tmp_path / "out.wav")
    # a pausa curta (0,2 s) fica; a de 1,5 s vira 0,12 s; começo e fim encolhem
    assert res.before == pytest.approx(7.0, abs=0.02)
    assert res.after == pytest.approx(0.06 + 1.0 + 0.2 + 0.5 + 0.12 + 1.0 + 0.25, abs=0.03)


# ------------------------------------------------------------------ legendas
def _words(text: str, step: float = 0.4) -> list[Word]:
    return [Word(w, round(k * step, 3), round(k * step + step - 0.05, 3)) for k, w in enumerate(text.split())]


def test_chunks_break_on_sentence_end_pause_and_size():
    from broll_bot.captions import chunks

    words = _words("Ninguém esperava isso. E o preço caiu")
    words[-1] = Word("caiu", words[-1].start + 1.0, words[-1].end + 1.0)  # pausa antes da última
    groups = [[w.text for w in g] for g in chunks(words, 3, 30)]
    assert groups == [["Ninguém", "esperava", "isso."], ["E", "o", "preço"], ["caiu"]]


def test_clean_word_drops_trailing_punctuation_keeps_numbers():
    from broll_bot.captions import clean_word

    assert clean_word("isso.", True) == "ISSO" and clean_word("verdade?", False) == "verdade?"
    assert clean_word("1.8", False) == "1.8" and clean_word("{x}", False) == "x"


@pytest.mark.parametrize("style", ["palavra", "frase", "caixa", "karaoke", "limpa", "impacto", "neon", "maquina"])
def test_every_caption_style_builds_valid_ass(style):
    from broll_bot.captions import build_ass

    ass = build_ass(_words("Ninguém esperava que essa garrafa mudasse tudo"), 1080, 1920, style)
    events = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass and events
    assert all(r"\pos(540," in e for e in events)
    if style == "karaoke":
        assert any(r"\kf" in e for e in events)
    if style == "caixa":  # tarja atrás da palavra falada: camada 0 com a caixa, camada 1 com o texto
        assert any(e.startswith("Dialogue: 0,") and ",Box," in e for e in events)
    if style == "maquina":
        assert any(r"\ko" in e for e in events)


def test_captions_move_down_while_a_template_is_on_screen():
    from broll_bot.captions import build_ass

    ass = build_ass(_words("um dois três quatro cinco seis", 0.5), 1080, 1920, "frase", busy=[(1.0, 2.0)])
    ys = [int(re.search(r"\\pos\(540,(\d+)\)", e).group(1)) for e in ass.splitlines() if e.startswith("Dialogue")]
    assert min(ys) == round(1920 * 0.64) and max(ys) == round(1920 * 0.79)


def test_two_lines_are_balanced():
    from broll_bot.captions import _Builder

    b = _Builder(1080, 1920, "frase", (255, 204, 0))
    lines = b._lines(["DE", "PLÁSTICO", "FOSSE"])
    assert lines == [[0, 1], [2]]


@ffmpeg
def test_burn_captions_and_progress_bar(tmp_path):
    from broll_bot.captions import build_ass, burn

    src = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=216x384:d=1.5", "-f", "lavfi",
                    "-i", "anullsrc=r=48000:cl=stereo", "-t", "1.5", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", str(src)], check=True)
    ass = tmp_path / "c.ass"
    ass.write_text(build_ass(_words("olá mundo bonito", 0.4), 216, 384, "caixa"), encoding="utf-8")
    out = burn(src, ass, tmp_path / "o.mp4", ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"],
               progress_bar=(255, 0, 0), total=1.5)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height", "-of",
                            "csv=p=0", str(out)], capture_output=True, text=True).stdout
    assert "video,216,384" in probe and "audio" in probe


# ------------------------------------------------------------------ formato
def test_vertical_profile():
    from broll_bot.config import AppConfig, _validate, apply_format

    cfg = AppConfig()
    cfg.output.format = "vertical"
    apply_format(cfg)
    apply_format(cfg)  # de novo (config, canal, ajustes do vídeo): o mesmo resultado
    _validate(cfg)
    assert (cfg.output.width, cfg.output.height) == (1080, 1920)
    assert cfg.search.orientation == "portrait" and cfg.captions.enabled and cfg.narration.cut_silence
    # nada sai do vídeo em pé: overlays e avatar continuam (adaptados à tela em pé)
    assert cfg.timeline.max_clip_seconds <= 2.5 and cfg.overlays.kinds == AppConfig().overlays.kinds
    assert cfg.avatar.enabled
    assert cfg.timeline.effect_weights["punch"] > cfg.timeline.effect_weights["burn"]
    plain = AppConfig()
    apply_format(plain)
    assert (plain.output.width, plain.output.height) == (1920, 1080) and not plain.captions.enabled


def test_app_choices_become_overrides(tmp_path):
    from broll_bot.channels import apply_overrides
    from broll_bot.config import AppConfig
    from broll_bot.looks import job_overrides

    o = job_overrides("mix", video_format="vertical", captions="caixa")
    assert o["output"] == {"format": "vertical"} and o["captions"]["style"] == "caixa"
    off = job_overrides("mix", video_format="vertical", captions="off", cut_silence="off")
    assert off["captions"] == {"enabled": False} and off["narration"] == {"cut_silence": False}
    import yaml

    path = tmp_path / "ajustes.yaml"
    path.write_text(yaml.safe_dump(o, allow_unicode=True), encoding="utf-8")
    cfg = apply_overrides(AppConfig(), path)
    assert cfg.output.height == 1920 and cfg.captions.style == "caixa" and cfg.captions.enabled
    path.write_text(yaml.safe_dump(off, allow_unicode=True), encoding="utf-8")
    cfg = apply_overrides(AppConfig(), path)
    assert cfg.output.height == 1920 and not cfg.captions.enabled and not cfg.narration.cut_silence


def test_vertical_layouts_fill_the_screen():
    from broll_bot.framing import choose_layout

    rng = random.Random(3)
    lay = choose_layout(MediaType.IMAGE, 16 / 9, {}, {"kenburns": 0.5, "inset": 0.5}, rng, None, out_aspect=9 / 16)
    assert lay["name"] == "kenburns" and lay["move"] in ("pan_left", "pan_right", "zoom_in")
    portrait = choose_layout(MediaType.VIDEO, 9 / 16, {"full": 1.0}, {}, rng, None, out_aspect=9 / 16)
    assert portrait["name"] == "full"  # mídia em pé num vídeo em pé: tela cheia (antes ia de inset)
    assert choose_layout(MediaType.VIDEO, 9 / 16, {"full": 1.0}, {}, rng, None)["name"] == "inset"


def test_ranking_prefers_portrait_media_in_a_vertical_video():
    from broll_bot.config import AppConfig
    from broll_bot.models import Candidate
    from broll_bot.ranking import Ranker

    cfg = AppConfig()
    cfg.output.width, cfg.output.height = 1080, 1920
    r = Ranker(cfg, None, None)
    tall = Candidate("pexels", "a", MediaType.VIDEO, 1080, 1920, 8, None, None)
    wide = Candidate("pexels", "b", MediaType.VIDEO, 1920, 1080, 8, None, None)
    assert r._orientation(tall) > r._orientation(wide)


# ------------------------------------------------------------------ templates em pé
def _media(tmp_path, n=3):
    from PIL import Image

    from broll_bot.motion import MediaRef

    out = []
    for k, color in enumerate(["red", "green", "blue"][:n]):
        p = tmp_path / f"m{k}.jpg"
        Image.new("RGB", (640, 360), color).save(p)
        out.append(MediaRef(str(p)))
    return out


CASES = {
    "stat": dict(number={"value": 89900, "prefix": "R$ ", "label": "FIPE"}, keyword="Corolla"),
    "ranking": dict(rank=3, keyword="Toyota Corolla", subtitle="2014 a 2019"),
    "steps": dict(number={"value": 2, "total": 5}, keyword="Confira o histórico"),
    "versus": dict(labels=["Corolla", "Civic"], stats=[{"label": "Potência", "value": "177", "unit": "cv",
                                                        "value_b": "155"}]),
    "proscons": dict(points=[{"text": "Econômico", "positive": True}, {"text": "Caro", "positive": False}],
                     keyword="Prós e contras"),
    "specs": dict(stats=[{"label": "Potência", "value": "177", "unit": "cv"}], keyword="Ficha"),
    "gallery": dict(keyword="Toyota Corolla", subtitle="o mais vendido"),
    "quote": dict(keyword="Nenhum carro chega aos", labels=["500 mil km"], subtitle="Mecânico"),
    "warning": dict(keyword="Câmbio CVT", body="Troque o óleo a cada 60 mil km"),
    "comparison": dict(labels=["2019", "2023"]),
    "vhs": dict(keyword="1998"),  # sem versão nativa: vira cartão
}


@pytest.mark.parametrize("kind", sorted(CASES))
def test_vertical_templates_render(tmp_path, kind):
    from broll_bot.motion import TemplateSpec
    from broll_bot.motion.templates_vertical import Card, make_template, vertical_kind

    spec = TemplateSpec(kind, "carbon", 216, 384, 30, 24, seed=1, media=_media(tmp_path), lang="pt", **CASES[kind])
    tpl = make_template(spec)
    try:
        frames = [tpl.frame(i) for i in (0, 12, 23)]
    finally:
        tpl.close()
    assert all(f.size == (216, 384) for f in frames)
    how = vertical_kind(kind)
    assert (how == "card") == isinstance(tpl, Card)
    assert how != "safe" or type(tpl).__name__ not in ("Card",)


def test_horizontal_templates_are_untouched(tmp_path):
    from broll_bot.motion import TEMPLATES, TemplateSpec
    from broll_bot.motion.templates_vertical import make_template

    spec = TemplateSpec("stat", "carbon", 320, 180, 30, 10, media=_media(tmp_path, 1), number={"value": 10})
    assert type(make_template(spec)) is TEMPLATES["stat"]


def test_every_template_has_a_vertical_plan():
    from broll_bot.motion import TEMPLATES
    from broll_bot.motion.templates_vertical import CLASSES, NATIVE, VERTICAL_SAFE

    assert not (VERTICAL_SAFE & set(NATIVE))
    assert set(NATIVE.values()) <= set(CLASSES)
    assert (VERTICAL_SAFE | set(NATIVE)) <= set(TEMPLATES)


def test_caption_previews_exist_for_every_style():
    from broll_bot.captions import STYLES

    web = Path(__file__).resolve().parent.parent / "studio" / "web" / "legendas"
    assert all((web / f"{k}.webp").exists() and (web / f"{k}.jpg").exists() for k in STYLES)


def test_config_local_yaml_and_env_hold_personal_settings(tmp_path, monkeypatch):
    """O git pull travava porque o config.yaml tinha o "firefox" do YouTube posto à mão."""
    from broll_bot.config import load_config

    (tmp_path / "config.yaml").write_text("output:\n  fps: 30\nyoutube_cc:\n  clip_seconds: 25\n", encoding="utf-8")
    (tmp_path / "config.local.yaml").write_text("youtube_cc:\n  min_subscribers: 50\n", encoding="utf-8")
    monkeypatch.setenv("YOUTUBE_COOKIES_FROM_BROWSER", "Firefox")
    cfg = load_config(tmp_path / "config.yaml", env_file=None)
    assert cfg.youtube_cc.min_subscribers == 50 and cfg.youtube_cc.clip_seconds == 25
    assert cfg.youtube_cc.cookies_from_browser == "firefox"


def test_update_helper_moves_the_browser_to_env_and_restores_config(tmp_path, monkeypatch):
    from studio import atualizar

    (tmp_path / "config.yaml").write_text("youtube_cc:\n  cookies_from_browser: firefox\n", encoding="utf-8")
    calls = []

    class R:
        def __init__(self, code):
            self.returncode = code

    def fake_git(*args):
        calls.append(args)
        return R(1 if args[0] == "diff" else 0)

    monkeypatch.setattr(atualizar, "_git", fake_git)
    notes = atualizar.migrate(tmp_path)
    assert (tmp_path / "config.yaml.antigo").exists() and ("checkout", "--", "config.yaml") in calls
    assert "YOUTUBE_COOKIES_FROM_BROWSER=firefox" in (tmp_path / ".env").read_text(encoding="utf-8")
    assert len(notes) == 3
    calls.clear()
    monkeypatch.setattr(atualizar, "_git", lambda *a: calls.append(a) or R(0))  # config.yaml sem mudança
    assert atualizar.migrate(tmp_path) == [] and calls == [("diff", "--quiet", "--", "config.yaml")]


def test_vertical_overlay_stays_above_the_app_buttons(tmp_path):
    """Overlays continuam no vertical, desenhados acima da faixa de baixo (botões do TikTok/Shorts)."""
    from PIL import Image

    from broll_bot.motion.overlays import VERTICAL_SAFE_BOTTOM, apply_overlay

    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg")
    base = tmp_path / "b.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=216x384:d=1", "-c:v",
                    "libx264", "-pix_fmt", "yuv420p", str(base)], check=True)
    out = apply_overlay(base, {"kind": "lowerthird", "title": "Toyota Corolla", "detail": "2014–2019", "start": 0.0},
                        tmp_path / "o.mp4", 216, 384, 30, 30, ["-c:v", "libx264", "-preset", "ultrafast",
                                                               "-pix_fmt", "yuv420p"])
    frame = tmp_path / "f.png"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "0.8", "-i", str(out), "-frames:v", "1", str(frame)], check=True)
    img = np.asarray(Image.open(frame).convert("L"), np.float32)
    below = img[round(384 * VERTICAL_SAFE_BOTTOM) + 2:]
    assert abs(below.mean() - img[:20].mean()) < 6  # nada desenhado na faixa dos botões


def test_vertical_avatar_corner_is_smaller_and_above_the_buttons():
    from broll_bot.avatar import avatar_filter
    from broll_bot.config import AvatarConfig

    cfg = AvatarConfig()
    cfg.layout = "corner"
    items = [{"shot_start": 0.0, "start": 0.0, "end": 3.0}]
    vert = avatar_filter(items, cfg, 1080, 1920, 30)
    horiz = avatar_filter(items, cfg, 1920, 1080, 30)
    assert "y=H-h-250" in vert and f"scale=-2:{int(round(1920 * 0.34 / 2) * 2)}" in vert
    assert "y=H-h:" in horiz


def test_app_starts_with_the_format_choice():
    html = (Path(__file__).resolve().parent.parent / "studio" / "web" / "index.html").read_text(encoding="utf-8")
    assert 'data-format="${id}"' in html and "Que tipo de vídeo?" in html and "em breve" not in html


def test_single_word_captions_are_not_bigger_than_the_phrase_ones():
    """Palavra por palavra estava enorme (1 palavra na tela não precisa de letra muito maior que a frase)."""
    from broll_bot.captions import STYLES, build_ass

    def font_px(style):
        line = next(x for x in build_ass([], 1080, 1920, style).splitlines() if x.startswith("Style: "))
        return int(line.split(",")[2])

    assert font_px("palavra") <= font_px("frase") * 1.1
    assert STYLES["impacto"].size <= 150


def test_caption_size_choice_goes_to_the_config():
    from broll_bot.captions import build_ass
    from broll_bot.looks import job_overrides

    small = job_overrides("mix", video_format="vertical", captions="palavra", caption_size="pequena")
    assert small["captions"] == {"enabled": True, "style": "palavra", "size": 0.8}
    auto_big = job_overrides("mix", video_format="vertical", caption_size="grande")
    assert auto_big["captions"] == {"size": 1.2}
    assert "captions" not in job_overrides("mix", video_format="vertical")  # média = padrão
    assert job_overrides("mix", captions="off", caption_size="grande")["captions"] == {"enabled": False}

    def font_px(size):
        line = next(x for x in build_ass([], 1080, 1920, "palavra", size=size).splitlines() if x.startswith("Style: "))
        return int(line.split(",")[2])

    assert font_px(0.8) < font_px(1.0) < font_px(1.2)

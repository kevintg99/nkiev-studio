"""Ritmo visual: tela preta com a frase, foto do carro no blueprint, setinha; paleta do canal."""

import shutil
import subprocess

import pytest
from PIL import Image

from broll_bot.coherence import enforce_coherence
from broll_bot.cues import set_language
from broll_bot.models import Scene, SceneAnalysis
from broll_bot.motion import TEMPLATES, gfx
from broll_bot.motion.overlays import OVERLAYS, OverlaySpec
from broll_bot.motion.templates import MediaRef, TemplateSpec, render_template
from broll_bot.rhythm import VISUALS, add_rhythm, is_punchy, keyword, part_word
from broll_bot.transcription import parse_script

ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg ausente")

SCRIPT = """La Logan a une tête d'utilitaire et un intérieur en plastique dur, pendant des années.
Mais soulevez le capot et regardez le moteur, un vieux bloc Renault que tout le monde connaît.
Et c'est une erreur. Beaucoup de gens la regardent de haut sur le marché de l'occasion.
Le coffre est énorme et la banquette arrière se replie facilement pour la famille entière.
Les pièces sont partout et elles coûtent trois fois rien chez votre garagiste du coin."""


def _scenes(subject=""):
    set_language("fr")
    sents = parse_script(SCRIPT)
    t = 0.0
    for s in sents:
        d = len(s.text.split()) * 0.45
        s.start, s.end = t, t + d
        t += d
    scenes = [Scene(k, s.text, s.start, s.end, SceneAnalysis("car", ["car"], "car", subject=subject))
              for k, s in enumerate(sents)]
    return scenes, sents


def test_punchy_sentences_and_words():
    set_language("fr")
    assert is_punchy("Et c'est une erreur.") and not is_punchy("Et c'est une erreur ?")
    assert not is_punchy("Numéro 3 : la Mazda 3.") and not is_punchy("Entre 5 000 et 9 000 euros.")
    assert part_word("Mais soulevez le capot et regardez le moteur.") == "capot"
    assert keyword("Beaucoup de gens la regardent de haut sur le marché") in ("regardent", "marché", "Beaucoup")


def test_long_broll_stretch_gets_a_black_statement_a_showcase_or_an_arrow():
    scenes, sents = _scenes("Dacia Logan MCV")
    out, notes = add_rhythm(scenes, sents, gap=1.0, statement_every=5.0, lead_in=0.0, statement_chance=1.0)
    styles = [s.analysis.style for s in out]
    kinds = [s.analysis.overlay.get("kind") for s in out if s.analysis.overlay]
    assert "statement" in styles and notes
    stat = next(s for s in out if s.analysis.style == "statement")
    assert stat.analysis.body == "Et c'est une erreur." and stat.text == "Et c'est une erreur."
    assert set(styles) & set(VISUALS) or kinds
    show = [s for s in out if s.analysis.style in VISUALS and s.analysis.style not in ("annotate", "textmask", "wordpop")]
    assert all(s.analysis.caption == "Dacia Logan MCV" for s in show)
    # cenas contíguas, sem buraco, e tudo coerente com a fala
    for a, b in zip(out, out[1:]):
        assert abs(a.end - b.start) < 1e-6
    assert not [n for n in enforce_coherence(out) if "removido" in n or "virou" in n]


def test_without_a_car_section_the_spoken_part_becomes_a_caption():
    scenes, sents = _scenes("")
    out, _ = add_rhythm(scenes, sents, gap=3.0, statement_every=1e9, lead_in=0.0, phrase_chance=0.0,
                        styles={"showcase", "gallery", "sketch", "dossier"})  # sem os da edição magnética
    caps = [s.analysis.overlay for s in out if s.analysis.overlay.get("kind") == "keyword"]
    assert caps and caps[0]["title"] in ("capot", "moteur", "coffre", "banquette")
    assert not [s for s in out if s.analysis.overlay.get("kind") == "arrow"]  # a seta saiu


@ffmpeg
@pytest.mark.parametrize("kind,data", [
    ("showcase", dict(keyword="Dacia Logan MCV", subtitle="Première génération")),
    ("statement", dict(body="Ce qui n'est pas là ne peut pas casser.", labels=["casser"])),
])
def test_new_templates_render_in_the_channel_palette(tmp_path, kind, data):
    p = tmp_path / "car.jpg"
    Image.new("RGB", (800, 500), "red").save(p)
    gfx.set_brand(True)
    try:
        spec = TemplateSpec(kind, "channel", 320, 180, 30, 24, seed=1, media=[MediaRef(str(p))], **data)
        dest = render_template(spec, tmp_path / f"{kind}.mp4", ["-c:v", "libx264", "-preset", "ultrafast",
                                                                 "-pix_fmt", "yuv420p"])
        out = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v", "-show_entries",
                              "stream=nb_read_frames", "-of", "csv=p=0", str(dest)], capture_output=True, text=True)
        assert out.stdout.strip() == "24"
        frame = TEMPLATES[kind](spec).frame(20).convert("RGB")
        if kind == "statement":  # fundo preto
            assert frame.getpixel((2, 2)) == (0, 0, 0)
        else:  # fundo azul-escuro do canal
            r, g, b = frame.getpixel((2, 2))
            assert b > r and b > g and b < 120
    finally:
        gfx.set_brand(False)


def test_brand_text_is_yellow_with_black_outline():
    gfx.set_brand(True)
    try:
        img = gfx.text_block(["MOTEUR"], gfx.font("black", 80), (255, 255, 255))
        px = {img.getpixel((x, y))[:3] for x in range(img.width) for y in range(img.height)
              if img.getpixel((x, y))[3] == 255}
        assert gfx.BRAND["text"] in px and (0, 0, 0) in px and (255, 255, 255) not in px
    finally:
        gfx.set_brand(False)
    plain = gfx.text_block(["MOTEUR"], gfx.font("black", 80), (255, 255, 255))
    assert (255, 255, 255) in {plain.getpixel((x, y))[:3] for x in range(plain.width) for y in range(plain.height)}


def test_caption_is_minimal_text_at_the_bottom_left_without_arrow():
    gfx.set_brand(True)
    try:
        spec = OverlaySpec.from_dict({"kind": "keyword", "title": "turbo", "w": 480, "h": 270, "fps": 30,
                                      "frames": 60, "start": 0.0})
        ov = OVERLAYS["keyword"](spec)
        late = ov.frame(30)
        box = late.getbbox()
        assert box is not None and box[0] < 480 * 0.15 and box[1] > 270 * 0.6  # canto de baixo, à esquerda
        assert box[2] < 480 * 0.5  # só a palavra e o filete, nada atravessando a tela
        assert ov.frame(1).getbbox() != box  # anima (filete e palavra subindo)
        assert OVERLAYS["arrow"] is OVERLAYS["keyword"]  # plano antigo com seta vira legenda
    finally:
        gfx.set_brand(False)


def test_statement_timing_and_look_are_not_a_fixed_beat():
    """Tela preta aleatória: o intervalo entre elas varia e a animação muda de uma para outra."""
    from broll_bot.motion.templates_brand import Statement

    looks = {Statement.variant(TemplateSpec("statement", "channel", 320, 180, 30, 60, seed=s)) for s in range(30)}
    assert looks == set(Statement.VARIANTS)
    set_language("fr")
    sents = parse_script(" ".join(["Et c'est une erreur. Le moteur est un vieux bloc Renault très solide et simple."] * 40))
    t = 0.0
    for s in sents:
        d = len(s.text.split()) * 0.45
        s.start, s.end = t, t + d
        t += d
    scenes = [Scene(k, s.text, s.start, s.end, SceneAnalysis("car", ["car"], "car")) for k, s in enumerate(sents)]
    out, _ = add_rhythm(scenes, sents, gap=4.0, statement_every=20.0, seed=3)
    times = [s.start for s in out if s.analysis.style == "statement"]
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert len(times) >= 3 and max(gaps) - min(gaps) > 5.0  # nada de compasso fixo
    again, _ = add_rhythm([Scene(k, s.text, s.start, s.end, SceneAnalysis("car", ["car"], "car"))
                           for k, s in enumerate(sents)], sents, gap=4.0, statement_every=20.0, seed=3)
    assert times == [s.start for s in again if s.analysis.style == "statement"]  # mesma seed, mesmo vídeo


def test_magnetic_pieces_also_enter_videos_without_a_car_section():
    from broll_bot.rhythm import FREE_VISUALS

    scenes, sents = _scenes("")
    out, _ = add_rhythm(scenes * 1, sents, gap=1.0, statement_every=1e9, lead_in=0.0, phrase_chance=0.0, seed=3)
    magnets = [s for s in out if s.analysis.style in FREE_VISUALS]
    assert magnets and all(s.analysis.caption or s.analysis.style == "wordpop" for s in magnets)

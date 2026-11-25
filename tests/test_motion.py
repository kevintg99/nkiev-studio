"""Templates de motion graphics: escolha das cenas, legendas, timeline e renderização."""

import random
import shutil
import subprocess

import pytest
from PIL import Image

from broll_bot.config import TemplatesConfig, TimelineConfig
from broll_bot.framing import build_filter, effect_chain
from broll_bot.models import Scene, SceneAnalysis, Word
from broll_bot.motion import TEMPLATES, MediaRef, TemplateSpec, render_template
from broll_bot.motion.planner import caption_chunks, caption_groups, select_template_scenes, template_length
from broll_bot.timeline import plan_timeline


def scene(i, start, end, style="normal"):
    return Scene(i, f"cena {i}", start, end, SceneAnalysis("x", ["x"], "x", style=style))


# ---------------------------------------------------------------- escolha
def test_selection_respects_gap_and_share():
    scenes = [scene(0, 0, 10, "comparison"), scene(1, 10, 20, "mosaic"), scene(2, 20, 30, "hud"),
              scene(3, 30, 40, "card"), scene(4, 40, 41, "vhs"), scene(5, 41, 60, "banana")]
    cfg = TemplatesConfig(min_gap_seconds=8, max_share=0.5)
    chosen = select_template_scenes(scenes, cfg, 60.0)
    # 1: colado no anterior; 3: menos de 8 s depois do hud; 4: curta demais; 5: estilo inválido
    assert chosen == {0: "comparison", 2: "hud"}


def test_template_length_follows_ai_then_limits():
    s = scene(0, 0, 30, "versus")
    assert template_length(s, TemplatesConfig()) == 10.5  # padrão do estilo
    s.analysis.template_seconds = 12
    assert template_length(s, TemplatesConfig()) == 12  # a IA decide
    s.analysis.template_seconds = 40
    assert template_length(s, TemplatesConfig(max_seconds=16)) == 16  # teto da config
    short = scene(1, 0, 6, "comparison")
    assert template_length(short, TemplatesConfig()) == 6  # nunca maior que a cena


def test_selection_disabled_or_filtered_styles():
    scenes = [scene(0, 0, 10, "vhs"), scene(1, 30, 40, "hud")]
    assert select_template_scenes(scenes, TemplatesConfig(enabled=False), 40) == {}
    assert select_template_scenes(scenes, TemplatesConfig(styles=["hud"]), 40) == {1: "hud"}


def test_selection_caps_total_share():
    scenes = [scene(i, i * 20, i * 20 + 10, "card" if i % 2 == 0 else "normal") for i in range(10)]
    chosen = select_template_scenes(scenes, TemplatesConfig(min_gap_seconds=0, max_share=0.2), 200)
    assert sum(7.0 for _ in chosen) / 200 <= 0.2 + 1e-9


# ---------------------------------------------------------------- legendas
WORDS = [Word(w, i * 0.4, i * 0.4 + 0.35) for i, w in enumerate("a maçã é crocante, a banana dá energia e a laranja refresca".split())]


def test_caption_groups_one_per_card_and_start_at_zero():
    groups = caption_groups(WORDS, 0.0, 4.8, 3)
    assert len(groups) == 3 and groups[0][0] == 0.0
    assert "maçã" in groups[0][2] and "banana" in groups[1][2] and "laranja" in groups[2][2]
    assert groups[0][0] < groups[1][0] < groups[2][0]
    assert not groups[0][2].endswith(",")


def test_caption_chunks_break_on_pauses_and_length():
    words = [Word("um", 0, 0.3), Word("dois", 0.35, 0.6), Word("três", 1.5, 1.8)] + \
            [Word(f"p{i}", 2 + i * 0.3, 2.25 + i * 0.3) for i in range(8)]
    chunks = caption_chunks(words, 0, 5, max_words=6)
    assert chunks[0][2] == "um dois"  # pausa longa depois de "dois"
    assert all(len(c[2].rstrip("…").split()) <= 6 for c in chunks)


# ---------------------------------------------------------------- timeline
def test_template_segment_uses_requested_length():
    cfg = TimelineConfig(effect_probability=1.0)
    scenes = [scene(0, 0, 9.3), scene(1, 9.3, 31.0), scene(2, 31.0, 33.9), scene(3, 33.9, 50.0)]
    shots = plan_timeline(scenes, [], 50.0, cfg, 30, random.Random(3), cfg.transitions,
                          template_scenes={1: "comparison", 2: "card"}, template_min=2.5,
                          template_seconds={1: 11.0, 2: 6.0})
    tpl = [s for s in shots if s.template]
    assert [(s.scene_index, s.template) for s in tpl] == [(1, "comparison"), (2, "card")]
    assert next(s for s in shots if s.scene_index == 1).template == "comparison"
    assert 10.3 <= tpl[0].cut_out - tpl[0].cut_in <= 11.7  # template longo (passa de 7 s)
    assert tpl[1].cut_out - tpl[1].cut_in == pytest.approx(2.9, abs=0.05)  # cena curta: ocupa toda
    assert all(s.clip_duration <= 7.0 for s in shots if not s.template)  # b-roll segue no teto
    for prev, cur in zip(shots, shots[1:]):  # efeitos nunca encostam num template
        if cur.template or prev.template:
            assert cur.effect_in is None and prev.effect_out is None
    assert any(s.effect_in for s in shots)
    for prev, cur in zip(shots, shots[1:]):
        if cur.effect_in == "whip":
            assert prev.effect_out == "whip"


# ---------------------------------------------------------------- filtros
def test_effect_chain_contents():
    assert "avgblur" in effect_chain(1920, 1080, 30, 150, "whip", None)
    assert "gt(t," in effect_chain(1920, 1080, 30, 150, None, "whip")
    assert "zoompan" in effect_chain(1920, 1080, 30, 150, "punch", None)
    assert "rgbashift" in effect_chain(1920, 1080, 30, 150, "glitch", None)
    assert effect_chain(1920, 1080, 30, 150, None, None) == ""
    leak = build_filter({"name": "full"}, 1920, 1080, 30, 150, effect_in="leak")
    assert "[1:v]" in leak and "overlay" in leak and leak.endswith("[v]")
    assert "setsar=1,format=yuv420p" in build_filter({"name": "full"}, 1920, 1080, 30, 150)


# ------------------------------------------------------------ renderização
ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg não instalado")


@ffmpeg
@pytest.mark.parametrize("kind,theme", [("comparison", "paper"), ("comparison", "blueprint"), ("mosaic", "blueprint"),
                                        ("hud", "redgrid"), ("card", "blueprint"), ("vhs", "blueprint")])
def test_every_template_renders_exact_frame_count(tmp_path, kind, theme):
    imgs = []
    for k, color in enumerate(["red", "green", "blue"]):
        p = tmp_path / f"i{k}.jpg"
        Image.new("RGB", (640, 400), color).save(p)
        imgs.append(p)
    spec = TemplateSpec(kind, theme, 320, 180, 30, 24, seed=1, media=[MediaRef(str(p)) for p in imgs],
                        tiles=[str(p) for p in imgs], captions=[(0.0, 0.3, "a maçã"), (0.3, 0.6, "a banana")],
                        keyword="frutas")
    dest = render_template(spec, tmp_path / f"{kind}.mp4", ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"])
    out = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v", "-show_entries",
                          "stream=nb_read_frames,width,height,sample_aspect_ratio", "-of", "csv=p=0", str(dest)],
                         capture_output=True, text=True).stdout.strip()
    assert out == "320,180,1:1,24"


def test_all_styles_have_a_template():
    from broll_bot.scenes import STYLES

    assert set(TEMPLATES) == STYLES - {"normal"}
    assert len(TEMPLATES) == 78


AUTO_CASES = {
    "specs": dict(stats=[{"label": "Potência", "value": "177", "unit": "cv", "bar": 0.8},
                         {"label": "Torque", "value": "21,4", "unit": "kgfm", "bar": 0.6}], keyword="Ficha técnica"),
    "stat": dict(number={"value": 89900, "prefix": "R$ ", "label": "FIPE"}),
    "gauge": dict(number={"value": 210, "max": 260, "suffix": "km/h", "label": "Máxima"}),
    "versus": dict(labels=["Corolla", "Civic"], stats=[{"label": "Potência", "value": "177", "unit": "cv", "value_b": "155"}]),
    "blueprint": dict(keyword="Corolla", stats=[{"label": "Comprimento", "value": "4.630", "unit": "mm"}]),
    "checklist": dict(keyword="Prós e contras", points=[{"text": "Econômico", "positive": True},
                                                        {"text": "Caro", "positive": False}]),
    "ranking": dict(rank=3, keyword="Civic"),
    "chapter": dict(keyword="Motor", subtitle="Desempenho", index=2),
    "headline": dict(keyword="Corolla XEi", subtitle="o sedã mais vendido"),
    "triptych": dict(labels=["47 PSI", "42 PSI"]),
    "counter": dict(keyword="Quatro em cada cinco", number={"value": 80, "suffix": "%", "label": "erram"}),
    "chart": dict(keyword="Frenagem", stats=[{"label": "20 km/h", "value": "19", "unit": "m"},
                                            {"label": "40 km/h", "value": "50", "unit": "m"}]),
    "quote": dict(keyword="Nenhum carro chega aos", labels=["500 mil km", "sem reparos"], subtitle="Nenhum."),
    "scrapbook": dict(),
    "timeline": dict(keyword="A evolução", labels=["1966", "2020"], notes=["E10", "E210"]),
    "speedometer": dict(number={"value": 9.2, "decimals": 1, "suffix": "s", "label": "0 a 100 km/h"}),
    "datasheet": dict(keyword="Ficha técnica", stats=[{"label": "Potência", "value": "177", "unit": "cv"},
                                                     {"label": "Câmbio", "value": "CVT"}]),
    "price": dict(number={"value": 129900, "prefix": "R$ ", "label": "FIPE", "level": 0.7}),
    "proscons": dict(points=[{"text": "Econômico", "positive": True}, {"text": "Caro", "positive": False}]),
    "score": dict(stats=[{"label": "Conforto", "value": "9"}, {"label": "Preço", "value": "6"}],
                  number={"value": 8.5, "decimals": 1}),
    "magnifier": dict(keyword="Faróis de LED"),
    "beforeafter": dict(labels=["2019", "2023"]),
    "news": dict(body="Toyota anuncia recall do Corolla", labels=["recall do Corolla"], subtitle="G1"),
    "comment": dict(body="Melhor carro que já tive", subtitle="@carlos", lang="en"),
    "poll": dict(keyword="Qual você escolheria?", labels=["Corolla", "Civic"], lang="pl"),
}


@ffmpeg
@pytest.mark.parametrize("kind", sorted(AUTO_CASES))
def test_new_templates_render(tmp_path, kind):
    imgs = []
    for k, color in enumerate(["red", "blue"]):
        p = tmp_path / f"i{k}.jpg"
        Image.new("RGB", (640, 400), color).save(p)
        imgs.append(MediaRef(str(p)))
    theme = {"blueprint": "engineering", "checklist": "paper"}.get(kind, "carbon")
    spec = TemplateSpec(kind, theme, 320, 180, 30, 20, seed=1, media=imgs, **AUTO_CASES[kind])
    dest = render_template(spec, tmp_path / f"{kind}.mp4", ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"])
    out = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v", "-show_entries",
                          "stream=nb_read_frames,width,height", "-of", "csv=p=0", str(dest)],
                         capture_output=True, text=True).stdout.strip()
    assert out == "320,180,20"


@ffmpeg
def test_vhs_renders_half_size_and_is_upscaled(tmp_path):
    p = tmp_path / "i.jpg"
    Image.new("RGB", (640, 400), "green").save(p)
    spec = TemplateSpec("vhs", "blueprint", 320, 180, 30, 10, media=[MediaRef(str(p))])
    tpl = TEMPLATES["vhs"](spec)
    assert tpl.render_size == (160, 90)
    tpl.close()


def test_number_helpers_ptbr():
    from broll_bot.motion.gfx import fmt_number, parse_number

    assert fmt_number(89900, 0, "R$ ") == "R$ 89.900"
    assert fmt_number(7.2, 1, "", " s") == "7,2 s"
    assert parse_number("21,4 kgfm") == (21.4, 1)
    assert parse_number("4.630") == (4630.0, 0)
    assert parse_number("sem número") is None


def test_point_times_follow_mentions():
    from broll_bot.motion.planner import point_times

    words = [Word(w, i * 0.5, i * 0.5 + 0.4) for i, w in enumerate(
        "o ponto forte é o consumo baixo mas a manutenção é cara".split())]
    times = point_times(words, 0.0, 8.0, ["Consumo baixo", "Manutenção cara"])
    assert times[0][0] == pytest.approx(next(w.start for w in words if w.text == "consumo"))
    assert times[1][0] == pytest.approx(next(w.start for w in words if w.text == "manutenção"))


def test_scene_plan_accepts_new_fields_and_old_files():
    from broll_bot.scenes import _plan_to_scenes, _ScenePlan
    from broll_bot.models import Sentence

    sents = [Sentence(i, f"frase {i}", 0, i * 3.0, i * 3.0 + 3) for i in range(3)]
    old = {"scenes": [{"first_sentence": 0, "last_sentence": 2, "visual_summary": "x", "queries": ["car"],
                       "fallback_query": "car", "negative_terms": [], "tone": "calm"}]}
    [sc] = _plan_to_scenes(_ScenePlan.model_validate(old), sents, 9.0)
    assert sc.analysis.style == "normal" and sc.analysis.template_seconds == 0
    new = {"scenes": [{**old["scenes"][0], "style": "gauge", "template_seconds": 6, "caption": "Máxima",
                       "number": {"value": 210, "max": 260, "suffix": " km/h"}, "stats": [], "points": []}]}
    [sc] = _plan_to_scenes(_ScenePlan.model_validate(new), sents, 9.0)
    assert sc.analysis.style == "gauge" and sc.analysis.number["value"] == 210 and sc.analysis.template_seconds == 6


def test_caption_groups_follow_item_mentions():
    words = [Word(w, i * 0.5, i * 0.5 + 0.4) for i, w in enumerate(
        "você sabia que a maçã é crocante e prática e que a banana dá muita energia".split())]
    groups = caption_groups(words, 0.0, 8.0, 2, labels=["maçã", "banana"])
    banana_start = next(w.start for w in words if w.text == "banana")
    assert groups[0][0] == 0.0 and groups[1][0] == pytest.approx(banana_start)
    assert groups[1][2].startswith("banana")

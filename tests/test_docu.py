"""Estilo Documentário (canais gringos de história/guerra): papel rasgado sobre carvão, frase datilografada,
barras no papel e o esquema desenhado da mina S ("a small charge throws it up", "hundreds of steel balls")."""

from types import SimpleNamespace

import numpy as np

import broll_bot.motion  # noqa: F401 - registra os templates
from broll_bot import director
from broll_bot.config import AppConfig
from broll_bot.looks import LOOKS, job_overrides
from broll_bot.models import SceneAnalysis
from broll_bot.motion.planner import MEDIA_FREE, TemplatePlanner
from broll_bot.motion.templates import TEMPLATES, TemplateSpec
from broll_bot.motion.templates_doc import template_cues
from broll_bot.motion.templates_docu import clean_elements

ELEMENTS = [{"t": "ground", "y": 40}, {"t": "box", "x": 46, "y": 40, "w": 6, "h": 9, "fill": "grey"},
            {"t": "arrow", "from": [49, 40], "to": [49, 28], "say": "throws"},
            {"t": "burst", "x": 49, "y": 24, "r": 12, "say": "steel"},
            {"t": "text", "x": 12, "y": 18, "text": "hundreds of steel balls", "size": "l", "color": "red"},
            {"t": "person", "x": 78, "y": 40, "h": 16, "pose": "walk", "face": "left"}]


def _spec(kind, **kw):
    return TemplateSpec(kind=kind, theme="navy", w=480, h=270, fps=10, frames=60, seed=2, lang="en", **kw)


def _red(img):
    a = np.asarray(img.convert("RGB")).astype(int)
    return int(((a[..., 0] > 190) & (a[..., 1] < 110) & (a[..., 2] < 110)).sum())


def test_elements_are_cleaned_to_known_pieces_inside_the_canvas():
    raw = [{"t": "box", "x": 500, "y": -3, "w": 6, "h": 9}, {"t": "laser", "x": 1}, {"t": "line", "pts": [[1, 1]]},
           {"t": "arrow", "from": [0, 0]}, {"t": "text", "x": 5, "y": 5, "text": ""}, "lixo",
           {"t": "person", "x": 50, "y": 40, "pose": "dance", "face": "left"}]
    out = clean_elements(raw)
    assert [e["t"] for e in out] == ["box", "person"]
    assert out[0]["x"] == 100 and out[0]["y"] == 0  # preso dentro da tela 100 x 56
    assert out[1]["pose"] == "stand" and out[1]["face"] == -1


def test_diagram_draws_piece_by_piece_and_when_the_narration_says_the_word():
    caps = [(0.0, 2.0, "a small charge throws it up"), (3.0, 5.0, "hundreds of steel balls")]
    tpl = TEMPLATES["diagram"](_spec("diagram", keyword="The S-mine", elements=ELEMENTS, captions=caps))
    starts = dict(zip([e["t"] for e in tpl.els], tpl.starts))
    assert abs(starts["burst"] - 3.0) < 0.01  # a explosão entra quando a fala diz "steel"
    assert _red(tpl.frame(1)) < _red(tpl.frame(59))  # as anotações vermelhas vão aparecendo
    assert tpl.frame(30).size == (480, 270)
    assert any(c[1].startswith("pencil:") for c in template_cues("diagram", _spec("diagram", elements=ELEMENTS)))


def test_phrase_and_bars_render_on_paper():
    phrase = TEMPLATES["docphrase"](_spec("docphrase", body="Dziesiątki milionów", labels=["milionów"]))
    assert _red(phrase.frame(59)) > _red(phrase.frame(2))  # a palavra marcada sai em vermelho
    bars = TEMPLATES["docbars"](_spec("docbars", keyword="Mines along the French coast",
                                      stats=[{"label": "Late 1943", "value": "1.7"}, {"label": "June 1944", "value": "5"}]))
    assert _red(bars.frame(59)) > _red(bars.frame(1))  # as barras vermelhas crescem
    assert {"docphrase", "docbars", "diagram"} <= MEDIA_FREE


def _an(**kw):
    return SceneAnalysis(visual_summary="", queries=[], fallback_query="", **kw)


def test_planner_needs_the_data_of_each_doc_template():
    assert TemplatePlanner._data_ok("diagram", _an(elements=ELEMENTS))
    assert not TemplatePlanner._data_ok("diagram", _an(elements=ELEMENTS[:2]))
    assert not TemplatePlanner._data_ok("docbars", _an(stats=[{"label": "x", "value": "1"}]))
    assert TemplatePlanner._data_ok("docphrase", _an(body="Tens of millions"))


def test_documentary_look_swaps_the_family_and_frees_the_diagram():
    look = LOOKS["documentario"]
    assert look["templates"]["swap"]["statement"] == "docphrase" and look["templates"]["swap"]["chart"] == "docbars"
    assert "diagram" in look["liberar"]["templates"]
    assert "diagram" not in AppConfig().templates.styles  # os outros estilos (e canais) não ganham o esquema
    assert job_overrides(style="documentario")["templates"]["swap"]["statement"] == "docphrase"


def test_director_asks_for_diagrams_only_when_the_style_allows_and_drops_invented_labels():
    assert '"diagram"' not in director.system_prompt()
    assert '"diagram"' in director.system_prompt(("diagram",))
    sents = [SimpleNamespace(index=i, text=t, start=i * 12.0, end=i * 12.0 + 4) for i, t in enumerate([
        "Welcome back to the channel, today is a dark story.",
        "A small charge throws it up and hundreds of steel balls fly out at waist height.",
        "That is all for today."])]
    els = ELEMENTS + [{"t": "text", "x": 60, "y": 10, "text": "Made by Krupp in 1936"}]
    raw = {"cards": [{"first": 1, "last": 1, "kind": "diagram",
                      "fields": {"caption": "steel balls", "elements": els}}]}
    plan = director.validate(raw, sents, "en")
    assert [c.kind for c in plan.cards] == ["diagram"]
    texts = [e.get("text") for e in plan.cards[0].fields["elements"] if e["t"] == "text"]
    assert texts == ["hundreds of steel balls"]  # o texto inventado saiu, o desenho ficou
    assert any("fora da fala" in d for d in plan.dropped)


def test_diagram_draws_ready_made_vehicles_instead_of_grey_boxes():
    """O Panther disfarçado de M10 saía como dois retângulos cinza: agora tanque, jeep, caminhão... prontos."""
    from broll_bot.motion.templates import TEMPLATES, TemplateSpec
    from broll_bot.motion.templates_docu import clean_elements

    els = clean_elements([{"t": "ground", "y": 42}, {"t": "vehicle", "kind": "Panther", "x": 30, "y": 42},
                          {"t": "vehicle", "kind": "jeep", "x": 75, "y": 42, "face": "left"},
                          {"t": "vehicle", "kind": "spaceship"},
                          {"t": "text", "x": 28, "y": 38, "text": "tôles soudées"}])
    assert [e.get("kind") for e in els if e["t"] == "vehicle"] == ["tank", "jeep"]
    for kind in ("tank", "jeep", "truck", "car", "plane", "ship", "cannon"):
        spec = TemplateSpec(kind="diagram", theme="navy", w=640, h=360, fps=10, frames=40, seed=1, lang="fr",
                            keyword="Test", elements=[{"t": "ground", "y": 42},
                                                      {"t": "vehicle", "kind": kind, "x": 50, "y": 42 if kind != "plane" else 20}])
        tpl = TEMPLATES["diagram"](spec)
        import numpy as np

        page = np.asarray(tpl.frame(39).convert("L")).astype(int)
        assert (page < 90).sum() > 300  # a silhueta foi desenhada
    tpl = TEMPLATES["diagram"](TemplateSpec(kind="diagram", theme="navy", w=640, h=360, fps=10, frames=40, seed=1,
                                            lang="fr", keyword="Panther", elements=els))
    tank = tpl._box(next(e for e in tpl.els if e["t"] == "vehicle"))
    text = next(e for e in tpl.els if e["t"] == "text")
    b = tpl._text_box(text, text["_px"], text["_py"])
    assert b[2] <= tank[0] or tank[2] <= b[0] or b[3] <= tank[1] or tank[3] <= b[1]  # o texto saiu de cima do tanque

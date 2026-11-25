"""Templates para qualquer nicho (templates_plus.py): desenham, têm som, entram no plano certo."""

from __future__ import annotations

import pytest
from PIL import Image

from broll_bot.models import Scene, SceneAnalysis
from broll_bot.motion import TEMPLATES, MediaRef, TemplateSpec
from broll_bot.motion.templates_doc import template_cues


@pytest.fixture()
def photo(tmp_path):
    p = tmp_path / "foto.jpg"
    Image.new("RGB", (640, 360), (90, 140, 70)).save(p)
    return str(p)


CASES = {
    "filmstrip": dict(theme="blueprint_white", keyword="A receita",
                      captions=[(0.2, 1.5, "Esterco fresco de vaca"), (1.5, 3.0, "água, leite e melado")]),
    "opener": dict(theme="navy", keyword="A invenção que pode aposentar o adubo", subtitle="Agro brasileiro"),
    "profile": dict(theme="navy", keyword="Fritz Haber", subtitle="Químico alemão", body="Nobel de Química"),
    "yearflash": dict(theme="navy", number={"value": 1909}, subtitle="Haber tira o nitrogênio do ar"),
    "flow": dict(theme="navy", keyword="Como é feito", points=[{"text": "Esterco"}, {"text": "Fermentação"},
                                                                {"text": "Biofertilizante"}]),
    "mythfact": dict(theme="navy", points=[{"text": "Substitui todo o adubo", "positive": False},
                                           {"text": "Corta parte do adubo", "positive": True}]),
    "definition": dict(theme="navy", keyword="Rochagem", body="Rochas moídas como fonte de minerais para o solo."),
    "agenda": dict(theme="navy", points=[{"text": "O químico alemão"}, {"text": "A guerra"}, {"text": "A cientista"}]),
}


@pytest.mark.parametrize("kind", sorted(CASES))
@pytest.mark.parametrize("with_media", [True, False])
def test_new_templates_draw_with_and_without_media(kind, with_media, photo):
    media = [MediaRef(photo), MediaRef(photo)] if with_media else []
    spec = TemplateSpec(kind=kind, w=384, h=216, fps=10, frames=40, media=media, lang="pt", **CASES[kind])
    tpl = TEMPLATES[kind](spec)
    try:
        frames = [tpl.frame(i).convert("RGB") for i in (0, 12, 39)]
    finally:
        tpl.close()
    assert all(f.size == (384, 216) for f in frames)
    assert frames[0].tobytes() != frames[-1].tobytes()  # anima
    assert template_cues(kind, spec)  # tem som


def test_planner_data_rules_for_the_new_templates():
    from broll_bot.motion.planner import TemplatePlanner

    ok = TemplatePlanner._data_ok
    a = SceneAnalysis("", [], "")
    assert not ok("yearflash", a) and not ok("flow", a) and not ok("mythfact", a) and not ok("definition", a)
    a.number = {"value": 1909}
    a.points = [{"text": "mito", "positive": False}, {"text": "fato", "positive": True}]
    a.caption, a.body = "Rochagem", "Rochas moídas"
    assert ok("yearflash", a) and ok("mythfact", a) and ok("definition", a) and ok("flow", a)


def test_opener_goes_on_the_first_broll_scene_after_the_presenter():
    from broll_bot.rhythm import add_opener

    scenes = [Scene(i, f"frase {i}", i * 5.0, i * 5.0 + 5.0, SceneAnalysis("", [], "")) for i in range(6)]
    notes = add_opener(scenes, "A invenção que pode aposentar o adubo", "Agro", after=9.0)
    assert notes and scenes[2].analysis.style == "opener" and scenes[2].analysis.caption.startswith("A invenção")
    assert all(s.analysis.style == "normal" for s in scenes if s.index != 2)
    assert add_opener(scenes, "", "") == []


def test_filmstrip_is_a_free_rhythm_visual():
    from broll_bot.rhythm import FREE_VISUALS, VISUALS

    assert "filmstrip" in FREE_VISUALS and "filmstrip" in VISUALS


def test_director_accepts_the_new_cards_and_the_title():
    from broll_bot import director
    from broll_bot.cues import set_language
    from broll_bot.models import Sentence

    set_language("pt")
    texts = ["Aí entra Fritz Haber.", "Em 1909, esse químico alemão conseguiu uma coisa que parecia impossível.",
             "Pesquisadores brasileiros chamam isso de rochagem.", "Rochas moídas liberam minerais aos poucos no solo.",
             "Biofertilizante substitui todo o adubo? Hoje, sozinho, não."]
    ss = [Sentence(i, t, 0, i * 12.0, i * 12.0 + 8) for i, t in enumerate(texts)]
    raw = {"title": "Fritz Haber e a rochagem", "kicker": "Agro",
           "cards": [{"first": 0, "last": 0, "kind": "profile", "fields": {
                         "caption": "Fritz Haber", "subtitle": "químico alemão",
                         "items": [{"label": "Fritz Haber", "query": "Fritz Haber portrait"}]}},
                     {"first": 1, "last": 1, "kind": "yearflash", "fields": {"number": {"value": 1909},
                                                                             "subtitle": "químico alemão"}},
                     {"first": 2, "last": 3, "kind": "definition", "fields": {
                         "caption": "Rochagem", "body": "Rochas moídas que liberam minerais aos poucos no solo"}},
                     {"first": 4, "last": 4, "kind": "mythfact", "fields": {"points": [
                         {"text": "Biofertilizante substitui todo o adubo", "positive": False},
                         {"text": "Hoje, sozinho, não", "positive": True}]}}]}
    plan = director.validate(raw, ss, "pt")
    try:
        assert [c.kind for c in plan.cards] == ["profile", "yearflash", "definition", "mythfact"], plan.dropped
        assert plan.title == "Fritz Haber e a rochagem" and plan.kicker == "Agro"
        bad = director.validate({"cards": [{"first": 1, "last": 1, "kind": "yearflash",
                                            "fields": {"number": {"value": 1910}}}]}, ss, "pt")
        assert not bad.cards  # ano que ninguém disse
    finally:
        set_language("en")

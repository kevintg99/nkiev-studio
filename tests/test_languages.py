"""Três idiomas (pt, fr, pl): detecção, números, coerência e detector de templates."""

from pathlib import Path

import pytest

from broll_bot.auto_templates import auto_templates
from broll_bot.coherence import enforce_coherence, numbers_in
from broll_bot.lang import detect_language, resolve
from broll_bot.models import Scene, SceneAnalysis
from broll_bot.motion import gfx
from broll_bot.text import tokens
from broll_bot.transcription import parse_script

EX = Path(__file__).resolve().parents[1] / "exemplos"


@pytest.mark.parametrize("name,lang", [("roteiro_teste_templates.txt", "pt"), ("roteiro_teste_templates_fr.txt", "fr"),
                                       ("roteiro_teste_templates_pl.txt", "pl")])
def test_language_is_detected_from_the_script(name, lang):
    assert detect_language((EX / name).read_text(encoding="utf-8")) == lang


def test_resolve_keeps_an_explicit_language():
    assert resolve("auto", "Le moteur est très bon et la voiture est confortable.") == "fr"
    assert resolve("pl", "texto em português") == "pl"


def test_polish_letters_do_not_break_words():
    assert tokens("89 900 złotych, łapka w górę") == ["89", "900", "zlotych", "lapka", "w", "gore"]


def test_numbers_follow_the_language():
    try:
        gfx.set_number_locale("pt")
        assert gfx.fmt_number(129900) == "129.900" and gfx.fmt_number(9.2, 1) == "9,2"
        gfx.set_number_locale("fr")
        assert gfx.fmt_number(32900, 0, "", " €") == "32 900 €"
        gfx.set_number_locale("pl")
        assert gfx.fmt_number(119900, 0, "", " zł") == "119 900 zł"
        assert gfx.parse_number("119 900 zł") == (119900.0, 0)
    finally:
        gfx.set_number_locale("pt")


def test_spoken_numbers_in_french_and_polish():
    assert {29900.0, 100.0, 9.2, 30000.0} <= numbers_in("29 900 euros, de zéro à cent en 9,2 secondes, 30 mille")
    assert {119900.0, 100.0, 9.2, 90000.0, 1.0} <= numbers_in("119 900 zł, od zera do stu w 9,2 s, 90 tys. zł, "
                                                               "pierwsza generacja")


def _scenes(name):
    text = (EX / name).read_text(encoding="utf-8")
    sents = parse_script(text)
    t = 0.0
    for s in sents:
        d = len(s.text.split()) * 0.42
        s.start, s.end = t, t + d
        t += d
    scenes = []
    for p in sorted({s.paragraph for s in sents}):
        ss = [s for s in sents if s.paragraph == p]
        scenes.append(Scene(len(scenes), " ".join(s.text for s in ss), ss[0].start, ss[-1].end,
                            SceneAnalysis("x", ["car"], "car")))
    return scenes, sents


ALL = {"headline", "timeline", "chapter", "datasheet", "speedometer", "magnifier", "beforeafter", "news", "price",
       "proscons", "comment", "score", "poll"}


@pytest.mark.parametrize("name,lang,currency,accel", [
    ("roteiro_teste_templates_fr.txt", "fr", " €", "0 à 100 km/h"),
    ("roteiro_teste_templates_pl.txt", "pl", " zł", "0-100 km/h"),
])
def test_detector_finds_every_template_in_french_and_polish(name, lang, currency, accel):
    scenes, sents = _scenes(name)
    out, _ = auto_templates(scenes, sents, "toyota corolla", lang=lang)
    by = {s.analysis.style: s.analysis for s in out}
    assert ALL <= set(by)
    assert by["price"].number["suffix"] == currency and by["price"].number["level"] == 0.5
    assert by["speedometer"].number["label"] == accel and by["speedometer"].number["value"] == 9.2
    assert [i["label"] for i in by["timeline"].items] == ["1966", "1983", "2002", "2014", "2020"]
    assert {p["positive"] for p in by["proscons"].points} == {True, False}
    assert by["score"].number["value"] == 8.5
    # os dados vieram da fala: a coerência não derruba nada
    enforce_coherence(out)
    assert ALL <= {s.analysis.style for s in out}


def test_polish_names_go_back_to_the_dictionary_form():
    scenes, sents = _scenes("roteiro_teste_templates_pl.txt")
    out, _ = auto_templates(scenes, sents, "toyota corolla", lang="pl")
    poll = next(s.analysis for s in out if s.analysis.style == "poll")
    assert [i["label"] for i in poll.items] == ["Corolla", "Civic", "Octavia"]
    specs = next(s.analysis for s in out if s.analysis.style == "datasheet")
    assert {st["label"]: st["value"] for st in specs.stats}["Skrzynia"] == "CVT"


def test_cta_in_french_and_polish_gets_the_subscribe_button():
    from broll_bot.plan_fix import add_cta_overlays

    scenes = [Scene(0, "Abonnez-vous et activez la cloche !", 0, 4, SceneAnalysis("x", ["x"], "x")),
              Scene(1, "Zostaw łapkę w górę i zasubskrybuj kanał.", 4, 8, SceneAnalysis("x", ["x"], "x"))]
    add_cta_overlays(scenes, "toyota corolla")
    assert [s.analysis.overlay.get("kind") for s in scenes] == ["subscribe", "subscribe"]


def test_canadian_dollar_channel():
    """Canal em dólar canadense: "dollars" na fala vira C$ (e "Canadian dollars" sempre), a nota de oficina e a
    etiqueta de preço da IA também; nos outros canais "dollars" continua $."""
    from broll_bot.coherence import fix_currency
    from broll_bot.cues import set_currency, set_language
    from broll_bot.magnet_detect import receipt
    from broll_bot.models import Scene, SceneAnalysis
    from broll_bot.spoken import screen_text
    from studio.canais import CURRENCIES

    assert "C$" in CURRENCIES
    set_language("en")
    try:
        set_currency("C$")
        assert screen_text("The repair cost two thousand dollars.") == "The repair cost C$2,000."
        d = receipt([screen_text("The repair bill was eighteen hundred dollars at the dealer.")], "Ford Escape")
        assert d and d.fields["number"]["prefix"] == "C$"
        sc = Scene(0, "It costs about thirty thousand.", 0, 5,
                   SceneAnalysis("", [], "", style="price", number={"value": 30000, "prefix": "$", "suffix": ""}))
        fix_currency(sc)
        assert sc.analysis.number["prefix"] == "C$"
        set_currency("£")
        assert screen_text("It cost two thousand dollars.") == "It cost $2,000."
        assert screen_text("It cost two thousand Canadian dollars.") == "It cost C$2,000."
    finally:
        set_currency(None)


def test_script_language_wins_over_a_wrong_channel():
    """Short em inglês feito sem querer no canal em português: a transcrição rodou em português e o diretor
    escreveu os cards em português (caíram todos)."""
    from broll_bot.lang import resolve

    en = ("Go look at your windshield. Right now. That little number in the corner tells you exactly where your car "
          "was built. If it is a two, it was built in Canada. The best-selling vehicle in Canada is not built here.")
    assert resolve("pt", en) == "en"
    assert resolve("pt", "Toyota Corolla 2014 RAV4") == "pt"  # pouco texto: vale o canal
    assert resolve("fr", "Le moteur de la voiture est très fiable et il ne coûte pas cher.") == "fr"

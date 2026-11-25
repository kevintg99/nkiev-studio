"""Roteiro em inglês: ranking, preço em dólar/libra, consumo em mpg, números com vírgula de milhar."""

import pytest

from broll_bot.auto_templates import _value, price, price_range
from broll_bot.coherence import enforce_coherence, fix_currency, numbers_in
from broll_bot.cues import set_language
from broll_bot.models import Scene, SceneAnalysis
from broll_bot.motion import gfx
from broll_bot.rhythm import fuel_fact, keyword
from broll_bot.subject import rank_header, section_eras, section_generations


@pytest.fixture(autouse=True)
def english():
    set_language("en")
    yield
    set_language("pt")


def test_rank_headers_in_english():
    assert rank_header("Number 8: the Toyota Corolla, 2014 to 2019.")[:2] == (8, "Toyota Corolla")
    assert rank_header("No. 3 - The Honda Fit with the 1.5 engine.")[:2] == (3, "Honda Fit")
    assert rank_header("#1: the Lexus RX 450h.")[:2] == (1, "Lexus RX 450h")


def test_prices_in_dollars_and_pounds():
    d = price(["On the used market it costs about $12,000 today."], "Honda Fit")
    assert d.fields["number"]["value"] == 12000 and d.fields["number"]["prefix"] == "$"
    r = price_range(["Expect to pay between 8 and 12 grand for a clean one."], "Honda Fit")
    assert (r.fields["number"]["value"], r.fields["number"]["value_to"]) == (8000, 12000)
    # a moeda falada vence: libra
    scene = Scene(0, "Prices go from £6,000 to £9,000.", 0, 5, SceneAnalysis("car", ["car"], "car", style="price"))
    scene.analysis.number = price_range([scene.text], "Honda Fit").fields["number"]
    fix_currency(scene)
    assert scene.analysis.number["prefix"] == "£"


def test_comma_is_a_thousands_separator_in_english_only():
    assert _value("12,000") == (12000.0, 0) and _value("1,250,000") == (1250000.0, 0)
    assert 12000.0 in numbers_in("It costs $12,000 and does 0 to 60 in 9.5 seconds")
    gfx.set_number_locale("en")
    try:
        assert gfx.parse_number("$12,000") == (12000.0, 0) and gfx.fmt_number(12000, 0, "$") == "$12,000"
    finally:
        gfx.set_number_locale("pt")
    set_language("fr")
    assert _value("4,5") == (4.5, 1)  # francês: vírgula continua sendo decimal


def test_fuel_economy_in_mpg():
    f = fuel_fact("In the city it gets around 40 mpg, which is great.")
    assert (f["title"], f["detail"], f["title_b"]) == ("40", "mpg", "IN THE CITY")
    f = fuel_fact("Expect 35 to 40 miles per gallon on the highway.")
    assert (f["title"], f["title_b"]) == ("35–40", "ON THE HIGHWAY")
    assert fuel_fact("6 liters per 100 km on a long trip")["detail"] == "L/100 km"
    assert not fuel_fact("A 2.0 liter engine with a timing chain.")


def test_years_generations_and_keywords():
    eras = section_eras(["It was built before 2018.", "Look for one from 2012 to 2015."], ["A", "B"])
    assert eras["A"][1] == 2018 + 5 and eras["B"] == (2010, 2020)
    assert section_generations(["The third generation Honda CR-V."], ["C"]) == {"C": 3}
    assert keyword("And honestly, the engine is basically bulletproof.") == "bulletproof"


def test_spoken_numbers_pass_coherence():
    s = Scene(0, "It costs about twelve thousand dollars, so fifteen grand is too much.", 0, 6,
              SceneAnalysis("car", ["car"], "car", overlay={"kind": "label", "title": "$15k"}))
    enforce_coherence([s])
    assert s.analysis.overlay  # "fifteen grand" = 15k

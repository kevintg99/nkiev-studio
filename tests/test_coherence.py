"""Verificador de coerência: templates e overlays precisam bater com a fala da cena."""

from broll_bot.coherence import enforce_coherence, numbers_in
from broll_bot.models import Scene, SceneAnalysis


def sc(i, text, **kw):
    return Scene(i, text, i * 5.0, i * 5.0 + 5, SceneAnalysis("x", ["x"], "x", **kw))


def test_spoken_numbers_include_words_thousands_and_ordinals():
    nums = numbers_in("Vai de zero a cem em 9,2 segundos, custa 110 mil reais, 4.630 mm, fica em primeiro lugar")
    assert {0.0, 100.0, 9.2, 110.0, 110000.0, 4630.0, 1.0} <= nums


def test_invented_numbers_and_items_are_demoted():
    scenes = [
        sc(0, "O Corolla tem 177 cavalos e 21,4 de torque.", style="chart",
           stats=[{"label": "Potência", "value": "177"}, {"label": "Peso", "value": "1.380"}]),
        sc(1, "Ele é confortável na cidade.", style="counter", number={"value": 80, "suffix": "%"}, caption="Conforto"),
        sc(2, "O Corolla enfrenta o Civic.", style="versus",
           items=[{"label": "BMW", "query": "bmw"}, {"label": "Audi", "query": "audi"}]),
        sc(3, "Um carro moderno e cheio de tecnologia.", style="vhs"),
        sc(4, "O consumo é baixo e a revenda é forte.", style="checklist",
           points=[{"text": "Consumo baixo", "positive": True}, {"text": "Revenda forte", "positive": True},
                   {"text": "Teto solar panorâmico", "positive": True}]),
    ]
    notes = enforce_coherence(scenes)
    assert [s.analysis.style for s in scenes] == ["normal", "normal", "normal", "normal", "checklist"]
    assert [p["text"] for p in scenes[4].analysis.points] == ["Consumo baixo", "Revenda forte"]
    assert len(notes) == 6  # a linha removida do gráfico também é anotada


def test_coherent_plan_is_left_alone():
    scenes = [
        sc(0, "Quem lembra do primeiro Corolla, lá nos anos noventa?", style="headline", caption="Anos 90"),
        sc(1, "O Corolla entrega 177 cavalos, enquanto o Civic fica com 155.", style="versus",
           items=[{"label": "Corolla", "query": "a"}, {"label": "Civic", "query": "b"}],
           stats=[{"label": "Potência", "value": "177", "value_b": "155"}],
           overlay={"kind": "pair", "title": "177 cv", "title_b": "155 cv"}),
        sc(2, "Hoje custa em média 110 mil reais na tabela FIPE.", overlay={"kind": "bignum", "title": "R$ 110 mil"}),
        sc(3, "No ranking, o Corolla fica em primeiro lugar.", style="ranking", rank=1, caption="Corolla"),
        sc(4, "Se curtiu, deixa o like e conta qual sedã você escolheria.", style="quote",
           caption="Deixa o like", highlights=["qual sedã", "você escolheria"]),
    ]
    assert enforce_coherence(scenes) == []
    assert [s.analysis.style for s in scenes] == ["headline", "versus", "normal", "ranking", "quote"]


def test_overlays_with_unspoken_facts_are_removed():
    scenes = [sc(0, "O Corolla faz 12 km/l na estrada.", overlay={"kind": "bignum", "title": "90%"}),
              sc(1, "Repare nos faróis de LED.", overlay={"kind": "tag", "title": "faróis de LED"}),
              sc(2, "Olha esse interior.", overlay={"kind": "keyword", "title": "motor turbo potente"})]
    enforce_coherence(scenes)
    assert [s.analysis.overlay.get("kind") for s in scenes] == [None, "tag", None]


def test_same_template_twice_in_a_row_is_demoted():
    scenes = [sc(0, "Agora, o motor.", style="chapter", caption="Motor"),
              sc(1, "Agora, o motor de novo.", style="chapter", caption="Motor")]
    enforce_coherence(scenes)
    assert [s.analysis.style for s in scenes] == ["chapter", "normal"]


def test_library_tags_keep_years_codes_and_folders(tmp_path):
    from broll_bot.providers.local_library import LocalLibraryProvider, tags_from_filename

    assert tags_from_filename("toyota-corolla-2020-xei_white-driving-city") == \
        ["toyota", "corolla", "2020", "xei", "white", "driving", "city"]
    assert tags_from_filename("mixkit-red-sports-car-4211-hd-ready") == ["red", "sports", "car"]
    folder = tmp_path / "lib" / "toyota-corolla-e100-1993"
    folder.mkdir(parents=True)
    from PIL import Image

    Image.new("RGB", (1920, 1080), "gray").save(folder / "sedan-front-street.jpg")
    lib = LocalLibraryProvider(tmp_path / "lib", tmp_path / "index.json")
    tags = lib.entries[0].tags
    assert {"toyota", "corolla", "e100", "1993", "sedan", "front", "street"} <= set(tags)

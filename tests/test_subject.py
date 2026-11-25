"""Assunto do vídeo, glossário automotivo e buscas ancoradas no carro."""

from broll_bot.models import Scene, SceneAnalysis
from broll_bot.subject import anchor_scenes, detect_subject

NICHE = "automotivo (carros, reviews, comparativos, preços)"
SCRIPT = ("Por que o Toyota Corolla virou o sedã mais desejado do Brasil? O Corolla XEi usa um motor 2.0. "
          "Corolla contra Civic: o Honda Civic é mais esportivo. Hoje, um Toyota Corolla usado custa 110 mil.")


def sc(i, text, queries, summary="x", negatives=()):
    return Scene(i, text, 0, 5, SceneAnalysis(summary, list(queries), queries[-1], list(negatives)))


def test_detects_most_cited_brand_and_model():
    assert detect_subject(SCRIPT) == "toyota corolla"
    assert detect_subject("Um vídeo sobre pneus e calibragem.") == ""


def test_generation_means_car_generation():
    s = sc(0, "A geração de 2014 era mais conservadora, e a de 2020 ganhou visual moderno.",
           ["generation z young people", "family generations"], summary="different generations of people",
           negatives=["cartoon"])
    notes = anchor_scenes([s], "toyota corolla", NICHE)
    a = s.analysis
    assert {"young people", "generation z"} <= set(a.negative_terms) and a.negative_terms[0] == "cartoon"
    assert all(q.startswith("corolla ") for q in a.queries)  # buscas sem carro ganham o carro
    assert a.visual_summary.startswith("toyota corolla")
    assert notes


def test_scene_about_the_car_gets_a_model_query_and_car_queries_stay():
    s = sc(1, "O Corolla XEi usa um motor 2.0 flex.", ["car engine bay", "engine close up"])
    anchor_scenes([s], "toyota corolla", NICHE)
    assert s.analysis.queries == ["car engine bay", "toyota corolla", "engine close up"]


def test_other_niches_are_untouched():
    s = sc(0, "A geração Z usa o celular o dia todo.", ["young people phone"])
    assert anchor_scenes([s], "", "tecnologia e comportamento") == []
    assert s.analysis.queries == ["young people phone"]

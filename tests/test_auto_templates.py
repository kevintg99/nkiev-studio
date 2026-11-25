"""Detector de templates: pistas claras na fala viram template, com os dados da própria fala."""

from pathlib import Path

from broll_bot.auto_templates import auto_templates, detect
from broll_bot.config import TemplatesConfig
from broll_bot.models import Scene, SceneAnalysis, Sentence
from broll_bot.motion.planner import overlay_fallbacks, select_template_scenes
from broll_bot.transcription import parse_script

EXAMPLE = Path(__file__).resolve().parents[1] / "exemplos" / "roteiro_teste_templates.txt"


def kinds(text: str) -> list[str]:
    return [d.kind for d in detect([s.strip() + "." for s in text.split(".") if s.strip()], "toyota corolla")]


def test_each_cue_becomes_its_template():
    assert kinds("Tudo começou em 1966. Em 1983 mudou. Em 2002 chegou ao Brasil") == ["timeline"]
    assert kinds("Ele faz de zero a cem em 9,2 segundos") == ["speedometer"]
    assert kinds("A velocidade máxima é de 210 km/h") == ["speedometer"]
    assert kinds("O motor 2.0 flex tem 177 cavalos e 21,4 kgfm") == ["datasheet"]
    assert kinds("Na tabela FIPE ele sai por 130 mil reais. É um preço justo") == ["price"]
    assert kinds("Os pontos fortes são o consumo e a revenda. Os pontos fracos são a manutenção cara") == ["proscons"]
    assert kinds("Minhas notas: conforto 9, consumo 8 e revenda 9,5. Nota final: 8,5") == ["score"]
    assert kinds("Um inscrito comentou: meu carro nunca me deixou na mão") == ["comment"]
    assert kinds("Repare nos faróis de LED") == ["magnifier"]
    assert kinds("Repare no preço dele") == []  # não é uma peça do carro
    assert kinds("O carro é confortável e econômico") == []


def test_data_is_copied_from_the_speech():
    [d] = detect(["Na tabela FIPE, um Corolla 2022 sai por cerca de 130 mil reais.", "É um preço justo."], "")
    assert d.fields["number"]["value"] == 130000 and d.fields["number"]["level"] == 0.5 and d.last == 1
    [d] = detect(["E você, qual escolheria: Corolla, Civic ou Sentra? Comenta aí."], "toyota corolla")
    assert d.fields["caption"] == "E você, qual escolheria?"
    assert [i["label"] for i in d.fields["items"]] == ["Corolla", "Civic", "Sentra"]
    assert d.fields["items"][0]["query"] == "toyota corolla"
    [d] = detect(["Os pontos fracos são a manutenção cara e a central multimídia, que já parece ultrapassada.",
                  "Os pontos fortes são o consumo baixo."], "")
    assert [(p["text"], p["positive"]) for p in d.fields["points"]] == [
        ("Manutenção cara", False), ("Central multimídia", False), ("Consumo baixo", True)]


def _example_scenes():
    sents = parse_script(EXAMPLE.read_text(encoding="utf-8"))
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
    return scenes, sents, t


def test_example_script_triggers_the_new_templates():
    scenes, sents, total = _example_scenes()
    out, notes = auto_templates(scenes, sents, "toyota corolla")
    styles = [s.analysis.style for s in out]
    for kind in ("headline", "timeline", "chapter", "datasheet", "speedometer", "magnifier", "beforeafter", "news",
                 "price", "proscons", "comment", "score", "poll"):
        assert kind in styles, kind
    assert styles[-1] == "normal"  # a chamada final fica para o botão de inscrição
    # ficha técnica e 0–100 estavam no mesmo parágrafo: a cena foi dividida
    assert any("dividida" in n for n in notes)
    assert all(a.end == b.start for a, b in zip(out, out[1:]))


def test_ai_choices_are_kept_unless_a_richer_version_fits():
    sents = [Sentence(0, "Ele faz de zero a cem em 9,2 segundos.", 0, 0, 4)]
    sc = Scene(0, sents[0].text, 0, 4, SceneAnalysis("x", ["car"], "car", style="versus"))
    out, notes = auto_templates([sc], sents)
    assert out[0].analysis.style == "versus" and notes == []
    # um "stat" da IA para o 0–100 vira o velocímetro
    sc = Scene(0, sents[0].text, 0, 4, SceneAnalysis("x", ["car"], "car", style="stat", number={"value": 9.2}))
    out, notes = auto_templates([sc], sents)
    assert out[0].analysis.style == "speedometer" and "trocado" in notes[0]


def test_scenes_split_by_the_ai_are_merged_back():
    """A IA pôs uma frase por cena: a linha do tempo, os prós e contras e o preço se juntam de novo."""
    texts = ["Tudo começou em 1966.", "Em 1983, ele ganhou tração dianteira.", "Em 2002, chegou ao Brasil.",
             "E em 2020, a geração atual.", "Agora, o motor.",
             "Na tabela FIPE, ele sai por cerca de 130 mil reais.", "É um preço justo.",
             "Vamos aos prós e contras.", "Os pontos fortes são o consumo baixo.",
             "Os pontos fracos são a manutenção cara."]
    sents = [Sentence(i, t, 0, i * 3.0, i * 3.0 + 3) for i, t in enumerate(texts)]
    groups = [[0], [1], [2], [3, 4], [5], [6], [7], [8], [9]]
    styles = {1: "headline", 3: "normal", 7: "checklist"}
    scenes = [Scene(k, " ".join(texts[i] for i in g), g[0] * 3.0, g[-1] * 3.0 + 3,
                    SceneAnalysis("x", [f"q{k}"], "car", style=styles.get(k, "normal"), caption="x"))
              for k, g in enumerate(groups)]
    out, notes = auto_templates(scenes, sents, "toyota corolla")
    kinds = [(s.analysis.style, s.text[:12]) for s in out]
    tl = next(s for s in out if s.analysis.style == "timeline")
    assert [i["label"] for i in tl.analysis.items] == ["1966", "1983", "2002", "2020"]
    assert ("chapter", "Agora, o mot") in kinds  # a frase que sobrou virou a própria cena
    price = next(s for s in out if s.analysis.style == "price")
    assert price.analysis.number["level"] == 0.5 and "justo" in price.text
    pc = next(s for s in out if s.analysis.style == "proscons")
    assert {p["positive"] for p in pc.analysis.points} == {True, False}
    assert all(a.end == b.start for a, b in zip(out, out[1:]))
    assert sum("juntadas" in n for n in notes) == 3


def test_without_limits_every_template_the_speech_asks_for_goes_in():
    scenes, sents, total = _example_scenes()
    out, _ = auto_templates(scenes, sents, "toyota corolla")
    chosen = select_template_scenes(out, TemplatesConfig(), total)
    wanted = {s.index for s in out if s.analysis.style not in ("normal", "chapter")}
    assert wanted <= set(chosen)  # o capítulo de 1 s é curto demais para o template (vira overlay)
    assert {"comment", "news", "magnifier", "poll"} <= set(chosen.values())


def test_with_limits_the_most_informative_stay_and_the_rest_become_overlays():
    scenes, sents, total = _example_scenes()
    out, _ = auto_templates(scenes, sents, "toyota corolla")
    chosen = select_template_scenes(out, TemplatesConfig(min_gap_seconds=3, max_share=0.5), total)
    picked = set(chosen.values())
    assert {"timeline", "datasheet", "price", "proscons", "score", "headline"} <= picked
    assert "speedometer" in picked  # a ficha técnica encurta para o 0–100 caber
    notes = overlay_fallbacks(out, chosen)
    left = {s.analysis.style: s.analysis.overlay.get("kind") for s in out if s.index not in chosen and
            s.analysis.style != "normal"}
    assert left.get("magnifier") == "callout"
    assert notes

def test_cta_scene_gets_the_subscribe_button_even_if_the_ai_made_a_title():
    from broll_bot.plan_fix import add_cta_overlays

    sc = Scene(0, "Se curtiu, deixa o like e se inscreve no canal.", 0, 4,
               SceneAnalysis("person typing a comment on a phone", ["smartphone commenting"], "phone",
                             style="headline", caption="Se curtiu"))
    add_cta_overlays([sc], "toyota corolla")
    a = sc.analysis
    assert a.style == "normal" and a.overlay["kind"] == "subscribe"
    assert all("toyota corolla" in q for q in a.queries)


def test_earlier_template_shrinks_so_the_next_one_fits():
    specs = Scene(0, "x", 0, 9.3, SceneAnalysis("x", ["x"], "x", style="specs"))
    speedo = Scene(1, "x", 9.3, 13, SceneAnalysis("x", ["x"], "x", style="speedometer", number={"value": 9.2}))
    chosen = select_template_scenes([specs, speedo], TemplatesConfig(), 60)
    assert chosen == {0: "specs", 1: "speedometer"}
    assert specs.analysis.template_seconds <= 9.3 - 3.0 + 1e-6


def test_commons_gets_short_identifying_queries():
    from broll_bot.providers.wikimedia import _minimal, commons_query

    assert commons_query("toyota corolla driving golden hour") == "toyota corolla"
    assert commons_query("toyota corolla 2014 front three quarter") == "toyota corolla 2014 front"
    assert _minimal("classic toyota corolla 1960s") == "toyota corolla"


def test_year_items_search_photos_and_prefer_the_spoken_year(tmp_path):
    from broll_bot.config import AppConfig
    from broll_bot.models import Candidate, MediaType
    from broll_bot.motion.planner import TemplatePlanner
    from broll_bot.search import SceneSearchResult

    calls = []

    class Searcher:
        def search_scene(self, scene, needed, needed_images, used, budget=None):
            calls.append((needed_images, budget))
            res = SceneSearchResult(scene.index, [], needed=1)
            modern = Candidate("pexels", "1", MediaType.VIDEO, 1920, 1080, 10, None, None, tags=["car"],
                               description="modern car")
            old = Candidate("wikimedia", "2", MediaType.IMAGE, 1920, 1080, 0, None, None,
                            tags=["toyota", "corolla", "1966"], description="1966 Toyota Corolla")
            res.ranked = [modern, old]
            return res

    class Sel:
        used_keys: set = set()

        def claim(self, cand):
            return tmp_path / f"{cand.media_id}.jpg"

    planner = TemplatePlanner(AppConfig(), selector=Sel(), searcher=Searcher(), thumbs=None)  # type: ignore[arg-type]
    sc = Scene(0, "Tudo começou em 1966.", 0, 5, SceneAnalysis("x", ["x"], "x"))
    cand, ref, label = planner._item_media(sc, {"label": "1966", "query": "toyota corolla 1966"}, 0, "timeline")
    assert cand.provider == "wikimedia" and label == "1966"
    assert calls[-1] == (1, 5)  # busca foto também, com orçamento para todos os bancos


def test_subscribe_ignores_the_overlay_gap():
    from broll_bot.config import AppConfig
    from broll_bot.models import Candidate, MediaType, Shot, Transition
    from broll_bot.motion.planner import plan_overlays

    cfg = AppConfig()
    cand = Candidate("pexels", "1", MediaType.VIDEO, 1920, 1080, 10, None, None)
    scenes = {0: Scene(0, "E você, qual escolheria?", 0, 4, SceneAnalysis("x", ["x"], "x", overlay={
                  "kind": "bullets", "title": "Qual?", "lines": ["A", "B"]})),
              1: Scene(1, "Se inscreve no canal!", 4, 8, SceneAnalysis("x", ["x"], "x", overlay={
                  "kind": "subscribe", "title": ""}))}
    shots = [Shot(0, 0, 0, 4, 4, Transition("cut", 0), 0, 120, candidate=cand),
             Shot(1, 1, 4, 8, 4, Transition("cut", 0), 120, 240, candidate=cand)]
    assert plan_overlays(shots, scenes, cfg, []) == 2
    assert shots[1].overlay["kind"] == "subscribe"


def test_poll_option_in_portuguese_searches_the_scene_queries(tmp_path):
    """Enquete do fim do vídeo: a opção "Agora" buscava "garrafa no telhado agora" nos bancos em inglês."""
    from broll_bot.config import AppConfig
    from broll_bot.cues import set_language
    from broll_bot.models import Candidate, MediaType
    from broll_bot.motion.planner import TemplatePlanner
    from broll_bot.search import SceneSearchResult

    seen = []

    class Searcher:
        def search_scene(self, scene, needed, needed_images, used, budget=None):
            seen.append(scene.analysis.queries[0])
            res = SceneSearchResult(scene.index, [], needed=1)
            res.ranked = [Candidate("pexels", str(len(seen)), MediaType.VIDEO, 1920, 1080, 10, None, None)]
            return res

    class Sel:
        used_keys: set = set()

        def claim(self, cand):
            return tmp_path / f"{cand.media_id}.mp4"

    planner = TemplatePlanner(AppConfig(), selector=Sel(), searcher=Searcher(), thumbs=None)  # type: ignore[arg-type]
    sc = Scene(0, "Você colocaria uma garrafa dessas no telhado?", 0, 6,
               SceneAnalysis("x", ["plastic bottle on roof", "family at home"], "bottle roof"))
    set_language("pt")
    try:
        planner._item_media(sc, {"label": "Agora", "query": "garrafa no telhado agora"}, 0, "poll")
        planner._item_media(sc, {"label": "Ainda não", "query": "mais anos de prova"}, 1, "poll")
        planner._item_media(sc, {"label": "Sim", "query": "solar bottle bulb"}, 2, "poll")
    finally:
        set_language("en")
    assert seen == ["plastic bottle on roof", "family at home", "solar bottle bulb"]

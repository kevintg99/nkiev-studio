"""Diretor de IA: validação do plano contra o roteiro, uso pelo ranking/listas/cards e a reserva das regras."""

from __future__ import annotations

import pytest

from broll_bot import director
from broll_bot.config import AppConfig
from broll_bot.cues import set_currency, set_language
from broll_bot.models import Scene, SceneAnalysis, Sentence

SCRIPT = [
    "In the last three months, one brand-new SUV sold three units in all of Canada.",  # 0
    "A year earlier, in the same three months, it sold four hundred and seventy-one.",  # 1
    "Let's count them down.",  # 2
    "Number two.",  # 3
    "The gas Chevrolet Blazer.",  # 4
    "Canadian sales fell thirty-four percent last year, according to Le Guide de l'Auto.",  # 5
    "There won't be a twenty twenty-seven Blazer at all.",  # 6
    "And number one.",  # 7
    "The Dodge Hornet.",  # 8
    "Stellantis Canada reported three sold.",  # 9
    "So how do you get a deal?",  # 10
    "One.",  # 11
    "Ask for in-stock twenty twenty-sixes.",  # 12
    "Two.",  # 13
    "Check the build date on the sticker.",  # 14
    "Honda is offering twelve thousand dollars on the Prologue.",  # 15
]


def _sentences() -> list[Sentence]:
    out, t = [], 0.0
    for i, text in enumerate(SCRIPT):
        d = max(1.0, len(text.split()) / 2.5)
        out.append(Sentence(i, text, 0, t, t + d))
        t += d + 4.0  # bem espaçadas: o intervalo mínimo entre cards não interfere
    return out


RAW = {
    "niche": "cars", "automotive": True, "subject": "", "currency": "C$",
    "visual_niche": ["a new car on a dealership lot"], "visual_off": ["a beach"],
    "sections": [
        {"header": 3, "last": 6, "rank": 2, "name": "Chevrolet Blazer", "verdict": "", "show": True},
        {"header": 7, "last": 15, "rank": 1, "name": "Dodge Hornet", "verdict": "", "show": True},
    ],
    "lists": [{"word": "", "items": [{"header": 11, "n": 1, "title": "Ask for in-stock 2026s"},
                                     {"header": 13, "n": 2, "title": "Check the build date"}]}],
    "cards": [
        {"first": 1, "last": 1, "kind": "fromto", "fields": {"number": {"from": 471, "value": 3},
                                                             "caption": "units sold"}},
        {"first": 5, "last": 5, "kind": "source", "fields": {"caption": "Le Guide de l'Auto", "subtitle": ""}},
        {"first": 9, "last": 9, "kind": "stat", "fields": {"number": {"value": 9000, "label": "sold"},
                                                           "caption": "Hornet"}},  # inventado
        {"first": 7, "last": 7, "kind": "stat", "fields": {"number": {"value": 1}, "caption": "x"}},  # no cabeçalho
        {"first": 15, "last": 15, "kind": "price", "fields": {"number": {"value": 12000, "prefix": "C$"},
                                                              "caption": "Honda Prologue"}},
        {"first": 14, "last": 14, "kind": "teleport", "fields": {}},  # tipo que não existe
    ],
}


@pytest.fixture(autouse=True)
def _clean():
    set_language("en")
    set_currency("C$")
    director.reset()
    yield
    director.reset()
    set_currency(None)


def test_validate_keeps_what_the_script_says_and_drops_the_rest():
    plan = director.validate(RAW, _sentences(), "en")
    assert [(s.rank, s.name) for s in plan.sections] == [(2, "Chevrolet Blazer"), (1, "Dodge Hornet")]
    assert [(s.n, s.total, s.title) for s in plan.steps] == [(1, 2, "Ask for in-stock 2026s"),
                                                            (2, 2, "Check the build date")]
    assert [c.kind for c in plan.cards] == ["fromto", "source", "price"]
    reasons = " | ".join(plan.dropped)
    assert "9000" in reasons and "cabeçalho" in reasons and "teleport" in reasons
    assert plan.per_sentence()[4] == "Chevrolet Blazer"


def test_section_name_must_be_in_the_script():
    raw = {**RAW, "sections": [{"header": 3, "last": 6, "rank": 2, "name": "Ford Mustang", "show": True},
                               {"header": 7, "last": 9, "rank": 1, "name": "Dodge Hornet", "show": True}]}
    plan = director.validate(raw, _sentences(), "en")
    assert [s.name for s in plan.sections] == []  # sobrou um item só: não é ranking
    assert any("Ford Mustang" in d for d in plan.dropped)


def test_installed_plan_drives_headers_steps_and_cards():
    from broll_bot.auto_templates import auto_templates
    from broll_bot.story_detect import prime_steps, step_of
    from broll_bot.subject import rank_header, sentence_subjects

    ss = _sentences()
    director.install(director.validate(RAW, ss, "en"))
    assert rank_header("Number two.") == (2, "Chevrolet Blazer", "")
    assert rank_header("Number ten.") is None  # só os cabeçalhos do diretor
    per = sentence_subjects([s.text for s in ss])
    assert per[5] == "Chevrolet Blazer" and per[9] == "Dodge Hornet"
    prime_steps([s.text for s in ss])
    assert step_of("One.", "Ask for in-stock twenty twenty-sixes.") == (1, 2)
    scenes = [Scene(i, s.text, s.start, s.end + 4.0, SceneAnalysis("", [], "")) for i, s in enumerate(ss)]
    scenes, _ = auto_templates(scenes, ss, "", lang="en")
    styles = {sc.text.split(".")[0]: sc.analysis.style for sc in scenes}
    assert styles["Number two"] == "ranking"
    assert any(sc.analysis.style == "fromto" and sc.analysis.number.get("from") == 471 for sc in scenes)
    assert any(sc.analysis.style == "price" and sc.analysis.number.get("value") == 12000 for sc in scenes)
    steps = [sc for sc in scenes if sc.analysis.style == "steps"]
    assert [sc.analysis.caption for sc in steps] == ["Ask for in-stock 2026s", "Check the build date"]


def test_ai_scene_styles_for_data_give_way_to_the_director():
    sc = Scene(0, "x", 0, 5, SceneAnalysis("", [], "", style="stat"))
    deco = Scene(1, "y", 5, 9, SceneAnalysis("", [], "", style="showcase"))
    notes = director.strip_ai_styles([sc, deco])
    assert sc.analysis.style == "normal" and deco.analysis.style == "showcase" and notes


def test_direct_uses_the_ai_and_falls_back_when_it_fails(monkeypatch):
    cfg = AppConfig()
    cfg.analysis.provider = "openai"
    calls = []

    def fake(cfg_, system, prompt):
        calls.append(prompt)
        assert "[3] Number two." in prompt and "percentbar" in system
        return RAW, "gpt-5"

    monkeypatch.setattr(director, "_call", fake)
    plan = director.direct(_sentences(), cfg, None, "en")
    assert plan is not None and plan.model == "gpt-5" and len(plan.sections) == 2 and calls

    def broken(*_a):
        raise RuntimeError("sem chave")

    monkeypatch.setattr(director, "_call", broken)
    assert director.direct(_sentences(), cfg, None, "en") is None  # seguem as regras
    cfg.director.enabled = False
    assert director.direct(_sentences(), cfg, None, "en") is None


def test_without_a_plan_the_rules_still_work():
    from broll_bot.subject import prime_headers, rank_header

    texts = ["Number ten.", "The Subaru W-R-X.", "Number nine.", "The Lexus U-X."]
    prime_headers(texts)
    assert rank_header("Number ten.")[1] == "Subaru WRX"


def test_other_niche_swaps_the_car_checks_for_the_video_niche():
    from broll_bot import visual_checks

    cfg = AppConfig()
    raw = {"niche": "cooking", "automotive": False, "visual_niche": ["a plate of pasta", "a kitchen counter"],
           "visual_off": ["a car on a road", "a city skyline"]}
    plan = director.validate(raw, _sentences(), "en")
    try:
        director.apply_niche(cfg, plan)
        assert cfg.analysis.niche == "cooking"
        texts = visual_checks.check_texts()
        assert "a plate of pasta" in texts and "a car on a road" in texts
        from broll_bot.subject import is_automotive

        assert not is_automotive(cfg.analysis.niche)
    finally:
        visual_checks.set_niche([], [])


def test_car_video_on_a_car_channel_keeps_the_car_checks_and_adds_the_topic():
    from broll_bot import visual_checks

    cfg = AppConfig()
    plan = director.validate({"niche": "winter tires", "automotive": True,
                              "visual_niche": ["a close-up of winter tire tread"]}, _sentences(), "en")
    try:
        director.apply_niche(cfg, plan)
        assert "automot" in cfg.analysis.niche
        assert "a close-up of winter tire tread" in visual_checks.check_texts()
    finally:
        visual_checks.set_niche([], [])


def test_numbers_in_names_and_dates_come_from_nearby_sentences():
    ss = [Sentence(0, "Number three.", 0, 0, 1), Sentence(1, "The Audi Q-four e-tron.", 0, 1, 3),
          Sentence(2, "It's down another sixty-eight percent.", 0, 3, 6),
          Sentence(3, "The incentive drops from five thousand dollars to four thousand on January first.", 0, 20, 25)]
    raw = {"cards": [{"first": 2, "last": 2, "kind": "stat",
                      "fields": {"number": {"value": 68, "prefix": "-", "suffix": "%"}, "caption": "Q4 e-tron"}},
                     {"first": 3, "last": 3, "kind": "fromto",
                      "fields": {"number": {"from": 5000, "value": 4000, "prefix": "C$"}, "subtitle": "January 1"}}]}
    plan = director.validate(raw, ss, "en")
    assert [c.kind for c in plan.cards] == ["stat", "fromto"], plan.dropped


def test_chart_values_have_no_sign():
    ss = [Sentence(0, "Sales fell thirty-four percent last year, and another forty-two percent this year.", 0, 0, 5)]
    raw = {"cards": [{"first": 0, "last": 0, "kind": "chart", "fields": {"caption": "Sales drop", "stats": [
        {"label": "Last year", "value": "-34", "unit": "%"}, {"label": "This year", "value": "-42", "unit": "%"}]}}]}
    plan = director.validate(raw, ss, "en")
    assert [s["value"] for s in plan.cards[0].fields["stats"]] == ["34", "42"]


def test_character_and_requery_prompts_follow_the_video_niche():
    from broll_bot.character import prompt_for
    from broll_bot.config import AnalysisConfig
    from broll_bot.query_lang import _requery_system

    assert "car video" in prompt_for("The belt snapped.", "Ford 1.0 EcoBoost", "automotivo (carros)")
    cooking = prompt_for("Add the garlic now.", "Lasagna", "cooking")
    assert "about cooking" in cooking and "car" not in cooking.lower().replace("cards", "")
    cfg = AnalysisConfig()
    cfg.niche = "personal finance"
    assert "personal finance" in _requery_system(cfg) and "workshop" not in _requery_system(cfg)


def test_unsaid_number_in_a_subtitle_drops_the_subtitle_not_the_card():
    ss = [Sentence(0, "Down forty-one percent in the first half of the year.", 0, 0, 4)]
    raw = {"cards": [{"first": 0, "last": 0, "kind": "stat", "fields": {
        "number": {"value": 41, "prefix": "-", "suffix": "%", "label": "first half of 2026"}, "caption": "Lexus UX",
        "subtitle": "since 2019"}}]}
    plan = director.validate(raw, ss, "en")
    assert len(plan.cards) == 1
    f = plan.cards[0].fields
    assert "label" not in f["number"] and "subtitle" not in f and f["number"]["value"] == 41


def test_price_numbers_leave_the_image_queries():
    from broll_bot.scenes import _clean_queries

    assert _clean_queries(["dealership 12000 off banner", "lexus ux 2019", "peugeot 3008 suv", "37% sales drop"]) == \
        ["dealership off banner", "lexus ux 2019", "peugeot 3008 suv", "sales drop"]


def test_step_titles_are_written_for_the_screen_and_never_cut_in_the_middle():
    texts = ["One.", "Ask for in-stock twenty twenty-sixes.", "Four.",
             "Look up the manufacturer's current Canadian offers yourself, before you walk in.", "And five.",
             "For a discontinued model, ask the dealer, in writing, about parts and service."]
    ss = [Sentence(i, t, 0, i * 5.0, i * 5.0 + 4) for i, t in enumerate(texts)]
    raw = {"lists": [{"word": "", "items": [{"header": 0, "n": 1, "title": "Ask for in-stock twenty twenty-sixes"},
                                            {"header": 2, "n": 2, "title": "Look up the manufacturer's"},
                                            {"header": 4, "n": 3, "title": "Ask about parts and service"}]}]}
    plan = director.validate(raw, ss, "en")
    assert [s.title for s in plan.steps] == ["Ask for in-stock 2026s",
                                             "Look up the manufacturer's current Canadian offers yourself",
                                             "Ask about parts and service"]


def test_dollar_sign_from_the_ai_follows_the_channel_currency():
    ss = [Sentence(0, "The Dodge Charger Daytona.", 0, 0, 2),
          Sentence(1, "Its starting price came down to around fifty thousand three hundred and sixty-five dollars.", 0, 2, 7)]
    raw = {"cards": [{"first": 1, "last": 1, "kind": "price", "fields": {
        "number": {"value": 50365, "prefix": "$"}, "caption": "Dodge Charger Daytona", "subtitle": "about $50,365"}}]}
    plan = director.validate(raw, ss, "en")  # canal em C$ (fixture)
    f = plan.cards[0].fields
    assert f["number"]["prefix"] == "C$" and f["subtitle"] == "about C$50,365"


def test_portuguese_plan_for_a_non_car_video():
    """Roteiro do biofertilizante (pt-BR, agro): números por extenso em português, texto da frase vizinha e
    o nicho do vídeo trocando as checagens de carro."""
    from broll_bot import visual_checks
    from broll_bot.subject import is_automotive

    set_language("pt")
    texts = ["Naquele ano, um químico alemão chamado Justus von Liebig publicou uma ideia.",
             "As plantas não comem terra.", "E mesmo assim, compra lá fora a grande maioria do adubo que usa.",
             "Estimativas do próprio governo falam em cerca de 85 por cento.",
             "O Brasil está entre os maiores importadores de fertilizantes do mundo.",
             "Uma lavoura de milho pode precisar de mais de cem quilos de nitrogênio por hectare."]
    ss = [Sentence(i, t, 0, i * 12.0, i * 12.0 + 8) for i, t in enumerate(texts)]
    raw = {"niche": "agriculture", "automotive": False, "visual_niche": ["a farmer in a crop field"],
           "visual_off": ["a car on a road"],
           "cards": [{"first": 0, "last": 0, "kind": "source", "fields": {"caption": "Justus von Liebig",
                                                                          "body": "As plantas não comem terra"}},
                     {"first": 2, "last": 3, "kind": "percentbar", "fields": {
                         "number": {"value": 85, "suffix": "%"}, "caption": "do adubo usado no Brasil vem de fora"}},
                     {"first": 5, "last": 5, "kind": "stat", "fields": {
                         "number": {"value": 100, "label": "kg de nitrogênio por hectare"}, "caption": "Milho"}}]}
    plan = director.validate(raw, ss, "pt")
    assert [c.kind for c in plan.cards] == ["source", "percentbar", "stat"], plan.dropped
    cfg = AppConfig()
    cfg.analysis.niche = "Variado / Top 10"
    try:
        director.apply_niche(cfg, plan)
        assert cfg.analysis.niche == "agriculture" and not is_automotive(cfg.analysis.niche)
    finally:
        visual_checks.set_niche([], [])
        set_language("en")


def test_step_titles_start_with_a_capital_letter():
    ss = [Sentence(0, "Primeiro, os biológicos explodiram.", 0, 0, 3), Sentence(1, "Bla bla.", 0, 4, 6),
          Sentence(2, "Segundo, tem a rocha.", 0, 20, 23), Sentence(3, "Mais texto.", 0, 24, 26)]
    raw = {"lists": [{"items": [{"header": 0, "n": 1, "title": "os biológicos explodiram"},
                                {"header": 2, "n": 2, "title": "tem a rocha"}]}]}
    assert [s.title for s in director.validate(raw, ss, "pt").steps] == ["Os biológicos explodiram", "Tem a rocha"]


def test_portuguese_spelled_compound_numbers_and_profile_name_said_before():
    """Vídeo da garrafa de Moser (pt-BR): "setecentos milhões", "vinte e oito horas" e o perfil que entra
    algumas frases depois de "O nome dele é Alfredo Moser." eram descartados como "não dito"."""
    set_language("pt")
    texts = ["Estimativas internacionais falam em algo perto de setecentos milhões de pessoas.",  # 0
             "O nome dele é Alfredo Moser.",  # 1
             "Alfredo nasceu em Itajaí, em Santa Catarina, no começo dos anos cinquenta.",  # 2
             "Estudou só até o ensino fundamental.",  # 3
             "Trabalhava como mecânico em Uberaba.",  # 4
             "No Chile, em dois mil e quinze, instalaram postes com bateria que dura até vinte e oito horas.",  # 5
             "Em dois mil e treze, as lâmpadas estavam em cento e quarenta mil casas."]  # 6
    ss = [Sentence(i, t, 0, i * 12.0, i * 12.0 + 8) for i, t in enumerate(texts)]
    raw = {"cards": [
        {"first": 0, "last": 0, "kind": "stat", "fields": {"number": {"value": 700, "label": "milhões"},
                                                            "caption": "pessoas"}},
        {"first": 3, "last": 4, "kind": "profile", "fields": {"caption": "Alfredo Moser",
                                                               "subtitle": "mecânico em Uberaba"}},
        {"first": 5, "last": 5, "kind": "stat", "fields": {"number": {"value": 28, "label": "horas"},
                                                            "caption": "bateria", "subtitle": "Chile, 2015"}},
        {"first": 6, "last": 6, "kind": "stat", "fields": {"number": {"value": 140000}, "caption": "casas",
                                                            "subtitle": "em 2013"}}]}
    try:
        plan = director.validate(raw, ss, "pt")
    finally:
        set_language("en")
    assert [c.kind for c in plan.cards] == ["stat", "profile", "stat", "stat"], plan.dropped
    assert plan.cards[2].fields.get("subtitle") == "Chile, 2015"


def test_profile_name_never_said_still_drops():
    ss = [Sentence(i, t, 0, i * 12.0, i * 12.0 + 8)
          for i, t in enumerate(["Um mecânico de Uberaba.", "Ele subiu no telhado.", "E abriu um buraco."])]
    raw = {"cards": [{"first": 2, "last": 2, "kind": "profile", "fields": {"caption": "Alfredo Moser"}}]}
    plan = director.validate(raw, ss, "pt")
    assert not plan.cards and "Alfredo Moser" in plan.dropped[0]


def test_profile_fact_can_come_from_the_biography_just_told():
    texts = ["O nome dele é Alfredo Moser.", "Alfredo nasceu em Itajaí, em Santa Catarina.",
             "Estudou só até o ensino fundamental.", "Mudou pra Uberaba.", "Virou mecânico.",
             "Consertava de tudo na cidade."]
    ss = [Sentence(i, t, 0, i * 12.0, i * 12.0 + 8) for i, t in enumerate(texts)]
    raw = {"cards": [{"first": 5, "last": 5, "kind": "profile", "fields": {
        "caption": "Alfredo Moser", "subtitle": "Estudou só até o ensino fundamental"}}]}
    plan = director.validate(raw, ss, "pt")
    assert [c.kind for c in plan.cards] == ["profile"], plan.dropped


def test_digits_on_screen_match_numbers_spoken_in_portuguese():
    """Agenda do tijolo de plástico: "A tragédia de mais de 150 mortos" caía porque a fala diz
    "mais de cento e cinquenta pessoas" (o 150 não contava como palavra dita)."""
    texts = ["Uma tragédia numa noite de chuva que matou mais de cento e cinquenta pessoas.",
             "E um tijolo que encaixa igual brinquedo de montar.",
             "Lá dentro, em temperaturas entre cento e cinquenta e duzentos e vinte graus, o plástico derrete."]
    ss = [Sentence(i, t, 0, i * 12.0, i * 12.0 + 8) for i, t in enumerate(texts)]
    raw = {"cards": [{"first": 0, "last": 1, "kind": "agenda", "fields": {"points": [
        {"text": "A tragédia de mais de 150 mortos"}, {"text": "O tijolo de montar"}]}},
        {"first": 2, "last": 2, "kind": "stat", "fields": {"number": {"value": 220, "suffix": "°C"},
                                                            "caption": "plástico derrete"}}]}
    plan = director.validate(raw, ss, "pt")
    assert [c.kind for c in plan.cards] == ["agenda", "stat"], plan.dropped


def test_spoken_fine_with_cents_and_short_plurals():
    """Vídeo do Dale: "two hundred and thirty-seven fifty" (237,50) virava 287, e "B.C. winter tire fines"
    caía porque a fala diz "winter tires" e "fine"."""
    from broll_bot.spoken import screen_text

    assert screen_text("he was fined two hundred and thirty-seven fifty for driving", "en") == \
        "he was fined 237.50 for driving"
    assert screen_text("three hundred and twenty five", "en") == "325"
    ss = [Sentence(0, "A Winnipeg driver was fined two hundred and thirty-seven fifty for snow on the roof.", 0, 0, 5),
          Sentence(1, "No winter tires on B.C.'s designated highways.", 0, 20, 24),
          Sentence(2, "The fine starts at a hundred and twenty-one dollars.", 0, 24, 28)]
    raw = {"cards": [
        {"first": 0, "last": 0, "kind": "stat", "fields": {"number": {"value": 237.5, "prefix": "$"},
                                                            "caption": "Snow on the roof"}},
        {"first": 1, "last": 2, "kind": "stat", "fields": {"number": {"value": 121, "prefix": "$"},
                                                            "caption": "B.C. winter tire fines"}}]}
    plan = director.validate(raw, ss, "en")
    assert [c.kind for c in plan.cards] == ["stat", "stat"], plan.dropped


def _pl_sentences(text):
    from broll_bot.transcription import parse_script

    sents = parse_script(text)
    for k, s in enumerate(sents):
        s.start, s.end = k * 4.0, k * 4.0 + 4.0
    return sents


def test_polish_spelled_numbers_are_not_dropped_from_cards():
    """Vídeo polonês: "trzydzieści sześć litrów" (36) e "dwieście metrów" (200) estavam na fala, mas o card saía
    como "número que não foi dito" (o leitor de números por extenso não conhecia o polonês)."""
    from broll_bot.director import validate
    from broll_bot.spelled import spelled_values

    assert 36 in spelled_values("To jest trzydzieści sześć litrów.", "pl")
    assert 36 in spelled_values("dla trzydziestu sześciu osób", "pl")
    assert 200 in spelled_values("co najmniej dwieście metrów", "pl")
    assert 2022 in spelled_values("w roku dwa tysiące dwadzieścia dwa", "pl")
    assert 2.5 in spelled_values("dwa przecinek pięć litra", "pl")
    assert spelled_values("trzy dni, cztery osoby", "pl") == {3, 4}
    sents = _pl_sentences("Rodzina, cztery osoby. Trzy dni minimum. To jest trzydzieści sześć litrów. "
                          "Generator stoi co najmniej dwieście metrów dalej. I to tylko do picia.")
    plan = validate({"cards": [{"first": 2, "kind": "stat", "fields": {"number": {"value": 36, "unit": "l"},
                                                                        "caption": "litrów"}}]}, sents, "pl")
    assert [c.kind for c in plan.cards] == ["stat"], plan.dropped


def test_numbered_list_the_ai_missed_is_found_in_the_script():
    """O roteiro polonês tinha "Rzecz pierwsza." ... "Rzecz piąta." (as 5 coisas do título) e a IA não marcou a
    lista: o vídeo saiu sem os cabeçalhos. Mesma palavra, 1, 2, 3... em ordem, 3 itens ou mais, vira lista."""
    from broll_bot.director import validate

    text = " ".join(f"Rzecz {o}. Poradnik mówi o {t}, ale nie mówi wszystkiego. Tak jest." for o, t in (
        ("pierwsza", "telefonach"), ("druga", "gotówce"), ("trzecia", "lodówce"), ("czwarta", "czadzie"),
        ("piąta", "wodzie")))
    sents = _pl_sentences("Dzień dobry. " + text + " Druga sprawa to coś innego.")
    plan = validate({}, sents, "pl")
    assert [(s.word, s.n, s.total) for s in plan.steps] == [("Rzecz", k, 5, ) for k in range(1, 6)]
    assert plan.steps[0].title.startswith("Poradnik mówi o telefonach")
    # duas menções soltas não viram lista
    assert validate({}, _pl_sentences("Rzecz pierwsza. Coś tam. Rzecz druga. Coś innego."), "pl").steps == []


def test_missed_list_also_in_portuguese_with_ordinal_first():
    from broll_bot.director import validate

    sents = _pl_sentences("Primeira coisa: o seguro do carro. Texto. Segunda coisa: a revisão anual. Texto. "
                          "Terceira coisa: os pneus de inverno. Texto.")
    plan = validate({}, sents, "pt")
    assert [(s.word, s.n) for s in plan.steps] == [("coisa", 1), ("coisa", 2), ("coisa", 3)]
    assert plan.steps[1].title == "A revisão anual"

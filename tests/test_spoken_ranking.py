"""Roteiro feito para voz de IA (números e siglas por extenso): ranking, nomes, anos e buscas por pedaço.

As frases são as do vídeo do Jhonny Walt (top 10 de confiabilidade) em que o ranking não foi detectado:
sem seções, sem cartão de posição, o assunto virou "rover paid" e o Audi aparecia uma cena depois.
"""

import pytest

from broll_bot.cues import set_language
from broll_bot.models import Scene, SceneAnalysis
from broll_bot.spoken import clean_model_name, spoken_years, word_int

SCRIPT = [
    "So choosing badly is getting more expensive.", "Right.",
    "Number ten.", "Number ten.",
    "The Range Rover Evoque, the current shape, on sale since twenty nineteen.",
    "Reliability rating, seventy-six point four per cent.",
    "Land Rover paid for eighty-one per cent of the repairs.",
    "If you want a small premium S U V, the Lexus U X hybrid scored ninety-eight per cent.",
    "The Audi Q two scored ninety-eight and a half, and every faulty one was back with its owner the same day.",
    "Number nine is a used car, and it is on tens of thousands of British driveways.",
    "The previous Nissan Qashqai, the petrol versions sold from twenty fourteen to twenty twenty-one.",
    "Seven in ten could still be driven, which is something.",
    "Number seven.",
    "The B M W X three, petrol, the generation sold from twenty eighteen to twenty twenty-four.",
    "B M W paid for only sixty-one per cent of repairs.",
    "Number six is the one a lot of you have been asking about.", "The Jaecoo Seven.",
    "This car came from nowhere.",
    "We are halfway. The bottom five are worse, and number one is the car from the start of this video.",
    "Number four, and our only fully electric car.",
    "The Volkswagen I D four, on sale since twenty twenty-one.",
    "Number three.", "The most expensive car on this list.", "The Mercedes G L E, the current model.",
    "And number one.", "The car from the start of this video.", "The Volkswagen Tiguan.",
    "And remember number eight on this list?",
]


@pytest.fixture()
def english():
    set_language("en")
    yield
    set_language("pt")


def test_spoken_numbers_and_years():
    assert word_int("ten") == 10 and word_int("Seven.") == 7 and word_int("twenty-one") == 21
    assert word_int("dix-huit") == 18 and word_int("dez") == 10 and word_int("car") is None
    assert spoken_years("on sale since twenty nineteen") == "on sale since 2019"
    assert spoken_years("sold from twenty fourteen to twenty twenty-one") == "sold from 2014 to 2021"
    assert spoken_years("two thousand and nine, nineteen ninety-eight") == "2009, 1998"
    assert spoken_years("twenty people") == "twenty people"


@pytest.mark.parametrize("spoken,written", [
    ("Mercedes G L E", "Mercedes GLE"), ("Audi Q two", "Audi Q2"), ("Volkswagen I D four", "Volkswagen ID4"),
    ("Jaecoo Seven", "Jaecoo 7"), ("B M W X three", "BMW X3"), ("Lexus U X", "Lexus UX"),
    ("Kia E V six", "Kia EV6"), ("Range Rover Evoque", "Range Rover Evoque"), ("Mazda 6", "Mazda 6"),
])
def test_model_names_written_for_the_voice(spoken, written):
    assert clean_model_name(spoken) == written


def test_rank_headers_with_spoken_numbers_and_name_in_next_sentence(english):
    from broll_bot.subject import is_rank_line, rank_header, section_names, sentence_subjects

    per = sentence_subjects(SCRIPT)
    heads = {t: rank_header(t) for t in SCRIPT if rank_header(t)}
    assert heads["Number ten."] == (10, "Range Rover Evoque", "")
    assert heads["Number nine is a used car, and it is on tens of thousands of British driveways."][1] == "Nissan Qashqai"
    assert heads["Number seven."][1] == "BMW X3"
    assert heads["Number six is the one a lot of you have been asking about."][1] == "Jaecoo 7"
    assert heads["Number four, and our only fully electric car."][1] == "Volkswagen ID4"
    assert heads["Number three."][1] == "Mercedes GLE"  # pula "The most expensive car on this list."
    assert heads["And number one."] == (1, "Volkswagen Tiguan", "")
    # menção no meio da frase e pergunta de volta não são cabeçalho
    assert not any("halfway" in t or "remember" in t for t in heads)
    assert not is_rank_line("And remember number eight on this list?")
    assert is_rank_line("Number ten.") and is_rank_line("Numéro 3 : la Clio")
    assert section_names(per)[:3] == ["Range Rover Evoque", "Nissan Qashqai", "BMW X3"]
    # o Audi e o Lexus citados como alternativa continuam na seção do Evoque
    assert per[SCRIPT.index("Land Rover paid for eighty-one per cent of the repairs.")] == "Range Rover Evoque"
    assert per[SCRIPT.index("Right.")] == ""


def test_written_rank_headers_still_work():
    from broll_bot.subject import rank_header

    set_language("fr")
    try:
        assert rank_header("Numéro 4 : la Mazda 6, en version 2.0 essence Skyactiv.")[:2] == (4, "Mazda 6")
        assert rank_header("Numéro dix : la Dacia Logan MCV.")[:2] == (10, "Dacia Logan MCV")
    finally:
        set_language("pt")
    assert rank_header("Número 3: o Toyota Corolla.")[:2] == (3, "Toyota Corolla")


def test_subject_ignores_lowercase_word_after_brand():
    from broll_bot.subject import detect_subject

    text = " ".join(["Land Rover paid for most repairs. B M W paid for only some."] * 3
                    + ["The Volkswagen Tiguan.", "My Volkswagen Tiguan broke."])
    assert detect_subject(text) == "volkswagen tiguan"


def test_chapters_from_spoken_ranking(english):
    from studio.relatorio import chapters

    scenes = [{"narrated_text": t, "start": i * 5.0, "end": i * 5.0 + 5} for i, t in enumerate(SCRIPT)]
    out = dict((name, at) for at, name in chapters({"scenes": scenes, "language": "en"}))
    assert "#10 Range Rover Evoque" in out and "#3 Mercedes GLE" in out and "#1 Volkswagen Tiguan" in out


def _scene(i, text, queries, subject=""):
    return Scene(i, text, i * 10.0, i * 10.0 + 10, SceneAnalysis("", list(queries), "car", subject=subject))


def test_each_piece_searches_the_car_it_talks_about():
    from broll_bot.plan_fix import focus_queries

    q = ["lexus ux hybrid driving", "audi q2 parked driveway", "nissan qashqai exterior", "auto ac technician car"]
    lexus = _scene(0, SCRIPT[7], q, "Range Rover Evoque")
    audi = _scene(1, SCRIPT[8], q[1:] + q[:1], "Range Rover Evoque")
    nissan = _scene(2, SCRIPT[11], q[2:] + q[:2], "Nissan Qashqai")
    focus_queries([lexus, audi, nissan])
    assert lexus.analysis.queries[0] == "lexus ux hybrid driving"
    assert audi.analysis.queries[0] == "audi q2 parked driveway"  # "Audi Q two" na fala
    assert "lexus ux hybrid driving" not in audi.analysis.queries
    assert nissan.analysis.queries[0] == "nissan qashqai exterior"  # o carro da seção
    assert not any("audi" in x or "lexus" in x for x in nissan.analysis.queries)


def test_header_scene_keeps_the_name_sentence():
    from broll_bot.models import Sentence
    from broll_bot.plan_fix import isolate_rank_headers
    from broll_bot.subject import prime_headers

    set_language("en")
    try:
        texts = ["Right.", "Number ten.", "Number ten.", SCRIPT[4], SCRIPT[5]]
        prime_headers(texts)
        sents = [Sentence(i, t, 0, i * 3.0, i * 3.0 + 3) for i, t in enumerate(texts)]
        scenes = [Scene(0, " ".join(texts), 0, 15, SceneAnalysis("", ["car"], "car", style="ranking"))]
        isolate_rank_headers(scenes, sents)
        head = next(s for s in scenes if s.analysis.style == "ranking")
        assert head.text.startswith("Number ten.") and "Range Rover Evoque" in head.text
        assert "Reliability" not in head.text
    finally:
        set_language("pt")


def test_year_card_only_for_the_section_car():
    from broll_bot.rhythm import add_year_overlays, year_fact

    assert year_fact("The previous Nissan Qashqai, the petrol versions sold from twenty fourteen to twenty "
                     "twenty-one.") == "2014–2021"
    assert year_fact("The Land Rover Discovery Sport, on sale since twenty fourteen.") == "2014"
    kona = _scene(0, "The older Hyundai Kona hybrid, twenty nineteen to twenty twenty-three, scored well.",
                  ["car"], "Nissan Qashqai")
    assert add_year_overlays([kona]) == []
    qashqai = _scene(1, SCRIPT[10], ["car"], "Nissan Qashqai")
    assert add_year_overlays([qashqai]) and qashqai.analysis.overlay["detail"] == "2014–2021"


def test_library_last_resort_never_shows_another_car(tmp_path):
    from tests.test_fallback import _selector

    sel, shots = _selector(tmp_path, ["dacia-logan-mcv-motor", "honda-jazz-bancos", "workshop-tools",
                                      "range-rover-evoque-front"])
    sel.section_of = {0: "Range Rover Evoque"}
    sel.assign(shots[:2], {})
    assert {s.candidate.media_id for s in shots[:2]} == {"range-rover-evoque-front", "workshop-tools"}
    # vídeo sem carro conhecido (introdução): só pastas sem marca
    sel2, shots2 = _selector(tmp_path, ["dacia-logan-mcv-motor", "workshop-tools"])
    sel2.assign(shots2[:1], {})
    assert shots2[0].candidate.media_id == "workshop-tools"


def test_hundred_ranges_are_two_numbers():
    from broll_bot.spoken import screen_text

    assert screen_text("cost between three hundred and five hundred pounds") == "cost between £300 and £500"
    assert screen_text("two hundred and twenty-five models") == "225 models"


# vídeo do Bernard (top 10 a evitar): cabeçalhos com motor/caixa no lugar do modelo, marcas em lista
FR_SCRIPT = [
    "On commence par la numéro 10.", "Et celle-là, elle ne tombe pas en panne.",
    "Numéro 10 : la Renault Zoé… avec batterie en location.",
    "La numéro 9, maintenant.", "Une belle allemande… et une chaîne très mal placée.",
    "Numéro 9 : les BMW diesel équipées du moteur N47.",
    "Numéro 8 : les Citroën et les Peugeot équipées de la boîte pilotée.",
    "Numéro 7 : les Volkswagen, Audi, Seat et Skoda équipées de la boîte DSG à 7 rapports à embrayages à sec.",
    "Numéro 6 : le Range Rover Evoque et le Land Rover Discovery Sport, en diesel 2.0 Ingenium, surtout sur les "
    "premières années, de 2015 à 2019.",
    "Numéro 5 : les Ford équipées du 1.0 EcoBoost.",
    "Numéro 4 : le moteur 1.6 THP, dans ses premières générations, entre 2006 et 2012 environ.",
    "On le trouve chez Peugeot, sur les 207, 308, RCZ et 3008.",
    "Numéro 3.", "Et là, on entre sur le podium, avec des moteurs qui ont fait parler d'eux bien au-delà des ateliers.",
    "Le 1.2 TCe de Renault, produit entre 2012 et 2016.",
    "Numéro 2 : le diesel 1.5 BlueHDi de Peugeot, Citroën, DS et Opel, produit à partir de 2017.",
    "Et si vous roulez beaucoup, regardez les anciens 2.0 HDi, beaucoup plus robustes.", "Et la numéro 1.",
    "Vous l'attendiez : le 1.2 PureTech à courroie humide.",
    "C'est le moteur essence de presque toutes les Peugeot, Citroën et DS, et de nombreuses Opel, depuis 2013.",
]


def test_french_headers_with_engines_gearboxes_and_brand_lists():
    from broll_bot.subject import prime_headers, rank_header

    set_language("fr")
    try:
        prime_headers(FR_SCRIPT)
        names = {}
        for t in FR_SCRIPT:
            h = rank_header(t)
            if h:
                names.setdefault(h[0], h[1])
        assert names == {10: "Renault Zoé", 9: "BMW diesel N47", 8: "Citroën / Peugeot boîte pilotée",
                         7: "Volkswagen / Audi / Seat / Skoda DSG", 6: "Range Rover Evoque",
                         5: "Ford 1.0 EcoBoost", 4: "Peugeot 1.6 THP", 3: "Renault 1.2 TCe",
                         2: "Peugeot 1.5 BlueHDi", 1: "Peugeot 1.2 PureTech"}
    finally:
        set_language("pt")


def test_engine_and_gearbox_subjects_search_the_part_and_accept_any_model():
    from broll_bot.subject import is_component, model_word, search_name, wrong_model

    assert is_component("Volkswagen / Audi / Seat / Skoda DSG") and is_component("Peugeot 1.2 PureTech")
    assert not is_component("Renault Zoé")
    assert search_name("Volkswagen / Audi / Seat / Skoda DSG") == "volkswagen dsg gearbox"
    assert search_name("Peugeot 1.2 PureTech") == "peugeot 1.2 puretech engine"
    assert model_word("Peugeot 1.2 PureTech") == "puretech" and model_word("Mazda 6") == "6"
    # o motor N47 está em vários modelos: um 320d não é "outro carro"
    assert not wrong_model("bmw 320d touring", "BMW diesel N47")
    assert wrong_model("dacia duster", "Dacia Logan MCV")


# roteiro da BYD (Jhonny Walt): números grandes, siglas soletradas e nomes de modelo por extenso
@pytest.mark.parametrize("spoken,written", [
    ("up to one hundred and twenty-five thousand miles", "up to 125,000 miles"),
    ("around ten and a half thousand US dollars less", "around $10,500 less"),
    ("up to ninety-three thousand, seven hundred and fifty miles", "up to 93,750 miles"),
    ("A B Y D Atto Three, a Nio E T Five, and a Tesla Model Y.", "A BYD Atto 3, a Nio ET5, and a Tesla Model Y."),
    ("Its Seal U D M i, a plug-in hybrid S U V", "Its Seal U DM-i, a plug-in hybrid SUV"),
    ("tells you B Y Ds are bulletproof, one of the best S U Vs", "tells you BYDs are bulletproof, one of the best SUVs"),
    ("the M G S five electric S U V scored ninety-eight point three per cent", "the MG S5 electric SUV scored 98.3%"),
    ("a Volkswagen I D three built in Europe", "a Volkswagen ID3 built in Europe"),
    ("five stars by Euro N CAP", "five stars by Euro NCAP"),
    ("The B M W X three and the Audi Q two", "The BMW X3 and the Audi Q2"),
    ("the Tesla Model Three", "the Tesla Model 3"), ("A B S fault light", "ABS fault light"),
    ("I want to be careful", "I want to be careful"), ("Number ten.", "Number ten."),
    # "two thousand ..." com dinheiro/quantidade é valor, não o ano 2000/2004
    ("the repair cost more than two thousand pounds", "the repair cost more than £2,000"),
    ("a bill of two thousand four hundred pounds", "a bill of £2,400"), ("two thousand cars", "2,000 cars"),
    ("since two thousand and nine", "since 2009"),
])
def test_screen_text_for_brand_videos(spoken, written):
    from broll_bot.spoken import screen_text

    assert screen_text(spoken) == written


def test_video_subject_is_the_spelled_brand_not_the_rival():
    from broll_bot.subject import detect_subject, other_brands

    text = (" ".join(["B Y D started out making batteries.", "The B Y D drew attention."] * 8)
            + " U B S took apart a B Y D Seal. A B Y D Seal again. A Tesla Model Three. A Tesla Model Y. Seal.")
    assert detect_subject(text) == "byd seal"
    # a foto da BYD numa cena que soletra a marca não é "outra marca"
    assert not other_brands("byd seal white", "A B Y D Atto Three, a Nio E T Five.")


def test_good_news_is_not_a_news_template():
    from broll_bot.auto_templates import detect

    set_language("en")
    try:
        assert not [d for d in detect(["Fewer parts is genuinely good news in one way."], "") if d.kind == "news"]
        assert [d for d in detect(["Toyota announced a recall of the bZ4X."], "") if d.kind == "news"]
    finally:
        set_language("pt")


def test_engine_ranking_with_spoken_names():
    """"The Wet Belt Problem": o nome do motor vem na frase seguinte, lido pela voz ("one point five", "one-litre",
    "Ford's"); "Number seven." + "Number seven is the grey area" é um cabeçalho só."""
    from broll_bot.cues import set_language
    from broll_bot.subject import prime_headers, rank_header

    set_language("en")
    texts = ["Right.", "Number seven.", "Number seven is the grey area, and it is the one most of you will want.",
             "The newer version of Ford's one-litre EcoBoost.", "Body.",
             "Number six.", "Ford's one point five and one point six litre EcoBoost petrol engines, fitted to the Kuga.",
             "Number five might surprise you.", "A Honda.", "Honda's one-litre VTEC Turbo, fitted to the Civic.",
             "Number four.", "The one-litre PureTech, fitted to little cars like the Peugeot one-oh-eight.",
             "Number three, and this is the one that hits people's livelihoods.", "Ford's two-litre EcoBlue diesel.",
             "And number one.", "The one point two litre PureTech petrol, from Stellantis.", "Why is this number one?",
             "Look at this list.", "The Peugeot two-oh-eight."]
    prime_headers(texts)
    heads = {t: rank_header(t) for t in texts if rank_header(t)}
    assert [(h[0], h[1]) for h in heads.values()] == [
        (7, "Ford 1.0 EcoBoost"), (6, "Ford 1.5/1.6 EcoBoost"), (5, "Honda 1.0 VTEC Turbo"), (4, "Peugeot 1.0 PureTech"),
        (3, "Ford 2.0 EcoBlue"), (1, "Peugeot 1.2 PureTech")]


def test_screen_codes_and_money_not_years():
    from broll_bot.spoken import screen_text

    assert screen_text("The Peugeot two-oh-eight, two-double-oh-eight and five-double-oh-eight.") == \
        "The Peugeot 208, 2008 and 5008."
    assert screen_text("invoices as high as two thousand.") == "invoices as high as 2,000."
    assert screen_text("twenty-five people") == "25 people"


def test_receipt_names_the_brand_in_the_sentence_and_no_fake_warning():
    from broll_bot.cues import set_language, set_currency
    from broll_bot.magnet_detect import receipt, warning

    set_language("en"), set_currency("£")
    d = receipt(["Parkers says it has heard of costs of at least eight hundred pounds on the Fords."],
                "Peugeot 1.2 PureTech")
    assert d and d.fields["caption"] == "Ford"
    assert warning(["This engine powers the Transit, two of the most common vans in Britain."], "Ford") is None


def test_one_second_rank_header_still_gets_the_card():
    """"Number six." sozinho (1 s) virava cena curta demais para o template: agora entra e avança sobre a cena
    seguinte para dar tempo de ler (vídeo do wet belt: só 2 das 7 posições tinham o cartão)."""
    from broll_bot.config import load_config
    from broll_bot.models import Scene, SceneAnalysis
    from broll_bot.motion.planner import extend_short_templates, select_template_scenes

    from pathlib import Path

    cfg = load_config(Path(__file__).resolve().parent.parent / "config.yaml", env_file=None).templates
    scenes = [Scene(0, "Body.", 0.0, 330.0, SceneAnalysis("", [], "")),
              Scene(1, "Number six.", 330.0, 331.1, SceneAnalysis("", [], "", style="newsrank", caption="Ford 1.5/1.6 EcoBoost", rank=6)),
              Scene(2, "Ford's one point five ... engines.", 331.1, 338.0, SceneAnalysis("", [], ""))]
    chosen = select_template_scenes(scenes, cfg, 1200.0)
    assert chosen == {1: "newsrank"}
    extend_short_templates(scenes, chosen, cfg)
    assert scenes[1].duration >= 2.0 and scenes[2].duration >= 2.5


def test_rank_header_right_before_another_template():
    """"Number two." (1,2 s) colado num checklist de 26 s: o checklist era escolhido antes e o cartão do
    ranking sumia (vídeo do plug-in no inverno). Agora os dois entram e o checklist começa um pouco depois."""
    from pathlib import Path

    from broll_bot.config import load_config
    from broll_bot.models import Scene, SceneAnalysis
    from broll_bot.motion.planner import extend_short_templates, select_template_scenes

    cfg = load_config(Path(__file__).resolve().parent.parent / "config.yaml", env_file=None).templates
    scenes = [Scene(0, "Body.", 0.0, 555.06, SceneAnalysis("", [], "")),
              Scene(1, "Number two.", 555.06, 556.24, SceneAnalysis("", [], "", style="newsrank",
                                                                    caption="Two cars to maintain", rank=2)),
              Scene(2, "You're maintaining two cars...", 556.24, 582.24,
                    SceneAnalysis("", [], "", style="checklist", caption="Two cars")),
              Scene(3, "Body.", 582.24, 600.0, SceneAnalysis("", [], ""))]
    chosen = select_template_scenes(scenes, cfg, 1200.0)
    assert chosen == {1: "newsrank", 2: "checklist"}
    extend_short_templates(scenes, chosen, cfg)
    assert scenes[1].duration >= 2.0 and scenes[2].start == scenes[1].end and scenes[2].duration >= 9

def test_tire_ranking_canadian_script():
    """Vídeo de pneus (canal canadense): os 9 pneus viram seções, siglas com hífen e números por extenso."""
    from broll_bot.cues import set_language
    from broll_bot.subject import detect_subject, is_component, prime_headers, rank_header, sentence_subjects

    set_language("en")
    texts = ["Intro.", "Number nine.", "MotoMaster winter tires.", "Canadian Tire's own brand.", "Body.",
             "Number seven.", "Toyo Observe G-S-I six.", "Body.", "Number two.", "Bridgestone Blizzak W-S ninety.",
             "Body.", "And number one.", "The one from a country with a longer winter than ours.",
             "The Nokian Hakkapeliitta R-five.", "From Finland."]
    prime_headers(texts)
    names = [rank_header(t)[1] for t in texts if rank_header(t)]
    assert names == ["MotoMaster", "Toyo Observe GSI6", "Bridgestone Blizzak WS90", "Nokian Hakkapeliitta R5"]
    per = sentence_subjects(texts)
    assert per[-1] == "Nokian Hakkapeliitta R5" and is_component("Nokian Hakkapeliitta R5")  # pneu: não é modelo
    # "run Blizzaks smart. If you drive" não é a marca Smart
    assert detect_subject("So here's how you run Blizzaks smart. If you drive a lot. It's a smart buy.") == ""


def test_screen_text_canadian_winter():
    from broll_bot.spoken import screen_text

    assert screen_text("Not at minus thirty. At plus seven.") == "Not at -30. At plus seven."
    assert screen_text("Seven degrees Celsius.") == "7°C."
    assert screen_text("the score went from ten minus two") == "the score went from ten minus two"
    assert screen_text("M plus S is not a traction test.") == "M+S is not a traction test."
    assert screen_text("Picture a Honda C-R-V, the A-B-S chatters, a loaded RAV-four.") == \
        "Picture a Honda CR-V, the ABS chatters, a loaded RAV4."
    assert screen_text("costs a couple of hundred dollars more") == "costs a couple of hundred dollars more"


def test_price_difference_is_not_a_repair_bill():
    from broll_bot.cues import set_currency, set_language
    from broll_bot.magnet_detect import receipt
    from broll_bot.spoken import screen_text

    set_language("en"), set_currency("C$")
    try:
        assert receipt([screen_text("Say a set of Hakkas costs you two or three hundred dollars more than a "
                                    "middle-of-the-pack set.")], "Nokian Hakkapeliitta R5") is None
        d = receipt([screen_text("The repair cost eighteen hundred dollars at the dealer.")], "Ford Escape")
        assert d and d.fields["number"]["prefix"] == "C$"
    finally:
        set_currency(None)


def test_reason_ranking_and_single_mention_subject():
    """Ranking de motivos (plug-in no inverno): a frase curta depois do número é o título; frase de suspense
    pula; frase longa usa a oração principal. Carro citado uma vez (recall) não vira o assunto do vídeo."""
    from broll_bot.cues import set_language
    from broll_bot.subject import detect_subject, prime_headers, rank_header, sentence_subjects

    set_language("en")
    texts = ["Intro.", "Number seven.", "The range on the sticker is a summer number.", "Body.",
             "Number four.", "And this one surprises people.",
             "On a long winter highway drive, a plug-in can burn about as much gas as a regular hybrid.",
             "Sometimes more.", "Body.", "Number three.", "The money.", "Body.",
             "And number one.", "The one that surprised me most.", "Not the battery.", "The gas engine.", "Body."]
    prime_headers(texts)
    assert [rank_header(t)[1] for t in texts if rank_header(t)] == [
        "The range on the sticker is a summer number", "A plug-in can burn about as much gas as a regular hybrid",
        "The money", "The gas engine"]
    assert sentence_subjects(texts) == [""] * len(texts)  # motivos não são carros: sem seções
    assert detect_subject("In late 2024, Stellantis recalled plug-in hybrid Jeep Wranglers and Grand Cherokees.") == ""
    assert detect_subject("The Toyota Corolla is great. The Toyota Corolla lasts. Buy a Toyota Corolla.") == "toyota corolla"


def test_disclaimer_is_not_news():
    from broll_bot.auto_templates import detect
    from broll_bot.cues import set_language

    set_language("en")
    assert detect(["This video is based on publicly available range studies and recall notices as understood at "
                   "the time of recording."], "") == []
    assert [d.kind for d in detect(["In late twenty twenty-four, Stellantis recalled plug-in hybrid Jeep Wranglers "
                                    "over a battery fire risk."], "")] == ["news"]


def test_mid_video_teaser_is_not_a_header():
    from broll_bot.cues import set_language
    from broll_bot.subject import prime_headers, rank_header

    set_language("en")
    texts = ["Number two.", "Bridgestone Blizzak W-S ninety.", "Body text here.",
             "And number one, by the way, comes from a country with a longer, harder winter than ours.",
             "All right.", "Let's go.", "Number three.", "Michelin X-Ice Snow.", "Body."]
    prime_headers(texts)
    assert rank_header(texts[3]) is None
    assert rank_header(texts[6])[1] == "Michelin X-Ice Snow"


def test_diesel_forecourts_screen_text():
    """Vídeo do diesel (Graham Mercer): libras com centavos, faixa de valores, bilhões, norma e preço com hífen."""
    from broll_bot.cues import set_currency, set_language
    from broll_bot.spoken import screen_text

    set_language("en"), set_currency("£")
    assert screen_text("That's about thirty-one pounds seventy more than the same tank.") == \
        "That's about £31.70 more than the same tank."
    assert screen_text("about one pound forty less") == "about £1.40 less"
    assert screen_text("A fill now costs one hundred and ten pounds and one penny.") == "A fill now costs £110.01."
    assert screen_text("runs from around eight hundred pounds to two thousand or more, depending on the engine.") == \
        "runs from around £800 to £2,000 or more, depending on the engine."
    assert screen_text("a deal worth around two and a half billion pounds.") == "a deal worth around £2.5 billion."
    assert screen_text("Remember that hundred-and-ten-pound tank?") == "Remember that £110 tank?"
    assert screen_text("a standard called B S E N five-nine-zero.") == "a standard called BS EN 590."
    assert screen_text("Nearly eleven pence cheaper.") == "Nearly 11 pence cheaper."
    assert screen_text("I paid five pounds twenty times") == "I paid £5 20 times"
    assert screen_text("a loaded RAV-four and an R-five") == "a loaded RAV4 and an R5"


def test_diesel_forecourts_detection():
    from broll_bot.cues import set_currency, set_language
    from broll_bot.models import SceneAnalysis
    from broll_bot.magnet_detect import receipt
    from broll_bot.models import Sentence
    from broll_bot.plan_fix import closing_headers
    from broll_bot.story_detect import source
    from broll_bot.subject import prime_headers

    set_language("en"), set_currency("£")
    d = receipt(["A typical fifty-five litre fill now costs one hundred and ten pounds and one penny."], "")
    assert d.fields["caption"] == "Fuel" and d.fields["points"] == [{"text": "55 litre"}]
    assert set(d.fields) <= set(SceneAnalysis.__dataclass_fields__)  # tudo vira campo da cena (senão o vídeo cai)
    d = receipt(["A typical diesel tank costs one hundred and ten pounds."], "")
    assert d.fields["caption"] == "Diesel"
    d = receipt(["And a set of diesel injectors, fitted at a garage, runs from around eight hundred pounds to two "
                 "thousand or more, depending on the engine."], "")
    assert d.fields["caption"] != "Fuel"  # injetor na oficina continua sendo a nota da oficina
    assert d.fields["caption"] == "injectors" and d.fields["number"] == {"value": 2000.0, "prefix": "£", "low": 800.0}
    d = receipt(["If the D P F gives up, a replacement can easily run from around twelve hundred to two thousand pounds."], "")
    assert d.fields["caption"] == "DPF"
    assert source(["That beats anything I've said here."], "") is None
    texts = ["Body.", "Right.", "Number seven.", "Number seven.", "Morrisons.", "Body.", "Number three.", "Esso.", "Esso clears the bar.",
             "Number three.", "Number two.", "Shell.", "Body.", "Number two.", "And number one.", "Costco."]
    prime_headers(texts)
    sents = [Sentence(i, t, 0) for i, t in enumerate(texts)]
    assert closing_headers(sents) == {2, 9, 13}


def test_every_rank_card_survives_closers_and_recap():
    """Vídeo do diesel: faltaram o 7 e o 2 da recapitulação (dois cabeçalhos na mesma cena) e o 2/1 depois de
    "Esso clears the bar. Number three." (o cabeçalho de fechamento segurava o cartão seguinte)."""
    from broll_bot.auto_templates import auto_templates
    from broll_bot.cues import set_language
    from broll_bot.models import Scene, SceneAnalysis, Sentence
    from broll_bot.plan_fix import drop_closing_rank_cards, isolate_rank_headers
    from broll_bot.subject import prime_headers

    set_language("en")
    texts = ["Intro about diesel prices.", "Number three.", "Esso.", "Esso sells a good diesel with a real test.",
             "Esso clears the bar.", "Number three.", "Number two.", "Shell, and specifically V-Power Diesel.",
             "Shell makes a strong claim about cleaning.", "For everybody else, it's a tool, not a habit.",
             "Number two.", "And number one.", "Costco.", "Costco diesel is the cheapest in the country.",
             "So, three forecourts.", "Number three.", "Esso.", "The only tested claim.", "Number two.",
             "Shell V-Power Diesel.", "The strongest claim.", "And number one.", "Costco.", "Sold as premium."]
    prime_headers(texts)
    sents, t = [], 0.0
    for i, x in enumerate(texts):
        d = max(1.0, len(x.split()) / 2.6)
        sents.append(Sentence(i, x, 0, t, t + d))
        t += d + 0.1
    for size in (2, 3, 4, 6):  # a IA agrupa as frases de vários jeitos
        groups = [sents[k:k + size] for k in range(0, len(sents), size)]
        scenes = [Scene(i, " ".join(s.text for s in g), g[0].start, g[-1].end + 0.1, SceneAnalysis("", [], ""))
                  for i, g in enumerate(groups)]
        isolate_rank_headers(scenes, sents)
        scenes, _ = auto_templates(scenes, sents, "", lang="en")
        drop_closing_rank_cards(scenes, sents)
        cards = [(s.analysis.rank, s.analysis.caption) for s in scenes if s.analysis.style == "ranking"]
        assert cards == [(3, "Esso"), (2, "Shell V-Power Diesel"), (1, "Costco")] * 2, (size, cards)


def test_french_ordinal_reasons_steps_and_general_subject():
    """Vídeo do Bernard (7 raisons): "Première raison : le prix." vira o cartão 1; "Et la septième raison, c'est ..."
    pega o motivo da frase seguinte; "Un : le moteur exact" são passos; a Corolla da abertura não é o assunto;
    "sans abonnement" não é chamada para se inscrever."""
    from broll_bot.cues import set_language
    from broll_bot.motion.i18n import has_cta
    from broll_bot.story_detect import prime_steps, steps
    from broll_bot.subject import detect_subject, prime_headers, rank_header

    set_language("fr")
    texts = ["Intro.", "Première raison : le prix.", "Corps.", "Deuxième raison : la location.", "C'est la deuxième "
             "raison.", "Corps.", "Et la septième raison, c'est peut-être la plus intéressante.",
             "Les Français ont arrêté de vouloir impressionner les voisins.", "Corps."]
    prime_headers(texts)
    assert [rank_header(t) for t in texts if rank_header(t)] == [
        (1, "Le prix", ""), (2, "La location", ""), (7, "Les Français ont arrêté de vouloir impressionner les voisins", "")]
    assert rank_header("Segunda razão: o aluguel.") == (2, "O aluguel", "")
    tips = ["Un : le moteur exact, pas seulement le modèle.", "Corps.", "Deux : les factures.", "Corps.",
            "Trois : le dessous de la voiture."]
    prime_steps(tips)
    d = steps([tips[2], "Corps."], "")
    assert d.fields["number"] == {"value": 2, "total": 3} and d.fields["caption"] == "Les factures"
    script = ("Un monsieur range ses courses dans une vieille Toyota Corolla. C'est le monsieur à la Corolla. "
              "Des exemples ? La Toyota Yaris hybride. La Honda Jazz. La Mazda 3 en 2.0 essence. La Suzuki Swift. "
              "Le Berlingo. La Toyota Corolla ou l'Auris. La Dacia Logan en essence. Le Suzuki Jimny ou le Toyota Hilux.")
    assert detect_subject(script) == ""
    assert detect_subject("Toyota Corolla. " * 5 + "Une Honda Jazz. Une Mazda 3.") == "toyota corolla"
    assert not has_cta("Votre garagiste sait la réparer sans valise, sans abonnement.")
    assert has_cta("Abonnez-vous à la chaîne.") and has_cta("hit subscribe") and not has_cta("Et il y a pire : les abonnements.")


def test_ordinal_rank_six_and_seven_pass_coherence():
    """Vídeo do Bernard: os cartões 6 e 7 ("Sixième raison", "septième raison") sumiam na checagem de que a
    posição é falada (só conhecia os ordinais até o cinco)."""
    from broll_bot.coherence import enforce_coherence
    from broll_bot.models import Scene, SceneAnalysis

    scenes = [Scene(0, "Sixième raison : la décote.", 0, 3, SceneAnalysis("", [], "", style="ranking", rank=6)),
              Scene(1, "Corps.", 3, 6, SceneAnalysis("", [], "")),
              Scene(2, "Et la septième raison, c'est peut-être la plus intéressante.", 6, 9,
                    SceneAnalysis("", [], "", style="ranking", rank=7)),
              Scene(3, "Corps.", 9, 12, SceneAnalysis("", [], "")),
              Scene(4, "The seventh reason: resale.", 12, 15, SceneAnalysis("", [], "", style="ranking", rank=7))]
    enforce_coherence(scenes)
    assert [s.analysis.style for s in scenes] == ["ranking", "normal", "ranking", "normal", "ranking"]


def test_avoid_and_buy_lists_canadian_suvs():
    """Vídeo dos SUVs (Dale): "Avoid number four." / "Buy number nine." com o veredito no subtítulo; os quatro
    inimigos do começo ("Number one. Salt.") não viram seção de carro; a metáfora "Land Cruiser hiding in a
    business suit" perde para "The Lexus G-X four-sixty, twenty ten to ..."; nomes falados da Mazda/Lexus/Toyota."""
    from broll_bot.cues import set_currency, set_language
    from broll_bot.spoken import screen_text
    from broll_bot.subject import detect_subject, prime_headers, rank_header, sentence_subjects

    set_language("en"), set_currency("C$")
    texts = ["Intro.", "Number one.", "Salt.", "Body.", "Number two.", "Cold starts.", "Body.",
             "Avoid number two.", "The Hyundai Santa Fe Sport and the Kia Sorento with the two-point-four.", "Body.",
             "Avoid number one.", "And this one gets me emails.", "The Nissan Rogue, twenty fourteen to twenty eighteen.",
             "Body.", "Buy number three.", "The Lexus R-X three-fifty, twenty ten to twenty fifteen.", "Body.",
             "Buy number two.", "The Toyota four-Runner, twenty ten to twenty twenty-four.", "Body.",
             "Buy number one.", "The one that surprised me most.", "The Land Cruiser hiding in a business suit.",
             "The Lexus G-X four-sixty, twenty ten to twenty twenty-three.", "Body."]
    prime_headers(texts)
    heads = [rank_header(t) for t in texts if rank_header(t)]
    assert heads == [(1, "Salt", ""), (2, "Cold starts", ""), (2, "Hyundai Santa Fe Sport", "Avoid"),
                     (1, "Nissan Rogue", "Avoid"), (3, "Lexus RX 350", "Buy"), (2, "Toyota 4Runner", "Buy"),
                     (1, "Lexus GX 460", "Buy")]
    per = sentence_subjects(texts)
    assert per[3] == "" and per[9] == "Hyundai Santa Fe Sport" and per[-1] == "Lexus GX 460"
    assert screen_text("The Mazda C-X-three and the older C-R-V two-point-four.") == "The Mazda CX-3 and the older CR-V 2.4."
    assert screen_text("the three-point-five V-six, the two-thousand-seven to two-thousand-nine R-X") == \
        "the 3.5 V6, the 2007 to 2009 RX"
    assert screen_text("Often for several thousand dollars.") == "Often for several thousand dollars."
    assert screen_text("somewhere between one and three hundred dollars.") == "somewhere between C$100 and C$300."
    many = ("The Land Cruiser hiding in a suit. The Lexus G-X. The Toyota Land Cruiser Prado. A Land Cruiser drivetrain. "
            "The Chevrolet Equinox. The Ford Escape. The Hyundai Santa Fe. The Nissan Rogue. The Mazda CX-3. "
            "The Honda CR-V. The Toyota RAV4. The Subaru Forester. The Toyota Highlander. The Lexus RX. The Kia Sorento.")
    assert detect_subject(many) == ""


def test_rustproofing_ranking_steps_and_money():
    """Vídeo da proteção contra ferrugem (Dale): suspense entre o número e o nome, motivo com vírgulas, dois
    trechos "One. Two." (só o de seis vira passos), preço falado "seven ninety-five", recibo falso e fonte."""
    from broll_bot.cues import set_currency, set_language
    from broll_bot.magnet_detect import receipt
    from broll_bot.spoken import screen_text
    from broll_bot.story_detect import prime_steps, source, step_of
    from broll_bot.subject import prime_headers, rank_header

    set_language("en"), set_currency("C$")
    texts = ["Intro.", "Number four.", "Rubberized, or hard, undercoating.", "Body.", "Number three.",
             "And this is the one that's going to get me emails.", "Doing nothing at all.", "Body."]
    prime_headers(texts)
    assert [rank_header(t) for t in texts if rank_header(t)] == [(4, "Rubberized, or hard, undercoating", ""),
                                                                  (3, "Doing nothing at all", "")]
    tips = ["Two things the dealer might say.", "One.", '"Drilling holes will void your warranty."', "Two.",
            '"It drips."', "Body.", "One.", "Timing.", "Two.", "Start early.", "Three.", "Wash it in winter."]
    prime_steps(tips)
    assert step_of("One.", '"Drilling holes will void your warranty."') is None
    assert step_of("One.", "Timing.") == (1, 3) and step_of("Three.", "Wash it in winter.") == (3, 3)
    assert screen_text("Sold to you for up to seven ninety-five.") == "Sold to you for up to 795."
    assert screen_text("American drivers spend about three billion dollars a year.") == \
        "American drivers spend about US$3 billion a year."
    assert receipt(["The dealer's cost for that treatment is usually around a hundred and fifty dollars."], "") is None
    assert receipt(["Say you pay around a hundred and fifty dollars a year."], "") is None
    assert source(["In the States, AAA found that these liquid de-icers creep into seams."], "").fields["caption"] == "AAA"
    assert source(["Here's what the APA says about it."], "") is None


def test_short_last_step_still_gets_the_card():
    """Vídeo da ferrugem: "And six. Never buy rust protection in the finance office." (3,4 s) ficava sem o
    quadro do passo (menos que o mínimo do estilo); agora entra e avança sobre a cena seguinte."""
    from pathlib import Path

    from broll_bot.config import load_config
    from broll_bot.models import Scene, SceneAnalysis
    from broll_bot.motion.planner import extend_short_templates, select_template_scenes

    cfg = load_config(Path(__file__).resolve().parent.parent / "config.yaml", env_file=None).templates
    scenes = [Scene(0, "Five. Keep the receipts.", 914.7, 924.3, SceneAnalysis("", [], "", style="steps",
                                                                                 caption="Keep the receipts")),
              Scene(1, "And six. Never buy rust protection in the finance office.", 924.3, 927.7,
                    SceneAnalysis("", [], "", style="steps", caption="Never buy rust protection in the finance office")),
              Scene(2, "Body text about the bottom line.", 927.7, 938.3, SceneAnalysis("", [], ""))]
    chosen = select_template_scenes(scenes, cfg, 1013.0)
    assert chosen == {0: "steps", 1: "steps"}
    extend_short_templates(scenes, chosen, cfg)
    assert scenes[1].duration >= 3.5 and scenes[2].duration >= 2.5


def test_most_stolen_vehicles_canada():
    """Vídeo dos carros mais roubados (Dale): F-150, Ram 1500, RXs; taxa "0.37%" não vira 37%; "a quarter of one
    percent" não é 1%; queda de 19% não é barra de parte do todo; fonte "And Équité" -> Équité."""
    from broll_bot.cues import set_currency, set_language
    from broll_bot.magnet_detect import percentbar
    from broll_bot.spoken import screen_text
    from broll_bot.story_detect import source
    from broll_bot.subject import lookahead_name

    set_language("en"), set_currency("C$")
    assert lookahead_name("The Ford F-one-fifty.") == "Ford F-150"
    assert lookahead_name("The Ram fifteen hundred.") == "Ram 1500"
    assert screen_text("About fifteen hundred stolen, of all insured R-Xs.") == "About 1,500 stolen, of all insured RXs."
    assert percentbar(["The rate is about zero point three seven percent, roughly one in 270."], "") is None
    assert percentbar(["The rate is about a quarter of one percent."], "") is None
    assert percentbar(["Nationally, thefts dropped about nineteen percent in twenty twenty-four."], "") is None
    assert percentbar(["About one point one percent of all insured R-Xs were stolen."], "").fields["number"]["value"] == 1.1
    assert source(["And Équité says the business is shifting."], "").fields["caption"] == "Équité"


def test_new_vehicle_recalls_canada():
    """Vídeo dos recalls (Dale): ano do modelo antes do nome ("The twenty twenty-six Ford Ranger"), C-HR, F-150s,
    10.1-inch; a palavra "recall" solta não vira notícia (só "Ford recalled..."); advérbio não é fonte."""
    from broll_bot.auto_templates import news
    from broll_bot.cues import set_currency, set_language
    from broll_bot.spoken import screen_text
    from broll_bot.story_detect import source, unverified
    from broll_bot.subject import lookahead_name

    set_language("en"), set_currency("C$")
    assert lookahead_name("The twenty twenty-five and twenty twenty-six Toyota Camry.") == "Toyota Camry"
    assert lookahead_name("The twenty twenty-six Hyundai Tucson, including the hybrid.") == "Hyundai Tucson"
    assert lookahead_name("The new twenty twenty-six Toyota C-H-R, the electric version.") == "Toyota C-HR"
    assert screen_text("On about fifty-seven thousand F-one-fifties, with the ten-point-one-inch display.") == \
        "On about 57,000 F-150s, with the 10.1-inch display."
    assert news(["A recall is not proof that a vehicle is junk."], "") is None
    assert news(["In one campaign, Ford recalled more than six hundred thousand vehicles."], "") is not None
    assert source(["Honestly, a company that finds and fixes problems is doing its job."], "") is None
    assert unverified(["Nobody knows you're coming over."], "") is None


def test_winter_vs_all_weather_rounds():
    """Vídeo dos pneus (Dale): "Round one. July." ... "Round eight. Ice." viram o quadro "ROUND 1 OF 8"; também
    "Layer one." (carros roubados); fonte "Globe and Mail" (não "20"); armazenagem não é conta de oficina;
    "two to five percent" é faixa (sem barra) e vira "2 to 5%" na tela."""
    from broll_bot.cues import set_currency, set_language
    from broll_bot.magnet_detect import percentbar, receipt
    from broll_bot.spoken import screen_text
    from broll_bot.story_detect import prime_steps, source, steps

    set_language("en"), set_currency("C$")
    texts = ["Let's start.", "Round one.", "July.", "Body.", "Round two.", "Tread life.", "Body.", "Round three.",
             "Money and time.", "Body.", "Round eight.", "Ice.", "Body."]
    prime_steps(texts)
    d = steps(["Round one.", "July."], "")
    assert d.fields == {"number": {"value": 1, "total": 3}, "caption": "July", "subtitle": "ROUND"}
    assert steps(["Round eight.", "Ice."], "") is None  # 8 não está na sequência 1, 2, 3 deste exemplo
    prime_steps(["Layer one.", "Block the port.", "Layer two.", "A steering wheel lock.", "Layer three.", "A tracker."])
    assert steps(["Layer two.", "A steering wheel lock."], "").fields["subtitle"] == "LAYER"
    prime_steps(["One.", "Get the quote.", "Two.", "Find the site.", "Three.", "Ask the dealer."])
    assert steps(["One.", "Get the quote."], "").fields["subtitle"] == ""  # "STEP 1 OF 3" como antes
    assert source(["Independent testing reported by the Globe and Mail found all-weathers stopping about twenty "
                   "percent shorter."], "").fields["caption"] == "Globe and Mail"
    assert receipt(["Storage can run anywhere from about fifty to two hundred and fifty dollars a season."], "") is None
    assert percentbar(["Insurers offer a discount, usually somewhere around two to five percent."], "") is None
    assert screen_text("usually somewhere around two to five percent.") == "usually somewhere around 2 to 5%."


def test_numbered_step_beats_the_ai_template():
    """Vídeo dos pneus: a cena do "Round eight. Ice." veio do plano da IA com outro template e o 8 de 8 sumiu.
    O passo da lista numerada vale sobre o template da IA."""
    from broll_bot.auto_templates import auto_templates
    from broll_bot.cues import set_language
    from broll_bot.models import Scene, SceneAnalysis, Sentence
    from broll_bot.subject import prime_headers

    set_language("en")
    texts = ["Round one.", "July.", "Body.", "Round two.", "Tread life.", "Body.", "Round three.", "Ice.",
             "The glare-ice intersection at minus fifteen."]
    prime_headers(texts)
    sents = [Sentence(i, t, 0, i * 3.0, i * 3.0 + 3) for i, t in enumerate(texts)]
    scenes = [Scene(0, " ".join(texts[:3]), 0, 9, SceneAnalysis("", ["tires"], "tires")),
              Scene(1, " ".join(texts[3:6]), 9, 18, SceneAnalysis("", ["tires"], "tires")),
              Scene(2, " ".join(texts[6:]), 18, 27, SceneAnalysis("", ["ice"], "ice", style="stat", caption="Ice",
                                                                     number={"value": 15, "suffix": "°"}))]
    out, _ = auto_templates(scenes, sents, "", lang="en")
    st = [(s.analysis.style, (s.analysis.number or {}).get("value")) for s in out if s.analysis.style == "steps"]
    assert st == [("steps", 1), ("steps", 2), ("steps", 3)]


def test_slow_sellers_script_screen_text():
    """Roteiro dos "carros que ninguém quer" (Dale): anos no plural, valores sem "dollars", IONIQ 5, bZ, 500e."""
    from broll_bot.cues import set_currency, set_language
    from broll_bot.spoken import screen_text

    set_language("en")
    set_currency("C$")
    try:
        assert screen_text("before the twenty twenty-sevens arrive") == "before the 2027s arrive"
        assert screen_text("They want the twenty twenty-sixes gone.") == "They want the 2026s gone."
        assert screen_text("the nineteen nineties") == "the 1990s"
        assert "to C$4,000 on January first" in screen_text(
            "scheduled to drop from five thousand dollars to four thousand on January first")
        assert screen_text("Toyota is offering ten thousand dollars after tax on the twenty twenty-six b-Z Limited, "
                           "and seven thousand on the b-Z Woodland.") == \
            "Toyota is offering C$10,000 after tax on the 2026 bZ Limited, and C$7,000 on the bZ Woodland."
        assert "taking C$10,000, even C$12,000 off" in screen_text(
            "some automakers are taking ten, even twelve thousand dollars off the price")
        assert "on the IONIQ 5 Preferred" in screen_text("five thousand dollars before tax on the IONIQ five Preferred")
        assert "the SUV five years later" in screen_text("the SUV five years later")
        assert screen_text("eight thousand dollars off the Fiat five hundred e Pop.") == "C$8,000 off the Fiat 500e Pop."
        assert "EX, EX-L and Touring" in screen_text("the Prologue E-X, E-X-L and Touring")
        # sem moeda na frase, o número solto continua como está
        assert screen_text("A model that sold a thousand last year and four hundred this year") == \
            "A model that sold 1,000 last year and 400 this year"
    finally:
        set_currency(None)


def test_powertrain_word_before_the_brand_is_not_the_name():
    from broll_bot.cues import set_language
    from broll_bot.subject import prime_headers, rank_header, sentence_subjects

    set_language("en")
    texts = ["Number nine.", "The Lexus U-X.", "Down forty-one percent.", "Number eight.",
             "The gas Chevrolet Blazer.", "Canadian sales fell thirty-four percent last year.",
             "There won't be a twenty twenty-seven Blazer at all."]
    prime_headers(texts)
    assert rank_header("Number eight.")[1] == "Chevrolet Blazer"
    assert sentence_subjects(texts)[5] == "Chevrolet Blazer"


def test_mid_countdown_recap_and_like_request_end_the_car_section():
    from broll_bot.cues import set_language
    from broll_bot.subject import sentence_subjects

    set_language("en")
    texts = ["Number seven. The Jeep Compass.", "Sales dropped.", "Number six. The Hyundai IONIQ five.",
             "Sales down fifty-six percent.", "So that's five.", "A sports sedan in a crossover world.",
             "Quick favour before the top five.", "If this is giving you ideas, tap like.", "All right. Let's go.",
             "Number five. The Nissan Ariya.", "Sales fell."]
    per = sentence_subjects(texts)
    assert per[3] == "Hyundai IONIQ 5"
    assert per[4:9] == [""] * 5
    assert per[10] == "Nissan Ariya"


def test_sales_change_is_not_a_percent_bar():
    """ "sales were down 37%" é variação, não parte de um todo (a barra enchendo até 37% diria outra coisa)."""
    from broll_bot.cues import set_language
    from broll_bot.magnet_detect import percentbar

    set_language("en")
    assert percentbar(["WRX sales in Canada were down 37% in the first half of 2026."], "") is None
    assert percentbar(["For the year so far, the market is down less than 1%."], "") is None
    assert percentbar(["After plummeting 75% last year, it's down another 68%."], "") is None
    assert percentbar(["Up to 40% of drivers never check their tyre pressure."], "") is not None


def test_made_in_canada_script_screen_text_and_names():
    """Roteiro dos carros feitos no Canadá (Dale): milhares com hífen, "half a million", "A J", valor sem moeda."""
    from broll_bot.cues import set_currency, set_language
    from broll_bot.spoken import screen_text
    from broll_bot.subject import prime_headers, rank_header

    set_language("en")
    set_currency("C$")
    try:
        assert screen_text("It built about a hundred and fifty-two thousand pickups in twenty twenty-four.") == \
            "It built about 152,000 pickups in 2024."
        assert screen_text("a hundred and two thousand units") == "102,000 units"
        assert screen_text("built in two thousand three") == "built in 2003"
        assert screen_text("roughly half a million vehicles a year") == "roughly half a million vehicles a year"
        assert screen_text("A J means Japan.") == "A J means Japan."
        assert "AA battery" in screen_text("An AA battery")
        assert screen_text("The two-door starts at about sixty-two thousand dollars, and the four-door at about "
                           "sixty-five thousand, including destination.") == \
            "The two-door starts at about C$62,000, and the four-door at about C$65,000, including destination."
        texts = ["Number three.", "The Honda C-R-V.", "Honda became the first Japanese automaker to build vehicles in "
                 "Canada in nineteen eighty-six.", "Number nine.", "The gas-powered Dodge Charger.", "Same plant."]
        prime_headers(texts)
        assert rank_header("Number three.")[1] == "Honda CR-V"
        assert rank_header("Number nine.")[1] == "Dodge Charger"
    finally:
        set_currency(None)

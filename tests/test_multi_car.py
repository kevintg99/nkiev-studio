"""Vídeo de ranking com vários carros: um carro por seção, cabeçalho "Numéro N" e faixa de preço."""

from broll_bot.auto_templates import auto_templates
from broll_bot.coherence import enforce_coherence
from broll_bot.cues import set_language
from broll_bot.models import MediaType, Scene, SceneAnalysis
from broll_bot.motion import gfx
from broll_bot.motion.templates import TemplateSpec
from broll_bot.motion.templates_pro import Price
from broll_bot.plan_fix import isolate_rank_headers
from broll_bot.subject import anchor_scenes, assign_scene_subjects, rank_header, section_names, sentence_subjects
from broll_bot.transcription import parse_script

SCRIPT = """Voici les voitures d'occasion que personne ne regarde. On commence par la plus humble.

Numéro 3 : la Dacia Logan MCV.

Elle a une tête d'utilitaire. Mais le moteur est simple et solide.

Les chiffres : entre 3 000 et 6 000 euros selon l'âge.

Numéro 2 : la Mazda 6, en version 2.0 essence Skyactiv.

Face à elle, une Passat diesel. La Mazda est plus fiable.

Les chiffres : entre 10 000 et 16 000 euros.

Numéro 1 : la Toyota Prius, troisième génération.

Les taxis l'adorent. Elle roule encore et encore.

Avant de vous laisser, un dernier conseil. Lisez toujours le contrôle technique."""


def _setup():
    set_language("fr")
    sents = parse_script(SCRIPT)
    t = 0.0
    for s in sents:
        d = len(s.text.split()) * 0.4
        s.start, s.end = t, t + d
        t += d
    return sents


def _scenes(sents, size):
    out = []
    for k in range(0, len(sents), size):
        g = sents[k:k + size]
        out.append(Scene(len(out), " ".join(s.text for s in g), g[0].start, g[-1].end,
                         SceneAnalysis("car on a road", ["car interior dashboard", "young generation"], "car")))
    return out


def test_rank_header_reads_position_name_and_detail():
    set_language("fr")
    assert rank_header("Numéro 4 : la Mazda 6, en version 2.0 essence Skyactiv.") == \
        (4, "Mazda 6", "en version 2.0 essence Skyactiv")
    assert rank_header("La numéro 2, c'est une voiture de luxe.") is None
    set_language("pt")
    assert rank_header("Número 1: o Toyota Corolla.")[:2] == (1, "Toyota Corolla")


def test_each_section_has_its_own_car_until_the_outro():
    sents = _setup()
    per = sentence_subjects([s.text for s in sents])
    assert section_names(per) == ["Dacia Logan MCV", "Mazda 6", "Toyota Prius"]
    assert per[0] == ""  # introdução: nenhum carro
    by_text = dict(zip((s.text for s in sents), per))
    assert by_text["Face à elle, une Passat diesel."] == "Mazda 6"
    assert by_text["Les taxis l'adorent."] == "Toyota Prius"
    assert by_text["Lisez toujours le contrôle technique."] == ""  # depois da despedida


def test_a_single_car_header_is_not_a_ranking():
    set_language("fr")
    texts = ["Numéro 1 : la Toyota Prius.", "Elle est fiable.", "Numéro 2 : le moteur."]
    assert sentence_subjects(texts) == ["", "", ""]


def test_headers_become_ranking_cards_and_ranges_become_price_tags():
    sents = _setup()
    per = sentence_subjects([s.text for s in sents])
    scenes = _scenes(sents, 3)
    assign_scene_subjects(scenes, sents, per)
    assert isolate_rank_headers(scenes, sents)
    for s in scenes:  # o cabeçalho ficou sozinho na cena
        if any(rank_header(x.text) for x in parse_script(s.text)):
            assert len(parse_script(s.text)) == 1
    enforce_coherence(scenes)
    out, _ = auto_templates(scenes, sents, "", lang="fr")
    assign_scene_subjects(out, sents, per)
    ranks = [(s.analysis.rank, s.analysis.caption) for s in out if s.analysis.style == "ranking"]
    assert ranks == [(3, "Dacia Logan MCV"), (2, "Mazda 6"), (1, "Toyota Prius")]
    prices = [s.analysis for s in out if s.analysis.style == "price"]
    assert [(p.number["value"], p.number["value_to"]) for p in prices] == [(3000, 6000), (10000, 16000)]
    assert prices[0].subtitle == "Dacia Logan MCV" and prices[0].number["suffix"] == " €"


def test_searches_follow_the_car_of_each_section():
    sents = _setup()
    per = sentence_subjects([s.text for s in sents])
    scenes = _scenes(sents, 2)
    assign_scene_subjects(scenes, sents, per)
    anchor_scenes(scenes, "", "automotivo")
    seen = set()
    for s in scenes:
        a = s.analysis
        seen.add(a.subject)
        if a.subject == "Mazda 6":
            assert "mazda 6" in a.visual_summary.lower()
            assert "mazda 6 young generation" in a.queries  # busca abstrata ganha o nome inteiro
            assert "toyota prius" in a.negative_terms and "mazda 6" not in a.negative_terms
        if not a.subject:
            assert not any("mazda" in q or "prius" in q or "logan" in q for q in a.queries)
    assert {"", "Dacia Logan MCV", "Mazda 6", "Toyota Prius"} <= seen


def test_unspoken_end_of_range_is_dropped():
    scene = Scene(0, "Elle coûte 3 000 euros.", 0, 4, SceneAnalysis("car", ["car"], "car", style="price",
                  number={"value": 3000, "value_to": 9000, "suffix": " €"}))
    enforce_coherence([scene])
    assert scene.analysis.style == "price" and "value_to" not in scene.analysis.number


def test_price_tag_shows_the_range_and_fits_the_screen():
    try:
        gfx.set_number_locale("fr")
        spec = TemplateSpec("price", "navy", 1920, 1080, 30, 150, lang="fr",
                            number={"value": 100000, "value_to": 160000, "suffix": " €", "label": "Prix"})
        tpl = Price(spec)
        assert tpl.text(1.0) == "100 000 – 160 000 €"
        assert tpl.tag_w <= 1920 * 0.9 + 1
        tpl.frame(100)
    finally:
        gfx.set_number_locale("pt")


def test_thousands_separator_exists_in_the_condensed_fonts():
    """Sem o motor raqm (Pillow do Windows) um espaço que a fonte não tem vira um quadrado riscado."""
    from PIL import ImageFont

    try:
        gfx.set_number_locale("fr")
        sep = gfx.fmt_number(5000)[1]
        for name in ("condensed", "oswald_bold"):
            path = gfx.font(name, 100).path
            basic = ImageFont.truetype(path, 100, layout_engine=ImageFont.Layout.BASIC)
            assert basic.getmask(sep).getbbox() is None, name  # espaço de verdade, sem desenho
        assert gfx.safe_text("3 000") == "3 000"
    finally:
        gfx.set_number_locale("pt")


def test_price_uses_the_spoken_currency():
    scene = Scene(0, "On trouve des Logan MCV entre 3 000 et 6 000 euros.", 0, 5, SceneAnalysis(
        "car", ["car"], "car", style="price", number={"value": 3000, "prefix": "R$ ", "suffix": ""}))
    enforce_coherence([scene])
    assert scene.analysis.number["prefix"] == "" and scene.analysis.number["suffix"] == " €"


def test_ai_single_price_is_upgraded_to_the_spoken_range():
    sents = _setup()
    target = next(s for s in sents if "3 000" in s.text)
    scene = Scene(0, target.text, target.start, target.end, SceneAnalysis(
        "car", ["car"], "car", style="price", number={"value": 3000, "suffix": " €"}))
    out, _ = auto_templates([scene], sents, "", lang="fr")
    assert out[0].analysis.number["value_to"] == 6000


def test_split_scene_keeps_the_price_where_it_is_spoken():
    from broll_bot.plan_fix import split_long_scenes

    set_language("fr")
    sents = parse_script("Sur la Logan, il y a peu d'électronique. Rien à diagnostiquer, rien ne casse. "
                         "Regardez la rouille sous la caisse avant d'acheter. Le reste, c'est du solide. "
                         "Les chiffres : entre 3 000 et 6 000 euros selon l'âge.")
    t = 0.0
    for s in sents:
        s.start, s.end = t, t + 5.0
        t += 5.0
    scene = Scene(0, " ".join(s.text for s in sents), 0, t, SceneAnalysis(
        "car", ["car"], "car", style="price", number={"value": 3000, "value_to": 6000, "suffix": " €"}))
    parts, _ = split_long_scenes([scene], sents, max_seconds=8, target_seconds=6)
    priced = [p for p in parts if p.analysis.style == "price"]
    assert len(parts) > 1 and len(priced) == 1 and "3 000" in priced[0].text


def test_same_brand_other_model_is_filtered_in_a_ranking_section():
    from broll_bot.config import AppConfig
    from broll_bot.models import Candidate, MediaType
    from broll_bot.ranking import Ranker
    from broll_bot.subject import wrong_model

    assert wrong_model("Dacia Duster on dirt road", "Dacia Logan MCV")
    assert not wrong_model("Dacia_Logan_MCV_140003.jpg", "Dacia Logan MCV")
    assert not wrong_model("Mazda6 2014 sedan", "Mazda 6") and wrong_model("mazda cx-5", "Mazda 6")
    assert not wrong_model("red car on a road", "Dacia Logan MCV")  # sem marca: o revisor decide

    def cand(i, url, local=None):
        return Candidate("pexels", str(i), MediaType.IMAGE, 1920, 1080, None, None, None, page_url=url,
                         local_path=local)

    scene = Scene(0, "Elle a une tête d'utilitaire.", 0, 4, SceneAnalysis(
        "Dacia Logan MCV rear", ["dacia logan mcv rear"], "car", subject="Dacia Logan MCV"))
    pool = [cand(1, "https://www.pexels.com/photo/dacia-duster-on-dirt-road-19923014/"),
            cand(2, "https://www.pexels.com/photo/dacia-logan-mcv-parked-123/"),
            cand(3, "https://www.pexels.com/photo/old-car-on-a-street-456/")]
    kept = {c.media_id for c in Ranker(AppConfig(), None, None)._prefilter(scene, pool)}
    assert kept == {"2", "3"}


def test_backup_shots_come_from_the_whole_section_before_repeating():
    from pathlib import Path

    from broll_bot.config import AppConfig
    from broll_bot.models import Candidate, MediaType, Shot, Transition
    from broll_bot.ranking import Ranker
    from broll_bot.search import SceneSearchResult
    from broll_bot.selection import Selector

    def cand(i):
        c = Candidate("wikimedia", f"m{i}", MediaType.IMAGE, 1920, 1080, None, None, None)
        c.review = "approved"
        return c

    cfg = AppConfig()
    sel = Selector(cfg, {}, Ranker(cfg, None, None), Path("."), seed=1)
    # cena 0 sem nada; cena 4 (mesmo carro) tem 3 fotos boas e só precisa de 1
    results = {0: SceneSearchResult(0, [], needed=1), 1: SceneSearchResult(1, [], needed=1),
               4: SceneSearchResult(4, [cand(1), cand(2), cand(3)], needed=1)}
    shot = Shot(0, 0, 0, 1, 1, Transition("cut", 0), 0, 30)
    assert sel._neighbor_surplus(shot, results) == []  # sem seção: só as vizinhas
    sel.section_of = {0: "Honda Jazz", 1: "Honda Jazz", 4: "Honda Jazz"}
    assert [c.media_id for c in sel._neighbor_surplus(shot, results)] == ["m2", "m3"]
    sel.section_of[4] = "Kia Venga"  # outro carro: não serve
    assert sel._neighbor_surplus(shot, results) == []


def test_other_model_filter_keeps_brand_only_photos_and_aliases():
    from broll_bot.subject import wrong_model

    assert wrong_model("red-mazda-3-car-exhibition", "Mazda 6") and wrong_model("lexus-rx-350", "Lexus CT 200h")
    assert not wrong_model("white-lexus-sedan-1005632", "Lexus CT 200h")
    assert not wrong_model("traffic-vehicle-car-mazda-road", "Mazda 6")
    assert not wrong_model("File:2009 Honda Fit Sport", "Honda Jazz")  # Fit = Jazz nas Américas
    assert not wrong_model("mazda atenza wagon", "Mazda 6")


def test_photos_from_another_generation_are_dropped():
    from broll_bot.subject import section_eras, wrong_era

    set_language("fr")
    texts = ["Numéro 6 : la Honda Jazz.", "Sur la deuxième génération, entre 2008 et 2015 environ.",
             "Numéro 5 : le Suzuki Vitara.", "Elle roule bien."]
    eras = section_eras(texts, sentence_subjects(texts))
    assert eras == {"Honda Jazz": (2006, 2020)}  # o Vitara não cita ano: qualquer geração passa
    assert wrong_era("File:1986_Honda_Jazz_1.2_Luxe_rear.jpg", eras["Honda Jazz"])
    assert not wrong_era("2011 honda jazz front", eras["Honda Jazz"])
    assert not wrong_era("honda jazz 19923014", eras["Honda Jazz"])  # id do Pexels não é ano
    assert not wrong_era("File:1986_Honda_Jazz.jpg", None)


def test_last_resort_never_takes_another_cars_folder():
    from broll_bot.subject import names_car

    assert names_car(["mazda", "mazda6"], "Mazda 6") and not names_car(["dacia", "logan", "mcv", "motor"], "Mazda 6")
    assert names_car(["lexus", "ct", "200h"], "Lexus CT 200h") and not names_car(["kia", "venga"], "Lexus CT 200h")
    assert not names_car(["motor", "6"], "Mazda 6")  # "6" sozinho não é o Mazda 6


def test_header_name_stops_at_the_first_complement():
    set_language("fr")
    assert rank_header("Numéro 1 : la Toyota Aygo… et ses deux jumelles, la Peugeot 107 et la Citroën C1.")[1] == \
        "Toyota Aygo"
    assert rank_header("Numéro 6 : le Citroën Berlingo de première génération, et son jumeau, le Peugeot Partner.")[1:] \
        == ("Citroën Berlingo", "de première génération, et son jumeau, le Peugeot Partner")
    assert rank_header("Numéro 7 : le Honda CR-V de troisième génération, entre 2007 et 2012.")[1] == "Honda CR-V"
    assert rank_header("Numéro 9 : le Lexus RX 450h.")[1] == "Lexus RX 450h"


def test_search_name_and_before_after_years():
    from broll_bot.subject import search_name, section_eras

    assert search_name("Citroën Berlingo") == "citroen berlingo"
    assert search_name("Toyota Yaris hybride") == "toyota yaris hybrid"
    set_language("fr")
    texts = ["Numéro 4 : la Mazda 3.", "Sur la Mazda 3 essence d'avant 2018 environ, pas de filtre.",
             "Numéro 5 : le Suzuki Vitara.", "Le Vitara sorti à partir de 2015 est simple."]
    eras = section_eras(texts, sentence_subjects(texts))
    assert eras["Mazda 3"] == (2008, 2023)  # "avant 2018": uma Mazda 3 de 2014 passa
    assert eras["Suzuki Vitara"] == (2013, 2025)  # "à partir de 2015": o Grand Vitara de 2006 não


def test_model_query_skips_a_busy_pexels_but_generic_queries_wait(tmp_path):
    import random as _random

    from broll_bot.cache import SQLiteCache
    from broll_bot.config import AppConfig
    from broll_bot.ranking import Ranker
    from broll_bot.search import BrollSearcher

    class Limiter:
        def __init__(self, wait):
            self.wait = wait

        def wait_time(self):
            return self.wait

    class Http:
        def __init__(self, wait):
            self.limiter = Limiter(wait)

    class Pexels:
        name, is_remote, cache_version = "pexels", True, ""

        def __init__(self, wait):
            self.http = Http(wait)

    cfg = AppConfig()
    searcher = BrollSearcher(cfg, {}, Ranker(cfg, None, None), SQLiteCache(tmp_path / "c.sqlite"), _random.Random(1))
    scene = Scene(0, "x", 0, 4, SceneAnalysis("car", ["car"], "car", subject="Dacia Logan MCV"))
    busy, free = Pexels(15.0), Pexels(0.0)
    assert searcher._skip_busy(busy, "dacia logan mcv rear", MediaType.VIDEO, "en", scene)
    assert not searcher._skip_busy(free, "dacia logan mcv rear", MediaType.VIDEO, "en", scene)
    assert not searcher._skip_busy(busy, "mechanic checking timing chain", MediaType.VIDEO, "en", scene)
    # já está no cache: vale sempre (não gasta a fila)
    searcher.cache.set("search", searcher._search_key(busy, "dacia logan mcv rear", MediaType.VIDEO, "en"), [], 3600)
    assert not searcher._skip_busy(busy, "dacia logan mcv rear", MediaType.VIDEO, "en", scene)
    # já esperou demais neste vídeo (8 min somados): até a busca genérica pula a fila cheia
    busy.http.limiter.waited = 9 * 60
    assert searcher._skip_busy(busy, "mechanic checking timing chain", MediaType.VIDEO, "en", scene)
    free.http.limiter.waited = 9 * 60
    assert not searcher._skip_busy(free, "mechanic checking timing chain", MediaType.VIDEO, "en", scene)


def test_other_brand_media_stays_in_the_scene_that_talks_about_it():
    """Foto do Peugeot 208 da comparação não vaza para a cena dos "Magic Seats" do Honda Jazz."""
    from pathlib import Path

    from broll_bot.config import AppConfig
    from broll_bot.models import Candidate
    from broll_bot.ranking import Ranker
    from broll_bot.search import SceneSearchResult
    from broll_bot.selection import Selector
    from broll_bot.subject import other_brands

    assert other_brands("peugeot 208 turbo engine closeup", "Les Magic Seats de la Jazz.", "Honda Jazz") == {"peugeot"}
    assert not other_brands("peugeot 208 engine", "Face à elle, une Clio ou une Peugeot 208.", "Honda Jazz")
    assert not other_brands("honda jazz magic seats", "Les Magic Seats.", "Honda Jazz")

    def cand(i, query):
        c = Candidate("wikimedia", str(i), MediaType.IMAGE, 1920, 1080, None, None, None, query=query)
        c.review = "approved"
        return c

    cfg = AppConfig()
    sel = Selector(cfg, {}, Ranker(cfg, None, None), Path("."), seed=1)
    sel.section_of = {0: "Honda Jazz", 1: "Honda Jazz"}
    sel.scene_text = {0: "Les Magic Seats se relèvent.", 1: "Face à elle, une Clio ou une 208 PureTech."}
    results = {0: SceneSearchResult(0, [], needed=1),
               # a cena 1 usa a melhor (2); sobram a do Peugeot (1) e a do Jazz (3)
               1: SceneSearchResult(1, [cand(2, "honda jazz interior"), cand(1, "peugeot 208 turbo engine"),
                                        cand(3, "honda jazz rear")], needed=1)}
    from broll_bot.models import Shot, Transition

    shot = Shot(0, 0, 0, 1, 1, Transition("cut", 0), 0, 30)
    assert [c.media_id for c in sel._neighbor_surplus(shot, results)] == ["3"]
    sel.scene_text[0] = "Comme la Peugeot 208, elle a des Magic Seats."  # esta fala cita a Peugeot: pode
    assert [c.media_id for c in sel._neighbor_surplus(shot, results)] == ["1", "3"]

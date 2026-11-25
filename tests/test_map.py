"""Mapa com setas (a incursão de Portsmouth a Dieppe), país x país com bandeiras e as peças do esquema sem
encostar umas nas outras (o "about one metre" saía por cima do boneco)."""

from types import SimpleNamespace

import numpy as np
from PIL import Image

import broll_bot.motion  # noqa: F401 - registra os templates
from broll_bot import director
from broll_bot.looks import LOOKS
from broll_bot.models import SceneAnalysis
from broll_bot.motion import geo
from broll_bot.motion.planner import MEDIA_FREE, TemplatePlanner
from broll_bot.motion.templates import TEMPLATES, TemplateSpec
from broll_bot.motion.templates_map import clean_map_elements, resolve

RAID = [{"t": "place", "name": "Portsmouth", "lat": 50.8, "lon": -1.09},
        {"t": "place", "name": "Dieppe", "lat": 49.92, "lon": 1.08},
        {"t": "route", "from": "Portsmouth", "to": "Dieppe"}]


def _spec(kind, **kw):
    return TemplateSpec(kind=kind, theme="navy", w=640, h=360, fps=10, frames=50, seed=3, lang="en", **kw)


def test_countries_and_places_are_found_by_name_in_any_language():
    assert geo.find_country("Niemcy").iso2 == "DE" and geo.find_country("Allemagne").iso2 == "DE"
    assert geo.find_country("Polska").iso2 == "PL" and geo.find_country("USA").iso2 == "US"
    lat, lon = geo.find_place("Dieppe", "France")
    assert abs(lat - 49.93) < 0.2 and abs(lon - 1.08) < 0.2
    # a base vence a IA quando as duas batem; o que a base não tem (praia, batalha) fica com o ponto da IA
    assert geo.locate("Dieppe", "France", 49.0, 1.0) == geo.find_place("Dieppe", "France")
    assert geo.locate("Omaha Beach", "France", 49.37, -0.87) == (49.37, -0.87)
    assert geo.locate("Lugar Nenhum") is None


def test_map_pieces_are_cleaned_and_routes_need_both_places():
    raw = [*RAID, {"t": "route", "from": "Dieppe", "to": "Atlantis"}, {"t": "volcano"}, {"t": "place", "name": ""}]
    assert [e["t"] for e in clean_map_elements(raw)] == ["place", "place", "route"]
    out = resolve([{"t": "place", "name": "Atlantis"}, {"t": "route", "from": "Atlantis", "to": "Dieppe"},
                   {"t": "place", "name": "Dieppe"}, {"t": "country", "name": "Narnia"}])
    assert [e["name"] for e in out] == ["Dieppe"]  # sem coordenada nem país de verdade, sai (e a rota junto)


def test_map_draws_land_route_and_label_without_black_borders_or_covering_names():
    tpl = TEMPLATES["map"](_spec("map", keyword="A raid: could a port be taken from the sea?", elements=RAID))
    first, last = tpl.frame(0), np.asarray(tpl.frame(49).convert("RGB")).astype(int)
    assert first.size == (640, 360)
    corners = [last[0, 0], last[0, -1], last[-1, 0], last[-1, -1]]
    assert all(c.sum() > 60 for c in corners)  # a câmera aproxima sem mostrar faixa preta na borda
    red = ((last[..., 0] > 200) & (last[..., 1] < 90) & (last[..., 2] > 50) & (last[..., 2] < 130)).sum()
    assert red > 50  # a seta vermelha de Portsmouth a Dieppe
    # a tarja do título não fica em cima de nenhum lugar
    tw, th = tpl.title.size
    cx, cy = tpl.title_at
    for e in tpl.places.values():
        x, y = tpl.px(e["lon"], e["lat"])
        assert not (cx - tw / 2 < x < cx + tw / 2 and cy - th / 2 < y < cy + th / 2)


def test_highlighted_country_turns_red():
    tpl = TEMPLATES["map"](_spec("map", elements=[{"t": "country", "name": "Poland"}]))
    px = tpl.px(19.4, 52.1)  # meio da Polônia
    before = np.asarray(tpl.frame(0).convert("RGB"))[round(px[1]), round(px[0])].astype(int)
    after = np.asarray(tpl.frame(49).convert("RGB"))[round(px[1]), round(px[0])].astype(int)
    assert after[0] - after[1] > before[0] - before[1] + 20


def test_flag_versus_shows_both_flags_and_works_without_them(tmp_path):
    pl, de = tmp_path / "pl.png", tmp_path / "de.png"
    Image.new("RGB", (64, 40), (220, 20, 60)).save(pl)
    Image.new("RGB", (64, 40), (255, 206, 0)).save(de)
    tpl = TEMPLATES["flagversus"](_spec("flagversus", keyword="Armies in 1939", labels=["Polska", "Niemcy"],
                                        tiles=[str(pl), str(de)], stats=[{"label": "Tanks", "value": "880",
                                                                         "value_b": "2,700"}]))
    a = np.asarray(tpl.frame(49).convert("RGB")).astype(int)
    left, right = a[:, : a.shape[1] // 2], a[:, a.shape[1] // 2:]
    assert ((left[..., 0] > 200) & (left[..., 1] < 60)).sum() > 200  # bandeira vermelha à esquerda
    assert ((right[..., 0] > 230) & (right[..., 1] > 180) & (right[..., 2] < 60)).sum() > 200  # amarela à direita
    TEMPLATES["flagversus"](_spec("flagversus", labels=["Polska", "Niemcy"], tiles=["", ""])).frame(49)


def test_flag_is_downloaded_once_and_bad_codes_are_ignored(tmp_path):
    calls = []

    class Session:
        def get(self, url, timeout=0):
            calls.append(url)
            return SimpleNamespace(status_code=200, content=b"\x89PNG fake")

    path = geo.flag_path("PL", tmp_path, session=Session())
    assert path.name == "pl.png" and path.read_bytes().startswith(b"\x89PNG")
    assert geo.flag_path("pl", tmp_path, session=Session()) == path and len(calls) == 1  # do cache
    assert geo.flag_path("-99", tmp_path, session=Session()) is None and len(calls) == 1


def _an(**kw):
    return SceneAnalysis(visual_summary="", queries=[], fallback_query="", **kw)


def test_planner_checks_map_and_flag_data():
    assert {"map", "flagversus"} <= MEDIA_FREE
    assert TemplatePlanner._data_ok("map", _an(elements=RAID))
    assert not TemplatePlanner._data_ok("map", _an(elements=[{"t": "place", "name": "Atlantis"}]))
    assert TemplatePlanner._data_ok("flagversus", _an(items=[{"label": "Polska", "query": "Poland"},
                                                            {"label": "Niemcy", "query": "Germany"}]))
    assert not TemplatePlanner._data_ok("flagversus", _an(items=[{"label": "Polska", "query": "Poland"}]))
    assert {"map", "flagversus"} <= set(LOOKS["documentario"]["liberar"]["templates"])


def _sents(texts):
    return [SimpleNamespace(index=i, text=t, start=i * 12.0, end=i * 12.0 + 4) for i, t in enumerate(texts)]


def test_director_keeps_only_places_the_narration_says_even_declined_in_polish():
    assert '"map"' not in director.system_prompt() and '"map"' in director.system_prompt(("map", "flagversus"))
    sents = _sents(["Witajcie na kanale, dzisiaj historia wojny.",
                    "We wrześniu 1939 roku Niemcy zaatakowały Polskę od zachodu.",
                    "Polska armia miała osiemset osiemdziesiąt czołgów, a niemiecka dwa tysiące siedemset.",
                    "To wszystko na dziś."])
    raw = {"cards": [
        {"first": 1, "last": 1, "kind": "map", "fields": {"caption": "Niemcy zaatakowały Polskę", "elements": [
            {"t": "country", "name": "Germany"}, {"t": "country", "name": "Poland"},
            {"t": "place", "name": "Vienna", "lat": 48.2, "lon": 16.37}]}},
        {"first": 2, "last": 2, "kind": "flagversus", "fields": {
            "items": [{"label": "Poland", "query": "Poland"}, {"label": "Germany", "query": "Germany"}],
            "stats": [{"label": "czołgi", "value": "880", "value_b": "2700"}]}},
    ]}
    plan = director.validate(raw, sents, "pl")
    kinds = {c.kind: c for c in plan.cards}
    assert set(kinds) == {"map", "flagversus"}
    els = kinds["map"].fields["elements"]
    assert [e["name"] for e in els] == ["Germany", "Poland"]  # Viena a fala não cita
    assert els[0]["label"] == "Niemcy"  # na tela, o nome como a fala diz
    assert [i["label"] for i in kinds["flagversus"].fields["items"]] == ["Polska", "Niemcy"]
    assert any("Vienna" in d for d in plan.dropped)


def test_diagram_texts_never_sit_on_the_drawing_or_on_each_other():
    els = [{"t": "ground", "y": 40}, {"t": "box", "x": 46, "y": 40, "w": 6, "h": 9},
           {"t": "burst", "x": 49, "y": 25.5, "r": 13},
           {"t": "text", "x": 40, "y": 25, "text": "hundreds of steel balls", "size": "l", "color": "red"},
           {"t": "text", "x": 42, "y": 26, "text": "at waist height"},
           {"t": "person", "x": 70, "y": 40, "h": 18, "pose": "point", "face": "left"},
           {"t": "measure", "x": 66, "y1": 40, "y2": 26, "label": "about one metre"}]
    tpl = TEMPLATES["diagram"](_spec("diagram", keyword="The S-mine", elements=els))
    texts = [e for e in tpl.els if e["t"] == "text"] + [e["_label"] for e in tpl.els if e.get("_label")]
    boxes = [tpl._text_box(e, e["_px"], e["_py"]) for e in texts]
    shapes = [b for b in (tpl._box(e) for e in tpl.els if e["t"] in ("box", "burst", "person")) if b]

    def overlap(a, b):
        return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])

    assert not any(overlap(a, b) for a in boxes for b in shapes)
    assert not any(overlap(boxes[i], boxes[j]) for i in range(len(boxes)) for j in range(i + 1, len(boxes)))
    assert tpl.moved >= 2  # os textos que a IA pôs em cima da explosão mudaram de lugar


def test_french_narration_with_elision_and_articles_keeps_the_places():
    """Canal em francês: "l'Allemagne", "d'Italie", "les États-Unis" (o apóstrofo grudava a palavra e o país sumia)."""
    assert geo.fold("l'Allemagne") == "l allemagne" and geo.find_country("États-Unis").iso2 == "US"
    sents = _sents(["Bonjour à tous, aujourd'hui une histoire de guerre.",
                    "En 1940, l'Allemagne attaque la France en passant par la Belgique.",
                    "Les États-Unis entrent en guerre contre l'Allemagne en 1941.",
                    "C'est tout pour aujourd'hui."])
    raw = {"cards": [
        {"first": 1, "last": 1, "kind": "map", "fields": {"caption": "L'Allemagne attaque la France", "elements": [
            {"t": "country", "name": "Germany", "say": "l'Allemagne"}, {"t": "country", "name": "France"},
            {"t": "country", "name": "Belgium"}, {"t": "place", "name": "Sedan", "lat": 49.7, "lon": 4.94}]}},
        {"first": 2, "last": 2, "kind": "flagversus", "fields": {
            "items": [{"label": "les États-Unis", "query": "USA"}, {"label": "l'Allemagne", "query": "Germany"}]}},
    ]}
    plan = director.validate(raw, sents, "fr")
    kinds = {c.kind: c for c in plan.cards}
    els = kinds["map"].fields["elements"]
    assert [e["name"] for e in els] == ["Germany", "France", "Belgium"]  # Sedan a fala não cita
    assert [e["label"] for e in els] == ["Allemagne", "France", "Belgique"]  # na tela, sem o artigo
    assert [i["label"] for i in kinds["flagversus"].fields["items"]] == ["États-Unis", "Allemagne"]
    assert director._bare_place("Le Havre") == "Le Havre" and director._bare_place("La Rochelle") == "La Rochelle"


def test_french_question_mark_never_falls_alone_on_the_next_line():
    from broll_bot.motion import gfx

    f = gfx.font("serif", 40)
    text = "Pourquoi l'Allemagne a-t-elle attaqué ?"
    for width in range(gfx.text_width("Pourquoi", f), gfx.text_width(text, f) + 10, 15):
        assert all(line.strip() not in ("?", "!", ":", "»") for line in gfx.wrap(text, f, width, 6))
    assert gfx.wrap("« Jamais » dit-il", f, 1, 6)[0] == "« Jamais »"
    tpl = TEMPLATES["map"](_spec("map", keyword="Pourquoi l'Allemagne a-t-elle attaqué ?",
                                 elements=[{"t": "country", "name": "Germany", "label": "Allemagne"}]))
    tpl.frame(10)


def test_typed_phrase_paints_the_french_word_red_even_after_l_apostrophe():
    tpl = TEMPLATES["docphrase"](_spec("docphrase", body="L'Allemagne perd des « millions » de soldats.",
                                       labels=["Allemagne", "millions"]))
    from broll_bot.motion.templates_docu import _mark_key

    assert {_mark_key(w) for w in "L'Allemagne « millions »".split()} >= tpl.red


def test_map_with_one_or_two_close_towns_still_shows_the_region_with_country_names_in_the_video_language():
    """Operação Greif: o mapa de Saint-Vith e Bastogne era só um pedaço de terra (sem país para situar), Paris e
    Versailles tinham os nomes um em cima do outro e o ponto de Bastogne ficava embaixo do nome de Saint-Vith."""
    assert geo.name_in(geo.find_country("Germany"), "fr") == "Allemagne"
    assert geo.name_in(geo.find_country("Germany"), "pl") == "Niemcy"
    els = [{"t": "place", "name": "Saint-Vith", "lat": 50.28, "lon": 6.13},
           {"t": "place", "name": "Bastogne", "lat": 50.0, "lon": 5.72}]
    tpl = TEMPLATES["map"](TemplateSpec(kind="map", theme="navy", w=640, h=360, fps=10, frames=50, seed=3, lang="fr",
                                        keyword="Ils s'accrochent à Saint-Vith et Bastogne", elements=els))
    assert tpl.h / tpl.scale >= 6.4  # no mínimo uns 700 km de altura
    assert "ALLEMAGNE" in tpl.context_names
    assert "GERMANY" in TEMPLATES["map"](_spec("map", elements=els)).context_names

    for els in ([{"t": "place", "name": "Paris"}, {"t": "place", "name": "Versailles", "lat": 48.8, "lon": 2.12}], els):
        tpl = TEMPLATES["map"](_spec("map", elements=els))
        boxes = tpl.label_boxes
        assert (boxes[0][2] <= boxes[1][0] or boxes[1][2] <= boxes[0][0] or boxes[0][3] <= boxes[1][1]
                or boxes[1][3] <= boxes[0][1])  # os dois nomes não se encostam
        for e in tpl.places.values():  # nenhum ponto embaixo de um nome
            x, y = tpl.px(e["lon"], e["lat"])
            assert not any(b[0] < x < b[2] and b[1] < y < b[3] for b in boxes)


def test_map_can_show_a_place_said_a_few_sentences_before_the_card():
    sents = _sents(["Le 17 décembre 1944.", "Aywaille, une petite ville belge, au sud de Liège.", "Il fait froid.",
                    "Une jeep s'approche.", "À bord, trois hommes.", "Rien d'anormal.", "Le front s'est effondré.",
                    "Une jeep de plus ne surprend personne."])
    raw = {"cards": [{"first": 6, "last": 6, "kind": "map", "fields": {"caption": "Aywaille", "elements": [
        {"t": "place", "name": "Aywaille", "lat": 50.47, "lon": 5.68}, {"t": "place", "name": "Liège"}]}}]}
    plan = director.validate(raw, sents, "fr")
    assert [e["name"] for e in plan.cards[0].fields["elements"]] == ["Aywaille", "Liège"]


def test_documentary_director_gets_a_card_count_and_the_other_channels_keep_their_prompt():
    sents = _sents(["Frase."] * 270)
    goal = director.card_goal(270, ("map", "flagversus"))
    assert "36-54 cards" in goal and "6-12 maps" in goal
    assert director.user_prompt(sents, "", "en") == director.user_prompt(sents, "", "en", ())
    assert "cards" not in director.user_prompt(sents, "", "en").split("Script:")[0]
    assert "6-12 maps" in director.user_prompt(sents, "", "fr", ("diagram", "map", "flagversus"))

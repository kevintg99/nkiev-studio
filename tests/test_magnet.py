"""Edição magnética: 10 templates com a cara de cada estilo, os detectores na fala e a pele por estilo."""

import pytest
from PIL import Image

from broll_bot.motion import TEMPLATES, MediaRef, TemplateSpec
from broll_bot.motion.templates_doc import template_cues
from broll_bot.motion.templates_magnet import MAGNET_TEMPLATES, SKINS


def _photo(tmp_path, name, color):
    path = tmp_path / name
    Image.new("RGB", (320, 180), color).save(path)
    return MediaRef(str(path))


CASES = {
    "percentbar": dict(number={"value": 39}, keyword="of owners reported a fault"),
    "peoplegrid": dict(number={"value": 3, "total": 10}, keyword="repairs cost more than £1,500"),
    "receipt": dict(number={"value": 1500, "prefix": "£"}, keyword="Mercedes GLE", points=[{"text": "Battery"}]),
    "downtime": dict(number={"value": 9}, keyword="more than a week in the workshop"),
    "warning": dict(keyword="Non-engine electrics", subtitle="24% of faults"),
    "textmask": dict(keyword="Tiguan", subtitle="the lowest score of all"),
    "photostack": dict(keyword="Nissan Qashqai"),
    "splitpanels": dict(keyword="Range Rover Evoque"),
    "annotate": dict(keyword="the hybrid system"),
    "wordpop": dict(captions=[(0.1, 0.8, "Not one fault was fixed"), (0.8, 1.6, "inside a single day")]),
}


@pytest.mark.parametrize("skin", SKINS)
def test_every_magnet_template_draws_in_every_skin(tmp_path, skin):
    media = [_photo(tmp_path, f"{k}.jpg", c) for k, c in enumerate([(200, 30, 30), (30, 160, 60), (30, 60, 200)])]
    assert set(CASES) == set(MAGNET_TEMPLATES) and all(k in TEMPLATES for k in CASES)
    for kind, kw in CASES.items():
        for m in (media, media[:1], []):
            spec = TemplateSpec(kind=kind, theme="channel", w=320, h=180, fps=10, frames=25, seed=5, media=list(m),
                                lang="en", skin=skin, **kw)
            tpl = TEMPLATES[kind](spec)
            for i in (0, 8, 24):
                assert tpl.frame(i).size == (320, 180)
            tpl.close()
            assert template_cues(kind, spec)  # sons sincronizados


def test_spoken_data_becomes_the_right_template():
    from broll_bot.cues import set_language
    from broll_bot.magnet_detect import MAGNET_DETECTORS

    set_language("en")
    try:
        def first(text):
            found = [d for f in MAGNET_DETECTORS if (d := f([text], "Range Rover Evoque"))]
            return max(found, key=lambda d: d.priority) if found else None

        d = first("Thirty-nine per cent of owners reported a fault.")
        assert d.kind == "percentbar" and d.fields["number"]["value"] == 39
        assert d.fields["caption"] == "of owners reported a fault"
        d = first("Reliability rating, seventy-six point four per cent.")
        assert d.kind == "percentbar" and d.fields["number"]["value"] == 76.4 and d.fields["caption"] == "Reliability rating"
        d = first("Seven in ten could still be driven, which is something.")
        assert d.kind == "peoplegrid" and d.fields["number"] == {"value": 7, "total": 10}
        d = first("But thirteen per cent of faults cost the owner more than fifteen hundred pounds.")
        assert d.kind == "receipt" and d.fields["number"] == {"value": 1500.0, "prefix": "£"}
        d = first("Eighty-five per cent of the faulty cars were off the road for more than a week.")
        assert d.kind == "downtime" and d.fields["number"]["value"] == 8
        d = first("The biggest single area was non-engine electrics, at twenty-four per cent.")
        assert d.kind == "warning" and d.fields["caption"] == "non-engine electrics" and d.fields["subtitle"] == "24%"
        assert first("The cabin is quiet and comfortable.") is None
    finally:
        set_language("pt")


def test_data_template_starts_when_the_number_is_spoken():
    from broll_bot.auto_templates import auto_templates
    from broll_bot.models import Scene, SceneAnalysis, Sentence

    texts = ["The Evoque looks great on the drive.", "It is a very popular car in Britain.",
             "Thirty-nine per cent of owners reported a fault."]
    sents = [Sentence(i, t, 0, i * 4.0, i * 4.0 + 4) for i, t in enumerate(texts)]
    scenes = [Scene(0, " ".join(texts), 0, 12, SceneAnalysis("", ["range rover evoque"], "car"))]
    out, _ = auto_templates(scenes, sents, "", lang="en")
    assert [s.analysis.style for s in out] == ["normal", "percentbar"] and out[1].start == 8.0


def test_each_style_gets_its_skin_and_mix_respects_the_channel(tmp_path):
    import yaml

    from broll_bot.channels import apply_channel, apply_overrides
    from broll_bot.config import AppConfig
    from broll_bot.looks import job_overrides
    from broll_bot.motion.planner import TemplatePlanner
    from tests.test_studio import FIXT

    for style, skin in [("minimal", "blueprint"), ("dossie", "paper"), ("broadcast", "tv"), ("dados", "data"),
                        ("kinetic", "kinetic"), ("cinema", "cinema"), ("mix", "mix")]:
        assert job_overrides(style)["templates"]["skin"] == skin
    cfg = AppConfig()
    apply_channel(cfg, FIXT / "canais" / "jhonny_walt.yaml")  # sem papel
    path = tmp_path / "a.yaml"
    path.write_text(yaml.safe_dump(job_overrides("mix")), encoding="utf-8")
    apply_overrides(cfg, path)
    planner = TemplatePlanner.__new__(TemplatePlanner)
    planner.cfg, planner.tcfg = cfg, cfg.templates
    skins = {planner._skin(i) for i in range(40)}
    assert "paper" not in skins and len(skins) >= 4


def test_planner_keeps_data_template_without_photo_and_sends_the_skin():
    from broll_bot.config import AppConfig
    from broll_bot.models import Scene, SceneAnalysis, Shot, Transition
    from broll_bot.motion.planner import TemplatePlanner

    cfg = AppConfig()
    cfg.templates.skin = "tv"
    planner = TemplatePlanner(cfg, selector=None, searcher=None, thumbs=None)  # type: ignore[arg-type]
    a = SceneAnalysis("x", ["x"], "x", style="percentbar", caption="of owners reported a fault",
                      number={"value": 39, "suffix": "%"})
    sc = Scene(0, "39% of owners reported a fault.", 0, 5, a)
    shot = Shot(0, 0, 0, 4, 4, Transition("cut", 0), 0, 120, template="percentbar")
    planner.prepare([shot], {0: sc}, {}, [])
    assert shot.template == "percentbar" and shot.template_data["skin"] == "tv"
    assert shot.template_data["number"]["value"] == 39
    # foto obrigatória (palavra com o vídeo dentro) e nenhuma imagem: vira título, não some
    b = SceneAnalysis("x", ["x"], "x", style="textmask", caption="Tiguan")
    shot2 = Shot(1, 1, 0, 4, 4, Transition("cut", 0), 0, 120, template="textmask")
    planner.prepare([shot2], {1: Scene(1, "The Tiguan is back.", 0, 5, b)}, {}, [])
    assert shot2.template == "headline"


def test_french_and_portuguese_data_become_templates():
    """Vídeo do Bernard (estilo Dados): nenhum gráfico de dado saiu porque as pistas eram só em inglês/£."""
    from broll_bot.cues import set_language
    from broll_bot.magnet_detect import MAGNET_DETECTORS

    def first(text, subject="Volkswagen / Audi / Seat / Skoda DSG"):
        found = [d for f in MAGNET_DETECTORS if (d := f([text], subject))]
        return max(found, key=lambda d: d.priority) if found else None

    set_language("fr")
    try:
        d = first("La facture : souvent entre 1 500 et 3 000 euros, selon qu'il faut changer la mécatronique, "
                  "l'embrayage, ou les deux.")
        assert d.kind == "receipt" and d.fields["number"] == {"value": 3000.0, "low": 1500.0, "suffix": " €"}
        assert d.fields["caption"] == "Volkswagen DSG"
        assert [p["text"] for p in d.fields["points"]] == ["mécatronique", "embrayage"]
        d = first("Et la remplacer, c'est une grosse opération : comptez souvent plus de 1 000 euros.")
        assert d.kind == "receipt" and d.fields["number"] == {"value": 1000.0, "suffix": " €"}
        d = first("Le défaut : une surconsommation d'huile.")
        assert d.kind == "warning" and d.fields["caption"] == "Surconsommation d'huile"
        assert first("Le problème, c'est sa chaîne de distribution.").fields["caption"] == "Chaîne de distribution"
        assert first("Une pompe à carburant haute pression fragile. Et une consommation d'huile parfois "
                     "importante.").fields["caption"] == "Pompe à carburant haute pression"
        d = first("Un propriétaire sur trois a eu une panne.")
        assert d.kind == "peoplegrid" and d.fields["number"] == {"value": 1, "total": 3}
        d = first("Selon l'enquête, 23 pour cent des voitures avaient un défaut.")
        assert d.kind == "percentbar" and d.fields["number"]["value"] == 23
        # preço do carro e conversa não são conta de oficina
        assert first("Et en occasion, on en trouve autour de 18 000 ou 20 000 euros.") is None
        assert first("Abonnez-vous à la chaîne, ça ne vous coûte rien.") is None
    finally:
        set_language("pt")
    d = first("O conserto sai entre 3.000 e 5.000 reais na oficina.", "Ford Ka")
    assert d.kind == "receipt" and d.fields["number"] == {"value": 5000.0, "low": 3000.0, "prefix": "R$"}


def test_receipt_shows_the_spoken_range_in_euros(tmp_path):
    from broll_bot.motion import gfx

    gfx.set_number_locale("fr")
    try:
        spec = TemplateSpec(kind="receipt", theme="channel", w=640, h=360, fps=10, frames=25, seed=1, media=[],
                            lang="fr", skin="data", keyword="Volkswagen DSG",
                            number={"value": 3000, "low": 1500, "suffix": " €"}, points=[{"text": "mécatronique"}])
        tpl = TEMPLATES["receipt"](spec)
        assert tpl.total.replace(" ", " ").replace(" ", " ") == "1 500–3 000 €"
        assert tpl.frame(24).size == (640, 360)
        tpl.close()
    finally:
        gfx.set_number_locale("pt")


def test_data_template_replaces_a_decorative_ai_pick():
    """No vídeo do Jhonny, a IA pôs um "card" em "Reliability rating, 76.4%" e a barra não entrava."""
    from broll_bot.auto_templates import auto_templates
    from broll_bot.models import Scene, SceneAnalysis, Sentence

    text = "Reliability rating, seventy-six point four per cent."
    sents = [Sentence(0, text, 0, 0.0, 4.0)]
    card = Scene(0, text, 0, 4, SceneAnalysis("", ["range rover evoque"], "car", style="card"))
    out, notes = auto_templates([card], sents, "", lang="en")
    assert out[0].analysis.style == "percentbar" and out[0].analysis.number["value"] == 76.4
    # escolha que não é decorativa (ranking, comparativo) continua valendo
    rank = Scene(0, text, 0, 4, SceneAnalysis("", ["range rover evoque"], "car", style="comparison"))
    out, _ = auto_templates([rank], sents, "", lang="en")
    assert out[0].analysis.style == "comparison"


def test_percent_differences_are_not_bars():
    """ "15% less to build", "25% more than petrol": diferença, não parte de um todo."""
    from broll_bot.cues import set_language
    from broll_bot.magnet_detect import percentbar, warning

    set_language("en")
    try:
        for text in ["The Seal cost about fifteen per cent less to build than a Model Three.",
                     "Against a Volkswagen I D three built in Europe, the gap was about thirty-five per cent.",
                     "It would still keep a cost advantage of around twenty-five per cent.",
                     "Repair costs have already come down by more than ten per cent."]:
            assert percentbar([text], "") is None, text
        d = percentbar(["It says the battery can make up as much as forty per cent of an electric car's total value."], "")
        assert d.fields["number"]["value"] == 40 and d.fields["caption"] == "of an electric car's total value"
        d = percentbar(["The same work put the figure for a China-built Tesla Model Three at around forty-six per cent."], "")
        assert d.fields["caption"] == "a China-built Tesla Model 3"
        # "top" do pack não é "o defeito mais comum"
        assert warning(["The top of the battery pack forms the floor of the car."], "") is None
    finally:
        set_language("pt")

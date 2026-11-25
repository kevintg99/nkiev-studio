"""Estilo Broadcast: templates e tarjas de telejornal e a troca de família depois do ritmo."""

import yaml
from PIL import Image

from broll_bot.channels import apply_channel, apply_overrides
from broll_bot.config import AppConfig
from broll_bot.looks import NEWS_OVERLAY_SWAP, NEWS_SWAP, job_overrides
from broll_bot.models import Scene, SceneAnalysis
from broll_bot.motion import TEMPLATES, MediaRef, TemplateSpec
from broll_bot.motion.i18n import text
from broll_bot.motion.overlays import OVERLAYS, OverlaySpec, overlay_cues
from broll_bot.motion.templates_doc import template_cues
from broll_bot.rhythm import apply_swaps

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXT = Path(__file__).resolve().parent / "fixtures"  # canais de exemplo fixos (os de canais/ mudam pelo app)


def _photo(tmp_path, name, color):
    path = tmp_path / name
    Image.new("RGB", (320, 180), color).save(path)
    return MediaRef(str(path))


def _spec(kind, media=(), **kw):
    return TemplateSpec(kind=kind, theme="channel", w=320, h=180, fps=10, frames=20, seed=3, media=list(media),
                        lang="en", **kw)


def test_news_templates_draw_with_and_without_media(tmp_path):
    a, b, c = (_photo(tmp_path, f"{k}.jpg", col) for k, col in enumerate([(200, 20, 20), (20, 200, 20), (20, 20, 200)]))
    cases = [("bulletin", [a], dict(body="And this is where it goes wrong.", keyword="Alfa Romeo Giulia")),
             ("bulletin", [], dict(body="No picture behind this one.")),
             ("report", [a], dict(keyword="Alfa Romeo Giulia", subtitle="the engine everyone feared")),
             ("videowall", [a, b, c], dict(keyword="Alfa Romeo Stelvio")),
             ("videowall", [a, b], dict(keyword="Alfa Romeo Stelvio"))]
    for kind, media, kw in cases:
        tpl = TEMPLATES[kind](_spec(kind, media, **kw))
        for i in (0, 5, 19):
            frame = tpl.frame(i)
            assert frame.size == (320, 180)
        tpl.close()
        assert template_cues(kind, _spec(kind, media, **kw))  # efeitos sonoros sincronizados


def test_news_overlays_draw_and_have_sounds():
    for kind, kw in [("lowerthird", dict(title="Alfa Romeo Giulia", detail="electrical gremlins")),
                     ("lowerthird", dict(title="Turbo")),
                     ("ticker", dict(title="It is not the engine that will cost you money.", detail="Giulia"))]:
        spec = OverlaySpec(kind=kind, w=320, h=180, fps=10, frames=30, lang="fr", **kw)
        ov = OVERLAYS[kind](spec)
        assert any(ov.frame(i).getchannel("A").getextrema()[1] > 0 for i in (5, 15, 25))
        assert overlay_cues({"kind": kind, **kw}, 30, 10)
    assert text("breaking", "fr") == "DERNIÈRE MINUTE" and text("live", "pt") == "AO VIVO"


def _scene(i, style="normal", overlay=None, subject="Alfa Romeo Giulia", text="The engine is a simple unit with a chain."):
    a = SceneAnalysis("car", ["car"], "car", style=style, overlay=overlay or {})
    a.subject = subject
    return Scene(i, text, i * 5.0, i * 5.0 + 5.0, a)


def test_swaps_turn_rhythm_pieces_into_tv_pieces():
    cfg = AppConfig()
    styles, kinds = set(cfg.templates.styles), set(cfg.overlays.kinds)
    statement = _scene(0, "statement")
    statement.analysis.body = "And that is the problem."
    show = _scene(1, "showcase")
    kw = _scene(2, overlay={"kind": "keyword", "title": "engine"})
    phrase = _scene(3, overlay={"kind": "phrase", "title": "It never lets you down."})
    impact = _scene(4, overlay={"kind": "impact", "title": "NEVER", "detail": "It never lets you down."})
    file = _scene(5, overlay={"kind": "file", "title": "Alfa Romeo Giulia"})
    price = _scene(6, "price")
    scenes = [statement, show, kw, phrase, impact, file, price]
    notes = apply_swaps(scenes, NEWS_SWAP, NEWS_OVERLAY_SWAP, styles, kinds)
    assert len(notes) == 6 and price.analysis.style == "price"  # conteúdo (preço) não muda
    assert statement.analysis.style == "bulletin" and statement.analysis.body == "And that is the problem."
    assert show.analysis.style in ("report", "newsfloat") and show.analysis.caption == "Alfa Romeo Giulia"
    assert show.analysis.subtitle  # trecho curto da própria fala na tarja
    assert kw.analysis.overlay["kind"] == "lowerthird" and kw.analysis.overlay["title"] == "engine"
    assert phrase.analysis.overlay == {"kind": "ticker", "title": "It never lets you down.",
                                       "detail": "Alfa Romeo Giulia", "title_b": "", "detail_b": "", "lines": []}
    assert impact.analysis.overlay["title"] == "It never lets you down."
    assert file.analysis.overlay["kind"] == "lowerthird" and file.analysis.overlay["title"] == "Alfa Romeo Giulia"


def test_swaps_respect_what_is_allowed_and_chance():
    s = _scene(0, "statement")
    assert not apply_swaps([s], NEWS_SWAP, {}, {"statement"}, set())  # bulletin não liberado: fica
    scenes = [_scene(i, "statement") for i in range(200)]
    apply_swaps(scenes, NEWS_SWAP, {}, {"bulletin"}, set(), chance=0.35, seed=1)
    n = sum(sc.analysis.style == "bulletin" for sc in scenes)
    assert 40 < n < 100  # Mix: só uma parte vira telejornal
    assert not apply_swaps(scenes, {}, {}, {"bulletin"}, set())


def test_broadcast_look_on_jhonny_walt(tmp_path):
    cfg = AppConfig()
    apply_channel(cfg, FIXT / "canais" / "jhonny_walt.yaml")  # canal sem ficha de papel
    path = tmp_path / "ajustes.yaml"
    path.write_text(yaml.safe_dump(job_overrides("broadcast")), encoding="utf-8")
    apply_overrides(cfg, path)
    assert cfg.templates.swap == NEWS_SWAP and cfg.overlays.swap == NEWS_OVERLAY_SWAP
    assert {"bulletin", "report", "videowall"} <= set(cfg.templates.styles)
    assert {"lowerthird", "ticker"} <= set(cfg.overlays.kinds) and "file" not in cfg.overlays.kinds
    # a ficha do carro (proibida no canal) vira a tarja com o nome: o pipeline liga as fichas pela troca
    assert cfg.overlays.swap.get("file") in cfg.overlays.kinds
    off = job_overrides("broadcast", statements=False, phrases=False)
    assert "statement" in off["remover"]["templates"] and "phrase" in off["remover"]["overlays"]


def test_crawl_items_come_from_the_speech():
    from broll_bot.cues import set_language
    from broll_bot.rhythm import crawl_items

    set_language("en")
    try:
        items = crawl_items("Number 2: the Alfa Romeo Stelvio. This SUV handles like a sports car. A clean one costs "
                            "about 18,000 pounds today. It gets around 38 mpg on the motorway, and the boot is huge. "
                            "Subscribe to the channel and hit the bell. Is it worth it?", subject="Alfa Romeo Stelvio")
    finally:
        set_language("pt")
    assert items == ["This SUV handles like a sports car", "A clean one costs about 18,000 pounds today",
                     "It gets around 38 mpg on the motorway", "the boot is huge"]


def test_crawl_runs_in_two_long_blocks_without_templates():
    from broll_bot.config import AppConfig
    from broll_bot.models import Shot, Transition
    from broll_bot.motion.planner import plan_overlays

    text = "The cabin is quiet and comfortable. A clean one costs about nine thousand pounds. The gearbox is smooth."
    scenes = {i: _scene(i, text=text) for i in range(60)}
    shots = []
    for i in range(60):  # 60 shots de 4 s = 240 s; templates nos shots 8, 20 e 31
        shots.append(Shot(index=i, scene_index=i, cut_in=i * 4.0, cut_out=i * 4.0 + 4, clip_duration=4.0,
                          start_frame=i * 120, end_frame=i * 120 + 120, transition_in=Transition("cut", 0.0),
                          template="bulletin" if i in (8, 20, 31) else None, candidate=object()))
    cfg = AppConfig()
    cfg.overlays.crawl_at, cfg.overlays.crawl_seconds = [0.04, 0.5], 24.0
    cfg.analysis.script_language = "en"
    plan_overlays(shots, scenes, cfg, [])
    crawl = [s.index for s in shots if (s.overlay or {}).get("kind") == "crawl"]
    blocks = [[crawl[0]]]
    for i in crawl[1:]:
        (blocks[-1].append(i) if i == blocks[-1][-1] + 1 else blocks.append([i]))
    assert len(blocks) == 2
    for b in blocks:
        assert 14 <= 4 * len(b) <= 28  # ~20–25 s contínuos
        assert not any(shots[i].template for i in b)  # nunca em cima de um template
        assert shots[b[0]].overlay["enter"] and shots[b[-1]].overlay["leave"]
        assert all(not shots[i].overlay["leave"] for i in b[:-1])
        assert shots[b[1]].overlay["offset"] > 0  # continua de onde parou, sem reiniciar no corte
    assert blocks[0][0] * 4 < 40 and abs(blocks[1][0] * 4 - 120) < 30  # um no começo, outro no meio
    assert any("£9,000" in line for line in shots[blocks[0][0]].overlay["lines"])  # número escrito


def test_crawl_continues_across_cuts():
    from broll_bot.models import Shot, Transition
    from broll_bot.motion.planner import _continue_crawls

    def shot(i, scene, f0, f1, template=None):
        return Shot(index=i, scene_index=scene, cut_in=f0 / 30, cut_out=f1 / 30, clip_duration=(f1 - f0) / 30,
                    start_frame=f0, end_frame=f1, transition_in=Transition("fade", 0.3), template=template)

    shots = [shot(0, 1, 0, 90), shot(1, 1, 90, 210), shot(2, 1, 210, 300), shot(3, 2, 300, 400)]
    shots[0].overlay = {"kind": "crawl", "title": "Giulia", "lines": ["a b c", "d e f"], "start": 0.5}
    _continue_crawls(shots, 30)
    assert shots[0].overlay["leave"] is False and shots[1].overlay["enter"] is False
    assert shots[1].overlay["offset"] == 2.5 and shots[2].overlay["offset"] == 6.5  # 3 s - 0,5 s; 7 s - 0,5 s
    assert shots[2].overlay["leave"] is True and shots[3].overlay == {}  # outra cena: não continua
    spec = OverlaySpec(kind="crawl", w=320, h=180, fps=10, frames=30, title="Giulia", lines=["one two three", "four five six"],
                       start=0.0, offset=2.5, enter=False, leave=True)
    ov = OVERLAYS["crawl"](spec)
    assert ov.frame(0).getchannel("A").getextrema()[1] > 0  # já entra rolando, sem animação de entrada
    assert overlay_cues({"kind": "crawl", "title": "Giulia", "enter": False}, 30, 10) == []


def test_broadcast_look_turns_on_the_crawl(tmp_path):
    cfg = AppConfig()
    path = tmp_path / "a.yaml"
    path.write_text(yaml.safe_dump(job_overrides("broadcast")), encoding="utf-8")
    apply_overrides(cfg, path)
    assert cfg.overlays.crawl_at == [0.04, 0.5] and "crawl" in cfg.overlays.kinds
    assert AppConfig().overlays.crawl_at == []  # fora do Broadcast, nada de letreiro


# ------------------------------------------------------------------ dossiê (papel) e ano/modelo
def test_paper_templates_draw(tmp_path):
    a, b, c = (_photo(tmp_path, f"p{k}.jpg", col) for k, col in enumerate([(200, 20, 20), (20, 200, 20), (20, 20, 200)]))
    cases = [("paperfloat", [a], dict(keyword="Alfa Romeo Giulia", subtitle="the engine everyone feared")),
             ("paperfloat", [], dict(keyword="Sem foto")),
             ("paperpair", [a, b], dict(keyword="Alfa Romeo Stelvio")),
             ("papertimeline", [a, b, c, a], dict(keyword="Giulia", labels=["1962", "1974", "2016", "2020"],
                                                  captions=[(0.2, 0.6, "x"), (0.7, 1.0, "y")])),
             ("papercompare", [a, b], dict(labels=["Giulia", "Stelvio"])),
             ("papercompare", [a, b, c], dict(labels=["A", "B", "C"])),
             ("paperstatement", [], dict(body="And that is where most owners get it wrong.", labels=["wrong"]))]
    for kind, media, kw in cases:
        tpl = TEMPLATES[kind](TemplateSpec(kind=kind, theme="dossier", w=320, h=180, fps=10, frames=40, seed=3,
                                           media=list(media), lang="en", **kw))
        for i in (0, 12, 39):
            assert tpl.frame(i).size == (320, 180)
        tpl.close()
        assert template_cues(kind, TemplateSpec(kind=kind, theme="dossier", w=320, h=180, fps=10, frames=40,
                                                media=list(media), **kw))


def test_year_facts_in_several_languages():
    from broll_bot.rhythm import year_fact

    assert year_fact("Number 8: the Toyota Corolla, 2014 to 2019.") == "2014–2019"
    assert year_fact("between 2009-12 the engine changed") == "2009–2012"
    assert year_fact("Les Clio de 2012 à 2016 sont fiables") == "2012–2016"
    assert year_fact("The 2008 model is the one to buy") == "2008"
    assert year_fact("produzido em 2010") == "2010" and year_fact("Le modèle 2011 est fiable") == "2011"
    assert year_fact("In 2014 they recalled it") == ""  # ano solto não é ano do modelo
    assert year_fact("It costs 2,000 to 3,000 pounds") == ""


def test_year_overlay_goes_to_the_next_broll_scene():
    from broll_bot.rhythm import add_year_overlays

    header = _scene(0, "ranking", text="Number 8: the Toyota Corolla, 2014 to 2019.", subject="Toyota Corolla")
    broll = _scene(1, subject="Toyota Corolla", text="The engine is simple and strong.")
    other = _scene(6, subject="Honda Jazz", text="Built in 2012, the Jazz is tiny.")  # 30 s depois
    notes = add_year_overlays([header, broll, other])
    assert broll.analysis.overlay == {"kind": "years", "title": "Toyota Corolla", "detail": "2014–2019",
                                      "title_b": "", "detail_b": "", "lines": []}
    assert other.analysis.overlay["detail"] == "2012" and len(notes) == 2


def test_years_overlay_family_versions():
    for kind in ("years", "paperyears", "papertag"):
        spec = OverlaySpec(kind=kind, w=320, h=180, fps=10, frames=30, title="Toyota Corolla", detail="2014–2019")
        ov = OVERLAYS[kind](spec)
        assert any(ov.frame(i).getchannel("A").getextrema()[1] > 0 for i in (8, 20))
        assert overlay_cues({"kind": kind, "title": "Toyota Corolla", "detail": "2014–2019"}, 30, 10)
    years = _scene(0, overlay={"kind": "years", "title": "Toyota Corolla", "detail": "2014–2019"})
    apply_swaps([years], {}, NEWS_OVERLAY_SWAP, set(), {"lowerthird"})
    assert years.analysis.overlay["title"] == "Toyota Corolla" and years.analysis.overlay["detail"] == "2014–2019"


def test_dossier_look_swaps_to_paper_and_respects_channel(tmp_path):
    from broll_bot.looks import PAPER_SWAP

    cfg = AppConfig()
    path = tmp_path / "a.yaml"
    path.write_text(yaml.safe_dump(job_overrides("dossie")), encoding="utf-8")
    apply_overrides(cfg, path)
    assert cfg.templates.swap == PAPER_SWAP and cfg.overlays.swap["years"] == "paperyears"
    scenes = [_scene(0, "timeline"), _scene(1, "statement"), _scene(2, overlay={"kind": "keyword", "title": "turbo"})]
    scenes[1].analysis.body = "That is the problem."
    apply_swaps(scenes, cfg.templates.swap, cfg.overlays.swap, set(cfg.templates.styles), set(cfg.overlays.kinds))
    assert [s.analysis.style for s in scenes[:2]] == ["papertimeline", "paperstatement"]
    assert scenes[2].analysis.overlay["kind"] == "papertag"
    jw = AppConfig()
    apply_channel(jw, FIXT / "canais" / "jhonny_walt.yaml")  # canal sem o visual de papel
    assert not {"paperfloat", "papertimeline"} & set(jw.templates.styles) and "papertag" not in jw.overlays.kinds
    assert "years" in jw.overlays.kinds and "timeline" in jw.templates.styles  # ano e linha do tempo continuam


def test_mix_picks_tv_or_paper():
    from broll_bot.looks import LOOKS

    swap = LOOKS["mix"]["templates"]["swap"]
    scenes = [_scene(i, "statement") for i in range(300)]
    for s in scenes:
        s.analysis.body = "That is it."
    apply_swaps(scenes, swap, {}, {"bulletin", "paperstatement"}, set(), chance=1.0, seed=4)
    kinds = {s.analysis.style for s in scenes}
    assert kinds == {"bulletin", "paperstatement"}


def test_new_tv_and_paper_templates_draw(tmp_path):
    a, b, c = (_photo(tmp_path, f"n{k}.jpg", col) for k, col in enumerate([(200, 20, 20), (20, 200, 20), (20, 20, 200)]))
    cases = [("newsrank", [a], dict(rank=10, keyword="Range Rover Evoque", subtitle="on sale since 2019")),
             ("newsrank", [], dict(rank=1, keyword="Volkswagen Tiguan")),
             ("paperrank", [a], dict(rank=3, keyword="Mercedes GLE", subtitle="the most expensive car")),
             ("paperrank", [], dict(rank=3, keyword="Mercedes GLE")),
             ("newsfloat", [b], dict(keyword="Nissan Qashqai", subtitle="petrol, 2014 to 2021")),
             ("newsfloat", [], dict(keyword="Nissan Qashqai")),
             ("newstimeline", [a, b, c], dict(keyword="Tiguan", labels=["2007", "2016", "2024"], notes=["1st gen"])),
             ("newstimeline", [a, b, c, a, b], dict(labels=["2001", "2005", "2010", "2015", "2020"])),
             ("newscompare", [a, b], dict(keyword="Lexus UX vs Audi Q2", labels=["Lexus UX", "Audi Q2"])),
             ("newscompare", [a, b, c], dict(labels=["A", "B", "C"]))]
    for kind, media, kw in cases:
        tpl = TEMPLATES[kind](_spec(kind, media, **kw))
        for i in (0, 5, 19):
            assert tpl.frame(i).size == (320, 180)
        tpl.close()
        assert template_cues(kind, _spec(kind, media, **kw))
    assert text("number", "fr") == "N°" and text("spotlight", "pt") == "EM FOCO"


def test_each_style_has_its_ranking_card():
    from broll_bot.looks import LOOKS, PAPER_SWAP

    assert NEWS_SWAP["ranking"] == "newsrank" and PAPER_SWAP["ranking"] == "paperrank"
    assert NEWS_SWAP["timeline"] == "newstimeline" and NEWS_SWAP["comparison"] == "newscompare"
    assert set(LOOKS["mix"]["templates"]["swap"]["ranking"]) == {"newsrank", "paperrank"}
    assert set(LOOKS["mix"]["templates"]["swap"]["showcase"]) == {"report", "newsfloat", "paperfloat"}
    # os demais estilos (Minimal, Dados, Kinetic, Cinema) usam o cartão de ranking padrão
    for name in ("minimal", "dados", "kinetic", "cinema"):
        assert "ranking" not in LOOKS[name].get("remover", {}).get("templates", [])
    cfg = AppConfig()
    rank = _scene(0, "ranking")
    rank.analysis.rank, rank.analysis.caption = 10, "Range Rover Evoque"
    apply_swaps([rank], NEWS_SWAP, {}, set(cfg.templates.styles), set())
    assert rank.analysis.style == "newsrank" and rank.analysis.rank == 10


def test_planner_maps_skins_to_their_base():
    from broll_bot.motion.planner import SKIN_BASE, SKIN_FALLBACK

    assert SKIN_BASE["newsrank"] == "ranking" and SKIN_BASE["paperrank"] == "ranking"
    assert SKIN_BASE["newstimeline"] == "timeline" and SKIN_BASE["newscompare"] == "comparison"
    assert SKIN_FALLBACK["news"]["card"] == "newsfloat" and SKIN_FALLBACK["paper"]["card"] == "paperfloat"

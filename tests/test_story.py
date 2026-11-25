"""Templates de reportagem (fonte, de X para Y, garantia, passo a passo, não confirmado, desmontagem) e o
Broadcast só com cara de notícia quando a fala é notícia. Frases do roteiro da BYD (Jhonny Walt)."""

import pytest
from PIL import Image

from broll_bot.motion import TEMPLATES, MediaRef, TemplateSpec
from broll_bot.motion.templates_doc import template_cues
from broll_bot.motion.templates_magnet import SKINS
from broll_bot.motion.templates_story import STORY_TEMPLATES

CASES = {
    "source": dict(keyword="UBS", subtitle="Swiss investment bank · 2023",
                   body="About 75% of the Seal's parts were made by BYD itself"),
    "fromto": dict(keyword="showrooms", subtitle="dealer network", number={"from": 5, "value": 143}),
    "coverage": dict(keyword="BYD · WARRANTY", points=[{"text": "General cover", "value": 6, "detail": "93,750 miles"},
                                                       {"text": "Battery", "value": 8, "detail": "125,000 miles"}]),
    "steps": dict(keyword="Get the insurance quote before you sign", number={"value": 1, "total": 5}),
    "unverified": dict(keyword="Which cars sold in Britain use this pack", subtitle="unconfirmed"),
    "exploded": dict(keyword="BYD Atto 3", points=[{"text": "motor"}, {"text": "inverter"}, {"text": "battery"}]),
}


@pytest.fixture()
def english():
    from broll_bot.cues import set_language

    set_language("en")
    yield
    set_language("pt")


@pytest.mark.parametrize("skin", SKINS)
def test_every_story_template_draws_in_every_skin(tmp_path, skin):
    path = tmp_path / "car.jpg"
    Image.new("RGB", (320, 180), (200, 30, 30)).save(path)
    assert set(CASES) == set(STORY_TEMPLATES) and all(k in TEMPLATES for k in CASES)
    for kind, kw in CASES.items():
        for media in ([MediaRef(str(path))], []):
            spec = TemplateSpec(kind=kind, theme="channel", w=320, h=180, fps=10, frames=30, seed=4, media=media,
                                lang="en", skin=skin, **kw)
            tpl = TEMPLATES[kind](spec)
            for i in (0, 9, 29):
                assert tpl.frame(i).size == (320, 180)
            tpl.close()
            assert template_cues(kind, spec)


def _first(text_or_list, subject="byd seal"):
    from broll_bot.story_detect import STORY_DETECTORS

    ss = text_or_list if isinstance(text_or_list, list) else [text_or_list]
    found = [d for f in STORY_DETECTORS if (d := f(ss, subject))]
    return max(found, key=lambda d: d.priority) if found else None


def test_sources_are_read_from_the_speech(english):
    d = _first("In twenty twenty-three, analysts at U B S, the Swiss investment bank, took apart a B Y D Seal.")
    assert d.kind == "source" and d.fields["caption"] == "UBS"
    assert d.fields["subtitle"] == "Swiss investment bank · 2023"
    d = _first("Around seventy Japanese parts companies came to look, as reported by the Japanese business paper Nikkei.")
    assert d.fields["caption"] == "Nikkei" and d.fields["subtitle"] == "Japanese business paper"
    d = _first("When Tesla made its battery part of the structure, a Reuters investigation in twenty twenty-three "
               "quoted experts.")
    assert d.fields["caption"] == "Reuters" and d.fields["subtitle"] == "investigation · 2023"
    d = _first("And separately, figures from the Association of British Insurers, reported in the trade press, found "
               "electric car repairs costing more.")
    assert d.fields["caption"] == "Association of British Insurers"
    d = _first("In March this year, Thatcham Research, which works for the British insurance industry, published what "
               "it calls an E V Blueprint.")
    assert d.fields["caption"] == "Thatcham Research"
    assert _first("The B Y D drew the most attention.") is None
    assert _first("I'm Graham Mercer.") is None


def test_growth_warranty_and_unknowns(english):
    d = _first("And the dealer network has gone from five retailers to around a hundred and forty-three sites.")
    assert d.kind == "fromto" and d.fields["number"] == {"from": 5.0, "value": 143.0}
    assert d.fields["caption"] == "sites" and d.fields["subtitle"] == "dealer network"
    assert _first("The range has gone from one model to ten.").fields["caption"] == "models"
    assert _first("It was built from 2012 to 2016.") is None  # anos: linha do tempo, não crescimento
    d = _first(["According to B Y D's own British terms, you get six years of general cover, up to ninety-three "
                "thousand, seven hundred and fifty miles.", "The drive unit is covered for eight years, up to the same "
                "mileage.", "And the battery is covered for eight years, up to one hundred and twenty-five thousand miles."])
    assert d.kind == "coverage" and d.fields["caption"] == "BYD · WARRANTY"
    assert d.fields["points"] == [{"text": "General cover", "value": 6.0, "detail": "93,750 miles"},
                                  {"text": "Drive unit", "value": 8.0, "detail": "93,750 miles"},
                                  {"text": "Battery", "value": 8.0, "detail": "125,000 miles"}]
    d = _first("I have not been able to confirm which cars sold in Britain use this exact second-generation pack.")
    assert d.kind == "unverified" and d.fields["subtitle"] == "unconfirmed"
    assert d.fields["caption"] == "Which cars sold in Britain use this exact second-generation pack"
    assert _first("The honest answer is we do not know yet.").fields["subtitle"] == "no_data"
    assert _first("Where it is an educated guess, I will tell you that too.") is None  # promessa, não palpite
    d = _first("A Chinese electric car, taken apart down to the last bolt.")
    assert d.kind == "exploded"
    assert _first("Nobody has taken a B Y D apart in a British garage and filmed it.") is None
    assert _first("The first teardown came from an unlikely place.") is None


def test_numbered_tips_become_steps_in_sequence(english):
    from broll_bot.auto_templates import auto_templates
    from broll_bot.models import Scene, SceneAnalysis, Sentence

    texts = ["So here is what I would do.", "One.", "Get the insurance quote before you sign, not after.",
             "Two.", "Find out where your nearest B Y D site is.", "Three.", "Ask the dealer the question."]
    sents = [Sentence(i, t, 0, i * 3.0, i * 3.0 + 3) for i, t in enumerate(texts)]
    scenes = [Scene(0, " ".join(texts[:3]), 0, 9, SceneAnalysis("", ["car"], "car")),
              Scene(1, " ".join(texts[3:5]), 9, 15, SceneAnalysis("", ["car"], "car")),
              Scene(2, " ".join(texts[5:]), 15, 21, SceneAnalysis("", ["car"], "car"))]
    out, _ = auto_templates(scenes, sents, "byd seal", lang="en")
    steps = [s.analysis for s in out if s.analysis.style == "steps"]
    assert [a.number for a in steps] == [{"value": k, "total": 3} for k in (1, 2, 3)]
    assert steps[0].caption == "Get the insurance quote before you sign, not after"
    assert out[0].analysis.style == "normal" and out[1].start == 3.0  # "So here is..." fica no b-roll
    # "One." solto num roteiro sem sequência não vira passo
    from broll_bot.story_detect import prime_steps

    assert prime_steps(["One.", "The car is fast."]) == {}


def test_broadcast_only_looks_like_news_when_the_speech_is_news():
    from broll_bot.cues import is_news_talk
    from broll_bot.motion.templates_news import Bulletin, Report

    assert is_news_talk("In March 2023, BYD registered its first car here, according to the SMMT.")
    assert is_news_talk("A Reuters investigation in 2023 quoted experts.")
    assert not is_news_talk("Fewer parts is genuinely good news in one way.")
    assert not is_news_talk("Fewer parts means fewer joints and fewer connectors.")

    def spec(kind, tone):
        return TemplateSpec(kind=kind, theme="channel", w=320, h=180, fps=10, frames=20, lang="en", tone=tone,
                            body="Fewer parts means fewer things to wear out", keyword="BYD Seal")

    calm, news = Bulletin(spec("bulletin", "")), Bulletin(spec("bulletin", "news"))
    assert calm.slab_text == "KEY POINT" and calm.latest == "SPOTLIGHT"
    assert news.slab_text == "BREAKING NEWS" and news.latest == "LATEST"
    assert calm.frame(15).size == (320, 180)
    assert not Report(spec("report", "")).news and Report(spec("report", "news")).news


def test_planner_marks_news_scenes():
    from broll_bot.config import AppConfig
    from broll_bot.models import Scene, SceneAnalysis, Shot, Transition
    from broll_bot.motion.planner import TemplatePlanner

    cfg = AppConfig()
    planner = TemplatePlanner(cfg, selector=None, searcher=None, thumbs=None)  # type: ignore[arg-type]
    a = SceneAnalysis("x", ["x"], "x", style="steps", caption="Get the insurance quote", number={"value": 1, "total": 5})
    sc = Scene(0, "One. Get the insurance quote before you sign.", 0, 5, a)
    shot = Shot(0, 0, 0, 4, 4, Transition("cut", 0), 0, 120, template="steps")
    planner.prepare([shot], {0: sc}, {}, [])
    assert shot.template == "steps" and shot.template_data["tone"] == ""
    b = SceneAnalysis("x", ["x"], "x", style="bulletin", body="Toyota announced a recall")
    shot2 = Shot(1, 1, 0, 4, 4, Transition("cut", 0), 0, 120, template="bulletin")
    planner.prepare([shot2], {1: Scene(1, "Toyota announced a recall of the bZ4X.", 0, 5, b)}, {}, [])
    assert shot2.template_data["tone"] == "news"


def test_byd_video_searches_and_checks():
    """Vídeo da BYD: "seal" sozinho na busca trazia foca; banco/analistas da fala eram barrados como "fora do nicho"."""
    from broll_bot.models import Scene, SceneAnalysis
    from broll_bot.subject import anchor_scenes
    from broll_bot.visual_checks import ACCIDENT_WORDS, OFF_PROMPTS

    cases = [("What I am going to do is walk you through four real teardowns. A Swiss investment bank.",
              ["bank analysts discussing report"]),
             ("B Y D started out making batteries, not cars.", ["battery factory production line"]),
             ("Before we start, let me be straight with you.", ["studio narrator addressing audience"])]
    scenes = [Scene(i, t, 0, 5, SceneAnalysis("x", q, "", query_language="en")) for i, (t, q) in enumerate(cases)]
    anchor_scenes(scenes, "byd seal", "automotivo")
    assert scenes[0].analysis.queries[0] == "bank analysts discussing report"  # a fala cita o banco
    assert scenes[1].analysis.queries[0] == "battery factory production line"  # "batteries" na fala
    assert scenes[2].analysis.queries[0].startswith("byd seal ")  # nunca "seal" sozinho
    released = {w for _, words in OFF_PROMPTS for w in words}
    assert {"bank", "analysts", "report", "insurers", "exhibition"} <= released
    assert ACCIDENT_WORDS.search("throw the battery away after a minor collision")
    assert ACCIDENT_WORDS.search("being written off unnecessarily")


def test_french_listing_is_not_news():
    from broll_bot.auto_templates import detect
    from broll_bot.cues import is_news_talk, set_language

    set_language("fr")
    try:
        t = "Comment la reconnaître : sur l'annonce, cherchez la mention « batterie en location »."
        assert not is_news_talk(t) and not [d for d in detect([t], "") if d.kind == "news"]
        assert is_news_talk("Renault a annoncé un rappel de 30 000 voitures.")
    finally:
        set_language("pt")


def test_step_numbers_glued_to_the_wrong_scene():
    """"…after a knock. Three." e "Two." numa cena só dele: cada dica vira o seu passo, em sequência."""
    from broll_bot.auto_templates import auto_templates
    from broll_bot.coherence import enforce_coherence
    from broll_bot.models import Scene, SceneAnalysis, Sentence

    texts = ["One.", "Get the insurance quote before you sign.", "Quotes vary a lot.", "Two.",
             "Find out where your nearest B Y D site is.", "The network has grown fast.",
             "Ask what happens after a knock.", "Three.", "Ask the dealer the question I gave you earlier.",
             "A good dealer will answer."]
    sents = [Sentence(i, t, 0, i * 3.0, i * 3.0 + 3) for i, t in enumerate(texts)]

    def sc(i, a, b, style="normal"):
        return Scene(i, " ".join(texts[a:b]), a * 3.0, b * 3.0, SceneAnalysis("", ["car"], "car", style=style))

    out, _ = auto_templates([sc(0, 0, 3), sc(1, 3, 4), sc(2, 4, 8, "checklist"), sc(3, 8, 10)], sents, "byd seal",
                            lang="en")
    enforce_coherence(out)
    assert [(s.analysis.number or {}).get("value") for s in out if s.analysis.style == "steps"] == [1, 2, 3]


def test_spoken_numbers_count_in_coherence():
    from broll_bot.coherence import enforce_coherence
    from broll_bot.models import Scene, SceneAnalysis

    a = SceneAnalysis("x", ["x"], "x", style="stat", number={"value": 75, "suffix": "%"})
    enforce_coherence([Scene(0, "About seventy-five per cent of the Seal's parts were made in-house.", 0, 5, a)])
    assert a.style == "stat"


def test_each_scene_shows_what_the_speech_talks_about():
    """Fala de carro: a imagem tem que ser carro. Fala de outra coisa concreta (banco, feira, bateria de
    celular): mostra isso. Frase genérica num canal de carros: carro. A marca da IA no plano vence."""
    from broll_bot.models import Scene, SceneAnalysis
    from broll_bot.scenes import SYSTEM_PROMPT, _ScenePlan, _plan_to_scenes, user_prompt
    from broll_bot.subject import anchor_scenes, car_scene

    def scene(text, shows="", queries=("bank analysts discussing report",)):
        return Scene(0, text, 0, 5, SceneAnalysis("x", list(queries), "", query_language="en", shows=shows))

    assert not car_scene(scene("A Swiss investment bank."))
    assert not car_scene(scene("Batteries for phones and electronics."))
    assert car_scene(scene("In March twenty twenty-three, B Y D sold its first car here."))
    assert car_scene(scene("Before we start, let me be straight with you."))  # genérico: carro
    assert car_scene(scene("Seventy Japanese car parts companies walked into an exhibition hall."))
    assert not car_scene(scene("Its warranty terms are good.", shows="other"))  # a IA decidiu
    assert car_scene(scene("A Swiss investment bank.", shows="car"))
    # cena de outro assunto: a busca não ganha o carro na frente
    bank = scene("A Swiss investment bank.", queries=("modern glass office building",))
    anchor_scenes([bank], "byd seal", "automotivo")
    assert bank.analysis.queries == ["modern glass office building"]
    # o plano da IA traz a marca
    assert '"other"' in SYSTEM_PROMPT and "talking head" in user_prompt([], __import__(
        "broll_bot.config", fromlist=["AnalysisConfig"]).AnalysisConfig(niche="automotivo"))
    from broll_bot.models import Sentence

    plan = _ScenePlan.model_validate({"scenes": [{"first_sentence": 0, "last_sentence": 0, "visual_summary": "bank",
                                                  "queries": ["bank building"], "fallback_query": "bank",
                                                  "negative_terms": [], "tone": "calm", "shows": "other"}]})
    out = _plan_to_scenes(plan, [Sentence(0, "A Swiss investment bank.", 0, 0.0, 2.0)], 2.0)
    assert out[0].analysis.shows == "other"


def test_test_video_replay_fixes():
    """Vídeo de teste: a fonte "UBS" (falada "U B S,") caía na coerência; o intervalo entre gráficos contava do
    começo da cena (a IA juntou parágrafos e o alerta sumia); o passo a passo segurava o carimbo seguinte."""
    from broll_bot.auto_templates import auto_templates
    from broll_bot.coherence import enforce_coherence
    from broll_bot.models import Scene, SceneAnalysis, Sentence
    from broll_bot.story_detect import unverified

    texts = ["In twenty twenty-three, analysts at U B S, the Swiss investment bank, took apart a B Y D Seal.",
             "In a survey of owners, one in five reported a fault in the first year.",
             "That sounds like a lot, but most of those faults were small and fixed quickly at the dealer.",
             "Fewer parts is genuinely good news in one way, there is simply less to go wrong.",
             "The biggest complaint was the infotainment system, at twenty-four per cent."]
    times = [0.0, 20.0, 25.0, 30.0, 36.0]
    sents = [Sentence(i, t, 0, times[i], times[i] + 5) for i, t in enumerate(texts)]
    scenes = [Scene(0, texts[0], 0, 20, SceneAnalysis("", ["x"], "")),
              Scene(1, " ".join(texts[1:]), 20, 41, SceneAnalysis("", ["x"], ""))]
    out, _ = auto_templates(scenes, sents, "byd seal", lang="en")
    enforce_coherence(out)
    styles = [s.analysis.style for s in out]
    assert "source" in styles and "peoplegrid" in styles and "warning" in styles
    d = unverified(["As for how reliable they will be after ten years, the honest answer is we do not know yet."], "")
    assert d.fields["caption"] == "As for how reliable they will be after ten years"

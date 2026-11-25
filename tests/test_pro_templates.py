"""Templates novos: coerência com a fala, CTA automático, sons, idiomas e plano da IA."""

from broll_bot.coherence import enforce_coherence
from broll_bot.models import Scene, SceneAnalysis, Sentence
from broll_bot.motion.i18n import text
from broll_bot.motion.planner import TemplatePlanner
from broll_bot.motion.templates import TemplateSpec
from broll_bot.motion.templates_doc import template_cues
from broll_bot.motion.templates_pro import Speedometer, item_starts
from broll_bot.plan_fix import add_cta_overlays


def sc(i, text_, **kw):
    return Scene(i, text_, i * 8.0, i * 8.0 + 8, SceneAnalysis("x", ["car"], "car", **kw))


def test_new_styles_keep_only_spoken_data():
    scenes = [
        sc(0, "O Corolla nasceu em 1966, mudou muito em 2002 e chegou à geração atual em 2020.", style="timeline",
           caption="Evolução", items=[{"label": "1966", "query": "a"}, {"label": "1983", "query": "b"},
                                      {"label": "2002", "query": "c"}, {"label": "2020", "query": "d"}]),
        sc(1, "Ele faz de zero a cem em 9,2 segundos.", style="speedometer",
           number={"value": 9.2, "suffix": "s", "label": "0 a 100"}),
        sc(2, "Os pontos fortes: consumo baixo e revenda. O ruim: manutenção cara.", style="proscons",
           points=[{"text": "Consumo baixo", "positive": True}, {"text": "Teto solar", "positive": True},
                   {"text": "Manutenção cara", "positive": False}]),
        sc(3, "Hoje ele custa em média 130 mil reais.", style="price", number={"value": 99000, "prefix": "R$ "}),
        sc(4, "Tudo que é bom tem prós: econômico e confiável.", style="proscons",
           points=[{"text": "Econômico", "positive": True}, {"text": "Confiável", "positive": True}]),
        sc(5, "Conforto nota nove, consumo nota oito, e a nota final fica em 8,5.", style="score",
           stats=[{"label": "Conforto", "value": "9"}, {"label": "Consumo", "value": "8"}],
           number={"value": 8.5}),
    ]
    enforce_coherence(scenes)
    assert [it["label"] for it in scenes[0].analysis.items] == ["1966", "2002", "2020"]
    assert scenes[1].analysis.style == "speedometer"
    assert [p["text"] for p in scenes[2].analysis.points] == ["Consumo baixo", "Manutenção cara"]
    assert scenes[3].analysis.style == "normal"  # preço que ninguém falou
    assert scenes[4].analysis.style == "checklist"  # só prós
    assert scenes[5].analysis.style == "score" and scenes[5].analysis.number["value"] == 8.5


def test_news_comment_and_magnifier_need_the_spoken_text():
    scenes = [sc(0, "A Toyota anunciou um recall do Corolla por falha no airbag.", style="news",
                 body="Toyota anuncia recall do Corolla por falha no airbag", highlights=["recall do Corolla"]),
              sc(1, "Olha o que o Carlos comentou: melhor carro que já tive.", style="comment",
                 body="Melhor carro que já tive"),
              sc(2, "Repare nos faróis de LED.", style="magnifier", caption="Motor turbo")]
    enforce_coherence(scenes)
    assert [s.analysis.style for s in scenes] == ["news", "comment", "normal"]


def test_cta_gets_the_subscribe_overlay_in_any_language():
    scenes = [sc(0, "Se inscreva no canal e ativa o sininho!"), sc(1, "Abonnez-vous et activez la cloche."),
              sc(2, "Zasubskrybuj kanał i kliknij dzwonek."), sc(3, "O motor é um 2.0 flex.")]
    notes = add_cta_overlays(scenes)
    assert [s.analysis.overlay.get("kind") for s in scenes] == ["subscribe", "subscribe", "subscribe", None]
    assert len(notes) == 3
    enforce_coherence(scenes)  # a fala tem a chamada: o overlay fica
    assert scenes[0].analysis.overlay["kind"] == "subscribe"
    fake = [sc(0, "O motor é forte.", overlay={"kind": "subscribe", "title": ""})]
    enforce_coherence(fake)
    assert fake[0].analysis.overlay == {}


def test_labels_follow_the_script_language():
    assert text("pros", "pt") == "PRÓS" and text("pros", "pl") == "ZALETY" and text("pros", "fr") == "POINTS FORTS"
    assert text("subscribe", "de") == "SUBSCRIBE"  # sem tradução: inglês


def test_items_enter_when_spoken_and_stay_ordered():
    spec = TemplateSpec("timeline", "navy", 1920, 1080, 30, 300,
                        captions=[(0.2, 9, "1966"), (3.1, 9, "2002"), (3.2, 9, "2020")])
    starts = item_starts(spec, 3, first=0.7, gap=0.35)
    assert starts[0] == 0.7 and starts[1] == 3.1 and abs(starts[2] - 3.45) < 1e-9


def test_speedometer_modes():
    accel = TemplateSpec("speedometer", "navy", 1920, 1080, 30, 150, number={"value": 9.2, "suffix": "s"})
    top = TemplateSpec("speedometer", "navy", 1920, 1080, 30, 150, number={"value": 210, "suffix": "km/h"})
    assert Speedometer.mode(accel)[:2] == (True, 100.0)
    is_accel, target, full, _ = Speedometer.mode(top)
    assert not is_accel and target == 210 and full > 210


def test_new_templates_have_synced_sounds():
    specs = {
        "timeline": dict(labels=["1966", "2020"], captions=[(1.0, 5, ""), (3.0, 5, "")]),
        "price": dict(number={"value": 99000, "level": 0.8}),
        "proscons": dict(points=[{"text": "a", "positive": True}, {"text": "b", "positive": False}]),
        "comment": dict(body="Melhor carro que já tive"),
        "poll": dict(keyword="Qual?", labels=["A", "B"]),
    }
    for kind, kw in specs.items():
        spec = TemplateSpec(kind, "navy", 1920, 1080, 30, 180, **kw)
        cues = template_cues(kind, spec)
        assert cues, kind
        assert all(0 <= t <= 6 for t, _, _ in cues), kind


def test_planner_data_checks():
    ok = SceneAnalysis("x", ["a"], "a", style="proscons",
                       points=[{"text": "a", "positive": True}, {"text": "b", "positive": False}])
    only_pros = SceneAnalysis("x", ["a"], "a", style="proscons", points=[{"text": "a", "positive": True}])
    assert TemplatePlanner._data_ok("proscons", ok) and not TemplatePlanner._data_ok("proscons", only_pros)
    assert not TemplatePlanner._data_ok("poll", SceneAnalysis("x", ["a"], "a", caption="Qual?"))
    assert TemplatePlanner._data_ok("news", SceneAnalysis("x", ["a"], "a", body="Recall do Corolla"))


def test_scene_plan_reads_body_note_and_level():
    from broll_bot.scenes import _plan_to_scenes, _ScenePlan

    sents = [Sentence(i, f"frase {i}", 0, i * 3.0, i * 3.0 + 3) for i in range(2)]
    plan = {"scenes": [{"first_sentence": 0, "last_sentence": 1, "visual_summary": "x", "queries": ["car"],
                        "fallback_query": "car", "negative_terms": [], "tone": "calm", "style": "timeline",
                        "items": [{"label": str(y), "query": f"corolla {y}", "note": "E10"} for y in
                                  (1966, 1970, 1974, 1979, 1983, 1987, 1991)],
                        "number": {"value": "129.900", "level": "0.8"}, "body": "Recall do Corolla",
                        "overlay": {"kind": "subscribe", "title": None}}]}
    [s] = _plan_to_scenes(_ScenePlan.model_validate(plan), sents, 6.0)
    a = s.analysis
    assert a.style == "timeline" and len(a.items) == 6 and a.items[0]["note"] == "E10"
    assert a.number["value"] == 129900 and a.number["level"] == 0.8
    assert a.body == "Recall do Corolla" and a.overlay["kind"] == "subscribe"


def test_planner_prepares_new_templates_without_media(tmp_path):
    from broll_bot.config import AppConfig
    from broll_bot.models import Candidate, MediaType, Shot, Transition, Word
    from PIL import Image

    cfg = AppConfig()
    cfg.analysis.script_language = "fr"

    class Sel:
        used_keys: set = set()

        def claim(self, cand):
            return None

    planner = TemplatePlanner(cfg, selector=Sel(), searcher=None, thumbs=None)  # type: ignore[arg-type]
    img = tmp_path / "a.jpg"
    Image.new("RGB", (640, 360), "red").save(img)
    cand = Candidate("pexels", "1", MediaType.IMAGE, 640, 360, 0.0, None, None)
    cases = {
        "proscons": dict(points=[{"text": "Consomme peu", "positive": True}, {"text": "Cher", "positive": False}]),
        "speedometer": dict(number={"value": 9.2, "suffix": "s"}),
        "timeline": dict(items=[{"label": "1966", "query": "a"}, {"label": "2020", "query": "b"}]),
        "comment": dict(body="Meilleure voiture"),
        "magnifier": dict(caption="Phares LED"),
    }
    words = [Word(w, k * 0.4, k * 0.4 + 0.3) for k, w in enumerate("consomme peu mais cher".split())]
    kinds = {}
    for k, (style, kw) in enumerate(cases.items()):
        sc_ = Scene(k, "texte", 0, 6, SceneAnalysis("x", ["x"], "x", style=style, **kw))
        shot = Shot(k, k, 0, 6, 6, Transition("cut", 0), 0, 180, template=style,
                    candidate=cand if style in ("magnifier", "timeline", "comment") else None,
                    media_path=str(img) if style in ("magnifier", "timeline", "comment") else None)
        planner.prepare([shot], {k: sc_}, {}, words)
        kinds[style] = shot.template
        if shot.template:
            assert shot.template_data["lang"] == "fr"
    # timeline sem as fotos das gerações vira card; os outros ficam
    assert kinds == {"proscons": "proscons", "speedometer": "speedometer", "timeline": "card",
                     "comment": "comment", "magnifier": "magnifier"}

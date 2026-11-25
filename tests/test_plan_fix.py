"""Ajustes do plano: cenas longas divididas, buscas de 'título' trocadas, título curto esticado."""

from broll_bot.models import Scene, SceneAnalysis, Sentence
from broll_bot.plan_fix import fix_meta_queries, is_meta, split_long_scenes, stretch_title_templates


def sentences(n, dur=4.0):
    return [Sentence(i, f"Frase {i}.", 0, i * dur, (i + 1) * dur) for i in range(n)]


def an(queries=("car key close up", "man opening car door", "car remote button"), **kw):
    return SceneAnalysis("hand holding a car key", list(queries), "car key", **kw)


def test_long_scene_is_split_at_sentences_with_rotated_queries():
    sents = sentences(10)  # 40 s
    scenes = [Scene(0, "...", 0.0, 40.0, an())]
    out, notes = split_long_scenes(scenes, sents, max_seconds=16, target_seconds=12)
    assert len(out) >= 3 and notes
    assert [s.index for s in out] == list(range(len(out)))
    assert out[0].start == 0.0 and out[-1].end == 40.0
    assert all(a.end == b.start for a, b in zip(out, out[1:]))  # sem buraco nem sobreposição
    assert all(s.duration <= 16 for s in out)
    assert out[1].analysis.queries[0] != out[0].analysis.queries[0]


def test_template_scene_keeps_template_in_first_part_only():
    sents = sentences(10)
    scenes = [Scene(0, "...", 0.0, 40.0, an(style="headline", caption="Dica 1", template_seconds=2.5,
                                            overlay={"kind": "tag", "title": "chave"}))]
    out, _ = split_long_scenes(scenes, sents, max_seconds=16, target_seconds=12)
    assert out[0].analysis.style == "headline"
    assert all(s.analysis.style == "normal" and not s.analysis.overlay for s in out[1:])


def test_short_scenes_are_untouched():
    sents = sentences(3)
    scenes = [Scene(0, "...", 0.0, 12.0, an())]
    out, notes = split_long_scenes(scenes, sents, max_seconds=16, target_seconds=12)
    assert out == scenes and notes == []


def test_meta_queries_are_replaced_by_next_scene_footage():
    assert is_meta("car number two title card") and is_meta("key icon") and not is_meta("car key close up")
    title = Scene(0, "Número dois.", 0, 3, SceneAnalysis("number two title card", ["car number two title card",
                                                                                   "number 2 icon"], "title card",
                                                           style="headline", caption="Dica 2"))
    nxt = Scene(1, "Abra o vidro pela chave.", 3, 10, an())
    notes = fix_meta_queries([title, nxt])
    assert notes and not any(is_meta(q) for q in title.analysis.queries)
    assert title.analysis.queries[0] == "car key close up"
    assert title.analysis.visual_summary == nxt.analysis.visual_summary
    assert title.analysis.fallback_query == "car key"


def test_short_title_scene_is_fully_covered_by_the_template():
    short = Scene(0, "Número um.", 0, 4.2, an(style="chapter", caption="Dica 1", template_seconds=2.0))
    long = Scene(1, "...", 4.2, 14.2, an(style="headline", caption="x", template_seconds=2.0))
    stretch_title_templates([short, long])
    assert short.analysis.template_seconds == 4.2 and long.analysis.template_seconds == 2.0


def test_list_templates_last_the_whole_scene():
    from broll_bot.plan_fix import stretch_item_templates

    tl = Scene(0, "...", 3.0, 18.7, an(style="timeline", template_seconds=10))
    stat = Scene(1, "...", 18.7, 28.0, an(style="stat", template_seconds=5))
    stretch_item_templates([tl, stat])
    assert tl.analysis.template_seconds == 15.5  # o ano de 2020, falado no fim, aparece
    assert stat.analysis.template_seconds == 5


def test_abstract_queries_are_dropped():
    from broll_bot.plan_fix import fix_meta_queries, is_abstract

    assert is_abstract("car chart with bottom highlighted") and is_abstract("car 75 percent on paper")
    assert is_abstract("car report with 39,332 figure") and is_abstract("tiguan sales chart top seller")
    assert not is_abstract("mechanic repairing car") and not is_abstract("range rover evoque driving")
    sc = Scene(0, "Thirty-nine per cent of owners reported a fault.", 0, 5,
               SceneAnalysis("owner at dealership", ["car stat circled percent", "range rover evoque driving",
                                                     "mechanic repairing car"], "car"))
    fix_meta_queries([sc])
    assert sc.analysis.queries == ["range rover evoque driving", "mechanic repairing car"]


def test_channel_call_to_action_at_the_end_searches_the_story_footage():
    """Fim do vídeo da garrafa de Moser: quatro cenas seguidas de "deixa o like, se inscreve" buscavam
    "youtube end card subscribe" e "comment prompt on screen" (fundo genérico e imagem repetida)."""
    story = Scene(0, "A maior ideia da vida de Alfredo Moser foi deixar a lâmpada apagada.", 0, 8,
                  SceneAnalysis("plastic bottle glowing in a dark room", ["plastic bottle skylight", "sunlight through bottle roof"],
                                "bottle light"))
    cta = [Scene(k, "Deixa o like e se inscreve no canal.", 8 + k * 4, 12 + k * 4,
                 SceneAnalysis("host asking viewers to subscribe", ["youtube end card subscribe", "comment prompt on screen",
                                                                    "video suggestion overlay next video"],
                               "call to action youtube")) for k in range(1, 5)]
    notes = fix_meta_queries([story, *cta])
    assert len(notes) == 4
    for sc in cta:
        assert sc.analysis.queries and not any(is_meta(q) for q in sc.analysis.queries)
        assert sc.analysis.queries[0] == "plastic bottle skylight" and not is_meta(sc.analysis.fallback_query)
